"""Unit tests for ``defi_sim_solana.replay.slot_client`` (PRD US-001 line 98, 214-215)."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from defi_sim_solana.replay import slot_client as slot_client_mod
from defi_sim_solana.replay.slot_client import (
    JsonRpcSolanaClient,
    SlotSnapshot,
    clear_slot_cache,
    default_client,
    get_slot,
    provider_id,
)


# Slot id of the placeholder block fixture committed under
# ``solana-plans/calibration/corpus/160000001/block.json.gz``.
COMMITTED_SLOT = 160_000_001


@pytest.fixture(autouse=True)
def _reset_slot_cache() -> None:
    clear_slot_cache()
    slot_client_mod._CLIENT_REGISTRY.clear()


class _FakeClient:
    """In-memory ``SolanaClient`` stub for offline tests."""

    def __init__(self, endpoint: str = "fake://rpc") -> None:
        self.endpoint = endpoint
        self.calls: list[int] = []

    def get_block(self, slot: int) -> dict[str, Any]:
        self.calls.append(slot)
        return {
            "slot": slot,
            "blockhash": f"hash-{slot}",
            "blockHeight": slot - 1,
            "transactions": [
                {
                    "transaction": {"signatures": [f"sig-{slot}"]},
                    "meta": {"computeUnitsConsumed": 1234, "err": None},
                }
            ],
            "rewards": [
                {"pubkey": "VALIDATOR1", "rewardType": "Fee", "lamports": 5000}
            ],
        }


def test_get_slot_uses_corpus_when_present_no_client_required() -> None:
    """PRD line 215: corpus path returns a snapshot with NO client configured."""
    snap = get_slot(COMMITTED_SLOT)
    assert isinstance(snap, SlotSnapshot)
    assert snap.slot == COMMITTED_SLOT


def test_get_slot_falls_back_to_client_when_no_fixture() -> None:
    """If the corpus loader returns ``None`` the client's get_block is called."""
    client = _FakeClient()
    snap = get_slot(999_111, client=client, corpus_loader=lambda *_a, **_kw: None)
    assert client.calls == [999_111]
    assert snap.slot == 999_111
    assert snap.blockhash == "hash-999111"
    assert snap.transactions and snap.transactions[0]["meta"]["computeUnitsConsumed"] == 1234
    assert snap.transaction_compute_units == (1234,)
    assert snap.leader == "VALIDATOR1"


def test_get_slot_lru_cached_on_repeat_call() -> None:
    """PRD line 214: a repeat call within one process returns the LRU-cached snapshot."""
    client = _FakeClient()
    a = get_slot(123, client=client, corpus_loader=lambda *_a, **_kw: None)
    b = get_slot(123, client=client, corpus_loader=lambda *_a, **_kw: None)
    assert a is b  # identity proves it came from the cache, not from a fresh hydrate
    assert client.calls == [123], "client.get_block must be called exactly once"


def test_clear_slot_cache_resets_lru() -> None:
    client = _FakeClient()
    get_slot(7, client=client, corpus_loader=lambda *_a, **_kw: None)
    clear_slot_cache()
    get_slot(7, client=client, corpus_loader=lambda *_a, **_kw: None)
    assert client.calls == [7, 7]


def test_corpus_loader_injectable() -> None:
    """An injected loader can hydrate from a synthetic fixture without touching disk."""
    payload = {"slot": 42, "blockhash": "synthetic", "transactions": []}
    snap = get_slot(42, corpus_loader=lambda *_a, **_kw: payload)
    assert snap.slot == 42
    assert snap.blockhash == "synthetic"


def test_get_slot_default_client_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a corpus fixture or injected client, the default factory fails loudly."""
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    with pytest.raises(RuntimeError, match="default_client"):
        get_slot(999_999_999_999, corpus_loader=lambda *_a, **_kw: None)


def test_default_client_uses_solana_rpc_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.example")
    client = default_client()
    assert provider_id(client) == "https://rpc.example"


def test_json_rpc_client_get_block_uses_solana_get_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":{"blockhash":"abc","transactions":[]}}'

    def _urlopen(req: Any, timeout: float) -> _Response:
        calls.append({"request": req, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(slot_client_mod.urllib.request, "urlopen", _urlopen)

    snap = JsonRpcSolanaClient("https://rpc.example", timeout=12).get_block(123)

    assert snap == {"blockhash": "abc", "transactions": [], "slot": 123}
    assert calls[0]["timeout"] == 12
    body = calls[0]["request"].data.decode("utf-8")
    assert '"method": "getBlock"' in body
    assert '"transactionDetails": "full"' in body


def test_json_rpc_client_retries_throttled_get_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":{"blockhash":"retry-ok","transactions":[]}}'

    def _urlopen(req: Any, timeout: float) -> _Response:
        calls.append({"request": req, "timeout": timeout})
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                url="https://rpc.example",
                code=429,
                msg="too many requests",
                hdrs={},
                fp=None,
            )
        return _Response()

    monkeypatch.setattr(slot_client_mod.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(slot_client_mod.time, "sleep", sleeps.append)

    snap = JsonRpcSolanaClient(
        "https://rpc.example",
        timeout=12,
        max_retries=2,
        backoff_base_seconds=0.01,
    ).get_block(123)

    assert snap["blockhash"] == "retry-ok"
    assert len(calls) == 2
    assert sleeps == [0.01]


def test_json_rpc_client_retries_rpc_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                return b'{"jsonrpc":"2.0","id":1,"error":{"code":429,"message":"rate limit"}}'
            return b'{"jsonrpc":"2.0","id":1,"result":{"blockhash":"rpc-retry-ok","transactions":[]}}'

    monkeypatch.setattr(
        slot_client_mod.urllib.request,
        "urlopen",
        lambda _req, timeout: _Response(),
    )
    monkeypatch.setattr(slot_client_mod.time, "sleep", sleeps.append)

    snap = JsonRpcSolanaClient(
        "https://rpc.example",
        max_retries=1,
        backoff_base_seconds=0.02,
    ).get_block(456)

    assert snap["blockhash"] == "rpc-retry-ok"
    assert calls == 2
    assert sleeps == [0.02]


def test_provider_id_prefers_endpoint_over_class_name() -> None:
    a = _FakeClient(endpoint="https://rpc-a.example")
    b = _FakeClient(endpoint="https://rpc-b.example")
    assert provider_id(a) == "https://rpc-a.example"
    assert provider_id(a) != provider_id(b)


def test_provider_id_falls_back_to_class_name_without_endpoint() -> None:
    class _NoEndpoint:
        def get_block(self, slot: int) -> dict[str, Any]:
            return {"slot": slot}

    pid = provider_id(_NoEndpoint())  # type: ignore[arg-type]
    assert pid.endswith("_NoEndpoint")


def test_two_clients_with_same_endpoint_share_cache_entry() -> None:
    """Stable provider_id keying means two equivalent clients reuse the LRU slot."""
    a = _FakeClient(endpoint="https://rpc.example")
    b = _FakeClient(endpoint="https://rpc.example")
    get_slot(55, client=a, corpus_loader=lambda *_a, **_kw: None)
    get_slot(55, client=b, corpus_loader=lambda *_a, **_kw: None)
    # Only one of the two clients was actually invoked (the second hit the cache).
    assert (a.calls, b.calls) in [([55], []), ([], [55])]


def test_slot_snapshot_from_raw_handles_minimal_fixture() -> None:
    snap = SlotSnapshot.from_raw({"slot": 1, "transactions": []})
    assert snap.slot == 1
    assert snap.transactions == ()
    assert snap.transaction_compute_units == ()
    assert snap.leader is None
