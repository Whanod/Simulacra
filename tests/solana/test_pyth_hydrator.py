"""Pyth PriceUpdateV2 fixture parser tests for FIX-011."""

from __future__ import annotations

import json

import pytest

from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.initial_state import InitialState
from defi_sim.engine.state_hydrator import StateHydrator
from defi_sim_solana.program_ids import PYTH_SOLANA_RECEIVER_PROGRAM
from defi_sim_solana.replay.account_client import (
    clear_program_accounts_cache,
    get_program_accounts_at_slot,
)
from defi_sim_solana.replay.pyth_hydrator import (
    PYTH_PRICE_UPDATE_V2_DISCRIMINATOR,
    PythPriceUpdateHydrator,
)

CORPUS_SLOT = 163_000_001
REFERENCE_TIME = 1_700_000_030
MAXIMUM_AGE_SECONDS = 60
MAX_CONFIDENCE_BPS = 100


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_program_accounts_cache()


def _hydrator() -> PythPriceUpdateHydrator:
    return PythPriceUpdateHydrator(
        reference_unix_timestamp=REFERENCE_TIME,
        maximum_age_seconds=MAXIMUM_AGE_SECONDS,
        max_confidence_bps=MAX_CONFIDENCE_BPS,
    )


def _parsed_by_pubkey() -> dict[str, dict[str, object]]:
    snapshot = get_program_accounts_at_slot(
        PYTH_SOLANA_RECEIVER_PROGRAM,
        CORPUS_SLOT,
    )
    hydrator = _hydrator()
    return {
        record.pubkey: hydrator.parse_account(
            record.pubkey,
            record.account_data,
        ).payload
        for record in snapshot.accounts
    }


def test_pyth_hydrator_program_id_schema_and_filter() -> None:
    assert PythPriceUpdateHydrator.program_id == PYTH_SOLANA_RECEIVER_PROGRAM
    assert isinstance(PythPriceUpdateHydrator.schema_version, int)
    assert PythPriceUpdateHydrator.schema_version >= 1
    assert issubclass(PythPriceUpdateHydrator, StateHydrator)

    filters = PythPriceUpdateHydrator().account_filters()
    assert len(filters) == 1
    assert filters[0].discriminator == PYTH_PRICE_UPDATE_V2_DISCRIMINATOR
    assert PYTH_PRICE_UPDATE_V2_DISCRIMINATOR.hex() == "22f123639d7ef4cd"


def test_pyth_fixture_accounts_parse_to_oracle_price_fragments() -> None:
    snapshot = get_program_accounts_at_slot(
        PYTH_SOLANA_RECEIVER_PROGRAM,
        CORPUS_SLOT,
    )
    assert snapshot.program_id == PYTH_SOLANA_RECEIVER_PROGRAM
    assert snapshot.slot == CORPUS_SLOT
    assert len(snapshot.accounts) == 4

    hydrator = _hydrator()
    fragment = hydrator.parse_account(
        snapshot.accounts[0].pubkey,
        snapshot.accounts[0].account_data,
    )

    assert fragment.kind == "oracle_price"
    assert fragment.protocol_model == "PythPull"
    assert fragment.owner is None
    assert fragment.pubkey == "PythSolUsdValid111111111111111111111111111111"
    assert fragment.payload["feed_id"] == (
        "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"
    )
    assert fragment.payload["price"] == 150_250_000_000
    assert fragment.payload["confidence"] == 12_000_000
    assert fragment.payload["exponent"] == -8
    assert fragment.payload["publish_time"] == 1_700_000_000
    assert fragment.payload["status"] == "valid"
    assert fragment.payload["is_valid"] is True
    assert fragment.payload["is_stale"] is False

    state = InitialState(slot=CORPUS_SLOT)
    state.merge(fragment)
    assert InitialState.from_json(state.to_json()).fragments[0].payload == (
        fragment.payload
    )


def test_pyth_fixture_surfaces_stale_invalid_and_confidence_heavy_states() -> None:
    parsed = _parsed_by_pubkey()

    stale = parsed["PythSolUsdStale111111111111111111111111111111"]
    assert stale["status"] == "stale"
    assert stale["is_stale"] is True
    assert stale["is_valid"] is True
    assert "stale" in stale["warnings"]

    wide = parsed["PythBtcUsdWideConf111111111111111111111111111"]
    assert wide["status"] == "confidence_heavy"
    assert wide["is_confidence_heavy"] is True
    assert wide["confidence_bps"] > MAX_CONFIDENCE_BPS
    assert "confidence_heavy" in wide["warnings"]

    invalid = parsed["PythSolUsdUnverified1111111111111111111111111"]
    assert invalid["status"] == "invalid"
    assert invalid["is_valid"] is False
    assert invalid["num_signatures"] == 0
    assert "insufficient_verification" in invalid["warnings"]


def test_pyth_hydrator_rejects_short_or_wrong_discriminator_payload() -> None:
    hydrator = PythPriceUpdateHydrator()

    with pytest.raises(ValueError, match="need at least"):
        hydrator.parse_price_update("ShortPyth", b"\x00" * 12)

    snapshot = get_program_accounts_at_slot(
        PYTH_SOLANA_RECEIVER_PROGRAM,
        CORPUS_SLOT,
    )
    bad = b"\x00" * 8 + snapshot.accounts[0].account_data[8:]
    with pytest.raises(ValueError, match="discriminator"):
        hydrator.parse_price_update("WrongDiscPyth", bad)


def test_pyth_fixture_fragments_are_json_safe() -> None:
    snapshot = get_program_accounts_at_slot(
        PYTH_SOLANA_RECEIVER_PROGRAM,
        CORPUS_SLOT,
    )
    hydrator = _hydrator()
    fragments = [
        hydrator.parse_account(record.pubkey, record.account_data)
        for record in snapshot.accounts
    ]
    state = InitialState(slot=CORPUS_SLOT)
    state.merge(fragments)

    payload = state.to_json()
    assert json.loads(payload)["fragments"][0]["kind"] == "oracle_price"


def test_production_oracle_hydration_still_fails_closed_without_state_source() -> None:
    class _OracleHydrator(StateHydrator):
        program_id = "FakeOracleConsumer111111111111111111111111111"
        schema_version = 1

        def account_filters(self):
            return []

        def parse_account(self, pubkey, data):  # type: ignore[override]
            raise AssertionError("not relevant")

        def oracle_dependencies(self):
            return ["PythSolUsdValid111111111111111111111111111111"]

    class _OracleConsumerMarket(ForkableMarket):
        state_hydrator = _OracleHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    class _Backend:
        endpoint = "fake://oracle-consumer"

        def get_program_accounts_at_slot(self, program_id, slot, *, discriminator=None):
            return {"program_id": program_id, "slot": slot, "accounts": []}

    loader = ForkLoader(
        ProtocolModelRegistry({"oracle_consumer": _OracleConsumerMarket}),
        historical_backend=_Backend(),
        corpus_loader=lambda *_a, **_kw: None,
    )

    with pytest.raises(NotImplementedError, match="exact as-of-slot"):
        loader.load(
            ForkSpec(
                slot=CORPUS_SLOT,
                protocols=[
                    ProtocolForkRequest(protocol_model="oracle_consumer")
                ],
            )
        )
