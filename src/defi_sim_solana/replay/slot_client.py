"""Slot-tx client for Solana mainnet replay (PRD US-001 line 98).

Pulls a single mainnet slot via ``getBlock``, with two fast paths:

1. **Corpus fixture** — if the slot has been committed to the on-disk corpus
   (see :mod:`defi_sim_solana.replay.corpus`), the snapshot is hydrated from
   that fixture and no RPC client is required at all. This keeps offline CI
   runs hermetic.
2. **LRU-cached RPC** — if no fixture is committed, the call is delegated to
   an injected (or default) :class:`SolanaClient` and memoized.

Caching discipline (PRD line 98): the LRU cache is keyed only on hashable
scalars — ``(provider_id, corpus_root, slot)``. Injected client/loader
objects never enter the cache key because their identity churns across
process invocations and they are often unhashable.
"""

from __future__ import annotations

import functools
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .corpus import corpus_root, load_corpus_fixture

__all__ = [
    "CorpusLoader",
    "JsonRpcSolanaClient",
    "SlotSnapshot",
    "SolanaClient",
    "clear_slot_cache",
    "default_client",
    "get_slot",
    "provider_id",
]

_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_RETRYABLE_RPC_ERROR_CODES = frozenset({429, -32004, -32005})


class SolanaClient(Protocol):
    """Minimal RPC surface used by the slot ingestion layer."""

    def get_block(self, slot: int) -> dict[str, Any]: ...


