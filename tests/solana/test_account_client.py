"""Unit tests for ``defi_sim_solana.replay.account_client`` (PRD US-001 lines 127, 232-233)."""

from __future__ import annotations

import base64
import inspect
from typing import Any

import pytest

from defi_sim_solana.replay import account_client as account_client_mod
from defi_sim_solana.replay.account_client import (
    AccountSnapshot,
    backend_id,
    clear_program_accounts_cache,
    get_program_accounts_at_slot,
)

PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


class _FakeBackend:
    """In-memory ``HistoricalAccountBackend`` stub for offline tests."""

    def __init__(self, endpoint: str = "fake://triton") -> None:
        self.endpoint = endpoint
        self.calls: list[tuple[str, int, bytes | None]] = []

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]:
        self.calls.append((program_id, slot, discriminator))
        data_blob = base64.b64encode(b"\xde\xad\xbe\xef").decode()
        return {
            "program_id": program_id,
            "slot": slot,
            "accounts": [
                {
                    "pubkey": "POOL1",
                    "account": {
                        "owner": program_id,
                        "lamports": 999_000,
                        "data": [data_blob, "base64"],
                    },
                    "slot": slot,
                }
            ],
        }


def test_get_program_accounts_at_slot_uses_corpus_when_present() -> None:
    """PRD line 232: corpus path returns a snapshot with NO backend configured."""
    fixture = {
        "program_id": PROGRAM_ID,
        "slot": 160_000_001,
        "accounts": [
            {
                "pubkey": "POOL_FROM_CORPUS",
                "account": {
                    "owner": PROGRAM_ID,
                    "lamports": 12345,
                    "data": [base64.b64encode(b"hi").decode(), "base64"],
                },
                "slot": 160_000_001,
            }
        ],
    }
    snap = get_program_accounts_at_slot(
        PROGRAM_ID,
        160_000_001,
        corpus_loader=lambda *_a, **_kw: fixture,
    )
    assert isinstance(snap, AccountSnapshot)
    assert snap.program_id == PROGRAM_ID
    assert snap.slot == 160_000_001
    assert len(snap.accounts) == 1
    record = snap.accounts[0]
    assert record.pubkey == "POOL_FROM_CORPUS"
    assert record.owner == PROGRAM_ID
    assert record.lamports == 12345
    assert record.account_data == b"hi"
    assert record.slot == 160_000_001


def test_get_program_accounts_at_slot_rejects_past_uncommitted_slot() -> None:
    """PRD line 240: a past slot that is neither committed nor the current
    latest must be rejected with a clear error rather than served from
    latest-state and labeled as historical."""

    class _LatestKnowingBackend:
        endpoint = "fake://recent"

        def get_latest_slot(self) -> int:
            return 200_000_000

        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            raise AssertionError(
                "wrapper must reject before calling backend on a past slot"
            )

    backend = _LatestKnowingBackend()
    with pytest.raises(RuntimeError, match="neither in the committed corpus"):
        get_program_accounts_at_slot(
            PROGRAM_ID,
            199_000_000,
            backend=backend,
            corpus_loader=lambda *_a, **_kw: None,
        )

    # And the wrapper still works when the requested slot equals latest.
    class _LatestServingBackend(_LatestKnowingBackend):
        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            return {"program_id": program_id, "slot": slot, "accounts": []}

    snap = get_program_accounts_at_slot(
        PROGRAM_ID,
        200_000_000,
        backend=_LatestServingBackend(),
        corpus_loader=lambda *_a, **_kw: None,
    )
    assert snap.slot == 200_000_000
    assert snap.accounts == ()


def test_get_program_accounts_at_slot_falls_back_to_backend() -> None:
    backend = _FakeBackend()
    snap = get_program_accounts_at_slot(
        PROGRAM_ID,
        500_000,
        backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )
    assert backend.calls == [(PROGRAM_ID, 500_000, None)]
    assert snap.program_id == PROGRAM_ID
    assert snap.slot == 500_000
    assert len(snap.accounts) == 1
    assert snap.accounts[0].account_data == b"\xde\xad\xbe\xef"


