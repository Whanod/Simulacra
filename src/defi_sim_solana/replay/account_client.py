"""Program-accounts client for Solana mainnet replay (PRD US-001 lines 137-172).

Pulls program accounts for a slot via two fast paths:

1. **Corpus fixture** — if the slot has been committed under
   ``solana-plans/calibration/corpus/<slot>/program_accounts-<program_id>.json[.gz]``,
   the snapshot is hydrated from disk and no backend is required at all.
   Keeps offline CI runs hermetic.
2. **LRU-cached recent backend** — if no fixture is committed, the call is
   delegated to an injected (or default) :class:`RecentAccountBackend` that
   fetches against the live RPC tip. The backend exposes
   :meth:`get_latest_slot`, and the wrapper enforces that the requested slot
   equals the current latest. **Past uncommitted slots are rejected** with a
   clear error rather than fabricated from latest state (PRD line 172).

Caching discipline (PRD line 137-152): the LRU cache is keyed only on hashable
scalars — ``(backend_id, corpus_root, program_id, slot, discriminator_hex)``.
Injected backend / loader objects never enter the cache key because their
identity churns across process invocations and they are often unhashable.

**Important** (PRD line 168): the backend MAY use ``minContextSlot`` against
``getProgramAccounts`` as a *freshness guard* for latest-state fetches (it
ensures the RPC node has reached at least the requested slot). It MUST NOT be
relied on to produce historical account state — ``minContextSlot`` does not
return account state as-of a slot. Wrapper semantics are: corpus → latest if
``slot == backend.get_latest_slot()`` → reject otherwise.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Protocol

from .corpus import corpus_root, load_corpus_fixture

__all__ = [
    "AccountRecord",
    "AccountSnapshot",
    "CorpusLoader",
    "HistoricalAccountBackend",
    "JsonRpcRecentAccountBackend",
    "RecentAccountBackend",
    "backend_id",
    "clear_program_accounts_cache",
    "default_historical_backend",
    "default_recent_backend",
    "get_program_accounts_at_slot",
]


class RecentAccountBackend(Protocol):
    """Latest-state ``getProgramAccounts`` surface for the account ingestion layer.

    The backend fetches against the live RPC tip. The wrapper enforces that
    the requested slot equals :meth:`get_latest_slot`; past uncommitted slots
    are rejected (PRD line 172). ``discriminator`` is an optional Anchor-style
    prefix passed through to ``getProgramAccounts`` filters.
    """

    def get_latest_slot(self) -> int: ...

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]: ...


# Deprecated alias retained so existing call sites (fork_loader, corpus tool,
# integration tests) keep importing the old name during the transition.
HistoricalAccountBackend = RecentAccountBackend


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
class AccountRecord:
    """One ``(pubkey, account_data, owner, lamports, slot)`` tuple per PRD line 159.

    ``account_data`` is raw bytes — Phase 2.3 hydrators do the parsing.
    """

    pubkey: str
    account_data: bytes
    owner: str
    lamports: int
    slot: int


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Frozen snapshot of program accounts as-of a slot (PRD line 159)."""

    program_id: str
    slot: int
    accounts: tuple[AccountRecord, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> AccountSnapshot:
        program_id = str(raw.get("program_id") or raw.get("programId") or "")
        slot_value = raw.get("slot")
        if not isinstance(slot_value, int):
            slot_value = int(slot_value or 0)
        accounts_raw = raw.get("accounts") or ()
        accounts: list[AccountRecord] = []
        for entry in accounts_raw:
            if not isinstance(entry, dict):
                continue
            pubkey = str(entry.get("pubkey") or "")
            account = entry.get("account") or {}
            owner = str(account.get("owner") or "")
            lamports = int(account.get("lamports") or 0)
            data = _decode_account_data(account.get("data"))
            entry_slot = entry.get("slot")
            if not isinstance(entry_slot, int):
                entry_slot = slot_value
            accounts.append(
                AccountRecord(
                    pubkey=pubkey,
                    account_data=data,
                    owner=owner,
                    lamports=lamports,
                    slot=int(entry_slot),
                )
            )
        return cls(
            program_id=program_id,
            slot=int(slot_value),
            accounts=tuple(accounts),
            raw=raw,
        )


def _decode_account_data(data: Any) -> bytes:
    """Best-effort decode of getProgramAccounts ``account.data``.

    The JSON-RPC response shape is ``[<base64-bytes>, "base64"]`` (or
    ``"base58"``); some providers also return raw ``bytes`` directly.
    Unknown encodings round-trip to empty bytes so the hydrator can still
    inspect the raw payload via :attr:`AccountSnapshot.raw`.
    """
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, list) and len(data) >= 2:
        payload, encoding = data[0], data[1]
        if not isinstance(payload, str):
            return b""
        encoding = str(encoding).lower()
        if encoding in ("base64", "base64+zstd"):
            import base64
            try:
                return base64.b64decode(payload)
            except (ValueError, TypeError):
                return b""
        if encoding == "base58":
            try:
                from base58 import b58decode  # type: ignore[import-not-found]
            except ImportError:
                return b""
            try:
                return bytes(b58decode(payload))
            except (ValueError, TypeError):
                return b""
    if isinstance(data, str):
        import base64
        try:
            return base64.b64decode(data)
        except (ValueError, TypeError):
            return b""
    return b""


