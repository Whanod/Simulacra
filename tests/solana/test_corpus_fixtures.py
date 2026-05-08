"""Unit tests for ``defi_sim_solana.replay.corpus`` (PRD US-001 line 222-228)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from defi_sim_solana.replay import corpus as corpus_mod
from defi_sim_solana.replay.corpus import corpus_root, load_corpus_fixture


# Slot id of the placeholder block fixture committed under
# ``solana-plans/calibration/corpus/160_000_001/block.json.gz``.
COMMITTED_SLOT = 160_000_001


def test_corpus_root_resolves_under_solana_plans() -> None:
    root = corpus_root()
    assert root.parts[-3:] == ("solana-plans", "calibration", "corpus")
    assert root.is_dir(), f"corpus root does not exist: {root}"


def test_load_corpus_fixture_returns_none_when_missing() -> None:
    assert load_corpus_fixture(slot=999_999_999_999, kind="block") is None
    assert (
        load_corpus_fixture(
            slot=999_999_999_999,
            kind="program_accounts",
            program_id="whirLb",
        )
        is None
    )


def test_load_corpus_fixture_returns_dict_for_committed_slot() -> None:
    fixture = load_corpus_fixture(slot=COMMITTED_SLOT, kind="block")
    assert isinstance(fixture, dict)
    assert fixture.get("slot") == COMMITTED_SLOT


def test_load_corpus_fixture_program_id_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot_dir = tmp_path / "12345"
    slot_dir.mkdir()
    payload = {"accounts": [{"pubkey": "abc", "lamports": 1}]}
    (slot_dir / "program_accounts-PROG1.json").write_text(json.dumps(payload))

    monkeypatch.setattr(corpus_mod, "corpus_root", lambda: tmp_path)

    assert load_corpus_fixture(slot=12345, kind="program_accounts", program_id="PROG1") == payload
    assert load_corpus_fixture(slot=12345, kind="program_accounts", program_id="PROG2") is None
    # ``kind`` alone (no program_id) must not match the program-scoped file.
    assert load_corpus_fixture(slot=12345, kind="program_accounts") is None


def test_load_corpus_fixture_prefers_gz_over_plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot_dir = tmp_path / "7"
    slot_dir.mkdir()
    plain_payload = {"src": "plain"}
    gz_payload = {"src": "gz"}
    (slot_dir / "block.json").write_text(json.dumps(plain_payload))
    with gzip.open(slot_dir / "block.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(gz_payload, fh)

    monkeypatch.setattr(corpus_mod, "corpus_root", lambda: tmp_path)
    assert load_corpus_fixture(slot=7, kind="block") == gz_payload


def test_load_corpus_fixture_reads_plain_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot_dir = tmp_path / "8"
    slot_dir.mkdir()
    payload = {"only": "plain"}
    (slot_dir / "block.json").write_text(json.dumps(payload))

    monkeypatch.setattr(corpus_mod, "corpus_root", lambda: tmp_path)
    assert load_corpus_fixture(slot=8, kind="block") == payload
