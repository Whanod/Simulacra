"""Probe a Solana JSON-RPC endpoint for replay/calibration capabilities.

This is intentionally a standard JSON-RPC probe. It answers:

* Can the endpoint fetch block/transaction history for a target date or slot?
* Can it read current account/program state while requiring the node to be at
  least the target slot?
* Does standard RPC prove true account state *as of* the target slot?

The last answer is expected to be "no" for ordinary Solana JSON-RPC:
``minContextSlot`` is a freshness guard, not a historical account-state
selector. If a provider offers true historical account state through a custom
API, this script will not detect that custom surface.

Examples:

    python tools/check_solana_rpc_capabilities.py \\
        --rpc-url "$SOLANA_RPC_URL" \\
        --months-ago 6 \\
        --account <PUBKEY>

    python tools/check_solana_rpc_capabilities.py \\
        --rpc-url "$SOLANA_RPC_URL" \\
        --date 2025-11-03 \\
        --program whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SECONDS_PER_DAY = 24 * 60 * 60


class RpcError(RuntimeError):
    """Raised when the endpoint returns transport or JSON-RPC errors."""


@dataclass
class Probe:
    name: str
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class RpcClient:
    def __init__(self, endpoint: str, *, timeout: float) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._next_id = 1

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or [],
        }
        self._next_id += 1
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RpcError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RpcError(f"transport error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RpcError(f"invalid JSON response: {exc}") from exc

        if body.get("error") is not None:
            raise RpcError(f"RPC error from {method}: {body['error']!r}")
        return body.get("result")


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_date(raw: str) -> dt.datetime:
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(
            f"invalid --date {raw!r}; use YYYY-MM-DD or ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _target_time(args: argparse.Namespace) -> dt.datetime | None:
    selectors = [
        args.slot is not None,
        args.date is not None,
        args.months_ago is not None,
    ]
    if sum(selectors) != 1:
        raise SystemExit("pass exactly one of --slot, --date, or --months-ago")
    if args.slot is not None:
        return None
    if args.date is not None:
        return _parse_date(args.date)
    # Calendar months need dateutil; keep this stdlib-only and document the
    # approximation. For capability probing, day-level precision is enough.
    return _utc_now() - dt.timedelta(days=float(args.months_ago) * 30.4375)


def _try_probe(name: str, fn: Any) -> Probe:
    try:
        details = fn()
        return Probe(name=name, ok=True, details=details or {})
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic tool.
        return Probe(name=name, ok=False, error=str(exc))


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _get_slot(client: RpcClient) -> int:
    slot = client.call("getSlot", [{"commitment": "finalized"}])
    if not isinstance(slot, int):
        raise RpcError(f"getSlot returned non-int result: {slot!r}")
    return slot


def _get_block_time(client: RpcClient, slot: int) -> int | None:
    value = client.call("getBlockTime", [slot])
    return value if isinstance(value, int) else None


def _scan_for_time(
    client: RpcClient,
    *,
    start_slot: int,
    high_slot: int,
    step: int,
    max_steps: int = 32,
) -> tuple[int, int] | None:
    slot = start_slot
    for _ in range(max_steps):
        if slot < 0 or slot > high_slot:
            return None
        block_time = _get_block_time(client, slot)
        if block_time is not None:
            return slot, block_time
        slot += step
    return None


def _resolve_slot_for_time(
    client: RpcClient,
    *,
    target: dt.datetime,
    first_available_block: int | None,
    minimum_ledger_slot: int | None,
    latest_slot: int,
    max_calls: int,
) -> dict[str, Any]:
    target_ts = int(target.timestamp())
    low = first_available_block
    if low is None:
        low = minimum_ledger_slot
    if low is None:
        low = max(0, latest_slot - int(365 * SECONDS_PER_DAY / 0.4))

    low_time_pair = _scan_for_time(
        client,
        start_slot=low,
        high_slot=latest_slot,
        step=max(1, (latest_slot - low) // 100 or 1),
    )
    latest_time = _get_block_time(client, latest_slot)
    if latest_time is None:
        latest_pair = _scan_for_time(
            client,
            start_slot=latest_slot - 1,
            high_slot=latest_slot,
            step=-1,
        )
        latest_time = latest_pair[1] if latest_pair else None

    if latest_time is not None and target_ts > latest_time:
        raise RpcError(
            "target date is newer than the endpoint's finalized latest block time"
        )
    if low_time_pair is not None and target_ts < low_time_pair[1]:
        return {
            "target_timestamp": target_ts,
            "target_datetime": target.isoformat(),
            "resolved_slot": None,
            "reason": "target date is older than the endpoint's first timed retained block",
            "first_timed_slot": low_time_pair[0],
            "first_timed_block_time": low_time_pair[1],
        }

    # Binary-search the largest slot whose block time is <= target_ts. Block
    # time is monotonic enough for this diagnostic; skipped/null slots are
    # handled by searching nearby.
    calls = 0
    best_slot: int | None = None
    best_time: int | None = None
    lo = low
    hi = latest_slot
    while lo <= hi and calls < max_calls:
        mid = (lo + hi) // 2
        calls += 1
        block_time = _get_block_time(client, mid)
        if block_time is None:
            nearby = (
                _scan_for_time(client, start_slot=mid - 1, high_slot=latest_slot, step=-1)
                or _scan_for_time(client, start_slot=mid + 1, high_slot=latest_slot, step=1)
            )
            if nearby is None:
                break
            mid, block_time = nearby
        if block_time <= target_ts:
            best_slot = mid
            best_time = block_time
            lo = mid + 1
        else:
            hi = mid - 1

    if best_slot is None:
        raise RpcError("could not resolve a slot for target date")
    return {
        "target_timestamp": target_ts,
        "target_datetime": target.isoformat(),
        "resolved_slot": best_slot,
        "resolved_block_time": best_time,
        "search_calls": calls,
        "search_call_limit": max_calls,
    }


def _get_block(client: RpcClient, slot: int) -> dict[str, Any]:
    result = client.call(
        "getBlock",
        [
            slot,
            {
                "commitment": "finalized",
                "encoding": "json",
                "transactionDetails": "full",
                "maxSupportedTransactionVersion": 0,
                "rewards": True,
            },
        ],
    )
    if not isinstance(result, dict):
        raise RpcError(f"getBlock({slot}) returned {result!r}")
    return result


def _first_signature(block: dict[str, Any]) -> str | None:
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        inner = tx.get("transaction")
        if not isinstance(inner, dict):
            continue
        signatures = inner.get("signatures")
        if isinstance(signatures, list) and signatures and isinstance(signatures[0], str):
            return signatures[0]
    return None


def _first_account(block: dict[str, Any]) -> str | None:
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        inner = tx.get("transaction")
        if not isinstance(inner, dict):
            continue
        message = inner.get("message")
        if not isinstance(message, dict):
            continue
        keys = message.get("accountKeys") or []
        for key in keys:
            if isinstance(key, str):
                return key
            if isinstance(key, dict) and isinstance(key.get("pubkey"), str):
                return key["pubkey"]
    return None


def _context_slot(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    context = result.get("context")
    if not isinstance(context, dict):
        return None
    return _as_int(context.get("slot"))


def _is_as_of_context(context_slot: int | None, target_slot: int, tolerance: int) -> bool:
    if context_slot is None:
        return False
    return abs(context_slot - target_slot) <= tolerance


def _print_text(report: dict[str, Any]) -> None:
    print("Solana RPC capability probe")
    print(f"RPC URL: {report['rpc_url']}")
    print(f"Target slot: {report.get('target_slot')}")
    if report.get("target_datetime"):
        print(f"Target date: {report['target_datetime']}")
    print()

    print("Endpoint")
    endpoint = report["endpoint"]
    print(f"  version: {endpoint.get('version')}")
    print(f"  latest_finalized_slot: {endpoint.get('latest_finalized_slot')}")
    print(f"  minimum_ledger_slot: {endpoint.get('minimum_ledger_slot')}")
    print(f"  first_available_block: {endpoint.get('first_available_block')}")
    print()

    print("Probes")
    for probe in report["probes"]:
        status = "PASS" if probe["ok"] else "FAIL"
        print(f"  [{status}] {probe['name']}")
        if probe.get("error"):
            print(f"    error: {probe['error']}")
        for key, value in probe.get("details", {}).items():
            print(f"    {key}: {value}")
    print()

    verdict = report["verdict"]
    print("Verdict")
    for key, value in verdict.items():
        print(f"  {key}: {value}")
    print()
    print(
        "Note: standard Solana JSON-RPC can prove block/transaction retention. "
        "It does not expose an arbitrary 'account state at slot S' selector; "
        "provider-specific archive/indexer APIs must be checked separately."
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    endpoint = args.rpc_url or os.environ.get("SOLANA_RPC_URL")
    if not endpoint:
        raise SystemExit("pass --rpc-url or export SOLANA_RPC_URL")

    target_dt = _target_time(args)
    client = RpcClient(endpoint, timeout=args.timeout)

    endpoint_info: dict[str, Any] = {}
    probes: list[Probe] = []

    probes.append(
        _try_probe("getVersion", lambda: {"version": client.call("getVersion")})
    )
    if probes[-1].ok:
        endpoint_info["version"] = probes[-1].details["version"]

    latest_slot = _get_slot(client)
    endpoint_info["latest_finalized_slot"] = latest_slot

    min_ledger_probe = _try_probe(
        "minimumLedgerSlot",
        lambda: {"minimum_ledger_slot": client.call("minimumLedgerSlot")},
    )
    probes.append(min_ledger_probe)
    minimum_ledger_slot = (
        _as_int(min_ledger_probe.details.get("minimum_ledger_slot"))
        if min_ledger_probe.ok
        else None
    )
    endpoint_info["minimum_ledger_slot"] = minimum_ledger_slot

    first_block_probe = _try_probe(
        "getFirstAvailableBlock",
        lambda: {"first_available_block": client.call("getFirstAvailableBlock")},
    )
    probes.append(first_block_probe)
    first_available_block = (
        _as_int(first_block_probe.details.get("first_available_block"))
        if first_block_probe.ok
        else None
    )
    endpoint_info["first_available_block"] = first_available_block

    if args.slot is not None:
        target_slot = int(args.slot)
        target_resolution: dict[str, Any] = {"resolved_slot": target_slot}
    else:
        assert target_dt is not None
        target_resolution = _resolve_slot_for_time(
            client,
            target=target_dt,
            first_available_block=first_available_block,
            minimum_ledger_slot=minimum_ledger_slot,
            latest_slot=latest_slot,
            max_calls=args.max_time_search_calls,
        )
        resolved = target_resolution.get("resolved_slot")
        if resolved is None:
            target_slot = -1
        else:
            target_slot = int(resolved)

    if target_dt is not None:
        probes.append(
            Probe(
                name="resolve date to slot",
                ok=target_slot >= 0,
                details=target_resolution,
                error=None if target_slot >= 0 else target_resolution.get("reason"),
            )
        )

    block: dict[str, Any] | None = None
    if target_slot >= 0:
        block_probe = _try_probe(
            "getBlock target slot",
            lambda: _block_probe_details(client, target_slot),
        )
        probes.append(block_probe)
        if block_probe.ok:
            block = block_probe.details.pop("_raw_block")

    if block is not None:
        signature = args.signature or _first_signature(block)
        if signature:
            probes.append(
                _try_probe(
                    "getTransaction sample signature",
                    lambda: _transaction_probe_details(client, signature),
                )
            )
        else:
            probes.append(
                Probe(
                    name="getTransaction sample signature",
                    ok=False,
                    error="no transaction signature found in target block",
                )
            )

        account = args.account or _first_account(block)
        if account:
            probes.append(
                _try_probe(
                    "getAccountInfo with minContextSlot",
                    lambda: _account_probe_details(
                        client,
                        account,
                        target_slot,
                        args.as_of_tolerance_slots,
                    ),
                )
            )
        else:
            probes.append(
                Probe(
                    name="getAccountInfo with minContextSlot",
                    ok=False,
                    error="no account provided and none found in target block",
                )
            )

    if args.program and target_slot >= 0:
        probes.append(
            _try_probe(
                "getProgramAccounts with minContextSlot",
                lambda: _program_accounts_probe_details(
                    client,
                    args.program,
                    target_slot,
                    args.as_of_tolerance_slots,
                ),
            )
        )

    probe_payloads = [
        {
            "name": probe.name,
            "ok": probe.ok,
            "details": probe.details,
            "error": probe.error,
        }
        for probe in probes
    ]
    verdict = _verdict(probe_payloads)

    return {
        "rpc_url": _redact_url(endpoint),
        "target_datetime": target_dt.isoformat() if target_dt is not None else None,
        "target_slot": target_slot if target_slot >= 0 else None,
        "endpoint": endpoint_info,
        "probes": probe_payloads,
        "verdict": verdict,
    }


def _redact_url(url: str) -> str:
    """Redact credential-shaped query params before displaying reports."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in {"api-key", "apikey", "key", "token", "access_token"}:
            query.append((key, "<redacted>"))
        else:
            query.append((key, value))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _block_probe_details(client: RpcClient, target_slot: int) -> dict[str, Any]:
    block = _get_block(client, target_slot)
    transactions = block.get("transactions") or []
    return {
        "slot": target_slot,
        "block_time": block.get("blockTime"),
        "block_height": block.get("blockHeight"),
        "transaction_count": len(transactions) if isinstance(transactions, list) else None,
        "blockhash": block.get("blockhash"),
        "_raw_block": block,
    }