def test_lru_cache_avoids_second_backend_call() -> None:
    backend = _FakeBackend()
    a = get_program_accounts_at_slot(
        PROGRAM_ID, 7, backend=backend, corpus_loader=lambda *_a, **_kw: None
    )
    b = get_program_accounts_at_slot(
        PROGRAM_ID, 7, backend=backend, corpus_loader=lambda *_a, **_kw: None
    )
    assert a is b
    assert len(backend.calls) == 1


def test_discriminator_partitions_cache() -> None:
    """Different discriminators must hit the backend separately."""
    backend = _FakeBackend()
    get_program_accounts_at_slot(
        PROGRAM_ID,
        9,
        backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
        discriminator=b"\x01\x02",
    )
    get_program_accounts_at_slot(
        PROGRAM_ID,
        9,
        backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
        discriminator=b"\x03\x04",
    )
    assert len(backend.calls) == 2
    assert backend.calls[0][2] == b"\x01\x02"
    assert backend.calls[1][2] == b"\x03\x04"


def test_corpus_discriminator_no_match_returns_empty_snapshot() -> None:
    """A discriminator filter with no matches must not hydrate all accounts.

    Returning the original unfiltered fixture would let wrong account kinds
    enter fork state when a multi-filter hydrator asks for a kind that is
    absent in the committed corpus.
    """
    fixture = {
        "program_id": PROGRAM_ID,
        "slot": 160_000_001,
        "accounts": [
            {
                "pubkey": "POOL_KIND_A",
                "account": {
                    "owner": PROGRAM_ID,
                    "lamports": 1,
                    "data": [base64.b64encode(b"\xaa\xbbpayload").decode(), "base64"],
                },
                "slot": 160_000_001,
            }
        ],
    }

    snap = get_program_accounts_at_slot(
        PROGRAM_ID,
        160_000_001,
        corpus_loader=lambda *_a, **_kw: fixture,
        discriminator=b"\x01\x02",
    )

    assert snap.accounts == ()


def test_clear_program_accounts_cache_resets_lru() -> None:
    backend = _FakeBackend()
    get_program_accounts_at_slot(
        PROGRAM_ID, 11, backend=backend, corpus_loader=lambda *_a, **_kw: None
    )
    clear_program_accounts_cache()
    get_program_accounts_at_slot(
        PROGRAM_ID, 11, backend=backend, corpus_loader=lambda *_a, **_kw: None
    )
    assert len(backend.calls) == 2


def test_default_backend_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    with pytest.raises(RuntimeError, match="default_recent_backend"):
        get_program_accounts_at_slot(
            PROGRAM_ID, 1, corpus_loader=lambda *_a, **_kw: None
        )


def test_backend_id_prefers_endpoint() -> None:
    a = _FakeBackend(endpoint="https://archive-a.example")
    b = _FakeBackend(endpoint="https://archive-b.example")
    assert backend_id(a) == "https://archive-a.example"
    assert backend_id(a) != backend_id(b)


def test_two_backends_with_same_endpoint_share_cache_entry() -> None:
    a = _FakeBackend(endpoint="https://archive.example")
    b = _FakeBackend(endpoint="https://archive.example")
    get_program_accounts_at_slot(
        PROGRAM_ID, 33, backend=a, corpus_loader=lambda *_a, **_kw: None
    )
    get_program_accounts_at_slot(
        PROGRAM_ID, 33, backend=b, corpus_loader=lambda *_a, **_kw: None
    )
    # Only one of the two backends was actually invoked.
    assert (len(a.calls), len(b.calls)) in [(1, 0), (0, 1)]


def test_corpus_loader_receives_program_id_kwarg() -> None:
    """The fixture lookup must include ``program_id`` so the corpus layout
    ``program_accounts-<program_id>.json[.gz]`` is honored."""
    seen: list[tuple[int, str, str | None]] = []

    def _loader(slot: int, kind: str, program_id: str | None = None) -> None:
        seen.append((slot, kind, program_id))
        return None

    backend = _FakeBackend()
    get_program_accounts_at_slot(
        PROGRAM_ID, 161_000_000, backend=backend, corpus_loader=_loader
    )
    assert seen == [(161_000_000, "program_accounts", PROGRAM_ID)]


def test_account_snapshot_from_raw_handles_minimal_fixture() -> None:
    snap = AccountSnapshot.from_raw(
        {"program_id": "P", "slot": 1, "accounts": []}
    )
    assert snap.program_id == "P"
    assert snap.slot == 1
    assert snap.accounts == ()
