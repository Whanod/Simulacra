"""Unit tests for ``tools.cache_corpus_slot`` (PRD US-001 line 247)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from defi_sim_solana.replay.corpus import load_corpus_fixture
from defi_sim_solana.replay import corpus as corpus_mod
from tools.cache_corpus_slot import cache_slot_corpus


WHIRLPOOL_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
DLMM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"


class _FakeSlotClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[int] = []
        self.endpoint = "fake://slot-rpc"

    def get_block(self, slot: int) -> dict[str, Any]:
        self.calls.append(slot)
        return self._payload


class _FakeAccountBackend:
    def __init__(self, by_program: dict[str, dict[str, Any]]) -> None:
        self._by_program = by_program
        self.calls: list[tuple[str, int]] = []
        self.endpoint = "fake://archive"

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]:
        self.calls.append((program_id, slot))
        return self._by_program[program_id]


def test_cache_corpus_slot_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD line 218 + 247: tool writes fixtures that load_corpus_fixture reads back identically."""
    slot = 200_000_555
    block_payload: dict[str, Any] = {
        "slot": slot,
        "blockhash": "BHASH",
        "previousBlockhash": "PREV",
        "parentSlot": slot - 1,
        "blockHeight": slot - 1,
        "blockTime": 1_700_000_000,
        "transactions": [
            {
                "transaction": {"signatures": ["sigA"]},
                "meta": {"computeUnitsConsumed": 4321, "err": None},
            }
        ],
        "rewards": [{"pubkey": "VAL", "rewardType": "Fee", "lamports": 50}],
    }
    whirlpool_payload: dict[str, Any] = {
        "program_id": WHIRLPOOL_ID,
        "slot": slot,
        "accounts": [
            {
                "pubkey": "POOL_SOL_USDC",
                "account": {
                    "owner": WHIRLPOOL_ID,
                    "lamports": 100_000_000,
                    "data": ["AAAA", "base64"],
                },
                "slot": slot,
            }
        ],
    }
    dlmm_payload: dict[str, Any] = {
        "program_id": DLMM_ID,
        "slot": slot,
        "accounts": [],
    }

    slot_client = _FakeSlotClient(block_payload)
    account_backend = _FakeAccountBackend(
        {WHIRLPOOL_ID: whirlpool_payload, DLMM_ID: dlmm_payload}
    )

    written = cache_slot_corpus(
        slot=slot,
        programs=[WHIRLPOOL_ID, DLMM_ID],
        out_dir=tmp_path,
        slot_client=slot_client,
        account_backend=account_backend,
    )

    slot_dir = tmp_path / str(slot)
    assert (slot_dir / "block.json.gz").is_file()
    assert (slot_dir / f"program_accounts-{WHIRLPOOL_ID}.json.gz").is_file()
    assert (slot_dir / f"program_accounts-{DLMM_ID}.json.gz").is_file()
    assert (slot_dir / "manifest.yaml").is_file()
    assert (slot_dir / "checksums.txt").is_file()
    assert written["block"] == slot_dir / "block.json.gz"
    assert written["manifest"] == slot_dir / "manifest.yaml"
    assert written["checksums"] == slot_dir / "checksums.txt"

    # Round-trip via load_corpus_fixture pointed at the test corpus root.
    monkeypatch.setattr(corpus_mod, "corpus_root", lambda: tmp_path)
    assert load_corpus_fixture(slot=slot, kind="block") == block_payload
    assert (
        load_corpus_fixture(slot=slot, kind="program_accounts", program_id=WHIRLPOOL_ID)
        == whirlpool_payload
    )
    assert (
        load_corpus_fixture(slot=slot, kind="program_accounts", program_id=DLMM_ID)
        == dlmm_payload
    )

    # Exactly one upstream call per source.
    assert slot_client.calls == [slot]
    assert sorted(account_backend.calls) == sorted(
        [(WHIRLPOOL_ID, slot), (DLMM_ID, slot)]
    )

    # Manifest reflects the slot header and the program list.
    manifest_text = (slot_dir / "manifest.yaml").read_text(encoding="utf-8")
    assert f"slot: {slot}" in manifest_text
    assert WHIRLPOOL_ID in manifest_text
    assert DLMM_ID in manifest_text
    assert "tx_count: 1" in manifest_text

    # Checksums file lists every committed payload once.
    checksum_text = (slot_dir / "checksums.txt").read_text(encoding="utf-8")
    for name in (
        "block.json.gz",
        f"program_accounts-{WHIRLPOOL_ID}.json.gz",
        f"program_accounts-{DLMM_ID}.json.gz",
        "manifest.yaml",
    ):
        assert name in checksum_text


def test_cache_corpus_slot_no_programs_skips_account_backend(tmp_path: Path) -> None:
    """A bare slot pull must work without an account backend wired."""
    slot = 1_234
    payload = {"slot": slot, "blockhash": "X", "transactions": []}
    slot_client = _FakeSlotClient(payload)

    written = cache_slot_corpus(
        slot=slot,
        programs=[],
        out_dir=tmp_path,
        slot_client=slot_client,
        account_backend=None,
    )
    assert "block" in written
    assert not any(k.startswith("program_accounts-") for k in written)


def test_cache_corpus_slot_records_checksum_addressed_raw_artifact_uri(
    tmp_path: Path,
) -> None:
    slot = 2_000
    block_payload = {"slot": slot, "blockhash": "raw", "transactions": []}
    program_payload = {"program_id": WHIRLPOOL_ID, "slot": slot, "accounts": []}
    artifact_root = tmp_path / "artifact-storage"

    written = cache_slot_corpus(
        slot=slot,
        programs=[WHIRLPOOL_ID],
        out_dir=tmp_path / "corpus",
        slot_client=_FakeSlotClient(block_payload),
        account_backend=_FakeAccountBackend({WHIRLPOOL_ID: program_payload}),
        artifact_storage_uri=artifact_root.as_uri(),
    )

    checksum_text = written["checksums"].read_text(encoding="utf-8")
    assert "block.raw.json.gz" in checksum_text
    assert f"program_accounts-{WHIRLPOOL_ID}.raw.json.gz" in checksum_text
    assert "file://" in checksum_text
    raw_files = sorted((artifact_root / "solana-corpus-raw" / str(slot)).glob("*.json.gz"))
    assert len(raw_files) == 2
    assert all(path.stem.split("-")[-1] for path in raw_files)


def test_cache_corpus_slot_rejects_unsupported_artifact_storage_scheme(
    tmp_path: Path,
) -> None:
    slot_client = _FakeSlotClient({"slot": 1, "transactions": []})
    with pytest.raises(ValueError, match="unsupported artifact storage URI"):
        cache_slot_corpus(
            slot=1,
            programs=[],
            out_dir=tmp_path,
            slot_client=slot_client,
            artifact_storage_uri="s3://bucket/corpus",
        )


def test_cache_corpus_slot_rejects_programs_without_backend(tmp_path: Path) -> None:
    """Programs requested but no backend wired -> hard error, no partial write."""
    slot_client = _FakeSlotClient({"slot": 1, "transactions": []})
    with pytest.raises(ValueError, match="account_backend"):
        cache_slot_corpus(
            slot=1,
            programs=[WHIRLPOOL_ID],
            out_dir=tmp_path,
            slot_client=slot_client,
            account_backend=None,
        )