class JsonRpcSolanaClient:
    """Stdlib Solana JSON-RPC client used by ``default_client()``.

    This intentionally keeps the default recent-slot path dependency-light:
    development agents can use ``SOLANA_RPC_URL`` for ``getBlock`` without
    inventing a fake provider or requiring a provider-specific SDK. Historical
    account-state access remains separate because standard Solana JSON-RPC
    does not provide true as-of-slot ``getProgramAccounts`` semantics.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.25,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint must be non-empty")
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))

    def get_block(self, slot: int) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBlock",
            "params": [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": True,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        body: dict[str, Any] | None = None
        for attempt in range(self.max_retries + 1):
            try:
                body = self._post_json(payload)
            except urllib.error.HTTPError as exc:
                if self._should_retry_http(exc, attempt):
                    self._sleep_before_retry(attempt)
                    continue
                raise RuntimeError(f"getBlock({slot}) RPC HTTP error: {exc.code}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    self._sleep_before_retry(attempt)
                    continue
                raise RuntimeError(
                    f"getBlock({slot}) RPC transport error: {exc.reason}"
                ) from exc

            error = body.get("error")
            if error is not None:
                if self._should_retry_rpc_error(error, attempt):
                    self._sleep_before_retry(attempt)
                    continue
                raise RuntimeError(f"getBlock({slot}) RPC error: {error!r}")
            break

        if body is None:
            raise RuntimeError(f"getBlock({slot}) RPC request failed without response")
        result = body.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"getBlock({slot}) returned non-dict result: {result!r}")
        result.setdefault("slot", slot)
        return result

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _should_retry_http(
        self,
        exc: urllib.error.HTTPError,
        attempt: int,
    ) -> bool:
        return (
            attempt < self.max_retries
            and exc.code in _RETRYABLE_HTTP_STATUS_CODES
        )

    def _should_retry_rpc_error(self, error: Any, attempt: int) -> bool:
        if attempt >= self.max_retries or not isinstance(error, dict):
            return False
        code = error.get("code")
        if isinstance(code, int):
            return code in _RETRYABLE_RPC_ERROR_CODES
        message = str(error.get("message", "")).lower()
        return "too many requests" in message or "rate limit" in message

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.backoff_base_seconds <= 0:
            return
        time.sleep(self.backoff_base_seconds * (2**attempt))


class CorpusLoader(Protocol):
    """Callable shape of :func:`load_corpus_fixture`.

    Defined as a protocol so tests / hosted environments can inject corpus
    roots without mutating module-global state.
    """

    def __call__(
        self,
        slot: int,
        kind: str,
        program_id: str | None = None,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SlotSnapshot:
    """Frozen snapshot of a single Solana slot.

    Mirrors the fields of ``getBlock`` (PRD line 125): transactions
    (raw + parsed), per-tx compute units, leader pubkey, Jito tips,
    account writes, blockhash. Optional fields default so partial /
    minimized fixtures still hydrate cleanly — the materializer
    (PRD line 191) decides whether a snapshot is rich enough to act on.
    """

    slot: int
    blockhash: str | None = None
    previous_blockhash: str | None = None
    parent_slot: int | None = None
    block_height: int | None = None
    block_time: int | None = None
    leader: str | None = None
    transactions: tuple[dict[str, Any], ...] = ()
    transaction_compute_units: tuple[int, ...] = ()
    jito_tips: tuple[dict[str, Any], ...] = ()
    account_writes: tuple[dict[str, Any], ...] = ()
    rewards: tuple[dict[str, Any], ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> SlotSnapshot:
        txns = tuple(raw.get("transactions") or ())
        per_tx_cu = tuple(
            int(((t.get("meta") or {}).get("computeUnitsConsumed")) or 0)
            for t in txns
            if isinstance(t, dict)
        )
        rewards = tuple(raw.get("rewards") or ())
        leader: str | None = None
        for r in rewards:
            if isinstance(r, dict) and r.get("rewardType") in ("Fee", "fee"):
                pubkey = r.get("pubkey")
                if isinstance(pubkey, str):
                    leader = pubkey
                    break
        slot_value = raw.get("slot")
        if not isinstance(slot_value, int):
            slot_value = int(raw.get("parentSlot", -1) or -1)
        return cls(
            slot=int(slot_value),
            blockhash=raw.get("blockhash"),
            previous_blockhash=raw.get("previousBlockhash"),
            parent_slot=raw.get("parentSlot"),
            block_height=raw.get("blockHeight"),
            block_time=raw.get("blockTime"),
            leader=leader,
            transactions=txns,
            transaction_compute_units=per_tx_cu,
            jito_tips=tuple(raw.get("jitoTips") or ()),
            account_writes=tuple(raw.get("accountWrites") or ()),
            rewards=rewards,
            raw=raw,
        )


_CLIENT_REGISTRY: dict[str, SolanaClient] = {}


def provider_id(client: SolanaClient) -> str:
    """Return a stable hashable identity for ``client``.

    Prefers the RPC endpoint URL (the conventional surface on Solana clients
    via ``endpoint`` / ``url`` / ``rpc_endpoint``) so two client instances
    pointing at the same provider share a cache key. Falls back to the
    fully-qualified class name when no endpoint attribute is exposed.
    """
    for attr in ("endpoint", "url", "rpc_endpoint"):
        ep = getattr(client, attr, None)
        if isinstance(ep, str) and ep:
            return ep
    return f"{type(client).__module__}.{type(client).__qualname__}"


def _register_client(client: SolanaClient) -> str:
    pid = provider_id(client)
    _CLIENT_REGISTRY[pid] = client
    return pid


def _client_for(pid: str) -> SolanaClient:
    try:
        return _CLIENT_REGISTRY[pid]
    except KeyError as exc:
        raise LookupError(
            f"no Solana client registered under provider_id {pid!r}; "
            "pass `client=` to get_slot or wire default_client() first."
        ) from exc


def default_client() -> SolanaClient:
    """Construct the default Solana RPC client.

    Uses ``SOLANA_RPC_URL`` for normal recent-slot ``getBlock`` development.
    This is deliberately not used for historical account-state reads; those
    still require ``default_historical_backend()`` / archival provider wiring
    because standard JSON-RPC cannot answer true as-of-slot account queries.
    """
    endpoint = os.environ.get("SOLANA_RPC_URL")
    if not endpoint:
        raise RuntimeError(
            "default_client() requires SOLANA_RPC_URL for recent-slot getBlock "
            "access. Export SOLANA_RPC_URL, pass client=, or use a committed "
            "corpus fixture."
        )
    timeout = float(os.environ.get("SOLANA_RPC_TIMEOUT", "60"))
    return JsonRpcSolanaClient(endpoint, timeout=timeout)


def _corpus_root_str() -> str:
    return str(corpus_root())


def get_slot(
    slot: int,
    *,
    client: SolanaClient | None = None,
    corpus_loader: CorpusLoader | None = None,
) -> SlotSnapshot:
    """Pull a slot via ``getBlock``. Falls back to corpus fixture if present.

    The corpus path is consulted first so offline / CI runs need no client
    at all. When a fixture is absent, the call is forwarded to ``client``
    (or :func:`default_client`) and memoized on
    ``(provider_id, corpus_root, slot)`` — all hashable scalars.
    """
    loader: CorpusLoader = corpus_loader or load_corpus_fixture
    fixture = loader(slot, kind="block")
    if fixture is not None:
        return SlotSnapshot.from_raw(fixture)
    client = client or default_client()
    pid = _register_client(client)
    return _get_slot_cached(pid, _corpus_root_str(), slot)


@functools.lru_cache(maxsize=128)
def _get_slot_cached(
    provider_id: str,
    corpus_root: str,
    slot: int,
) -> SlotSnapshot:
    """Inner cached fetch keyed only on hashable scalars."""
    raw = _client_for(provider_id).get_block(slot)
    return SlotSnapshot.from_raw(raw)


def clear_slot_cache() -> None:
    """Drop all cached ``get_slot`` results."""
    _get_slot_cached.cache_clear()
