"""LST exchange-rate advancement tests (US-007, PRD line 571)."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from defi_sim.core.clock import SolanaSlotClock
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.events import EventBus, EventType
from defi_sim.engine.lst import advance_lst_rate
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.specs import ExchangeRateDriftSpec, TokenSpec
from defi_sim.core.types import Token
from defi_sim.markets.cfamm import CfammMarket


def _make_lst(
    drift: float = 0.0001,
    volatility: float = 0.0,
    rate: str = "1.0",
) -> TokenSpec:
    return TokenSpec(
        id="mSOL",
        symbol="mSOL",
        decimals=9,
        standard="spl",
        exchange_rate_to_sol=Decimal(rate),
        exchange_rate_drift=ExchangeRateDriftSpec(
            drift_per_epoch=drift,
            volatility_per_epoch=volatility,
            seed=42,
        ),
    )


def test_advance_lst_rate_deterministic_drift_no_noise() -> None:
    token = _make_lst(drift=0.001, volatility=0.0, rate="1.0")
    rng = np.random.default_rng(0)
    new_rate, delta = advance_lst_rate(token, epoch=1, rng=rng)
    assert token.exchange_rate_to_sol == Decimal(str(1.0 + 0.001))
    assert new_rate == token.exchange_rate_to_sol
    assert delta == new_rate - Decimal("1.0")


def test_advance_lst_rate_compounds_over_many_epochs() -> None:
    token = _make_lst(drift=0.0001, volatility=0.0, rate="1.0")
    rng = np.random.default_rng(0)
    for epoch in range(1, 366):
        advance_lst_rate(token, epoch=epoch, rng=rng)
    final = float(token.exchange_rate_to_sol)
    expected = (1 + 0.0001) ** 365
    assert abs(final - expected) < 1e-6


def test_advance_lst_rate_with_noise_uses_rng() -> None:
    token = _make_lst(drift=0.0, volatility=0.01, rate="1.0")
    rng = np.random.default_rng(123)
    advance_lst_rate(token, epoch=1, rng=rng)
    assert token.exchange_rate_to_sol != Decimal("1.0")


def test_advance_lst_rate_raises_when_drift_missing() -> None:
    token = TokenSpec(
        id="x",
        symbol="x",
        exchange_rate_to_sol=Decimal("1.0"),
    )
    with pytest.raises(ValueError, match="exchange_rate_drift"):
        advance_lst_rate(token, epoch=1, rng=np.random.default_rng(0))


def test_advance_lst_rate_raises_when_rate_missing() -> None:
    token = TokenSpec(
        id="x",
        symbol="x",
        exchange_rate_drift=ExchangeRateDriftSpec(),
    )
    with pytest.raises(ValueError, match="exchange_rate_to_sol"):
        advance_lst_rate(token, epoch=1, rng=np.random.default_rng(0))


def test_engine_emits_lst_rate_updated_on_epoch_boundary() -> None:
    """Run a few rounds across an epoch boundary; the engine should emit
    LST_RATE_UPDATED for every registered LST and mutate rates in place."""

    token = _make_lst(drift=0.01, volatility=0.0, rate="1.0")
    clock = SolanaSlotClock(
        slot_duration_seconds=0.4,
        epoch_length_slots=2,
        skip_rate=0.0,
        seed=0,
    )
    market = CfammMarket(
        tokens=[Token(id="SOL", symbol="SOL", decimals=9), Token(id="USDC", symbol="USDC", decimals=6)],
        initial_liquidity=1_000,
    )
    bus = EventBus(record_history=True)
    config = SimulationConfig(
        num_rounds=6,
        clock=clock,
        lst_tokens=[token],
        seed=7,
    )
    SimulationEngine(market, [], config, event_bus=bus).run()

    boundaries = [e for e in bus.history if e.type == EventType.EPOCH_BOUNDARY]
    rate_events = [e for e in bus.history if e.type == EventType.LST_RATE_UPDATED]

    assert len(boundaries) >= 1
    assert len(rate_events) == len(boundaries)
    for evt in rate_events:
        assert evt.data["token_id"] == "mSOL"
        assert "epoch" in evt.data
        assert "new_rate" in evt.data
        assert "delta" in evt.data

    expected = Decimal(str(1.0 + 0.01)) ** len(boundaries)
    assert token.exchange_rate_to_sol == expected


def test_token_spec_from_dict_coerces_drift_and_hook_dicts() -> None:
    """TokenSpec.from_dict must materialize nested dicts into spec instances
    so consumers (advance_lst_rate, transfer-hook overhead) can access typed
    fields without crashing on AttributeError."""

    payload = {
        "id": "mSOL",
        "symbol": "mSOL",
        "decimals": 9,
        "exchange_rate_to_sol": "1.05",
        "exchange_rate_drift": {
            "drift_per_epoch": 0.0002,
            "volatility_per_epoch": 0.001,
            "seed": 11,
        },
        "transfer_hook": {
            "program_id": "Hook111",
            "additional_cu_per_transfer": 2000,
            "additional_lamports_per_transfer": 50,
        },
    }
    spec = TokenSpec.from_dict(payload)
    assert isinstance(spec.exchange_rate_drift, ExchangeRateDriftSpec)
    assert spec.exchange_rate_drift.drift_per_epoch == 0.0002
    assert spec.exchange_rate_drift.volatility_per_epoch == 0.001
    assert spec.exchange_rate_drift.seed == 11
    assert spec.transfer_hook is not None
    assert spec.transfer_hook.program_id == "Hook111"
    assert spec.transfer_hook.additional_cu_per_transfer == 2000
    assert spec.transfer_hook.additional_lamports_per_transfer == 50