def _transaction_probe_details(client: RpcClient, signature: str) -> dict[str, Any]:
    tx = client.call(
        "getTransaction",
        [
            signature,
            {
                "commitment": "finalized",
                "encoding": "json",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    if not isinstance(tx, dict):
        raise RpcError(f"getTransaction returned {tx!r}")
    return {
        "signature": signature,
        "slot": tx.get("slot"),
        "has_meta": isinstance(tx.get("meta"), dict),
    }


def _account_probe_details(
    client: RpcClient,
    account: str,
    target_slot: int,
    tolerance: int,
) -> dict[str, Any]:
    result = client.call(
        "getAccountInfo",
        [
            account,
            {
                "commitment": "finalized",
                "encoding": "base64",
                "minContextSlot": target_slot,
            },
        ],
    )
    context_slot = _context_slot(result)
    value = result.get("value") if isinstance(result, dict) else None
    return {
        "account": account,
        "context_slot": context_slot,
        "target_slot": target_slot,
        "account_exists_at_returned_context": value is not None,
        "as_of_target_slot_proven": _is_as_of_context(
            context_slot,
            target_slot,
            tolerance,
        ),
        "interpretation": (
            "PASS here only means the endpoint can read account state after "
            "the node has reached minContextSlot. If context_slot is far above "
            "target_slot, this is current-ish state, not historical as-of state."
        ),
    }


def _program_accounts_probe_details(
    client: RpcClient,
    program: str,
    target_slot: int,
    tolerance: int,
) -> dict[str, Any]:
    result = client.call(
        "getProgramAccounts",
        [
            program,
            {
                "commitment": "finalized",
                "encoding": "base64",
                "withContext": True,
                "dataSlice": {"offset": 0, "length": 0},
                "minContextSlot": target_slot,
            },
        ],
    )
    context_slot = _context_slot(result)
    value = result.get("value") if isinstance(result, dict) else None
    count = len(value) if isinstance(value, list) else None
    return {
        "program": program,
        "context_slot": context_slot,
        "target_slot": target_slot,
        "account_count_at_returned_context": count,
        "as_of_target_slot_proven": _is_as_of_context(
            context_slot,
            target_slot,
            tolerance,
        ),
        "interpretation": (
            "This is a program-account read at the returned context slot. "
            "Standard RPC does not provide a slot parameter that selects "
            "historical program state."
        ),
    }


def _verdict(probes: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {probe["name"]: probe for probe in probes}
    block_ok = bool(by_name.get("getBlock target slot", {}).get("ok"))
    tx_ok = bool(by_name.get("getTransaction sample signature", {}).get("ok"))
    account_probe = by_name.get("getAccountInfo with minContextSlot")
    program_probe = by_name.get("getProgramAccounts with minContextSlot")

    account_as_of = False
    if account_probe and account_probe.get("ok"):
        account_as_of = bool(
            account_probe.get("details", {}).get("as_of_target_slot_proven")
        )
    program_as_of = False
    if program_probe and program_probe.get("ok"):
        program_as_of = bool(
            program_probe.get("details", {}).get("as_of_target_slot_proven")
        )

    as_of_known = account_as_of or program_as_of
    return {
        "block_history_available": block_ok,
        "transaction_history_available": tx_ok,
        "standard_rpc_proves_as_of_account_state": as_of_known,
        "sufficient_for_opaque_replay": block_ok and tx_ok,
        "sufficient_for_real_fork_or_calibration": (
            block_ok
            and tx_ok
            and as_of_known
        ),
        "next_step_if_false": (
            "Use a provider-specific historical account-state API, an indexer/"
            "Geyser archive, or a prebuilt corpus fixture for the target slot."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check a Solana RPC endpoint's block/transaction/history-account "
            "capabilities for a target date or slot."
        )
    )
    parser.add_argument("--rpc-url", help="RPC endpoint; defaults to SOLANA_RPC_URL")
    parser.add_argument("--slot", type=int, help="Exact target slot to probe")
    parser.add_argument("--date", help="Target UTC date/time, e.g. 2025-11-03")
    parser.add_argument(
        "--months-ago",
        type=float,
        help="Approximate target age in months, using 30.4375 days per month",
    )
    parser.add_argument(
        "--account",
        help=(
            "Account pubkey to probe with getAccountInfo. Defaults to the first "
            "account found in the target block."
        ),
    )
    parser.add_argument(
        "--signature",
        help=(
            "Transaction signature to probe with getTransaction. Defaults to the "
            "first signature found in the target block."
        ),
    )
    parser.add_argument(
        "--program",
        help=(
            "Optional program ID to probe with getProgramAccounts. This can be "
            "expensive for large programs even with dataSlice length 0."
        ),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--max-time-search-calls",
        type=int,
        default=48,
        help="Max getBlockTime calls used to resolve --date/--months-ago",
    )
    parser.add_argument(
        "--as-of-tolerance-slots",
        type=int,
        default=2,
        help="Context-slot tolerance for considering a read as target-slot state",
    )

    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        report = build_report(args)
    except Exception as exc:  # noqa: BLE001 - diagnostic CLI.
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
