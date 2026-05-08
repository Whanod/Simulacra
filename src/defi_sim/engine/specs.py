"""Typed run specs and factories for JSON-driven simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from typing import Any, Callable, Literal, Mapping

import numpy as np

from defi_sim.agents.arbitrageur import ArbitrageParams, Arbitrageur
from defi_sim.agents.informed import InformedParams, InformedTrader
from defi_sim.agents.jito_searcher import JitoSearcher, JitoSearcherParams
from defi_sim.agents.lp import LPParams, PassiveLP, RebalancingLP
from defi_sim.agents.manipulator import Manipulator, ManipulatorParams
from defi_sim.agents.noise import NoiseParams, NoiseTrader
from defi_sim.agents.swap_noise import SwapNoiseParams, SwapNoiseTrader
from defi_sim.agents.tip_curve import TipCurveSpec
from defi_sim.agents.validator import Validator, ValidatorParams
from defi_sim.core.agent import DelayedInformation, FullTransparency, InformationFilter
from defi_sim.core.clock import BlockClock, Clock, SolanaSlotClock, VariableBlockClock
from defi_sim.core.market import Market
from defi_sim.core.types import Action, AgentId, FLOAT_MODE, FIXED_POINT, Numeric, NumericMode, Token
from defi_sim.engine.bundle import MAX_BUNDLE_TXS, MIN_BUNDLE_TIP_LAMPORTS
from defi_sim.engine.blockhash import BLOCKHASH_VALIDITY_SLOTS, BlockhashHistory
from defi_sim.engine.bundle_auction import (
    DEFAULT_JITO_TIP_ACCOUNTS,
    BundleAuction,
)
from defi_sim.engine.compute_budget import ComputeBudget, ComputeBudgetSource
from defi_sim.engine.fork import ChainReorgForkSpec
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.execution import (
    BatchExecution,
    DirectExecution,
    ExecutionModel,
    SolanaLikeExecution,
)
from defi_sim.engine.feeds import CompositeFeed, HistoricalFeed, StochasticFeed
from defi_sim.engine.gas import (
    ComputeUnitCost,
    EIP1559Cost,
    FixedCost,
    TransactionCostModel,
    TypedCost,
    ZeroCost,
)
from defi_sim.engine.json import decode_jsonable, to_json, to_jsonable
from defi_sim.engine.leader_schedule import LeaderSchedule, ValidatorStake
from defi_sim.engine.metadata import schema_and_defaults
from defi_sim.engine.ordering import FIFOOrdering, OrderingStrategy, PriorityOrdering, RandomOrdering, SandwichOrdering
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.scheduler import deserialize_scheduler
from defi_sim.engine.submission_priors import SubmissionPathPriors
from defi_sim.engine.world import World
from defi_sim.fees.models import dynamic_fee, flat_fee, spread_fee, tiered_fee, time_weighted_fee
from defi_sim.markets.cfamm import CfammMarket
from defi_sim.markets.clob import ClobMarket
from defi_sim.markets.whirlpool import WhirlpoolMarket


SpecFactory = Callable[[Any], Any]


@dataclass(slots=True, frozen=True)
class EntityMetadata:
    """Optional enrichment attached to a factory registration (BE-001).

    The backend `/registry` contract (BE-002) exposes this metadata so the
    frontend can render schema-driven editors without hardcoding per-type
    knowledge. Every field except ``label`` is optional; factories that
    register without metadata still work, and the registry endpoint
    falls back to a title-cased label derived from the spec type.

    The ``schema`` and ``ui_schema`` fields mirror the shapes documented in
    the frontend refactor plan (`refactor.md`, Target Architecture §1 and §6):

    - ``schema``: JSON Schema for the factory's params block, typically
      derived from a per-entity params dataclass via
      :func:`defi_sim.engine.metadata.schema_for_dataclass` (BE-004).
    - ``ui_schema``: hand-authored presentation hints — field order,
      labels, widget hints, sectioning, ``specialEditor`` keys.
    - ``defaults``: default param values, also typically derived from a
      params dataclass.
    - ``badges``: short tags displayed next to the entity name.
    - ``builder_supported``: when ``False``, the frontend renders the
      entity read-only (e.g. historical/composite feeds before a
      structured editor exists).
    """

    label: str
    description: str = ""
    schema: dict[str, Any] | None = None
    ui_schema: dict[str, Any] | None = None
    defaults: dict[str, Any] | None = None
    badges: tuple[dict[str, str], ...] | None = None
    builder_supported: bool = True
    examples: tuple[dict[str, Any], ...] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class TokenSpec:
    """Token configuration for a spec payload.

    The ``decimals`` default of 18 is preserved for legacy / chain-neutral
    artifact compatibility: scenarios saved before the Solana pivot omitted
    the field and assumed 18-decimal tokens. Solana-shaped specs must write
    explicit decimals at creation time (``SOL=9``, ``USDC=6``); see
    :func:`default_tokens_for_execution`.

    Future migration note: a global flip to 9-decimal defaults requires a
    schema-version bump and a one-time artifact migration that writes
    explicit decimals into every legacy fixture before the default changes.

    US-007 (Phase 1.9) adds a ``standard`` discriminator plus optional
    LST and Token-2022 surfaces. All new fields are additive with
    ``None``/``"spl"`` defaults so legacy artifacts remain compatible.
    The companion specs ``ExchangeRateDriftSpec`` and ``TransferHookSpec``
    are defined in follow-up tasks; this dataclass references them via
    forward strings (``from __future__ import annotations`` is active).

    The ``confidential`` flag (PRD line 580) is meaningful only when
    ``standard == "spl_2022"`` and is a stub — confidential-transfer
    mechanics are not simulated; arithmetic still runs on the public
    balance. The flag exists so future work can branch on it without
    a schema bump.
    """

    id: str
    symbol: str
    decimals: int = 18
    standard: Literal["native", "spl", "spl_2022"] = "spl"
    exchange_rate_to_sol: Decimal | None = None
    exchange_rate_drift: "ExchangeRateDriftSpec | None" = None
    transfer_hook: "TransferHookSpec | None" = None
    confidential: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TokenSpec":
        payload = decode_jsonable(dict(data))
        rate_raw = payload.get("exchange_rate_to_sol")
        rate = Decimal(str(rate_raw)) if rate_raw is not None else None
        drift_raw = payload.get("exchange_rate_drift")
        drift = (
            ExchangeRateDriftSpec.from_dict(drift_raw)
            if isinstance(drift_raw, Mapping)
            else drift_raw
        )
        hook_raw = payload.get("transfer_hook")
        hook = (
            TransferHookSpec.from_dict(hook_raw)
            if isinstance(hook_raw, Mapping)
            else hook_raw
        )
        return cls(
            id=payload["id"],
            symbol=payload["symbol"],
            decimals=payload.get("decimals", 18),
            standard=payload.get("standard", "spl"),
            exchange_rate_to_sol=rate,
            exchange_rate_drift=drift,
            transfer_hook=hook,
            confidential=bool(payload.get("confidential", False)),
        )


def default_tokens_for_execution(execution_type: str | None) -> list[TokenSpec]:
    """Return the canonical token list for a given execution model.

    For ``solana_like`` returns ``[SOL(decimals=9), USDC(decimals=6)]`` so
    builder-generated Solana specs and templates always write explicit
    decimals into the payload. For any other / unknown execution type
    returns an empty list — callers fall back to whatever neutral tokens
    they were already producing.

    This helper does not change ``TokenSpec`` deserialization semantics:
    legacy artifacts that omit ``decimals`` still load as 18-decimal tokens.
    """

    if execution_type == "solana_like":
        return [
            TokenSpec(id="SOL", symbol="SOL", decimals=9),
            TokenSpec(id="USDC", symbol="USDC", decimals=6),
        ]
    return []


@dataclass(slots=True)
class ExchangeRateDriftSpec:
    """LST exchange-rate drift parameters (US-007, PRD line 554).

    Drives per-epoch advancement of an LST's ``exchange_rate_to_sol`` via
    ``rate *= 1 + drift_per_epoch + N(0, volatility_per_epoch)``. The
    ``drift_per_epoch`` baseline of ``0.0001`` is roughly mSOL's historical
    ~7%/year stake rate (1bps/epoch * 432 epochs/year ≈ 4.3%; calibrated
    against current mainnet feeds in Phase 2.4).

    ``volatility_per_epoch`` controls the per-epoch standard deviation of
    the multiplicative noise term. Set to ``0.0`` for deterministic drift.
    ``seed`` makes the noise reproducible per-spec; ``None`` lets the
    engine derive a seed from the global RNG.
    """

    drift_per_epoch: float = 0.0001
    volatility_per_epoch: float = 0.0
    seed: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExchangeRateDriftSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            drift_per_epoch=float(payload.get("drift_per_epoch", 0.0001)),
            volatility_per_epoch=float(payload.get("volatility_per_epoch", 0.0)),
            seed=payload.get("seed"),
        )


@dataclass(slots=True)
class TransferHookSpec:
    """SPL Token-2022 transfer-hook overhead spec (US-007, PRD line 562).

    On every SPL-2022 transfer for a token whose ``TokenSpec.transfer_hook``
    is non-None, the engine adds ``additional_cu_per_transfer`` compute
    units and ``additional_lamports_per_transfer`` lamports of overhead.

    No actual transfer-hook program execution is simulated — this is an
    overhead-only stub. ``program_id`` is the on-chain program address
    (informational only; ``None`` means no hook installed and the engine
    skips the overhead).
    """

    program_id: str | None = None
    additional_cu_per_transfer: int = 0
    additional_lamports_per_transfer: int = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TransferHookSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            program_id=payload.get("program_id"),
            additional_cu_per_transfer=int(payload.get("additional_cu_per_transfer", 0)),
            additional_lamports_per_transfer=int(payload.get("additional_lamports_per_transfer", 0)),
        )


@dataclass(slots=True)
class PairSpec:
    base: TokenSpec
    quote: TokenSpec

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PairSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            base=TokenSpec.from_dict(payload["base"]),
            quote=TokenSpec.from_dict(payload["quote"]),
        )


@dataclass(slots=True)
class ClockSpec:
    type: str = "block"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClockSpec":
        payload = decode_jsonable(dict(data))
        return cls(type=payload.get("type", "block"), params=dict(payload.get("params", {})))


@dataclass(slots=True)
class SlotClockSpec:
    """Typed spec for the Solana slot clock (US-001).

    Mirrors the params consumed by ``SolanaSlotClock``; callers can either
    pass a ``SlotClockSpec`` to ``build_clock`` (it is converted to a
    generic ``ClockSpec`` with ``type='solana_slot'``) or write the
    generic form directly.
    """

    slot_duration_seconds: float = 0.4
    epoch_length_slots: int = 432_000
    skip_rate: float = 0.0
    seed: int | None = None
    genesis: int = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SlotClockSpec":
        payload = decode_jsonable(dict(data))
        params = payload.get("params") if "params" in payload else payload
        return cls(
            slot_duration_seconds=float(params.get("slot_duration_seconds", 0.4)),
            epoch_length_slots=int(params.get("epoch_length_slots", 432_000)),
            skip_rate=float(params.get("skip_rate", 0.0)),
            seed=params.get("seed"),
            genesis=int(params.get("genesis", 0)),
        )

    def to_clock_spec(self) -> "ClockSpec":
        return ClockSpec(
            type="solana_slot",
            params={
                "slot_duration_seconds": self.slot_duration_seconds,
                "epoch_length_slots": self.epoch_length_slots,
                "skip_rate": self.skip_rate,
                "seed": self.seed,
                "genesis": self.genesis,
            },
        )


@dataclass(slots=True)
class ValidatorStakeSpec:
    """Single validator entry for ``LeaderScheduleSpec``."""

    pubkey: str
    stake_lamports: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidatorStakeSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            pubkey=str(payload["pubkey"]),
            stake_lamports=int(payload["stake_lamports"]),
        )


@dataclass(slots=True)
class LeaderScheduleSpec:
    """Typed spec for the stake-weighted leader schedule (US-001).

    The default spec type is ``stake_weighted``; the validator list is
    a primitive seed (1-validator default for the Solana template). 1.10
    introduces ``LeaderSchedule.from_validator_agents(...)`` which
    re-seeds the schedule from full ``Validator`` agents — the primitive
    form here continues to support tests and standalone scenarios.
    """

    type: str = "stake_weighted"
    validators: list[ValidatorStakeSpec] = field(default_factory=list)
    seed: int = 0
    epoch_length_slots: int = 432_000

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LeaderScheduleSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            type=payload.get("type", "stake_weighted"),
            validators=[
                ValidatorStakeSpec.from_dict(v) for v in payload.get("validators", [])
            ],
            seed=int(payload.get("seed", 0)),
            epoch_length_slots=int(payload.get("epoch_length_slots", 432_000)),
        )


@dataclass(slots=True)
class ComputeBudgetSourceSpec:
    """Activation context for a non-current ``ComputeBudgetSpec`` preset."""

    activation_slot: int
    reference: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComputeBudgetSourceSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            activation_slot=int(payload["activation_slot"]),
            reference=str(payload["reference"]),
        )

    def to_source(self) -> ComputeBudgetSource:
        return ComputeBudgetSource(
            activation_slot=self.activation_slot,
            reference=self.reference,
        )


@dataclass(slots=True)
class ComputeBudgetSpec:
    """Typed spec mirroring ``ComputeBudget`` (US-002).

    Defaults match current Solana mainnet caps; the same triple feeds the
    ``solana_like`` execution preset when no explicit spec is supplied.
    Non-current presets must carry source metadata to round-trip into a
    valid ``ComputeBudget`` (per PRD line 180).
    """

    per_slot: int = 60_000_000
    per_tx: int = 1_400_000
    per_writable_account: int = 12_000_000
    source: ComputeBudgetSourceSpec | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComputeBudgetSpec":
        payload = decode_jsonable(dict(data))
        source_payload = payload.get("source")
        return cls(
            per_slot=int(payload.get("per_slot", 60_000_000)),
            per_tx=int(payload.get("per_tx", 1_400_000)),
            per_writable_account=int(payload.get("per_writable_account", 12_000_000)),
            source=(
                ComputeBudgetSourceSpec.from_dict(source_payload)
                if source_payload is not None
                else None
            ),
        )

    def to_compute_budget(self) -> ComputeBudget:
        return ComputeBudget(
            per_slot=self.per_slot,
            per_tx=self.per_tx,
            per_writable_account=self.per_writable_account,
            source=self.source.to_source() if self.source is not None else None,
        )

    @classmethod
    def from_compute_budget(cls, budget: ComputeBudget) -> "ComputeBudgetSpec":
        return cls(
            per_slot=budget.per_slot,
            per_tx=budget.per_tx,
            per_writable_account=budget.per_writable_account,
            source=(
                ComputeBudgetSourceSpec(
                    activation_slot=budget.source.activation_slot,
                    reference=budget.source.reference,
                )
                if budget.source is not None
                else None
            ),
        )


@dataclass(slots=True)
class PriorityFeeMarketPreRollSpec:
    """Pre-roll seed for the priority fee market (Phase 1.5 lighthouse, PRD US-001 line 58).

    Drives synthetic ``observe()`` calls before slot 0 so the per-account
    distributions are non-degenerate when the simulation starts. Without
    pre-roll, the lighthouse scenario's percentiles collapse to the
    engine-level floor for the first ~``window_slots`` slots, which
    invalidates EV math for any agent that quotes a percentile before
    the warm-up has happened.

    All observations are deterministic given ``seed``.
    """

    slots: int = 0
    accounts: tuple[str, ...] = ()
    cu_price_min: int = 1
    cu_price_max: int = 1
    observations_per_slot: int = 1
    seed: int = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriorityFeeMarketPreRollSpec":
        payload = decode_jsonable(dict(data))
        accounts_raw = payload.get("accounts", ())
        accounts: tuple[str, ...]
        if isinstance(accounts_raw, (list, tuple)):
            accounts = tuple(str(a) for a in accounts_raw)
        else:
            accounts = ()
        cu_min = int(payload.get("cu_price_min", 1))
        cu_max = int(payload.get("cu_price_max", cu_min))
        if cu_max < cu_min:
            raise ValueError(
                f"PriorityFeeMarket pre_roll: cu_price_max ({cu_max}) must be "
                f">= cu_price_min ({cu_min})"
            )
        return cls(
            slots=int(payload.get("slots", 0)),
            accounts=accounts,
            cu_price_min=cu_min,
            cu_price_max=cu_max,
            observations_per_slot=int(payload.get("observations_per_slot", 1)),
            seed=int(payload.get("seed", 0)),
        )

    def is_active(self) -> bool:
        return self.slots > 0 and len(self.accounts) > 0


@dataclass(slots=True)
class PriorityFeeMarketSpec:
    """Typed spec for ``PriorityFeeMarket`` (US-010, PRD line 746).

    Defaults match the PRD-stated 150 slot window and 30 slot EWMA half-life.
    The floor is the engine-level guard against zero-quote on unseen accounts.
    """

    window_slots: int = 150
    ewma_half_life_slots: int = 30
    floor_micro_lamports: int = 1
    update_event_threshold: float = 0.05
    pre_roll: PriorityFeeMarketPreRollSpec | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriorityFeeMarketSpec":
        payload = decode_jsonable(dict(data))
        pre_roll_raw = payload.get("pre_roll")
        pre_roll = (
            PriorityFeeMarketPreRollSpec.from_dict(pre_roll_raw)
            if isinstance(pre_roll_raw, dict)
            else None
        )
        return cls(
            window_slots=int(payload.get("window_slots", 150)),
            ewma_half_life_slots=int(payload.get("ewma_half_life_slots", 30)),
            floor_micro_lamports=int(payload.get("floor_micro_lamports", 1)),
            update_event_threshold=float(payload.get("update_event_threshold", 0.05)),
            pre_roll=pre_roll,
        )

    def to_priority_fee_market(self) -> PriorityFeeMarket:
        pfm = PriorityFeeMarket(
            window_slots=self.window_slots,
            ewma_half_life_slots=self.ewma_half_life_slots,
            floor_micro_lamports=self.floor_micro_lamports,
            update_event_threshold=self.update_event_threshold,
        )
        if self.pre_roll is not None and self.pre_roll.is_active():
            _seed_pre_roll(pfm, self.pre_roll)
        return pfm


def _seed_pre_roll(
    pfm: PriorityFeeMarket, spec: PriorityFeeMarketPreRollSpec
) -> None:
    """Drive deterministic synthetic observations into ``pfm`` before slot 0.

    Uses negative slot indices ``[-N, -1]`` so genuine slot-0 observations
    don't collide with seed entries, and so a snapshot reader can tell
    seed observations from real ones by sign. The PriorityFeeMarket's
    ring buffer caps the surviving entries at ``window_slots``, so a
    longer pre-roll just means the recent end of the window is denser.
    """

    import random

    rng = random.Random(spec.seed)
    obs_per_slot = max(1, spec.observations_per_slot)
    for slot_idx in range(spec.slots):
        slot = -(spec.slots - slot_idx)
        for account in spec.accounts:
            for _ in range(obs_per_slot):
                price = rng.randint(spec.cu_price_min, spec.cu_price_max)
                pfm.observe(account, slot, price)


@dataclass(slots=True)
class BundleAuctionSpec:
    """Typed spec for ``BundleAuction`` (US-011, PRD line 890).

    Defaults: ``max_bundles_per_slot=5``, ``jito_stake_pool_share=0.05``
    (5% to JitoSOL stakers, 95% to the validator), and the 8 well-known
    Jito tip-account pubkeys. The dataclass invariants
    (``max_bundle_txs``, ``min_bundle_tip_lamports``) mirror the
    ``Bundle`` / Jito-mainnet defaults so a calibration scenario can
    tighten admission below the dataclass-level guard if desired.

    ``tip_quote_curve_path`` (FIX-020) optionally points at a fitted
    :class:`defi_sim_solana.calibration.TipQuoteCurve` YAML. When set, the
    auction's :meth:`BundleAuction.tip_quote` Beta-blends the calibrated
    prior with in-process observations so a fresh run quotes a sensible
    cohort tip rather than the floor for ~150 slots.
    """

    max_bundles_per_slot: int = 5
    jito_stake_pool_share: float = 0.05
    tip_account_set: tuple[str, ...] = DEFAULT_JITO_TIP_ACCOUNTS
    max_bundle_txs: int = MAX_BUNDLE_TXS
    min_bundle_tip_lamports: int = MIN_BUNDLE_TIP_LAMPORTS
    tip_quote_curve_path: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BundleAuctionSpec":
        payload = decode_jsonable(dict(data))
        tip_accounts_raw = payload.get("tip_account_set")
        if tip_accounts_raw is None:
            tip_account_set = DEFAULT_JITO_TIP_ACCOUNTS
        else:
            tip_account_set = tuple(str(a) for a in tip_accounts_raw)
        curve_path_raw = payload.get("tip_quote_curve_path")
        return cls(
            max_bundles_per_slot=int(payload.get("max_bundles_per_slot", 5)),
            jito_stake_pool_share=float(payload.get("jito_stake_pool_share", 0.05)),
            tip_account_set=tip_account_set,
            max_bundle_txs=int(payload.get("max_bundle_txs", MAX_BUNDLE_TXS)),
            min_bundle_tip_lamports=int(
                payload.get("min_bundle_tip_lamports", MIN_BUNDLE_TIP_LAMPORTS)
            ),
            tip_quote_curve_path=(
                str(curve_path_raw) if curve_path_raw is not None else None
            ),
        )

    def to_bundle_auction(self) -> BundleAuction:
        curve = None
        if self.tip_quote_curve_path:
            from pathlib import Path

            from defi_sim_solana.calibration import load_tip_quote_curve
            from defi_sim_solana.replay.corpus import corpus_root

            path = Path(self.tip_quote_curve_path)
            if not path.is_absolute():
                # The lighthouse template ships a relative path
                # ("solana-plans/calibration/jito_tip_curves.yaml") so the
                # repo can be checked out anywhere. Resolve it against the
                # CWD first (cheap), then fall back to the repo root via
                # the same anchor used by ``corpus_root()``.
                if not path.exists():
                    repo_root = corpus_root().resolve().parents[2]
                    candidate = repo_root / path
                    if candidate.exists():
                        path = candidate
            curve = load_tip_quote_curve(path)
        return BundleAuction(
            max_bundle_txs=self.max_bundle_txs,
            min_bundle_tip_lamports=self.min_bundle_tip_lamports,
            max_bundles_per_slot=self.max_bundles_per_slot,
            jito_stake_pool_share=self.jito_stake_pool_share,
            tip_account_set=self.tip_account_set,
            tip_quote_curve=curve,
        )


@dataclass(slots=True)
class OrderingSpec:
    type: str = "fifo"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OrderingSpec":
        payload = decode_jsonable(dict(data))
        return cls(type=payload.get("type", "fifo"), params=dict(payload.get("params", {})))


@dataclass(slots=True)
class GasSpec:
    type: str = "zero"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GasSpec":
        payload = decode_jsonable(dict(data))
        return cls(type=payload.get("type", "zero"), params=dict(payload.get("params", {})))


@dataclass(slots=True)
class InformationFilterSpec:
    type: str = "full_transparency"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InformationFilterSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            type=payload.get("type", "full_transparency"),
            params=dict(payload.get("params", {})),
        )


@dataclass(slots=True)
class FeeModelSpec:
    type: str = "flat"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeeModelSpec":
        payload = decode_jsonable(dict(data))
        return cls(type=payload["type"], params=dict(payload.get("params", {})))


@dataclass(slots=True)
class FeedSpec:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    feeds: dict[str, "FeedSpec"] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeedSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            type=payload["type"],
            params=dict(payload.get("params", {})),
            feeds={
                token_id: FeedSpec.from_dict(inner)
                for token_id, inner in payload.get("feeds", {}).items()
            },
        )


@dataclass(slots=True)
class ExecutionSpec:
    type: str = "direct"
    params: dict[str, Any] = field(default_factory=dict)
    ordering: OrderingSpec | None = None
    gas_model: GasSpec | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionSpec":
        payload = decode_jsonable(dict(data))
        ordering = payload.get("ordering")
        gas_model = payload.get("gas_model")
        return cls(
            type=payload.get("type", "direct"),
            params=dict(payload.get("params", {})),
            ordering=OrderingSpec.from_dict(ordering) if ordering is not None else None,
            gas_model=GasSpec.from_dict(gas_model) if gas_model is not None else None,
        )


@dataclass(slots=True)
class MarketSpec:
    type: str
    tokens: list[TokenSpec] = field(default_factory=list)
    pairs: list[PairSpec] = field(default_factory=list)
    fee_model: FeeModelSpec | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarketSpec":
        payload = decode_jsonable(dict(data))
        fee_model = payload.get("fee_model")
        return cls(
            type=payload["type"],
            tokens=[TokenSpec.from_dict(token) for token in payload.get("tokens", [])],
            pairs=[PairSpec.from_dict(pair) for pair in payload.get("pairs", [])],
            fee_model=FeeModelSpec.from_dict(fee_model) if fee_model is not None else None,
            params=dict(payload.get("params", {})),
        )


@dataclass(slots=True)
class WorldSpec:
    type: str = "world"
    markets: dict[str, MarketSpec] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorldSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            type=payload.get("type", "world"),
            markets={
                name: _parse_market_spec(market_data)
                for name, market_data in payload.get("markets", {}).items()
            },
        )


@dataclass(slots=True)
class AgentSpec:
    type: str
    agent_id: AgentId
    params: dict[str, Any] = field(default_factory=dict)
    initial_balances: dict[str, Numeric] = field(default_factory=dict)
    initial_cumulative_volume: Numeric = 0
    initial_realized_pnl: Numeric = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            type=payload["type"],
            agent_id=payload["agent_id"],
            params=dict(payload.get("params", {})),
            initial_balances=dict(payload.get("initial_balances", {})),
            initial_cumulative_volume=payload.get("initial_cumulative_volume", 0),
            initial_realized_pnl=payload.get("initial_realized_pnl", 0),
        )


@dataclass(slots=True)
class AltSpec:
    """Address Lookup Table seed entry for a RunSpec (US-009, PRD line 676).

    Each entry is materialized into an
    :class:`defi_sim.engine.transactions.AddressLookupTable` and registered
    on the engine's ``alts`` registry on init, so any
    ``VersionedTransaction`` submitted later that references the table by
    ``id`` resolves its accounts as 3-byte refs instead of 32-byte pubkeys.
    """

    id: str
    entries: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AltSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            id=str(payload["id"]),
            entries=[str(entry) for entry in payload.get("entries", [])],
        )


@dataclass(slots=True)
class RunSpec:
    market: MarketSpec | WorldSpec
    agents: list[AgentSpec]
    num_rounds: int = 200
    snapshot_interval: int = 10
    seed: int = 42
    retain_snapshots: bool = True
    numeric_mode: str = "fixed"
    clock: ClockSpec | None = None
    ordering: OrderingSpec | None = None
    gas_model: GasSpec | None = None
    execution: ExecutionSpec | None = None
    information_filter: InformationFilterSpec | None = None
    default_fee_model: FeeModelSpec | None = None
    feeds: list[FeedSpec] = field(default_factory=list)
    alts: list[AltSpec] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.num_rounds < 0:
            raise ValueError("num_rounds must be >= 0")
        if self.snapshot_interval <= 0:
            raise ValueError("snapshot_interval must be > 0")
        if self.numeric_mode not in {"fixed", "float"}:
            raise ValueError("numeric_mode must be 'fixed' or 'float'")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunSpec":
        payload = decode_jsonable(dict(data))
        return cls(
            market=_parse_market_spec(payload["market"]),
            agents=[AgentSpec.from_dict(agent) for agent in payload.get("agents", [])],
            num_rounds=payload.get("num_rounds", 200),
            snapshot_interval=payload.get("snapshot_interval", 10),
            seed=payload.get("seed", 42),
            retain_snapshots=payload.get("retain_snapshots", True),
            numeric_mode=payload.get("numeric_mode", "fixed"),
            clock=ClockSpec.from_dict(payload["clock"]) if payload.get("clock") is not None else None,
            ordering=OrderingSpec.from_dict(payload["ordering"]) if payload.get("ordering") is not None else None,
            gas_model=GasSpec.from_dict(payload["gas_model"]) if payload.get("gas_model") is not None else None,
            execution=ExecutionSpec.from_dict(payload["execution"]) if payload.get("execution") is not None else None,
            information_filter=(
                InformationFilterSpec.from_dict(payload["information_filter"])
                if payload.get("information_filter") is not None else None
            ),
            default_fee_model=(
                FeeModelSpec.from_dict(payload["default_fee_model"])
                if payload.get("default_fee_model") is not None else None
            ),
            feeds=[FeedSpec.from_dict(feed) for feed in payload.get("feeds", [])],
            alts=[AltSpec.from_dict(alt) for alt in payload.get("alts", [])],
            parameters=dict(payload.get("parameters", {})),
            metadata=dict(payload.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self, include_type_tags=False)

    def to_json(self, *, indent: int | None = None) -> str:
        return to_json(self, indent=indent, include_type_tags=False)


_CLOCK_FACTORIES: dict[str, SpecFactory] = {}
_ORDERING_FACTORIES: dict[str, SpecFactory] = {}
_GAS_FACTORIES: dict[str, SpecFactory] = {}
_INFO_FILTER_FACTORIES: dict[str, SpecFactory] = {}
_FEE_MODEL_FACTORIES: dict[str, SpecFactory] = {}
_FEED_FACTORIES: dict[str, SpecFactory] = {}
_MARKET_FACTORIES: dict[str, SpecFactory] = {}
_AGENT_FACTORIES: dict[str, SpecFactory] = {}
_EXECUTION_FACTORIES: dict[str, SpecFactory] = {}
_LEADER_SCHEDULE_FACTORIES: dict[str, SpecFactory] = {}

# Parallel metadata tables (BE-001). Keyed by the same spec type as the
# factory tables. Missing entries mean "no metadata supplied"; the
# /registry endpoint falls back to a title-cased label in that case.
_CLOCK_META: dict[str, EntityMetadata] = {}
_ORDERING_META: dict[str, EntityMetadata] = {}
_GAS_META: dict[str, EntityMetadata] = {}
_INFO_FILTER_META: dict[str, EntityMetadata] = {}
_FEE_MODEL_META: dict[str, EntityMetadata] = {}
_FEED_META: dict[str, EntityMetadata] = {}
_MARKET_META: dict[str, EntityMetadata] = {}
_AGENT_META: dict[str, EntityMetadata] = {}
_EXECUTION_META: dict[str, EntityMetadata] = {}
_LEADER_SCHEDULE_META: dict[str, EntityMetadata] = {}


def register_clock_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _CLOCK_FACTORIES[spec_type] = factory
    if metadata is not None:
        _CLOCK_META[spec_type] = metadata


def register_ordering_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _ORDERING_FACTORIES[spec_type] = factory
    if metadata is not None:
        _ORDERING_META[spec_type] = metadata


def register_gas_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _GAS_FACTORIES[spec_type] = factory
    if metadata is not None:
        _GAS_META[spec_type] = metadata


def register_information_filter_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _INFO_FILTER_FACTORIES[spec_type] = factory
    if metadata is not None:
        _INFO_FILTER_META[spec_type] = metadata


def register_fee_model_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _FEE_MODEL_FACTORIES[spec_type] = factory
    if metadata is not None:
        _FEE_MODEL_META[spec_type] = metadata


def register_feed_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _FEED_FACTORIES[spec_type] = factory
    if metadata is not None:
        _FEED_META[spec_type] = metadata


def register_market_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _MARKET_FACTORIES[spec_type] = factory
    if metadata is not None:
        _MARKET_META[spec_type] = metadata


def register_agent_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _AGENT_FACTORIES[spec_type] = factory
    if metadata is not None:
        _AGENT_META[spec_type] = metadata


def register_execution_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _EXECUTION_FACTORIES[spec_type] = factory
    if metadata is not None:
        _EXECUTION_META[spec_type] = metadata


def register_leader_schedule_factory(
    spec_type: str,
    factory: SpecFactory,
    *,
    metadata: EntityMetadata | None = None,
) -> None:
    _LEADER_SCHEDULE_FACTORIES[spec_type] = factory
    if metadata is not None:
        _LEADER_SCHEDULE_META[spec_type] = metadata


# Mapping from category name → (factory dict, metadata dict). Used by
# the registry endpoint (BE-002) to walk all categories uniformly.
_CATEGORY_TABLES: dict[str, tuple[dict[str, SpecFactory], dict[str, EntityMetadata]]] = {
    "markets": (_MARKET_FACTORIES, _MARKET_META),
    "agents": (_AGENT_FACTORIES, _AGENT_META),
    "clocks": (_CLOCK_FACTORIES, _CLOCK_META),
    "orderings": (_ORDERING_FACTORIES, _ORDERING_META),
    "gas_models": (_GAS_FACTORIES, _GAS_META),
    "fee_models": (_FEE_MODEL_FACTORIES, _FEE_MODEL_META),
    "feeds": (_FEED_FACTORIES, _FEED_META),
    "execution_models": (_EXECUTION_FACTORIES, _EXECUTION_META),
    "information_filters": (_INFO_FILTER_FACTORIES, _INFO_FILTER_META),
    "leader_schedules": (_LEADER_SCHEDULE_FACTORIES, _LEADER_SCHEDULE_META),
}


def get_registry_metadata(category: str, spec_type: str) -> EntityMetadata | None:
    """Return metadata for a registered entity, or ``None`` if absent."""
    tables = _CATEGORY_TABLES.get(category)
    if tables is None:
        return None
    _, meta_table = tables
    return meta_table.get(spec_type)


def iter_registry_categories() -> list[str]:
    """Return the ordered list of category keys understood by the
    registry tables. The order is stable and used by the /registry
    endpoint as the default category ordering."""
    return list(_CATEGORY_TABLES.keys())


def _ensure_spec(spec: Any, cls: type[Any]) -> Any:
    if isinstance(spec, cls):
        return spec
    if isinstance(spec, Mapping):
        return cls.from_dict(spec)
    raise TypeError(f"expected {cls.__name__} or mapping, got {type(spec)!r}")


def _parse_market_spec(data: Mapping[str, Any]) -> MarketSpec | WorldSpec:
    market_type = data.get("type")
    if market_type == "world":
        return WorldSpec.from_dict(data)
    return MarketSpec.from_dict(data)


def _coerce_token(token: TokenSpec | Mapping[str, Any]) -> Token:
    token_spec = _ensure_spec(token, TokenSpec)
    return Token(id=token_spec.id, symbol=token_spec.symbol, decimals=token_spec.decimals)


def _coerce_pair(pair: PairSpec | Mapping[str, Any]) -> tuple[Token, Token]:
    pair_spec = _ensure_spec(pair, PairSpec)
    return _coerce_token(pair_spec.base), _coerce_token(pair_spec.quote)


def _numeric_mode_from_spec(numeric_mode: str | NumericMode) -> NumericMode:
    if isinstance(numeric_mode, NumericMode):
        return numeric_mode
    if numeric_mode == "fixed":
        return FIXED_POINT
    if numeric_mode == "float":
        return FLOAT_MODE
    raise ValueError(f"unknown numeric mode: {numeric_mode!r}")


def _resolve_action_type(name: str) -> type[Action]:
    from defi_sim.core import types as core_types

    action_type = getattr(core_types, name, None)
    if not isinstance(action_type, type) or not issubclass(action_type, Action):
        raise ValueError(f"unknown action type for JSON spec: {name}")
    return action_type


def _coerce_typed_costs(raw_costs: Mapping[str, Numeric]) -> dict[type[Action], Numeric]:
    return {_resolve_action_type(action_name): cost for action_name, cost in raw_costs.items()}


def build_clock(
    spec: ClockSpec | SlotClockSpec | Mapping[str, Any] | None,
) -> Clock | None:
    if spec is None:
        return None
    if isinstance(spec, SlotClockSpec):
        spec = spec.to_clock_spec()
    clock_spec = _ensure_spec(spec, ClockSpec)
    factory = _CLOCK_FACTORIES.get(clock_spec.type)
    if factory is None:
        raise ValueError(f"unsupported clock spec type: {clock_spec.type}")
    return factory(clock_spec)


def build_compute_budget(
    spec: ComputeBudgetSpec | Mapping[str, Any] | None,
) -> ComputeBudget:
    """Convert a ``ComputeBudgetSpec`` (or dict) to a ``ComputeBudget``.

    Returns the current-mainnet default when ``spec`` is ``None`` —
    matches the US-002 contract that ``solana_like`` execution defaults
    to ``ComputeBudget()`` when no explicit budget is supplied.
    """
    if spec is None:
        return ComputeBudget()
    budget_spec = _ensure_spec(spec, ComputeBudgetSpec)
    return budget_spec.to_compute_budget()


def build_leader_schedule(
    spec: LeaderScheduleSpec | Mapping[str, Any] | None,
) -> LeaderSchedule | None:
    if spec is None:
        return None
    schedule_spec = _ensure_spec(spec, LeaderScheduleSpec)
    factory = _LEADER_SCHEDULE_FACTORIES.get(schedule_spec.type)
    if factory is None:
        raise ValueError(
            f"unsupported leader schedule spec type: {schedule_spec.type}"
        )
    return factory(schedule_spec)


def build_ordering(spec: OrderingSpec | Mapping[str, Any] | None) -> OrderingStrategy | None:
    if spec is None:
        return None
    ordering_spec = _ensure_spec(spec, OrderingSpec)
    factory = _ORDERING_FACTORIES.get(ordering_spec.type)
    if factory is None:
        raise ValueError(f"unsupported ordering spec type: {ordering_spec.type}")
    return factory(ordering_spec)


def build_gas_model(spec: GasSpec | Mapping[str, Any] | None) -> TransactionCostModel | None:
    if spec is None:
        return None
    gas_spec = _ensure_spec(spec, GasSpec)
    factory = _GAS_FACTORIES.get(gas_spec.type)
    if factory is None:
        raise ValueError(f"unsupported gas spec type: {gas_spec.type}")
    return factory(gas_spec)


def build_information_filter(
    spec: InformationFilterSpec | Mapping[str, Any] | None,
) -> InformationFilter | None:
    if spec is None:
        return None
    filter_spec = _ensure_spec(spec, InformationFilterSpec)
    factory = _INFO_FILTER_FACTORIES.get(filter_spec.type)
    if factory is None:
        raise ValueError(f"unsupported information filter spec type: {filter_spec.type}")
    return factory(filter_spec)


def build_fee_model(spec: FeeModelSpec | Mapping[str, Any] | None) -> Callable[..., Any] | None:
    if spec is None:
        return None
    fee_spec = _ensure_spec(spec, FeeModelSpec)
    factory = _FEE_MODEL_FACTORIES.get(fee_spec.type)
    if factory is None:
        raise ValueError(f"unsupported fee model spec type: {fee_spec.type}")
    return factory(fee_spec)


def build_feed(spec: FeedSpec | Mapping[str, Any]) -> Any:
    feed_spec = _ensure_spec(spec, FeedSpec)
    factory = _FEED_FACTORIES.get(feed_spec.type)
    if factory is None:
        raise ValueError(f"unsupported feed spec type: {feed_spec.type}")
    return factory(feed_spec)


def build_execution_model(
    spec: ExecutionSpec | Mapping[str, Any] | None,
    *,
    ordering_spec: OrderingSpec | Mapping[str, Any] | None = None,
    gas_spec: GasSpec | Mapping[str, Any] | None = None,
) -> ExecutionModel:
    execution_spec = _ensure_spec(spec, ExecutionSpec) if spec is not None else ExecutionSpec()
    factory = _EXECUTION_FACTORIES.get(execution_spec.type)
    if factory is None:
        raise ValueError(f"unsupported execution spec type: {execution_spec.type}")
    return factory(
        execution_spec,
        ordering_spec=ordering_spec or execution_spec.ordering,
        gas_spec=gas_spec or execution_spec.gas_model,
    )


def build_market(spec: MarketSpec | WorldSpec | Mapping[str, Any]) -> Market | World:
    if isinstance(spec, Mapping):
        parsed = _parse_market_spec(spec)
        return build_market(parsed)
    if isinstance(spec, WorldSpec):
        return _build_world(spec)
    market_spec = _ensure_spec(spec, MarketSpec)
    factory = _MARKET_FACTORIES.get(market_spec.type)
    if factory is None:
        raise ValueError(f"unsupported market spec type: {market_spec.type}")
    return factory(market_spec)


def build_agent(spec: AgentSpec | Mapping[str, Any]) -> Any:
    agent_spec = _ensure_spec(spec, AgentSpec)
    factory = _AGENT_FACTORIES.get(agent_spec.type)
    if factory is None:
        raise ValueError(f"unsupported agent spec type: {agent_spec.type}")
    agent = factory(agent_spec)
    agent.state.balances.update(agent_spec.initial_balances)
    agent.state.cumulative_volume = agent_spec.initial_cumulative_volume
    agent.state.realized_pnl = agent_spec.initial_realized_pnl
    return agent


def build_agents(specs: list[AgentSpec | Mapping[str, Any]]) -> list[Any]:
    return [build_agent(spec) for spec in specs]


def build_simulation_config(
    spec: RunSpec | Mapping[str, Any],
    *,
    cancel_token: Any = None,
) -> SimulationConfig:
    from defi_sim.engine.transactions import AddressLookupTable

    run_spec = _ensure_spec(spec, RunSpec)
    return SimulationConfig(
        num_rounds=run_spec.num_rounds,
        snapshot_interval=run_spec.snapshot_interval,
        seed=run_spec.seed,
        clock=build_clock(run_spec.clock),
        default_fee_model=build_fee_model(run_spec.default_fee_model),
        execution_model=build_execution_model(
            run_spec.execution,
            ordering_spec=run_spec.ordering,
            gas_spec=run_spec.gas_model,
        ),
        feeds=[build_feed(feed_spec) for feed_spec in run_spec.feeds] or None,
        information_filter=build_information_filter(run_spec.information_filter),
        parameters=ParameterStore(defaults=run_spec.parameters) if run_spec.parameters else None,
        numeric_mode=_numeric_mode_from_spec(run_spec.numeric_mode),
        retain_snapshots=run_spec.retain_snapshots,
        cancel_token=cancel_token,
        alts=[AddressLookupTable(id=alt.id, entries=list(alt.entries)) for alt in run_spec.alts] or None,
    )


def _build_block_clock(spec: ClockSpec) -> Clock:
    return BlockClock(
        genesis=spec.params.get("genesis", 0),
        block_time=spec.params.get("block_time", 1),
        epoch_length=spec.params.get("epoch_length", 1),
    )


def _build_variable_block_clock(spec: ClockSpec) -> Clock:
    return VariableBlockClock(
        timestamps=list(spec.params["timestamps"]),
        epoch_length=spec.params.get("epoch_length", 1),
    )


def _build_solana_slot_clock(spec: ClockSpec) -> Clock:
    return SolanaSlotClock(
        slot_duration_seconds=float(spec.params.get("slot_duration_seconds", 0.4)),
        epoch_length_slots=int(spec.params.get("epoch_length_slots", 432_000)),
        skip_rate=float(spec.params.get("skip_rate", 0.0)),
        genesis=int(spec.params.get("genesis", 0)),
        seed=spec.params.get("seed"),
    )


def _build_stake_weighted_leader_schedule(spec: "LeaderScheduleSpec") -> LeaderSchedule:
    return LeaderSchedule(
        validators=[
            ValidatorStake(pubkey=v.pubkey, stake_lamports=v.stake_lamports)
            for v in spec.validators
        ],
        seed=spec.seed,
        epoch_length_slots=spec.epoch_length_slots,
    )


def _build_fifo_ordering(spec: OrderingSpec) -> OrderingStrategy:
    return FIFOOrdering()


def _build_random_ordering(spec: OrderingSpec) -> OrderingStrategy:
    return RandomOrdering()


def _build_priority_ordering(spec: OrderingSpec) -> OrderingStrategy:
    return PriorityOrdering()


def _build_sandwich_ordering(spec: OrderingSpec) -> OrderingStrategy:
    return SandwichOrdering(
        adversarial_agent_ids=set(spec.params.get("adversarial_agent_ids", [])),
        target_agent_ids=set(spec.params.get("target_agent_ids", [])),
    )


def _build_zero_cost(spec: GasSpec) -> TransactionCostModel:
    return ZeroCost()


def _build_fixed_cost(spec: GasSpec) -> TransactionCostModel:
    return FixedCost(cost_per_action=spec.params["cost_per_action"])


def _build_eip1559_cost(spec: GasSpec) -> TransactionCostModel:
    return EIP1559Cost(
        base_fee=spec.params["base_fee"],
        target_actions_per_round=spec.params.get("target_actions_per_round", 50),
        adjustment_factor=spec.params.get("adjustment_factor", 8),
    )


def _build_compute_unit_cost(spec: GasSpec) -> TransactionCostModel:
    unit_costs = spec.params.get("unit_costs", {})
    return ComputeUnitCost(
        unit_costs=_coerce_typed_costs(unit_costs),
        default_units=spec.params.get("default_units", 1),
        base_cost=spec.params.get("base_cost", 0),
    )


def _build_typed_cost(spec: GasSpec) -> TransactionCostModel:
    return TypedCost(
        costs=_coerce_typed_costs(spec.params.get("costs", {})),
        default_cost=spec.params.get("default_cost", 0),
    )


def _build_full_transparency(spec: InformationFilterSpec) -> InformationFilter:
    return FullTransparency()


def _build_delayed_information(spec: InformationFilterSpec) -> InformationFilter:
    return DelayedInformation(delays=dict(spec.params.get("delays", {})))


def _build_partial_fee_model(spec: FeeModelSpec, fn: Callable[..., Any]) -> Callable[..., Any]:
    return partial(fn, **spec.params)


def _build_historical_feed(spec: FeedSpec) -> HistoricalFeed:
    return HistoricalFeed({
        token_id: np.asarray(prices)
        for token_id, prices in spec.params["prices"].items()
    })


def _build_stochastic_feed(spec: FeedSpec) -> StochasticFeed:
    return StochasticFeed(
        process=spec.params["process"],
        params=dict(spec.params.get("process_params", {})),
        seed=spec.params.get("seed"),
    )


def _build_composite_feed(spec: FeedSpec) -> CompositeFeed:
    return CompositeFeed({
        token_id: build_feed(inner_spec)
        for token_id, inner_spec in spec.feeds.items()
    })


def _pop_execution_params(spec: ExecutionSpec, *allowed: str) -> dict[str, Any]:
    params = dict(spec.params)
    unknown = set(params) - set(allowed)
    if unknown:
        unknown_csv = ", ".join(sorted(unknown))
        raise ValueError(f"unsupported execution params for type={spec.type}: {unknown_csv}")
    return params


def _build_direct_execution(
    spec: ExecutionSpec,
    *,
    ordering_spec: OrderingSpec | Mapping[str, Any] | None,
    gas_spec: GasSpec | Mapping[str, Any] | None,
) -> ExecutionModel:
    params = _pop_execution_params(
        spec,
        "cost_token",
        "expose_pending_actions",
        "refund_failed_costs",
    )
    return DirectExecution(
        ordering=build_ordering(ordering_spec),
        cost_model=build_gas_model(gas_spec),
        cost_token=params.get("cost_token", "COLLATERAL"),
        expose_pending_actions=params.get("expose_pending_actions", False),
        refund_failed_costs=params.get("refund_failed_costs", False),
    )


def _build_batch_execution(
    spec: ExecutionSpec,
    *,
    ordering_spec: OrderingSpec | Mapping[str, Any] | None,
    gas_spec: GasSpec | Mapping[str, Any] | None,
) -> ExecutionModel:
    params = _pop_execution_params(spec, "cost_token", "refund_failed_costs")
    return BatchExecution(
        ordering=build_ordering(ordering_spec),
        cost_model=build_gas_model(gas_spec),
        cost_token=params.get("cost_token", "COLLATERAL"),
        refund_failed_costs=params.get("refund_failed_costs", False),
    )


def _build_solana_like_execution(
    spec: ExecutionSpec,
    *,
    ordering_spec: OrderingSpec | Mapping[str, Any] | None,
    gas_spec: GasSpec | Mapping[str, Any] | None,
) -> ExecutionModel:
    params = _pop_execution_params(
        spec,
        "cost_token",
        "visible_roles",
        "compute_budget",
        "scheduler",
        "submission_priors",
        "priority_fee_market",
        "bundle_auction",
        "fork_spec",
        "blockhash_history",
        # PRD US-012 line 974: validator_set is normally consumed by
        # ``build_engine._expand_validator_set_into_agents`` *before* this
        # builder runs. Listed here so callers that bypass build_engine
        # (tests, direct spec→model construction) don't hit a spurious
        # "unknown param" rejection; the value itself is then discarded.
        "validator_set",
        # PRD US-006 line 497: oracle_preset is consumed by
        # ``build_engine._register_oracle_preset`` after the execution
        # model is built. Allowlisted here so callers that bypass
        # build_engine don't hit a spurious "unknown param" rejection.
        "oracle_preset",
    )
    params.pop("validator_set", None)
    params.pop("oracle_preset", None)
    priors_param = params.get("submission_priors")
    if priors_param is None:
        submission_priors = None
    elif isinstance(priors_param, SubmissionPathPriors):
        submission_priors = priors_param
    else:
        submission_priors = SubmissionPathPriors(**priors_param)
    # US-001 builder default: a Solana template with no explicit leader
    # schedule still produces a runnable engine with a 1-validator
    # schedule attached. 1.10 will replace this with the agent-driven
    # path that re-seeds from `Validator.params.stake_lamports`.
    default_schedule = LeaderSchedule(
        validators=[ValidatorStake(pubkey="validator-default", stake_lamports=1)],
        seed=0,
    )
    scheduler_param = params.get("scheduler")
    if isinstance(scheduler_param, str):
        scheduler_param = {"type": scheduler_param}
    scheduler = deserialize_scheduler(scheduler_param) if scheduler_param else None
    # US-010 PRD line 747: builder forwards priority-fee market tuning as
    # `execution.params.priority_fee_market`. Mirror it through to the
    # SolanaLikeExecution so the spec controls the engine's market.
    pfm_param = params.get("priority_fee_market")
    if pfm_param is None:
        priority_fee_market = None
    elif isinstance(pfm_param, PriorityFeeMarketSpec):
        priority_fee_market = pfm_param.to_priority_fee_market()
    else:
        priority_fee_market = PriorityFeeMarketSpec.from_dict(
            pfm_param
        ).to_priority_fee_market()
    # PRD US-011 line 895: bundle auction defaults on for Solana execution.
    # Caller may pass ``bundle_auction=None`` in params to disable explicitly.
    if "bundle_auction" in params:
        ba_param = params["bundle_auction"]
        if ba_param is None:
            bundle_auction = None
        elif isinstance(ba_param, BundleAuctionSpec):
            bundle_auction = ba_param.to_bundle_auction()
        elif isinstance(ba_param, BundleAuction):
            bundle_auction = ba_param
        else:
            bundle_auction = BundleAuctionSpec.from_dict(ba_param).to_bundle_auction()
    else:
        bundle_auction = BundleAuctionSpec().to_bundle_auction()
    # PRD US-014 line 1109 / line 1101: spec-level forwarding of fork
    # configuration and blockhash history. ``fork_spec`` toggles the
    # per-slot fork roll on the engine; ``blockhash_history`` enables
    # admit-time stale-blockhash drops. Both stay optional so chain-neutral
    # specs and tests built without these knobs are unaffected.
    fork_param = params.get("fork_spec")
    if fork_param is None:
        fork_spec_obj = None
    elif isinstance(fork_param, ChainReorgForkSpec):
        fork_spec_obj = fork_param
    else:
        fork_spec_obj = ChainReorgForkSpec(**fork_param)
    bh_param = params.get("blockhash_history")
    if bh_param is None:
        blockhash_history = None
    elif isinstance(bh_param, BlockhashHistory):
        blockhash_history = bh_param
    elif isinstance(bh_param, bool):
        # Convenience: ``blockhash_history: true`` enables a default-window
        # rolling history without forcing the spec to construct it.
        blockhash_history = BlockhashHistory() if bh_param else None
    elif isinstance(bh_param, Mapping):
        validity_slots = int(bh_param.get("validity_slots", BLOCKHASH_VALIDITY_SLOTS))
        blockhash_history = BlockhashHistory(validity_slots=validity_slots)
    else:
        raise TypeError(
            f"Unsupported blockhash_history spec value: {type(bh_param).__name__}"
        )
    return SolanaLikeExecution(
        cost_model=build_gas_model(gas_spec),
        cost_token=params.get("cost_token", "COLLATERAL"),
        ordering=build_ordering(ordering_spec),
        visible_roles=set(params.get("visible_roles", [])),
        leader_schedule=default_schedule,
        compute_budget=build_compute_budget(params.get("compute_budget")),
        scheduler=scheduler,
        submission_priors=submission_priors,
        priority_fee_market=priority_fee_market,
        bundle_auction=bundle_auction,
        fork_spec=fork_spec_obj,
        blockhash_history=blockhash_history,
    )


def _build_world_market_stub(spec: MarketSpec) -> Market:
    """World markets dispatch via ``build_market``'s isinstance branch
    on :class:`WorldSpec`, so this stub only exists so ``world`` shows
    up in the market registry for the frontend builder. If it is ever
    invoked with a plain :class:`MarketSpec`, that is a user error
    (the spec should be a :class:`WorldSpec`) — raise a clear message."""
    raise ValueError(
        "world markets must be supplied as a WorldSpec, not a MarketSpec"
    )


def _build_cfamm_market(spec: MarketSpec) -> Market:
    if not spec.tokens:
        raise ValueError("cfamm market specs require at least one token")
    if "initial_liquidity" not in spec.params:
        raise ValueError("cfamm market specs require params.initial_liquidity")
    pool_account_id = spec.params.get("pool_account_id")
    return CfammMarket(
        tokens=[_coerce_token(token_spec) for token_spec in spec.tokens],
        initial_liquidity=spec.params["initial_liquidity"],
        fee_model=build_fee_model(spec.fee_model),
        collateral_token=spec.params.get("collateral_token", "COLLATERAL"),
        pool_account_id=str(pool_account_id) if pool_account_id is not None else None,
    )


def _build_clob_market(spec: MarketSpec) -> Market:
    if not spec.pairs:
        raise ValueError("clob market specs require at least one trading pair")
    return ClobMarket(
        pairs=[_coerce_pair(pair_spec) for pair_spec in spec.pairs],
        fee_model=build_fee_model(spec.fee_model),
    )


def _build_whirlpool_market(spec: MarketSpec) -> Market:
    """Hydrate a real Whirlpool CLMM market from a captured corpus slot.

    Spec params:
      * ``corpus_slot`` (int) — slot whose corpus fixture to load. Required.
      * ``pool_pubkey`` (str) — Whirlpool pool account pubkey to select
        from the captured fixture. Required.
      * ``token_a_id`` / ``token_b_id`` (str) — ids the engine uses for the
        two tokens (e.g., ``"SOL"`` / ``"USDC"``). Defaults to the mint
        pubkey strings from the pool account.
      * ``fee_model`` (FeeModelSpec) — flat-fee override. When ``type ==
        "flat"`` and ``params.trade_fee_bps`` is set, the captured pool's
        ``fee_rate`` is overwritten so the Builder's "Fee model" panel
        actually drives the CLMM swap math (1 bp = 100 whirlpool fee_rate
        units; denominator is 1e6). Non-flat fee models are stored on
        the market for symmetry with cfamm but do not affect the swap
        path — Whirlpool's on-chain fee_rate is the only fee surface.
      * ``initial_liquidity`` (int | float) — optional. Target token-B
        vault depth in human units (decimals stripped). When supplied,
        the captured pool's ``liquidity``, both vaults, and per-tick
        ``liquidity_net``/``liquidity_gross`` are scaled by
        ``target_b / captured_b`` so the slider drives depth without
        moving price (sqrt_price) or the tick distribution.
    """
    from defi_sim.markets.whirlpool_fork import build_whirlpool_market_from_corpus

    if not spec.params or "corpus_slot" not in spec.params:
        raise ValueError("whirlpool market specs require params.corpus_slot")
    if "pool_pubkey" not in spec.params:
        raise ValueError("whirlpool market specs require params.pool_pubkey")
    fee_rate_override: int | None = None
    if spec.fee_model and spec.fee_model.type == "flat":
        bps = spec.fee_model.params.get("trade_fee_bps")
        if bps is not None:
            fee_rate_override = int(round(float(bps) * 100))

    return build_whirlpool_market_from_corpus(
        corpus_slot=int(spec.params["corpus_slot"]),
        pool_pubkey=str(spec.params["pool_pubkey"]),
        token_a_id=str(spec.params.get("token_a_id", "")),
        token_b_id=str(spec.params.get("token_b_id", "")),
        token_a_symbol=str(spec.params.get("token_a_symbol", "")),
        token_b_symbol=str(spec.params.get("token_b_symbol", "")),
        fee_model=build_fee_model(spec.fee_model),
        pool_account_id=str(
            spec.params.get("pool_account_id") or spec.params["pool_pubkey"]
        ),
        initial_liquidity=spec.params.get("initial_liquidity"),
        fee_rate_override=fee_rate_override,
    )


def _build_world(spec: WorldSpec) -> World:
    world = World()
    for name, market_spec in spec.markets.items():
        child_market = build_market(market_spec)
        if isinstance(child_market, World):
            raise ValueError("nested world specs are not supported")
        world.add_market(name, child_market)
    return world


def _build_noise_agent(spec: AgentSpec) -> Any:
    params = NoiseParams(**spec.params) if spec.params else None
    return NoiseTrader(agent_id=spec.agent_id, params=params)


def _build_swap_noise_agent(spec: AgentSpec) -> Any:
    params = SwapNoiseParams(**spec.params) if spec.params else None
    return SwapNoiseTrader(agent_id=spec.agent_id, params=params)


def _build_informed_agent(spec: AgentSpec) -> Any:
    params = InformedParams(**spec.params) if spec.params else None
    return InformedTrader(agent_id=spec.agent_id, params=params)


def _build_arbitrageur_agent(spec: AgentSpec) -> Any:
    params = ArbitrageParams(**spec.params) if spec.params else None
    return Arbitrageur(agent_id=spec.agent_id, params=params)


def _build_manipulator_agent(spec: AgentSpec) -> Any:
    params = ManipulatorParams(**spec.params) if spec.params else None
    return Manipulator(agent_id=spec.agent_id, params=params)


def _build_passive_lp_agent(spec: AgentSpec) -> Any:
    params = LPParams(**spec.params) if spec.params else None
    return PassiveLP(agent_id=spec.agent_id, params=params)


def _build_rebalancing_lp_agent(spec: AgentSpec) -> Any:
    params = LPParams(**spec.params) if spec.params else None
    return RebalancingLP(agent_id=spec.agent_id, params=params)


def _build_validator_agent(spec: AgentSpec) -> Any:
    """PRD US-012 line 947: ``Validator`` agent factory.

    Reads ``ValidatorParams`` straight from the spec params; ``pubkey`` is
    required, the rest default per PRD line 947-955.
    """
    params_dict = dict(spec.params or {})
    if not params_dict.get("pubkey"):
        # default to the agent_id when the caller didn't supply a pubkey
        # (or supplied None / empty string from a registry-defaults
        # round-trip); keeps single-validator templates ergonomic without
        # changing PRD semantics (the agent's pubkey is its on-chain
        # identity and the agent_id is the engine's bookkeeping handle).
        params_dict["pubkey"] = spec.agent_id
    params = ValidatorParams(**params_dict)
    return Validator(agent_id=spec.agent_id, params=params)


def _build_jito_searcher_agent(spec: AgentSpec) -> Any:
    """PRD US-013 line 999: ``JitoSearcher`` agent factory.

    Reads ``JitoSearcherParams`` from the spec params, deserializing the
    nested ``tip_curve`` mapping into a :class:`TipCurveSpec` so JSON-driven
    runs can configure searchers without Python instantiation.
    """
    params_dict = dict(spec.params or {})
    tip_curve_value = params_dict.get("tip_curve")
    if isinstance(tip_curve_value, Mapping):
        params_dict["tip_curve"] = TipCurveSpec(**dict(tip_curve_value))
    elif tip_curve_value is None:
        params_dict["tip_curve"] = TipCurveSpec(kind="linear")
    alt_ids_value = params_dict.get("alt_ids")
    if alt_ids_value is not None and not isinstance(alt_ids_value, tuple):
        params_dict["alt_ids"] = tuple(str(a) for a in alt_ids_value)
    params = JitoSearcherParams(**params_dict)
    return JitoSearcher(agent_id=spec.agent_id, params=params)


def _register_builtins() -> None:
    noise_schema, noise_defaults = schema_and_defaults(NoiseParams)
    swap_noise_schema, swap_noise_defaults = schema_and_defaults(SwapNoiseParams)
    informed_schema, informed_defaults = schema_and_defaults(InformedParams)
    arb_schema, arb_defaults = schema_and_defaults(ArbitrageParams)
    manip_schema, manip_defaults = schema_and_defaults(ManipulatorParams)
    lp_schema, lp_defaults = schema_and_defaults(LPParams)

    # ── Clocks ──────────────────────────────────────────────────────
    register_clock_factory(
        "block",
        _build_block_clock,
        metadata=EntityMetadata(
            label="Block Clock",
            description="Fixed block time with deterministic round progression.",
            badges=({"label": "Default", "variant": "green"},),
            schema={
                "type": "object",
                "properties": {
                    "genesis": {"type": "integer", "default": 0, "title": "Genesis"},
                    "block_time": {"type": "number", "default": 1, "title": "Block Time"},
                    "epoch_length": {"type": "integer", "default": 1, "title": "Epoch Length"},
                },
            },
            defaults={"genesis": 0, "block_time": 1, "epoch_length": 1},
            ui_schema={
                "fields": {
                    "genesis": {"label": "Genesis", "widget": "number", "order": 1},
                    "block_time": {
                        "label": "Block Time",
                        "widget": "number",
                        "unit": "s",
                        "order": 2,
                    },
                    "epoch_length": {
                        "label": "Epoch Length",
                        "widget": "number",
                        "unit": "blocks",
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_clock_factory(
        "solana_slot",
        _build_solana_slot_clock,
        metadata=EntityMetadata(
            label="Solana Slot Clock",
            description="Solana-native slot clock — 0.4 s per slot, 432 000-slot epochs, configurable skip rate.",
            badges=({"label": "Solana", "variant": "purple"},),
            schema={
                "type": "object",
                "properties": {
                    "slot_duration_seconds": {
                        "type": "number",
                        "default": 0.4,
                        "title": "Slot Duration (s)",
                    },
                    "epoch_length_slots": {
                        "type": "integer",
                        "default": 432_000,
                        "title": "Epoch Length (slots)",
                    },
                    "skip_rate": {
                        "type": "number",
                        "default": 0.0,
                        "title": "Skip Rate",
                    },
                    "genesis": {
                        "type": "integer",
                        "default": 0,
                        "title": "Genesis",
                    },
                    "seed": {
                        "type": ["integer", "null"],
                        "default": None,
                        "title": "Seed",
                    },
                },
            },
            defaults={
                "slot_duration_seconds": 0.4,
                "epoch_length_slots": 432_000,
                "skip_rate": 0.0,
                "genesis": 0,
                "seed": None,
            },
            ui_schema={
                "fields": {
                    "slot_duration_seconds": {
                        "label": "Slot Duration",
                        "widget": "number",
                        "unit": "s",
                        "order": 1,
                    },
                    "epoch_length_slots": {
                        "label": "Epoch Length",
                        "widget": "number",
                        "unit": "slots",
                        "order": 2,
                    },
                    "skip_rate": {
                        "label": "Skip Rate",
                        "widget": "number",
                        "order": 3,
                    },
                    "genesis": {
                        "label": "Genesis",
                        "widget": "number",
                        "order": 4,
                    },
                    "seed": {
                        "label": "Seed",
                        "widget": "number",
                        "order": 5,
                    },
                },
            },
        ),
    )
    register_clock_factory(
        "variable_block",
        _build_variable_block_clock,
        metadata=EntityMetadata(
            label="Variable Block Clock",
            description="Variable block interval sampled per round from a distribution.",
            schema={
                "type": "object",
                "properties": {
                    "timestamps": {
                        "type": "array",
                        "items": {"type": "number"},
                        "title": "Timestamps",
                    },
                    "epoch_length": {"type": "integer", "default": 1, "title": "Epoch Length"},
                },
                "required": ["timestamps"],
            },
            defaults={"timestamps": [0, 1, 2], "epoch_length": 1},
            ui_schema={
                "fields": {
                    "timestamps": {
                        "label": "Timestamps",
                        "widget": "json",
                        "order": 1,
                    },
                    "epoch_length": {
                        "label": "Epoch Length",
                        "widget": "number",
                        "unit": "blocks",
                        "order": 2,
                    },
                },
            },
        ),
    )

    # ── Ordering ────────────────────────────────────────────────────
    register_ordering_factory(
        "fifo",
        _build_fifo_ordering,
        metadata=EntityMetadata(
            label="FIFO",
            description="First-in, first-out arrival order. Default for direct execution.",
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_ordering_factory(
        "random",
        _build_random_ordering,
        metadata=EntityMetadata(
            label="Random",
            description="RNG-based shuffle. Eliminates ordering bias.",
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_ordering_factory(
        "priority",
        _build_priority_ordering,
        metadata=EntityMetadata(
            label="Priority",
            description="Sorted by compute-unit priority lamports (price × CU limit) descending. Models the Solana priority fee market.",
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_ordering_factory(
        "sandwich",
        _build_sandwich_ordering,
        metadata=EntityMetadata(
            label="Sandwich",
            description="Front-run / back-run sandwich attack patterns. MEV simulation.",
            schema={
                "type": "object",
                "properties": {
                    "adversarial_agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Adversarial Agent IDs",
                    },
                    "target_agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Target Agent IDs",
                    },
                },
            },
            defaults={"adversarial_agent_ids": [], "target_agent_ids": []},
            ui_schema={
                "fields": {
                    "adversarial_agent_ids": {
                        "label": "Adversarial Agents",
                        "widget": "token-list",
                        "order": 1,
                    },
                    "target_agent_ids": {
                        "label": "Target Agents",
                        "widget": "token-list",
                        "order": 2,
                    },
                },
            },
        ),
    )

    # ── Gas / cost models ───────────────────────────────────────────
    register_gas_factory(
        "zero",
        _build_zero_cost,
        metadata=EntityMetadata(
            label="Zero Cost",
            description="No transaction costs. Default.",
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_gas_factory(
        "fixed",
        _build_fixed_cost,
        metadata=EntityMetadata(
            label="Fixed Cost",
            description="Constant cost per action regardless of type.",
            schema={
                "type": "object",
                "properties": {
                    "cost_per_action": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0,
                        "title": "Cost Per Action",
                    }
                },
                "required": ["cost_per_action"],
            },
            defaults={"cost_per_action": 0},
            ui_schema={
                "fields": {
                    "cost_per_action": {
                        "label": "Cost Per Action",
                        "widget": "number",
                        "min": 0,
                    },
                },
            },
        ),
    )
    register_gas_factory(
        "eip1559",
        _build_eip1559_cost,
        metadata=EntityMetadata(
            label="EIP-1559",
            description="Ethereum-like base fee + priority fee with dynamic adjustment.",
            schema={
                "type": "object",
                "properties": {
                    "base_fee": {"type": "number", "minimum": 0, "default": 0, "title": "Base Fee"},
                    "target_actions_per_round": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 50,
                        "title": "Target Actions Per Round",
                    },
                    "adjustment_factor": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 8,
                        "title": "Adjustment Factor",
                    },
                },
                "required": ["base_fee"],
            },
            defaults={"base_fee": 0, "target_actions_per_round": 50, "adjustment_factor": 8},
            ui_schema={
                "sections": [
                    {
                        "key": "basic",
                        "label": "Basic",
                        "level": "basic",
                        "fields": ["base_fee"],
                    },
                    {
                        "key": "advanced",
                        "label": "Advanced",
                        "level": "advanced",
                        "fields": ["target_actions_per_round", "adjustment_factor"],
                    },
                ],
                "fields": {
                    "base_fee": {
                        "label": "Base Fee",
                        "widget": "number",
                        "min": 0,
                        "unit": "gwei",
                        "section": "basic",
                        "order": 1,
                    },
                    "target_actions_per_round": {
                        "label": "Target Actions / Round",
                        "widget": "number",
                        "min": 1,
                        "section": "advanced",
                        "order": 1,
                    },
                    "adjustment_factor": {
                        "label": "Adjustment Factor",
                        "widget": "number",
                        "min": 1,
                        "section": "advanced",
                        "order": 2,
                    },
                },
            },
        ),
    )
    register_gas_factory(
        "compute_unit",
        _build_compute_unit_cost,
        metadata=EntityMetadata(
            label="Compute Unit",
            description="Solana mainnet fee formula: 5,000 lamports per signer plus ceil(compute-unit price × CU limit / 1,000,000).",
            schema={
                "type": "object",
                "properties": {
                    "unit_costs": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                        "title": "Unit Costs",
                    },
                    "default_units": {"type": "integer", "default": 1, "title": "Default Units"},
                    "base_cost": {"type": "number", "default": 0, "title": "Base Cost"},
                },
            },
            defaults={
                "unit_costs": {},
                "default_units": 1,
                "base_cost": 0,
            },
            ui_schema={
                "fields": {
                    "unit_costs": {"label": "Unit Costs", "widget": "json", "order": 1},
                    "default_units": {
                        "label": "Default Units",
                        "widget": "number",
                        "min": 1,
                        "order": 2,
                    },
                    "base_cost": {
                        "label": "Base Cost",
                        "widget": "number",
                        "min": 0,
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_gas_factory(
        "typed",
        _build_typed_cost,
        metadata=EntityMetadata(
            label="Typed Cost",
            description="Per-action-type cost schedule. Different costs for swaps vs. LP vs. orders.",
            schema={
                "type": "object",
                "properties": {
                    "costs": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                        "title": "Costs",
                    },
                    "default_cost": {"type": "number", "default": 0, "title": "Default Cost"},
                },
            },
            defaults={"costs": {}, "default_cost": 0},
            ui_schema={
                "fields": {
                    "costs": {"label": "Costs", "widget": "json", "order": 1},
                    "default_cost": {
                        "label": "Default Cost",
                        "widget": "number",
                        "min": 0,
                        "order": 2,
                    },
                },
            },
        ),
    )

    # ── Information filters ────────────────────────────────────────
    register_information_filter_factory(
        "full_transparency",
        _build_full_transparency,
        metadata=EntityMetadata(
            label="Full Transparency",
            description="Agents see complete market state. Default for simulations without information asymmetry.",
            badges=({"label": "Default", "variant": "green"},),
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_information_filter_factory(
        "delayed_information",
        _build_delayed_information,
        metadata=EntityMetadata(
            label="Delayed Information",
            description="Agents receive state with configurable round delay. Models stale book feeds.",
            schema={
                "type": "object",
                "properties": {
                    "delays": {
                        "type": "object",
                        "additionalProperties": {"type": "integer", "minimum": 0},
                        "title": "Delays",
                    }
                },
            },
            defaults={"delays": {}},
            ui_schema={
                "fields": {
                    "delays": {"label": "Delays (per token)", "widget": "json"},
                },
            },
        ),
    )

    # ── Fee models ──────────────────────────────────────────────────
    _fee_split_schema = {
        "type": "object",
        "additionalProperties": {"type": "integer"},
        "title": "Split Config",
    }
    register_fee_model_factory(
        "flat",
        lambda spec: _build_partial_fee_model(spec, flat_fee),
        metadata=EntityMetadata(
            label="Flat Fee",
            description="Fixed percentage fee on trade volume.",
            schema={
                "type": "object",
                "properties": {
                    "trade_fee_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 10000,
                        "default": 30,
                        "title": "Trade Fee (bps)",
                    },
                    "split_config": _fee_split_schema,
                },
            },
            defaults={"trade_fee_bps": 30},
            ui_schema={
                "fields": {
                    "trade_fee_bps": {
                        "label": "Trade Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 10000,
                        "step": 1,
                        "unit": "bps",
                        "order": 1,
                    },
                    "split_config": {
                        "label": "Fee Split",
                        "widget": "json",
                        "level": "advanced",
                        "order": 2,
                    },
                },
            },
        ),
    )
    register_fee_model_factory(
        "dynamic",
        lambda spec: _build_partial_fee_model(spec, dynamic_fee),
        metadata=EntityMetadata(
            label="Dynamic Fee",
            description="Fee rate adjusts based on market conditions.",
            schema={
                "type": "object",
                "properties": {
                    "base_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                        "title": "Base (bps)",
                    },
                    "max_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 100,
                        "title": "Max (bps)",
                    },
                    "volatility_multiplier": {
                        "type": "number",
                        "minimum": 0,
                        "default": 2.0,
                        "title": "Volatility Multiplier",
                    },
                    "split_config": _fee_split_schema,
                },
            },
            defaults={"base_bps": 30, "max_bps": 100, "volatility_multiplier": 2.0},
            ui_schema={
                "fields": {
                    "base_bps": {
                        "label": "Base Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 1000,
                        "step": 1,
                        "unit": "bps",
                        "order": 1,
                    },
                    "max_bps": {
                        "label": "Max Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 10000,
                        "step": 1,
                        "unit": "bps",
                        "order": 2,
                    },
                    "volatility_multiplier": {
                        "label": "Volatility Multiplier",
                        "widget": "slider",
                        "min": 0,
                        "max": 10,
                        "step": 0.1,
                        "order": 3,
                    },
                    "split_config": {
                        "label": "Fee Split",
                        "widget": "json",
                        "level": "advanced",
                        "order": 4,
                    },
                },
            },
        ),
    )
    register_fee_model_factory(
        "tiered",
        lambda spec: _build_partial_fee_model(spec, tiered_fee),
        metadata=EntityMetadata(
            label="Tiered Fee",
            description="Volume-based fee tiers with breakpoints.",
            schema={
                "type": "object",
                "properties": {
                    "base_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                        "title": "Base (bps)",
                    },
                    "tiers": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "prefixItems": [{"type": "number"}, {"type": "integer"}],
                        },
                        "title": "Tiers",
                    },
                    "split_config": _fee_split_schema,
                },
            },
            defaults={"base_bps": 30},
            ui_schema={
                "fields": {
                    "base_bps": {
                        "label": "Base Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 1000,
                        "step": 1,
                        "unit": "bps",
                        "order": 1,
                    },
                    "tiers": {"label": "Tiers", "widget": "json", "order": 2},
                    "split_config": {
                        "label": "Fee Split",
                        "widget": "json",
                        "level": "advanced",
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_fee_model_factory(
        "spread",
        lambda spec: _build_partial_fee_model(spec, spread_fee),
        metadata=EntityMetadata(
            label="Spread Fee",
            description="Fee proportional to bid-ask spread.",
            schema={
                "type": "object",
                "properties": {
                    "base_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                        "title": "Base (bps)",
                    },
                    "spread_multiplier": {
                        "type": "number",
                        "minimum": 0,
                        "default": 1.5,
                        "title": "Spread Multiplier",
                    },
                    "split_config": _fee_split_schema,
                },
            },
            defaults={"base_bps": 30, "spread_multiplier": 1.5},
            ui_schema={
                "fields": {
                    "base_bps": {
                        "label": "Base Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 1000,
                        "step": 1,
                        "unit": "bps",
                        "order": 1,
                    },
                    "spread_multiplier": {
                        "label": "Spread Multiplier",
                        "widget": "slider",
                        "min": 0,
                        "max": 10,
                        "step": 0.1,
                        "order": 2,
                    },
                    "split_config": {
                        "label": "Fee Split",
                        "widget": "json",
                        "level": "advanced",
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_fee_model_factory(
        "time_weighted",
        lambda spec: _build_partial_fee_model(spec, time_weighted_fee),
        metadata=EntityMetadata(
            label="Time-Weighted Fee",
            description="Fee varies based on time since last trade.",
            schema={
                "type": "object",
                "properties": {
                    "base_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 10,
                        "title": "Base (bps)",
                    },
                    "max_bps": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 50,
                        "title": "Max (bps)",
                    },
                    "split_config": _fee_split_schema,
                },
            },
            defaults={"base_bps": 10, "max_bps": 50},
            ui_schema={
                "fields": {
                    "base_bps": {
                        "label": "Base Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 1000,
                        "step": 1,
                        "unit": "bps",
                        "order": 1,
                    },
                    "max_bps": {
                        "label": "Max Fee",
                        "widget": "slider",
                        "min": 0,
                        "max": 10000,
                        "step": 1,
                        "unit": "bps",
                        "order": 2,
                    },
                    "split_config": {
                        "label": "Fee Split",
                        "widget": "json",
                        "level": "advanced",
                        "order": 3,
                    },
                },
            },
        ),
    )

    # ── Feeds ──────────────────────────────────────────────────────
    register_feed_factory(
        "historical",
        _build_historical_feed,
        metadata=EntityMetadata(
            label="Historical Feed",
            description="Replay from arrays, CSV, or Parquet files. Deterministic.",
            builder_supported=False,
            schema={
                "type": "object",
                "properties": {
                    "prices": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "number"},
                        },
                        "title": "Prices",
                    }
                },
                "required": ["prices"],
            },
            defaults={"prices": {"COLLATERAL": [1.0]}},
            ui_schema={
                "specialEditor": "code-editor",
                "fields": {
                    "prices": {"label": "Prices", "widget": "code-editor"},
                },
            },
        ),
    )
    register_feed_factory(
        "stochastic",
        _build_stochastic_feed,
        metadata=EntityMetadata(
            label="Stochastic Feed",
            description="Stochastic process feeds — GBM, mean-reversion, jump diffusion, configurable per token.",
            schema={
                "type": "object",
                "properties": {
                    "process": {
                        "type": "string",
                        "enum": ["gbm", "ou", "jump_diffusion"],
                        "default": "gbm",
                        "title": "Process",
                    },
                    "process_params": {
                        "type": "object",
                        "additionalProperties": True,
                        "title": "Process Params",
                    },
                    "seed": {
                        "type": ["integer", "null"],
                        "default": None,
                        "title": "Seed",
                    },
                },
                "required": ["process"],
            },
            defaults={"process": "gbm", "process_params": {}},
            ui_schema={
                "fields": {
                    "process": {
                        "label": "Process",
                        "widget": "select",
                        "enumLabels": {
                            "gbm": "Geometric Brownian Motion",
                            "ou": "Ornstein–Uhlenbeck",
                            "jump_diffusion": "Jump Diffusion",
                        },
                        "order": 1,
                    },
                    "process_params": {
                        "label": "Process Parameters",
                        "widget": "json",
                        "order": 2,
                    },
                    "seed": {
                        "label": "Seed",
                        "widget": "number",
                        "level": "advanced",
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_feed_factory(
        "composite",
        _build_composite_feed,
        metadata=EntityMetadata(
            label="Composite Feed",
            description="Combine multiple feed types per token. Weighted or fallback.",
            builder_supported=False,
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={"specialEditor": "code-editor"},
        ),
    )

    # ── Execution models ───────────────────────────────────────────
    _cost_token_schema = {
        "type": "string",
        "default": "COLLATERAL",
        "title": "Cost Token",
    }
    _cost_token_ui = {"label": "Cost Token", "widget": "text"}
    register_execution_factory(
        "direct",
        _build_direct_execution,
        metadata=EntityMetadata(
            label="Direct",
            description=(
                "Network-neutral default. FIFO ordering, zero cost, no queue visibility. "
                "Suitable for protocol-level analysis without network effects."
            ),
            badges=({"label": "Default", "variant": "green"},),
            schema={
                "type": "object",
                "properties": {
                    "cost_token": _cost_token_schema,
                    "expose_pending_actions": {
                        "type": "boolean",
                        "default": False,
                        "title": "Expose Pending Actions",
                    },
                    "refund_failed_costs": {
                        "type": "boolean",
                        "default": False,
                        "title": "Refund Failed Costs",
                    },
                },
            },
            defaults={
                "cost_token": "COLLATERAL",
                "expose_pending_actions": False,
                "refund_failed_costs": False,
            },
            ui_schema={
                "fields": {
                    "cost_token": {**_cost_token_ui, "order": 1},
                    "expose_pending_actions": {
                        "label": "Expose Pending Actions",
                        "widget": "switch",
                        "order": 2,
                    },
                    "refund_failed_costs": {
                        "label": "Refund Failed Costs",
                        "widget": "switch",
                        "order": 3,
                    },
                },
            },
        ),
    )
    register_execution_factory(
        "batch",
        _build_batch_execution,
        metadata=EntityMetadata(
            label="Batch",
            description=(
                "Composable with queue visibility and admission policies. "
                "Supports custom ordering and cost models."
            ),
            schema={
                "type": "object",
                "properties": {
                    "cost_token": _cost_token_schema,
                    "refund_failed_costs": {
                        "type": "boolean",
                        "default": False,
                        "title": "Refund Failed Costs",
                    },
                },
            },
            defaults={"cost_token": "COLLATERAL", "refund_failed_costs": False},
            ui_schema={
                "fields": {
                    "cost_token": {**_cost_token_ui, "order": 1},
                    "refund_failed_costs": {
                        "label": "Refund Failed Costs",
                        "widget": "switch",
                        "order": 2,
                    },
                },
            },
        ),
    )
    register_execution_factory(
        "solana_like",
        _build_solana_like_execution,
        metadata=EntityMetadata(
            label="Solana-like",
            description="Compute-unit pricing with priority fees and fast finality. Solana mainnet fee model.",
            badges=({"label": "Compute Unit", "variant": "purple"},),
            schema={
                "type": "object",
                "properties": {
                    "cost_token": _cost_token_schema,
                    "visible_roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Visible Roles",
                    },
                },
            },
            defaults={"cost_token": "COLLATERAL", "visible_roles": []},
            ui_schema={
                "fields": {
                    "cost_token": {**_cost_token_ui, "order": 1},
                    "visible_roles": {
                        "label": "Visible Roles",
                        "widget": "token-list",
                        "order": 2,
                    },
                },
            },
        ),
    )

    # ── Markets ────────────────────────────────────────────────────
    register_market_factory(
        "cfamm",
        _build_cfamm_market,
        metadata=EntityMetadata(
            label="Constant Function AMM",
            description="L²-norm constant-function AMM with LP position tracking and multi-asset support.",
            badges=(
                {"label": "PricedMarket", "variant": "blue"},
                {"label": "LiquidityPool", "variant": "purple"},
            ),
            schema={
                "type": "object",
                "properties": {
                    "initial_liquidity": {
                        "type": "number",
                        "minimum": 0,
                        "default": 1_000_000,
                        "title": "Initial Liquidity",
                    },
                    "collateral_token": {
                        "type": "string",
                        "default": "COLLATERAL",
                        "title": "Collateral Token",
                    },
                },
                "required": ["initial_liquidity"],
            },
            defaults={"initial_liquidity": 1_000_000, "collateral_token": "COLLATERAL"},
            ui_schema={
                "fields": {
                    "initial_liquidity": {
                        "label": "Initial Liquidity",
                        "widget": "number",
                        "min": 0,
                        "order": 1,
                    },
                    "collateral_token": {
                        "label": "Collateral Token",
                        "widget": "text",
                        "order": 2,
                        "level": "advanced",
                    },
                },
            },
        ),
    )
    register_market_factory(
        "whirlpool",
        _build_whirlpool_market,
        metadata=EntityMetadata(
            label="Orca Whirlpool (real CLMM)",
            description=(
                "Hydrated from a captured mainnet corpus slot — runs the real "
                "Whirlpool CLMM swap math (sqrt-price, tick crossings, fee "
                "tiers) against captured pool / tick-array / vault state."
            ),
            badges=(
                {"label": "PricedMarket", "variant": "blue"},
                {"label": "ConcentratedLiquidity", "variant": "purple"},
                {"label": "RealState", "variant": "green"},
            ),
            schema={
                "type": "object",
                "properties": {
                    "corpus_slot": {
                        "type": "integer",
                        "minimum": 1,
                        "title": "Corpus Slot",
                        "default": 417595698,
                    },
                    "pool_pubkey": {
                        "type": "string",
                        "title": "Pool Pubkey",
                        "default": "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
                    },
                    "token_a_id": {"type": "string", "default": "SOL"},
                    "token_b_id": {"type": "string", "default": "USDC"},
                },
                "required": ["corpus_slot", "pool_pubkey"],
            },
            # Default to the canonical SOL/USDC capture so a fresh "drop a
            # Whirlpool" affordance in the studio resolves to a working
            # market without forcing the user to know the slot number.
            defaults={
                "corpus_slot": 417595698,
                "pool_pubkey": "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
                "token_a_id": "SOL",
                "token_b_id": "USDC",
            },
            ui_schema={
                "fields": {
                    "corpus_slot": {
                        "label": "Corpus Slot",
                        "widget": "number",
                        "min": 1,
                        "order": 1,
                    },
                    "pool_pubkey": {
                        "label": "Pool Pubkey",
                        "widget": "text",
                        "order": 2,
                    },
                    "token_a_id": {"label": "Token A id", "widget": "text", "order": 3},
                    "token_b_id": {"label": "Token B id", "widget": "text", "order": 4},
                },
            },
        ),
    )
    register_market_factory(
        "clob",
        _build_clob_market,
        metadata=EntityMetadata(
            label="Central Limit Order Book",
            description="Central limit order book with price-time priority matching and per-pair books.",
            badges=(
                {"label": "PricedMarket", "variant": "blue"},
                {"label": "OrderBook", "variant": "green"},
            ),
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={},
        ),
    )
    register_market_factory(
        "world",
        _build_world_market_stub,
        metadata=EntityMetadata(
            label="Composite World Market",
            description="Composite world market — run multiple markets in one simulation with cross-market agents.",
            badges=({"label": "Composite", "variant": "yellow"},),
            schema={"type": "object", "properties": {}},
            defaults={},
            ui_schema={"specialEditor": "world-markets-graph"},
        ),
    )

    # ── Agents ─────────────────────────────────────────────────────
    noise_ui = {
        "sections": [
            {
                "key": "trade",
                "label": "Trade Sizing",
                "fields": ["trade_min", "trade_max"],
            },
            {
                "key": "behavior",
                "label": "Behavior",
                "fields": ["frequency", "bundle_probability"],
            },
            {"key": "advanced", "label": "Advanced", "level": "advanced", "fields": ["collateral"]},
        ],
        "fields": {
            "collateral": {
                "label": "Collateral Token",
                "widget": "text",
                "level": "advanced",
                "section": "advanced",
            },
            "trade_min": {
                "label": "Min Trade",
                "widget": "number",
                "min": 0,
                "section": "trade",
                "order": 1,
            },
            "trade_max": {
                "label": "Max Trade",
                "widget": "number",
                "min": 0,
                "section": "trade",
                "order": 2,
            },
            "frequency": {
                "label": "Frequency",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "section": "behavior",
                "order": 1,
            },
            "bundle_probability": {
                "label": "Bundle Probability",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "section": "behavior",
                "order": 2,
            },
        },
    }
    informed_ui = {
        "fields": {
            "collateral": {
                "label": "Collateral Token",
                "widget": "text",
                "level": "advanced",
                "order": 99,
            },
            "conviction": {
                "label": "Conviction",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 1,
            },
            "trade_fraction": {
                "label": "Trade Fraction",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 2,
            },
            "capital_limit": {
                "label": "Capital Limit",
                "widget": "number",
                "min": 0,
                "order": 3,
            },
        },
    }
    arb_ui = {
        "fields": {
            "collateral": {
                "label": "Collateral Token",
                "widget": "text",
                "level": "advanced",
                "order": 99,
            },
            "min_edge_bps": {
                "label": "Min Edge",
                "widget": "slider",
                "min": 0,
                "max": 1000,
                "step": 1,
                "unit": "bps",
                "order": 1,
            },
            "trade_fraction": {
                "label": "Trade Fraction",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 2,
            },
            "max_trade": {
                "label": "Max Trade",
                "widget": "number",
                "min": 0,
                "order": 3,
            },
        },
    }
    manip_ui = {
        "fields": {
            "collateral": {
                "label": "Collateral Token",
                "widget": "text",
                "level": "advanced",
                "order": 99,
            },
            "strategy": {
                "label": "Strategy",
                "widget": "select",
                "enumLabels": {
                    "price_distortion": "Price Distortion",
                    "volume_wash": "Volume Wash",
                },
                "order": 1,
            },
            "target_token": {
                "label": "Target Token",
                "widget": "text",
                "order": 2,
            },
            "budget": {"label": "Budget", "widget": "number", "min": 0, "order": 3},
            "num_tranches": {
                "label": "Tranches",
                "widget": "number",
                "min": 1,
                "order": 4,
            },
            "spend_fraction": {
                "label": "Spend Fraction",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 5,
            },
        },
    }
    lp_ui = {
        "fields": {
            "collateral": {
                "label": "Collateral Token",
                "widget": "text",
                "level": "advanced",
                "order": 99,
            },
            "min_yield_per_round": {
                "label": "Min Yield / Round",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.0001,
                "order": 1,
            },
            "max_loss_threshold": {
                "label": "Max Loss Threshold",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 2,
            },
            "deposit_fraction": {
                "label": "Deposit Fraction",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 3,
            },
            "rebalance_interval": {
                "label": "Rebalance Interval",
                "widget": "number",
                "min": 1,
                "unit": "rounds",
                "order": 4,
            },
        },
    }
    register_agent_factory(
        "noise",
        _build_noise_agent,
        metadata=EntityMetadata(
            label="Noise Trader",
            description="Random trades within configurable size/frequency bounds. Provides background liquidity.",
            schema=noise_schema,
            defaults=noise_defaults,
            ui_schema=noise_ui,
        ),
    )
    swap_noise_ui = {
        "fields": {
            "token_in": {"label": "Token In", "widget": "text", "order": 1},
            "token_out": {"label": "Token Out", "widget": "text", "order": 2},
            "amount_min": {
                "label": "Min Swap Amount",
                "widget": "number",
                "min": 0,
                "order": 3,
            },
            "amount_max": {
                "label": "Max Swap Amount",
                "widget": "number",
                "min": 0,
                "order": 4,
            },
            "frequency": {
                "label": "Frequency",
                "widget": "slider",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 5,
            },
            "cu_price_min": {
                "label": "Min CU Price (μlamports)",
                "widget": "number",
                "min": 0,
                "order": 6,
            },
            "cu_price_max": {
                "label": "Max CU Price (μlamports)",
                "widget": "number",
                "min": 0,
                "order": 7,
            },
        },
    }
    register_agent_factory(
        "swap_noise",
        _build_swap_noise_agent,
        metadata=EntityMetadata(
            label="Swap Noise Trader",
            description=(
                "Emits SwapAction(token_in→token_out) with random size and "
                "compute-unit price. Used as the victim source for "
                "JitoSearcher sandwich/back-run strategies in synthetic "
                "Solana scenarios."
            ),
            badges=({"label": "Solana", "variant": "purple"},),
            schema=swap_noise_schema,
            defaults=swap_noise_defaults,
            ui_schema=swap_noise_ui,
        ),
    )
    register_agent_factory(
        "informed",
        _build_informed_agent,
        metadata=EntityMetadata(
            label="Informed Trader",
            description="Trades toward belief distribution weighted by conviction. Bundle-based execution.",
            schema=informed_schema,
            defaults=informed_defaults,
            ui_schema=informed_ui,
        ),
    )
    register_agent_factory(
        "arbitrageur",
        _build_arbitrageur_agent,
        metadata=EntityMetadata(
            label="Arbitrageur",
            description="Exploits price differences between market and feed. Corrective positions.",
            schema=arb_schema,
            defaults=arb_defaults,
            ui_schema=arb_ui,
        ),
    )
    register_agent_factory(
        "manipulator",
        _build_manipulator_agent,
        metadata=EntityMetadata(
            label="Manipulator",
            description="Strategic price manipulation with attack budgets. Measures attack success.",
            schema=manip_schema,
            defaults=manip_defaults,
            ui_schema=manip_ui,
        ),
    )
    register_agent_factory(
        "lp",
        _build_passive_lp_agent,
        metadata=EntityMetadata(
            label="Liquidity Provider",
            description="Generic liquidity provider role shared across AMM/CLOB markets.",
            schema=lp_schema,
            defaults=lp_defaults,
            ui_schema=lp_ui,
        ),
    )
    register_agent_factory(
        "passive_lp",
        _build_passive_lp_agent,
        metadata=EntityMetadata(
            label="Passive LP",
            description="Deposits if yield attractive, withdraws if loss exceeds threshold.",
            schema=lp_schema,
            defaults=lp_defaults,
            ui_schema=lp_ui,
        ),
    )
    register_agent_factory(
        "rebalancing_lp",
        _build_rebalancing_lp_agent,
        metadata=EntityMetadata(
            label="Rebalancing LP",
            description="Maintains uniform portfolio weights across assets with periodic rebalancing.",
            schema=lp_schema,
            defaults=lp_defaults,
            ui_schema=lp_ui,
        ),
    )
    validator_schema, validator_defaults = schema_and_defaults(ValidatorParams)
    validator_ui = {
        "fields": {
            "pubkey": {"label": "Pubkey", "widget": "text", "order": 1},
            "client": {
                "label": "Client",
                "widget": "select",
                "options": ["jito_solana", "vanilla"],
                "order": 2,
            },
            "stake_lamports": {
                "label": "Stake (lamports)",
                "widget": "number",
                "min": 0,
                "order": 3,
            },
            "stake_pool_share": {
                "label": "Stake Pool Share",
                "widget": "number",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 4,
            },
            "stake_pool_address": {
                "label": "Stake Pool Address",
                "widget": "text",
                "order": 5,
            },
            "commission_pct": {
                "label": "Commission (%)",
                "widget": "number",
                "min": 0,
                "max": 1,
                "step": 0.01,
                "order": 6,
            },
        },
    }
    register_agent_factory(
        "validator",
        _build_validator_agent,
        metadata=EntityMetadata(
            label="Validator",
            description="Solana validator: receives leader slots by stake weight and accrues bundle tips (Jito-Solana) or block rewards only (vanilla).",
            badges=({"label": "Solana", "variant": "purple"},),
            schema=validator_schema,
            defaults=validator_defaults,
            ui_schema=validator_ui,
        ),
    )
    # PRD US-013 line 999: ``JitoSearcher`` schema is hand-written because
    # ``JitoSearcherParams`` requires args (``strategies``, ``tip_curve``,
    # ``min_ev_to_submit_lamports``, ``tip_account``) — the
    # ``schema_and_defaults`` helper instantiates with no arguments and would
    # throw. Defaults mirror the Phase-1.11 ergonomic baseline.
    jito_searcher_schema: dict[str, Any] = {
        "type": "object",
        "required": [
            "strategies",
            "tip_curve",
            "min_ev_to_submit_lamports",
            "tip_account",
        ],
        "properties": {
            "strategies": {
                "type": "array",
                "title": "Strategies",
                "items": {
                    "type": "string",
                    "enum": ["backrun", "sandwich", "jit_lp", "liquidation"],
                },
            },
            "tip_curve": {
                "type": "object",
                "title": "Tip Curve",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["linear", "percent_of_ev", "custom"],
                    },
                    "slope_micro_lamports_per_ev": {"type": "number"},
                    "percent": {"type": "number"},
                },
            },
            "min_ev_to_submit_lamports": {
                "type": "integer",
                "title": "Min EV to Submit (lamports)",
            },
            "tip_account": {"type": "string", "title": "Tip Account"},
            "max_bundle_size": {
                "type": "integer",
                "title": "Max Bundle Size",
                "default": 5,
            },
            "priority_fee_percentile_target": {
                "type": "integer",
                "title": "Priority Fee Percentile Target",
                "default": 75,
            },
            "alt_ids": {
                "type": "array",
                "title": "Address Lookup Table IDs",
                "items": {"type": "string"},
                "default": [],
            },
        },
    }
    jito_searcher_defaults: dict[str, Any] = {
        "strategies": ["backrun"],
        "tip_curve": {"kind": "linear", "slope_micro_lamports_per_ev": 0.05},
        "min_ev_to_submit_lamports": 10_000,
        "tip_account": DEFAULT_JITO_TIP_ACCOUNTS[0],
        "max_bundle_size": 5,
        "priority_fee_percentile_target": 75,
        "alt_ids": [],
    }
    jito_searcher_ui: dict[str, Any] = {
        "fields": {
            "strategies": {"label": "Strategies", "widget": "multiselect", "order": 1},
            "tip_curve": {"label": "Tip Curve", "widget": "object", "order": 2},
            "min_ev_to_submit_lamports": {
                "label": "Min EV to Submit",
                "widget": "number",
                "min": 0,
                "unit": "lamports",
                "order": 3,
            },
            "tip_account": {"label": "Tip Account", "widget": "text", "order": 4},
            "max_bundle_size": {
                "label": "Max Bundle Size",
                "widget": "number",
                "min": 1,
                "max": MAX_BUNDLE_TXS,
                "order": 5,
            },
            "priority_fee_percentile_target": {
                "label": "Priority Fee Percentile",
                "widget": "number",
                "min": 0,
                "max": 100,
                "order": 6,
            },
            "alt_ids": {
                "label": "Address Lookup Table IDs",
                "widget": "token-list",
                "order": 7,
            },
        },
    }
    register_agent_factory(
        "jito_searcher",
        _build_jito_searcher_agent,
        metadata=EntityMetadata(
            label="Jito Searcher",
            description="Searcher agent that submits Jito bundles tipping the leader (backrun / sandwich strategies).",
            badges=({"label": "Solana", "variant": "purple"},),
            schema=jito_searcher_schema,
            defaults=jito_searcher_defaults,
            ui_schema=jito_searcher_ui,
        ),
    )

    # ── Leader Schedules ────────────────────────────────────────────
    register_leader_schedule_factory(
        "stake_weighted",
        _build_stake_weighted_leader_schedule,
        metadata=EntityMetadata(
            label="Stake-Weighted Leader Schedule",
            description="Per-slot leader chosen by stake-weighted random selection. Cached per epoch; deterministic given seed.",
            badges=({"label": "Solana", "variant": "purple"},),
            builder_supported=False,
            schema={
                "type": "object",
                "properties": {
                    "validators": {
                        "type": "array",
                        "title": "Validators",
                        "items": {
                            "type": "object",
                            "properties": {
                                "pubkey": {"type": "string", "title": "Pubkey"},
                                "stake_lamports": {
                                    "type": "integer",
                                    "title": "Stake (lamports)",
                                },
                            },
                            "required": ["pubkey", "stake_lamports"],
                        },
                    },
                    "seed": {"type": "integer", "default": 0, "title": "Seed"},
                    "epoch_length_slots": {
                        "type": "integer",
                        "default": 432_000,
                        "title": "Epoch Length (slots)",
                    },
                },
            },
            defaults={
                "validators": [
                    {"pubkey": "validator-1", "stake_lamports": 1_000_000_000}
                ],
                "seed": 0,
                "epoch_length_slots": 432_000,
            },
            ui_schema={
                "fields": {
                    "validators": {
                        "label": "Validators",
                        "widget": "json",
                        "order": 1,
                    },
                    "seed": {"label": "Seed", "widget": "number", "order": 2},
                    "epoch_length_slots": {
                        "label": "Epoch Length",
                        "widget": "number",
                        "unit": "slots",
                        "order": 3,
                    },
                },
            },
        ),
    )


_register_builtins()