_BACKEND_REGISTRY: dict[str, RecentAccountBackend] = {}


def backend_id(backend: RecentAccountBackend) -> str:
    """Return a stable hashable identity for ``backend``.

    Mirrors :func:`slot_client.provider_id`: prefers an endpoint-style
    attribute so two backends pointing at the same archival provider share
    a cache key. Falls back to the fully-qualified class name.
    """
    for attr in ("endpoint", "url", "rpc_endpoint", "archive_url"):
        ep = getattr(backend, attr, None)
        if isinstance(ep, str) and ep:
            return ep
    return f"{type(backend).__module__}.{type(backend).__qualname__}"


def _register_backend(backend: RecentAccountBackend) -> str:
    bid = backend_id(backend)
    _BACKEND_REGISTRY[bid] = backend
    return bid


def _backend_for(bid: str) -> RecentAccountBackend:
    try:
        return _BACKEND_REGISTRY[bid]
    except KeyError as exc:
        raise LookupError(
            f"no recent-account backend registered under backend_id {bid!r}; "
            "pass `backend=` to get_program_accounts_at_slot or wire "
            "default_recent_backend() first."
        ) from exc


def default_recent_backend() -> RecentAccountBackend:
    """Construct the default :class:`RecentAccountBackend`.

    Uses ``SOLANA_RPC_URL`` as the latest-state ``getProgramAccounts``
    endpoint. Imported lazily so the unit-test path does not require the
    `solana-rpc` extra to be installed.
    """
    import os

    endpoint = os.environ.get("SOLANA_RPC_URL")
    if not endpoint:
        raise RuntimeError(
            "default_recent_backend() requires SOLANA_RPC_URL pointing at a "
            "latest-state getProgramAccounts endpoint. Export SOLANA_RPC_URL, "
            "pass `backend=`, or rely on the corpus path."
        )
    timeout = float(os.environ.get("SOLANA_RPC_TIMEOUT", "60"))
    return JsonRpcRecentAccountBackend(endpoint, timeout=timeout)


# Backwards-compatible alias kept until callers migrate to the recent-only name.
default_historical_backend = default_recent_backend


def _corpus_root_str() -> str:
    return str(corpus_root())


def get_program_accounts_at_slot(
    program_id: str,
    slot: int,
    *,
    backend: RecentAccountBackend | None = None,
    corpus_loader: CorpusLoader | None = None,
    discriminator: bytes | None = None,
) -> AccountSnapshot:
    """Pull program accounts for ``slot``. Falls back to corpus fixture if present.

    Resolution order (PRD line 172):

    1. **Corpus** — if a fixture is committed for ``(slot, program_id)``,
       return it. Offline / CI runs need no backend at all.
    2. **Latest-state RPC** — if ``slot == backend.get_latest_slot()``, fetch
       latest from the backend.
    3. **Reject** — any other slot (i.e. a past, uncommitted slot) raises
       ``RuntimeError``. Standard JSON-RPC cannot return as-of-slot account
       state, so faking it from latest would silently corrupt downstream
       calibration.

    The fetch path is memoized on
    ``(backend_id, corpus_root, program_id, slot, discriminator_hex)`` — all
    hashable scalars.
    """
    loader: CorpusLoader = corpus_loader or load_corpus_fixture
    fixture = loader(slot, kind="program_accounts", program_id=program_id)
    if fixture is not None:
        return _filter_snapshot_by_discriminator(
            AccountSnapshot.from_raw(fixture),
            discriminator,
        )
    backend = backend or default_recent_backend()
    _enforce_slot_is_latest(backend, slot, program_id)
    bid = _register_backend(backend)
    disc_hex = discriminator.hex() if discriminator is not None else ""
    return _get_program_accounts_cached(
        bid, _corpus_root_str(), program_id, slot, disc_hex
    )


_LATEST_SLOT_TOLERANCE = 256
"""Max slots the requested slot may trail the live tip while still counting as
"latest" for the recent-state path. ~100 s at 400 ms/slot — large enough to
absorb call latency and slot drift between the caller's ``getSlot`` and the
wrapper's enforcement check, small enough to reject genuinely historical
queries (which the PRD line 172 forbids fabricating from latest state)."""


