"""Bundle simulator route (PRD US-005 line 879).

``POST /v1/simulate-bundle`` accepts a Solana bundle plus context slot and
returns landing probability, expected tip, profit distribution, ALT
compression, CU budget, write-lock contention, and an optional tip-optimizer
recommendation. The response shape is locked by
``solana-plans/api-specs/simulate-bundle.openapi.yaml`` (PRD line 855); the
forthcoming ``test_simulate_bundle_response_matches_spec`` (PRD line 942)
will pin both directions of the contract.

This iteration ships the route surface with engine-backed tip-optimizer math
(reads ``PriorityFeeMarket`` per PRD 1.6 + ``BundleAuction.min_bundle_tip_lamports``
per PRD 1.7); auth, real ALT decoding, real CU estimation, calibration-block
lookup, and fork hydration are explicit follow-ups. Until those land, the
response carries best-effort placeholders that satisfy the OpenAPI schema
without claiming mainnet calibration.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from defi_sim.calibration.thresholds import load_thresholds
from defi_sim_api.auth import verify_request_auth
from defi_sim.core.types import BundleOutcome
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS
from defi_sim.engine.bundle_auction import BundleAuction
from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_loader import ForkLoader
from defi_sim.engine.gas import alt_lookup_cu
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.metrics.replay import (
    compute_bundle_landing_rate,
    compute_slot_inclusion_latency,
    compute_tip_efficiency,
)
from defi_sim_solana.program_ids import COMPUTE_BUDGET_PROGRAM
from defi_sim_solana.replay.corpus import corpus_root

router = APIRouter(prefix="/v1/simulate-bundle", tags=["bundle-simulator"])

# Module-level singletons. PRD line 879 requires the route to "read"
# PriorityFeeMarket (1.6) and BundleAuction (1.7); reusing one instance
# across requests keeps quote stability slot-to-slot, matching the engine's
# in-process scoping. Real wiring to per-engine state is a future iteration.
_PRIORITY_FEE_MARKET = PriorityFeeMarket()
_BUNDLE_AUCTION = BundleAuction()

# Optional ForkLoader injection point (PRD line 879 "optionally `2.3 ForkLoader`").
# Production old-slot fork hydration requires exact as-of-slot account state.
# When no loader is configured, requests carrying ``fork_spec`` fail closed
# instead of silently returning unforked results.
_FORK_LOADER: ForkLoader | None = None

# Solana per-slot CU cap (PRD US-009). Headroom is still a local estimate
# until provider-backed transaction simulation and calibrated replay land.
_SLOT_CU_CAP = 48_000_000
# Conservative per-tx fallback for undecodable placeholder strings. Decodable
# transactions use explicit ComputeBudget limits or message-shape estimates.
_DEFAULT_TX_CU_STUB = 200_000
# Per-tx ALT compression fallback for undecodable placeholder strings.
_STUB_UNCOMPRESSED_BYTES_PER_TX = 1500
_STUB_COMPRESSED_BYTES_PER_TX = 800
_MAX_TX_CU_ESTIMATE = 1_400_000
_SHAPE_BASE_CU = 50_000
_SHAPE_CU_PER_INSTRUCTION = 20_000
_SHAPE_CU_PER_ACCOUNT = 1_000
# Tip-optimizer safety margin (PRD line 905).
_TIP_OPTIMIZER_SAFETY_MARGIN_LAMPORTS = 1_000

# Request-body size cap (PRD line 928 ``test_simulate_bundle_oversized_request_rejected``).
# Sized for the worst-case legitimate payload: 5 txs × 1232 raw bytes ≈ 8.2 KB
# base64-encoded, plus a fork_spec that may carry hundreds of pubkeys in its
# allowlist (44 chars each) and the JSON envelope. 256 KB leaves comfortable
# headroom for that without admitting denial-of-service-shaped requests.
_MAX_REQUEST_BYTES = 256 * 1024
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass(frozen=True)
class _TransactionAnalysis:
    decoded: bool
    serialized_len: int
    instruction_count: int = 0
    static_account_count: int = 0
    writable_accounts: tuple[str, ...] = ()
    used_alts: tuple[str, ...] = ()
    alt_resolved_entries: int = 0
    explicit_cu_limit: int | None = None


class BundleRequestModel(BaseModel):
    txs: list[str] = Field(..., min_length=1, max_length=5)
    tip_lamports: int = Field(..., ge=0)
    tip_recipient: str


class TipOptimizerRequestModel(BaseModel):
    target_percentile: int = Field(..., ge=1, le=99)


class ProtocolForkRequestModel(BaseModel):
    protocol_model: str
    account_pubkey_allowlist: list[str] | None = None


class ForkSpecRequestModel(BaseModel):
    slot: int = Field(..., ge=0)
    protocols: list[ProtocolForkRequestModel] = Field(default_factory=list)
    include_wallet_accounts: list[str] | None = None


class SimulateBundleRequest(BaseModel):
    bundle: BundleRequestModel
    context_slot: int | str
    fork_spec: ForkSpecRequestModel | None = None
    search_tip_optimizer: TipOptimizerRequestModel | None = None

    @field_validator("context_slot")
    @classmethod
    def _validate_context_slot(cls, value: int | str) -> int | str:
        if isinstance(value, str) and value != "latest":
            raise ValueError("context_slot string must be 'latest'")
        if isinstance(value, int) and value < 0:
            raise ValueError("context_slot int must be >= 0")
        return value


class ProfitDistributionModel(BaseModel):
    p10: int | None = None
    p50: int
    p75: int | None = None
    p90: int
    p99: int | None = None


class AltCompressionModel(BaseModel):
    uncompressed_bytes: int = Field(..., ge=0)
    compressed_bytes: int = Field(..., ge=0)
    used_alts: list[str] = Field(default_factory=list)


class CuBudgetModel(BaseModel):
    tx_cu_used: list[int]
    slot_cu_headroom: int = Field(..., ge=0)
    slot_full: bool = False


class WriteLockContentionModel(BaseModel):
    blocking_pubkeys: list[str] = Field(default_factory=list)
    contended_lock_count: int = 0
    relaxed_lock_count: int = 0


class TipOptimizerResultModel(BaseModel):
    target_percentile: int
    minimum_tip_lamports: int = Field(..., ge=0)
    safety_margin_lamports: int = Field(..., ge=0)
    priority_fee_quote_lamports: int = Field(..., ge=0)


class ReplayMetricsBlock(BaseModel):
    """PRD US-006 line 990: bundle simulator response surfaces a relevant
    subset of the replay metric calculators (landing rate, tip efficiency,
    slot inclusion latency) under ``metrics.replay``. Each entry is the
    calculator's JSON-safe output dict — chart components (PRD line 982)
    consume this shape uniformly across run snapshots and bundle simulator
    responses.
    """

    bundle_landing_rate: dict[str, Any]
    tip_efficiency: dict[str, Any]
    slot_inclusion_latency: dict[str, Any]


class MetricsBlock(BaseModel):
    replay: ReplayMetricsBlock


class SimulateBundleResponse(BaseModel):
    expected_tip_to_land_lamports: int = Field(..., ge=0)
    landing_probability: float = Field(..., ge=0.0, le=1.0)
    profit_distribution: ProfitDistributionModel
    alt_compression: AltCompressionModel
    cu_budget: CuBudgetModel
    write_lock_contention: WriteLockContentionModel
    tip_optimizer: TipOptimizerResultModel | None = None
    calibration: dict[str, Any] | None = None
    metrics: MetricsBlock


def _expected_tip_floor() -> int:
    """Floor the auction will reject below, per ``BundleAuction.admit``."""
    return _BUNDLE_AUCTION.min_bundle_tip_lamports


def _landing_probability(tip_lamports: int) -> float:
    """Uncalibrated heuristic: floor tips land 50%, 10x floor lands ~95%.

    FIX-017 replaces this with calibrated corpus or engine-backed output.
    Until then the response must not be presented as a mainnet accuracy claim.
    """
    floor = max(_expected_tip_floor(), 1)
    if tip_lamports <= 0:
        return 0.0
    ratio = tip_lamports / floor
    if ratio <= 1.0:
        return 0.5 * ratio
    # Saturating curve: P(land) = 1 - 0.5 / ratio. ratio=1 -> 0.5; ratio=10 -> 0.95.
    return min(1.0 - 0.5 / ratio, 0.99)


def _profit_distribution(tip_lamports: int) -> ProfitDistributionModel:
    """Uncalibrated profit distribution centered loosely on the paid tip.

    Real distribution comes from bundle EV minus tip + fees in FIX-017. Until
    then this remains a deliberately non-calibrated placeholder.
    """
    p10 = max(tip_lamports // 2, 0)
    p50 = max(2 * tip_lamports, 0)
    p75 = max(3 * tip_lamports, p50)
    p90 = max(4 * tip_lamports, p75)
    p99 = max(6 * tip_lamports, p90)
    return ProfitDistributionModel(p10=p10, p50=p50, p75=p75, p90=p90, p99=p99)


def _decode_base58(value: str) -> bytes:
    number = 0
    for char in value:
        digit = _BASE58_ALPHABET.find(char)
        if digit < 0:
            raise ValueError("invalid base58 character")
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return (b"\x00" * leading_zeroes) + raw


def _transaction_byte_candidates(encoded: str) -> list[bytes]:
    candidates: list[bytes] = []
    try:
        candidates.append(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError):
        pass
    try:
        base58_bytes = _decode_base58(encoded)
    except ValueError:
        base58_bytes = None
    if base58_bytes is not None and base58_bytes not in candidates:
        candidates.append(base58_bytes)
    return candidates


def _analyze_transaction(encoded: str) -> _TransactionAnalysis:
    candidates = _transaction_byte_candidates(encoded)
    if not candidates:
        return _TransactionAnalysis(decoded=False, serialized_len=0)
    from solders.transaction import VersionedTransaction

    tx = None
    raw = candidates[0]
    for candidate in candidates:
        try:
            tx = VersionedTransaction.from_bytes(candidate)
            raw = candidate
            break
        except Exception:
            continue
    if tx is None:
        return _TransactionAnalysis(decoded=False, serialized_len=len(raw))

    message = tx.message
    account_keys = list(getattr(message, "account_keys", []) or [])
    instructions = list(getattr(message, "instructions", []) or [])
    writable_accounts = _writable_static_accounts(message, account_keys)
    used_alts: list[str] = []
    alt_entries = 0
    for lookup in getattr(message, "address_table_lookups", []) or []:
        used_alts.append(str(lookup.account_key))
        alt_entries += len(lookup.writable_indexes) + len(lookup.readonly_indexes)

    explicit_limit: int | None = None
    for instruction in instructions:
        program_index = int(instruction.program_id_index)
        if program_index >= len(account_keys):
            continue
        if str(account_keys[program_index]) != COMPUTE_BUDGET_PROGRAM:
            continue
        data = bytes(instruction.data)
        if len(data) >= 5 and data[0] == 2:
            explicit_limit = int.from_bytes(data[1:5], "little")

    return _TransactionAnalysis(
        decoded=True,
        serialized_len=len(raw),
        instruction_count=len(instructions),
        static_account_count=len(account_keys),
        writable_accounts=writable_accounts,
        used_alts=tuple(used_alts),
        alt_resolved_entries=alt_entries,
        explicit_cu_limit=explicit_limit,
    )


def _analyze_bundle_transactions(txs: list[str]) -> list[_TransactionAnalysis]:
    return [_analyze_transaction(tx) for tx in txs]


def _writable_static_accounts(message: Any, account_keys: list[Any]) -> tuple[str, ...]:
    is_writable = getattr(message, "is_maybe_writable", None)
    if callable(is_writable):
        return tuple(
            str(account)
            for index, account in enumerate(account_keys)
            if bool(is_writable(index))
        )

    header = getattr(message, "header", None)
    if header is None:
        return ()
    required = int(getattr(header, "num_required_signatures", 0))
    readonly_signed = int(getattr(header, "num_readonly_signed_accounts", 0))
    readonly_unsigned = int(getattr(header, "num_readonly_unsigned_accounts", 0))
    signed_writable_end = max(0, required - readonly_signed)
    unsigned_readonly_start = max(required, len(account_keys) - readonly_unsigned)
    writable: list[str] = []
    for index, account in enumerate(account_keys):
        if index < signed_writable_end:
            writable.append(str(account))
        elif required <= index < unsigned_readonly_start:
            writable.append(str(account))
    return tuple(writable)


def _alt_compression(analyses: list[_TransactionAnalysis]) -> AltCompressionModel:
    if not analyses:
        return AltCompressionModel(uncompressed_bytes=0, compressed_bytes=0)
    used_alts: list[str] = []
    uncompressed_bytes = 0
    compressed_bytes = 0
    for analysis in analyses:
        if not analysis.decoded:
            uncompressed_bytes += _STUB_UNCOMPRESSED_BYTES_PER_TX
            compressed_bytes += _STUB_COMPRESSED_BYTES_PER_TX
            continue
        compressed_bytes += analysis.serialized_len
        uncompressed_bytes += analysis.serialized_len + (
            analysis.alt_resolved_entries * 31
        )
        for alt in analysis.used_alts:
            if alt not in used_alts:
                used_alts.append(alt)
    return AltCompressionModel(
        uncompressed_bytes=uncompressed_bytes,
        compressed_bytes=compressed_bytes,
        used_alts=used_alts,
    )


def _estimate_transaction_cu(analysis: _TransactionAnalysis) -> int:
    if not analysis.decoded:
        return _DEFAULT_TX_CU_STUB
    if analysis.explicit_cu_limit is not None:
        return max(0, min(int(analysis.explicit_cu_limit), _MAX_TX_CU_ESTIMATE))
    estimated = (
        _SHAPE_BASE_CU
        + analysis.instruction_count * _SHAPE_CU_PER_INSTRUCTION
        + (analysis.static_account_count + analysis.alt_resolved_entries)
        * _SHAPE_CU_PER_ACCOUNT
        + alt_lookup_cu(len(analysis.used_alts), analysis.alt_resolved_entries)
    )
    return max(1, min(estimated, _MAX_TX_CU_ESTIMATE))


def _cu_budget(analyses: list[_TransactionAnalysis]) -> CuBudgetModel:
    tx_cu_used = [_estimate_transaction_cu(analysis) for analysis in analyses]
    used = sum(tx_cu_used)
    headroom = max(_SLOT_CU_CAP - used, 0)
    return CuBudgetModel(
        tx_cu_used=tx_cu_used,
        slot_cu_headroom=headroom,
        slot_full=headroom == 0,
    )


def _write_lock_contention(
    forked_pubkeys: list[str] | None = None,
) -> WriteLockContentionModel:
    """Contention block — populated from forked state when a ``ForkLoader`` is
    injected and ``fork_spec`` is provided (PRD line 879).

    Without forked state the block is empty (the route doesn't decode raw txs
    yet, so it has no other source of contended pubkeys). With forked state,
    the loader's parsed pool/reserve/market account pubkeys surface here:
    those are the accounts the bundle would write-lock at submission time, so
    they are the candidate contention set under the simulator's stub.
    """
    blocking = list(forked_pubkeys) if forked_pubkeys else []
    return WriteLockContentionModel(
        blocking_pubkeys=blocking,
        contended_lock_count=len(blocking),
        relaxed_lock_count=0,
    )


def _hydrate_forked_pubkeys(
    fork_spec_request: ForkSpecRequestModel | None,
) -> list[str]:
    """Run the injected ``ForkLoader`` against ``fork_spec_request`` and return
    the parsed accounts' pubkeys (PRD US-003 line 483 + line 879).

    Returns ``[]`` when ``fork_spec_request`` is ``None``. If a fork is
    requested without a configured loader, fail closed because production
    hydration would otherwise require exact historical account state.
    """
    if fork_spec_request is None:
        return []
    if _FORK_LOADER is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "fork_spec requires exact historical account-state hydration, "
                "but no ForkLoader is configured"
            ),
        )
    domain_spec = ForkSpec(
        slot=fork_spec_request.slot,
        protocols=[
            ProtocolForkRequest(
                protocol_model=p.protocol_model,
                account_pubkey_allowlist=(
                    list(p.account_pubkey_allowlist)
                    if p.account_pubkey_allowlist is not None
                    else None
                ),
            )
            for p in fork_spec_request.protocols
        ],
        include_wallet_accounts=(
            list(fork_spec_request.include_wallet_accounts)
            if fork_spec_request.include_wallet_accounts is not None
            else None
        ),
    )
    try:
        initial_state = _FORK_LOADER.load(domain_spec)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "fork_spec requires exact historical account-state hydration, "
                f"but the configured ForkLoader cannot satisfy it: {exc}"
            ),
        ) from exc
    return [fragment.pubkey for fragment in initial_state.fragments]


def _replay_metrics_block(
    *,
    landing_probability: float,
    tip_lamports: int,
    profit_p50: int,
    num_txs: int,
    context_slot: int | str,
) -> ReplayMetricsBlock:
    """Compose the three replay metric calculator outputs from the simulator's
    predictions (PRD US-006 line 990).

    The bundle simulator does not run the engine — it returns predicted scalars
    (``landing_probability``, ``profit_distribution``, ...). To surface those
    predictions through the same calculator API used by run snapshots (so chart
    components can read both shapes uniformly), we synthesize the calculator
    inputs from the predictions:

    * ``bundle_landing_rate``: 100 ``BundleOutcome``s with ``landed_count =
      round(landing_probability * 100)`` so the calculator returns the
      simulator's predicted rate at sample_size=100.
    * ``tip_efficiency``: a single ``(tip_lamports, profit_p50)`` sample
      treating the median profit as the bundle's predicted extracted value;
      sample_size=1 (or 0 when tip_lamports==0 to avoid divide-by-zero shape
      surprises downstream).
    * ``slot_inclusion_latency``: a single ``(slot, slot)`` sample with
      latency=0 — the simulator's stub assumption is that the bundle lands
      in the requested context slot. Real distribution lands once the engine
      produces multi-slot replay outcomes.
    """
    # 100-sample synthesis for landing rate so the calculator's value
    # equals the predicted probability rounded to nearest 1%.
    n_samples = 100
    landed_count = int(round(max(0.0, min(1.0, landing_probability)) * n_samples))
    slot_int = context_slot if isinstance(context_slot, int) else 0
    outcomes: list[BundleOutcome] = []
    for i in range(n_samples):
        if i < landed_count:
            outcomes.append(
                BundleOutcome(
                    slot=slot_int,
                    bundle_index=i,
                    status="landed",
                    tip_lamports=tip_lamports,
                    validator_revenue_lamports=tip_lamports // 2,
                    stake_pool_revenue_lamports=tip_lamports - tip_lamports // 2,
                    num_txs=num_txs,
                )
            )
        else:
            outcomes.append(
                BundleOutcome(
                    slot=slot_int,
                    bundle_index=i,
                    status="dropped",
                    tip_lamports=0,
                    validator_revenue_lamports=0,
                    stake_pool_revenue_lamports=0,
                    num_txs=num_txs,
                    drop_reason="STUB_PREDICTED_NOT_LANDED",
                )
            )
    landing_rate = compute_bundle_landing_rate(outcomes)

    # tip_efficiency: one (tip, ev) sample. Skip when tip==0 (yields zero-sample
    # sentinel) since a zero-tip bundle has no meaningful efficiency reading.
    tip_samples: list[tuple[int, int]] = []
    if tip_lamports > 0:
        tip_samples.append((int(tip_lamports), int(profit_p50)))
    tip_efficiency = compute_tip_efficiency(tip_samples)

    # slot_inclusion_latency: single zero-latency sample at the context slot.
    latency = compute_slot_inclusion_latency([(slot_int, slot_int)])

    return ReplayMetricsBlock(
        bundle_landing_rate={
            "value": landing_rate.value,
            "unit": landing_rate.unit,
            "sample_size": landing_rate.sample_size,
        },
        tip_efficiency={
            "value": tip_efficiency.value,
            "unit": tip_efficiency.unit,
            "sample_size": tip_efficiency.sample_size,
        },
        slot_inclusion_latency={
            "value": latency.headline.value,
            "unit": latency.unit,
            "sample_size": latency.sample_size,
            "mean": latency.mean,
            "median": latency.median,
            "p95": latency.p95,
            "p99": latency.p99,
            "samples": list(latency.samples),
        },
    )


def _calibration_block(context_slot: int | str) -> dict[str, Any] | None:
    """Build the calibration metadata block when ``context_slot`` is covered by
    the calibration corpus (PRD US-005 line 914 / US-004 line 875).

    Returns ``None`` for ``"latest"``, uncovered integer slots, development
    corpus placeholders, and marker-only manifests. A manifest must explicitly
    set calibration markers and also carry real expected bundle metrics,
    provenance, and non-empty proof artifacts before the simulator surfaces
    calibration.

    When covered, returns a dict matching ``CalibrationBlock`` in
    ``solana-plans/api-specs/simulate-bundle.openapi.yaml``: ``calibrated_at``
    (manifest mtime as ISO-8601 UTC), ``corpus_slot``, and ``metric_thresholds``
    keyed by metric name with the per-row ``relative`` / ``absolute`` band.
    """
    if not isinstance(context_slot, int):
        return None
    slot_dir = corpus_root() / str(context_slot)
    manifest_path = slot_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        import yaml

        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("fixture_kind") != "calibration":
        return None
    if manifest.get("calibrated") is not True:
        return None
    if manifest.get("mainnet_accuracy_claim") is not True:
        return None
    if not str(manifest.get("calibration_source") or "").strip():
        return None
    if not _manifest_has_bundle_calibration_evidence(manifest, slot_dir):
        return None
    calibrated_at = (
        datetime.fromtimestamp(manifest_path.stat().st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    thresholds = load_thresholds()
    metric_thresholds: dict[str, dict[str, Any]] = {}
    for metric, threshold in thresholds.items():
        row: dict[str, Any] = {"supported": True}
        if threshold.threshold_relative is not None:
            row["relative"] = threshold.threshold_relative
        if threshold.threshold_absolute is not None:
            row["absolute"] = threshold.threshold_absolute
        metric_thresholds[metric] = row
    return {
        "calibrated_at": calibrated_at,
        "corpus_slot": context_slot,
        "metric_thresholds": metric_thresholds,
    }


def _manifest_has_bundle_calibration_evidence(
    manifest: dict[str, Any], slot_dir: Any
) -> bool:
    expected = manifest.get("expected")
    expected_metrics = manifest.get("expected_metrics")
    metrics = expected_metrics if isinstance(expected_metrics, dict) else expected
    if not isinstance(metrics, dict):
        return False
    landing_probability = _manifest_metric(
        metrics, "bundle_landing_probability", "landing_probability"
    )
    if not isinstance(landing_probability, int | float) or not (
        0 <= landing_probability <= 1
    ):
        return False
    provenance = (
        manifest.get("calibration_provenance")
        or manifest.get("provenance")
        or metrics.get("provenance")
    )
    if not str(provenance or "").strip():
        return False
    return _manifest_artifacts_have_proof(manifest, slot_dir)


def _manifest_metric(metrics: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metrics:
            return metrics[key]
    return None


def _manifest_artifacts_have_proof(manifest: dict[str, Any], slot_dir: Any) -> bool:
    raw_paths = manifest.get("artifact_paths") or manifest.get("proof_artifacts")
    if not isinstance(raw_paths, list) or not raw_paths:
        return False
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return False
        path = (slot_dir / raw_path).resolve()
        try:
            path.relative_to(slot_dir.resolve())
        except ValueError:
            return False
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict) or not payload:
            return False
    return True


def _tip_optimizer_result(
    target_percentile: int,
    *,
    lock_set: frozenset[str],
    priority_fee_account: str,
) -> TipOptimizerResultModel:
    """Compose tip optimizer per PRD line 905.

    ``minimum_tip = BundleAuction.tip_quote(lock_set, percentile) +
    safety_margin``. Decodable bundle transactions use their static writable
    accounts as the local lock cohort. Undecodable placeholder payloads fall
    back to the request's tip recipient so development callers still receive a
    stable quote without claiming decoded lock coverage.

    ``priority_fee_quote_lamports`` surfaces
    ``PriorityFeeMarket.quote(priority_fee_account, percentile)`` separately — it is
    reported alongside the Jito tip per PRD line 905, *not* added to it (the
    priority-fee market is for CU-price guidance, not tip bidding).
    """
    tip_quote = _BUNDLE_AUCTION.tip_quote(lock_set, target_percentile)
    minimum_tip = tip_quote + _TIP_OPTIMIZER_SAFETY_MARGIN_LAMPORTS
    pf_quote = _PRIORITY_FEE_MARKET.quote(priority_fee_account, target_percentile)
    return TipOptimizerResultModel(
        target_percentile=target_percentile,
        minimum_tip_lamports=minimum_tip,
        safety_margin_lamports=_TIP_OPTIMIZER_SAFETY_MARGIN_LAMPORTS,
        priority_fee_quote_lamports=int(pf_quote),
    )


def _bundle_lock_set(
    analyses: list[_TransactionAnalysis],
    *,
    fallback_tip_recipient: str,
) -> frozenset[str]:
    writable_accounts: set[str] = set()
    for analysis in analyses:
        writable_accounts.update(analysis.writable_accounts)
    if writable_accounts:
        return frozenset(writable_accounts)
    return frozenset({fallback_tip_recipient})


@router.post(
    "",
    response_model=SimulateBundleResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate a bundle and return tip / landing / profit metrics.",
)
def post_simulate_bundle(
    body: SimulateBundleRequest, request: Request, response: Response
) -> SimulateBundleResponse:
    # PRD lines 881-884: auth check happens before any payload work. Open mode
    # (no DEFI_SIM_API_KEYS configured) returns key_id=None and no header is
    # set — historical/dev behavior. A matched bearer or session cookie returns
    # the key id, surfaced in X-API-Key-Id for support reference (line 884).
    key_id = verify_request_auth(request)
    if key_id is not None:
        response.headers["X-API-Key-Id"] = key_id

    # PRD line 928: oversized requests must be rejected. Check Content-Length
    # against ``_MAX_REQUEST_BYTES`` and return 413 before doing any
    # engine-side work. If the header is absent (unusual for JSON clients) we
    # fall back to the post-parse body length, which is bounded by what
    # FastAPI already loaded into ``body``.
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > _MAX_REQUEST_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"request body exceeds maximum {_MAX_REQUEST_BYTES} bytes",
        )

    return simulate_bundle_internal(body)


def simulate_bundle_internal(body: SimulateBundleRequest) -> SimulateBundleResponse:
    """Run bundle simulation core logic for both REST and JSON-RPC adapters.

    The REST route owns HTTP-only concerns (auth and payload-size enforcement).
    JSON-RPC compatibility translates Solana-shaped calls into this same
    request model so 2.10 does not reimplement bundle simulation.
    """
    bundle = body.bundle
    if bundle.tip_lamports < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tip_lamports must be non-negative",
        )
    if bundle.tip_lamports > 0 and bundle.tip_lamports < MIN_BUNDLE_TIP_LAMPORTS:
        # PRD US-011 line 832 sets the Jito floor; flag below-floor tips as
        # 400 so integrators learn the constraint without consuming a 200.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"tip_lamports {bundle.tip_lamports} below Jito minimum "
                f"{MIN_BUNDLE_TIP_LAMPORTS}"
            ),
        )

    analyses = _analyze_bundle_transactions(bundle.txs)
    num_txs = len(analyses)
    lock_set = _bundle_lock_set(analyses, fallback_tip_recipient=bundle.tip_recipient)
    tip_optimizer = (
        _tip_optimizer_result(
            body.search_tip_optimizer.target_percentile,
            lock_set=lock_set,
            priority_fee_account=bundle.tip_recipient,
        )
        if body.search_tip_optimizer is not None
        else None
    )

    landing_prob = _landing_probability(bundle.tip_lamports)
    profit = _profit_distribution(bundle.tip_lamports)
    cu_budget = _cu_budget(analyses)
    metrics = MetricsBlock(
        replay=_replay_metrics_block(
            landing_probability=landing_prob,
            tip_lamports=bundle.tip_lamports,
            profit_p50=profit.p50,
            num_txs=num_txs,
            context_slot=body.context_slot,
        )
    )
    forked_pubkeys = _hydrate_forked_pubkeys(body.fork_spec)

    return SimulateBundleResponse(
        expected_tip_to_land_lamports=_expected_tip_floor(),
        landing_probability=landing_prob,
        profit_distribution=profit,
        alt_compression=_alt_compression(analyses),
        cu_budget=cu_budget,
        write_lock_contention=_write_lock_contention(forked_pubkeys),
        tip_optimizer=tip_optimizer,
        calibration=_calibration_block(body.context_slot),
        metrics=metrics,
    )