def _enforce_slot_is_latest(
    backend: RecentAccountBackend, slot: int, program_id: str
) -> None:
    """Reject past uncommitted slots (PRD line 172).

    The check is "slot is within ``_LATEST_SLOT_TOLERANCE`` of the live tip",
    not strict equality: Solana advances a slot every ~400 ms, often faster
    than a single ``getProgramAccounts`` round-trip, so strict equality would
    fail under normal RPC latency. Slots far behind the tip (the only thing
    the PRD actually forbids — silently fabricating historical state) still
    raise.

    Forward slots (``slot > latest``) are also rejected: a request for a slot
    the chain has not yet produced cannot be the tip.

    Backends without a ``get_latest_slot`` method are accepted as-is so older
    test stubs and archive-style backends keep working — the wrapper can only
    enforce the rule when the backend tells it where the tip is.
    """
    get_latest = getattr(backend, "get_latest_slot", None)
    if not callable(get_latest):
        return
    latest = int(get_latest())
    if 0 <= latest - slot <= _LATEST_SLOT_TOLERANCE:
        return
    raise RuntimeError(
        f"get_program_accounts_at_slot({program_id!r}, slot={slot}) requested "
        f"a slot that is neither in the committed corpus nor within "
        f"{_LATEST_SLOT_TOLERANCE} slots of the current latest "
        f"(latest={latest}). Standard RPC cannot return as-of-slot account "
        f"state for past slots; commit a corpus fixture or query the current "
        f"tip."
    )


@functools.lru_cache(maxsize=64)
def _get_program_accounts_cached(
    backend_id: str,
    corpus_root: str,
    program_id: str,
    slot: int,
    discriminator_hex: str,
) -> AccountSnapshot:
    """Inner cached fetch keyed only on hashable scalars."""
    discriminator = (
        bytes.fromhex(discriminator_hex) if discriminator_hex else None
    )
    raw = _backend_for(backend_id).get_program_accounts_at_slot(
        program_id, slot, discriminator=discriminator
    )
    return AccountSnapshot.from_raw(raw)


def clear_program_accounts_cache() -> None:
    """Drop all cached ``get_program_accounts_at_slot`` results."""
    _get_program_accounts_cached.cache_clear()


def _filter_snapshot_by_discriminator(
    snapshot: AccountSnapshot,
    discriminator: bytes | None,
) -> AccountSnapshot:
    if discriminator is None:
        return snapshot
    matching = tuple(
        record
        for record in snapshot.accounts
        if record.account_data.startswith(discriminator)
    )
    return AccountSnapshot(
        program_id=snapshot.program_id,
        slot=snapshot.slot,
        accounts=matching,
        raw=snapshot.raw,
    )


class JsonRpcRecentAccountBackend:
    """Stdlib-only :class:`RecentAccountBackend` for ``SOLANA_RPC_URL``.

    Calls ``getSlot`` for :meth:`get_latest_slot` and standard
    ``getProgramAccounts`` (with ``minContextSlot`` as a freshness guard) for
    the actual fetch. The wrapper enforces ``slot == get_latest_slot()`` so
    this backend is only ever asked for the live tip.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 60.0,
        commitment: str = "confirmed",
        max_retries: int = 3,
        backoff_base_seconds: float = 0.25,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint must be non-empty")
        self.endpoint = endpoint
        self.timeout = timeout
        self.commitment = commitment
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))

    def get_latest_slot(self) -> int:
        result = self._call("getSlot", [{"commitment": self.commitment}])
        if not isinstance(result, int):
            raise RuntimeError(f"getSlot returned non-int: {result!r}")
        return result

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "encoding": "base64",
            "commitment": self.commitment,
            "minContextSlot": slot,
        }
        if discriminator is not None:
            import base64

            config["filters"] = [
                {
                    "memcmp": {
                        "offset": 0,
                        "bytes": base64.b64encode(discriminator).decode("ascii"),
                        "encoding": "base64",
                    }
                }
            ]
        accounts = self._call("getProgramAccounts", [program_id, config])
        if not isinstance(accounts, list):
            raise RuntimeError(
                f"getProgramAccounts returned non-list: {accounts!r}"
            )
        return {"program_id": program_id, "slot": slot, "accounts": accounts}

    def _call(self, method: str, params: list[Any]) -> Any:
        import json
        import time
        import urllib.error
        import urllib.request

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        body: dict[str, Any] | None = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if attempt < self.max_retries and exc.code in (
                    408, 425, 429, 500, 502, 503, 504,
                ):
                    if self.backoff_base_seconds > 0:
                        time.sleep(self.backoff_base_seconds * (2**attempt))
                    continue
                raise RuntimeError(
                    f"{method} RPC HTTP error: {exc.code}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    if self.backoff_base_seconds > 0:
                        time.sleep(self.backoff_base_seconds * (2**attempt))
                    continue
                raise RuntimeError(
                    f"{method} RPC transport error: {exc.reason}"
                ) from exc

            error = body.get("error") if body else None
            if error is not None:
                code = error.get("code") if isinstance(error, dict) else None
                if (
                    attempt < self.max_retries
                    and isinstance(code, int)
                    and code in (429, -32004, -32005)
                ):
                    if self.backoff_base_seconds > 0:
                        time.sleep(self.backoff_base_seconds * (2**attempt))
                    continue
                raise RuntimeError(f"{method} RPC error: {error!r}")
            break

        if body is None:
            raise RuntimeError(f"{method} RPC request failed without response")
        return body.get("result")
