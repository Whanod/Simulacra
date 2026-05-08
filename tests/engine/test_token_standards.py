"""Token-standard behaviour tests for US-007 (PRD lines 588-593).

This module pins the discriminator field added in PRD line 539
(``TokenSpec.standard``) and the LST/transfer-hook surfaces added in
follow-up tasks. Each test is a single PRD checkbox; do not bundle
unrelated assertions here.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np

from defi_sim.core.types import SwapAction
from defi_sim.engine.gas import ComputeUnitCost
from defi_sim.engine.lst import advance_lst_rate
from defi_sim.engine.specs import ExchangeRateDriftSpec, TokenSpec, TransferHookSpec


def test_token_default_standard_is_spl() -> None:
    """PRD line 589: ``TokenSpec().standard`` must default to ``"spl"``.

    Locks the regression test for the US-007 discriminator: legacy
    artifacts that omit the ``standard`` key (and ad-hoc constructions
    that pass only ``id``/``symbol``) must continue to deserialize as
    plain SPL tokens, not Token-2022 or native-lamports tokens.
    """

    spec = TokenSpec(id="X", symbol="X")
    assert spec.standard == "spl"

    legacy = TokenSpec.from_dict({"id": "X", "symbol": "X"})
    assert legacy.standard == "spl"


def test_native_token_uses_lamports_unit() -> None:
    """PRD line 590: a ``standard="native"`` token's base unit is one lamport.

    Solana's native SOL is denominated in lamports — 1 SOL = 10**9
    lamports. The simulator encodes this as ``decimals=9`` together with
    ``standard="native"``: the minor base unit (``10**-decimals`` SOL)
    is exactly one lamport. This test pins that convention so a future
    refactor can't ship a "native" token at any other decimals without
    a deliberate decision.
    """

    sol = TokenSpec(id="SOL", symbol="SOL", decimals=9, standard="native")
    assert sol.standard == "native"
    assert sol.decimals == 9
    assert Decimal(10) ** -sol.decimals == Decimal("1e-9")

    payload = {"id": "SOL", "symbol": "SOL", "decimals": 9, "standard": "native"}
    round_tripped = TokenSpec.from_dict(payload)
    assert round_tripped.standard == "native"
    assert round_tripped.decimals == 9


def test_lst_exchange_rate_advances_per_epoch() -> None:
    """PRD line 592: ``advance_lst_rate`` advances the rate by the configured
    drift on each call (one call per epoch boundary).

    Pins the per-epoch advancement invariant: with ``drift_per_epoch=0.001``
    and zero volatility, three successive calls must compound to
    ``(1 + 0.001) ** 3``. This is the test-file-mandated entry for the
    behaviour exercised more broadly in ``tests/engine/test_lst.py``;
    keep this assertion narrow so the PRD checkbox maps cleanly to one
    invariant.
    """

    token = TokenSpec(
        id="mSOL",
        symbol="mSOL",
        decimals=9,
        standard="spl",
        exchange_rate_to_sol=Decimal("1.0"),
        exchange_rate_drift=ExchangeRateDriftSpec(
            drift_per_epoch=0.001,
            volatility_per_epoch=0.0,
            seed=0,
        ),
    )
    rng = np.random.default_rng(0)

    advance_lst_rate(token, epoch=1, rng=rng)
    assert token.exchange_rate_to_sol == Decimal(str(1.0 + 0.001))

    advance_lst_rate(token, epoch=2, rng=rng)
    advance_lst_rate(token, epoch=3, rng=rng)
    assert token.exchange_rate_to_sol == Decimal(str(1.0 + 0.001)) ** 3


def test_lst_exchange_rate_compounds_to_validation_target_over_365_epochs() -> None:
    """PRD line 585 (validation): an LST with ``drift_per_epoch=0.0001`` run for
    365 epochs has ``exchange_rate_to_sol ≈ 1.037`` (compounded).

    Pins the PRD-cited number directly: ``(1 + 0.0001) ** 365 ≈ 1.0372``.
    The deeper compounding behaviour is exercised in
    ``tests/engine/test_lst.py::test_advance_lst_rate_compounds_over_many_epochs``;
    this entry exists so the PRD-mandated test file carries a narrow
    assertion against the validation row's exact target value.
    """

    token = TokenSpec(
        id="mSOL",
        symbol="mSOL",
        decimals=9,
        standard="spl",
        exchange_rate_to_sol=Decimal("1.0"),
        exchange_rate_drift=ExchangeRateDriftSpec(
            drift_per_epoch=0.0001,
            volatility_per_epoch=0.0,
            seed=0,
        ),
    )
    rng = np.random.default_rng(0)
    for epoch in range(1, 366):
        advance_lst_rate(token, epoch=epoch, rng=rng)

    final = float(token.exchange_rate_to_sol)
    assert abs(final - 1.037) < 5e-4


def test_lst_volatility_within_tolerance() -> None:
    """PRD line 593: with ``volatility_per_epoch=0.001`` and zero drift, the
    per-epoch rate increments' standard deviation must be within tolerance
    of ``0.001``.

    ``advance_lst_rate`` applies ``factor = 1 + drift + noise`` where
    ``noise ~ Normal(0, sigma)``. With ``drift=0`` and starting rate ``1.0``,
    the one-step delta ``new_rate - 1.0`` is exactly the noise, so its
    sample stdev across many independent draws must converge to ``sigma``.
    Pins the noise injection contract for the LST volatility surface
    exercised more broadly in ``tests/engine/test_lst.py``.
    """

    sigma = 0.001
    n_trials = 5_000
    rng = np.random.default_rng(42)
    deltas: list[float] = []
    for _ in range(n_trials):
        token = TokenSpec(
            id="mSOL",
            symbol="mSOL",
            decimals=9,
            standard="spl",
            exchange_rate_to_sol=Decimal("1.0"),
            exchange_rate_drift=ExchangeRateDriftSpec(
                drift_per_epoch=0.0,
                volatility_per_epoch=sigma,
                seed=0,
            ),
        )
        _, delta = advance_lst_rate(token, epoch=1, rng=rng)
        deltas.append(float(delta))

    sample_std = float(np.std(deltas, ddof=1))
    assert abs(sample_std - sigma) < 0.05 * sigma


def test_spl_2022_with_transfer_hook_charges_extra_cu() -> None:
    """PRD line 591 (and validation line 584): a token with ``standard="spl_2022"``
    and a ``TransferHookSpec`` makes the engine charge the configured extra
    CU + lamports per transfer through the transaction cost path.

    With ``compute_unit_price_micro_lamports = 1_000_000`` (1 lamport per CU),
    a 50_000-CU hook overhead contributes 50_000 lamports of priority fee, and
    a 1_000-lamport flat surcharge adds 1_000 lamports on top. The cost-model
    delta vs. an identical action whose token has no hook must equal the sum
    of those two surcharges, with no other line items shifting.
    """

    base = TokenSpec(id="USDC", symbol="USDC", decimals=6, standard="spl")
    hooked = TokenSpec(
        id="HOOK",
        symbol="HOOK",
        decimals=6,
        standard="spl_2022",
        transfer_hook=TransferHookSpec(
            program_id="HookProgram111111111111111111111111111111111",
            additional_cu_per_transfer=50_000,
            additional_lamports_per_transfer=1_000,
        ),
    )
    plain = TokenSpec(id="PLAIN", symbol="PLAIN", decimals=6, standard="spl_2022")

    action_baseline = SwapAction(
        agent_id="a",
        token_in="USDC",
        token_out="PLAIN",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    action_hooked = SwapAction(
        agent_id="a",
        token_in="USDC",
        token_out="HOOK",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )

    tokens = {tok.id: tok for tok in (base, hooked, plain)}
    model = ComputeUnitCost(tokens=tokens)

    fb_baseline = model.breakdown(action_baseline, 0)
    fb_hooked = model.breakdown(action_hooked, 0)

    assert fb_hooked.total_lamports - fb_baseline.total_lamports == 50_000 + 1_000
    assert fb_hooked.priority_fee_lamports == fb_baseline.priority_fee_lamports + 51_000
    assert fb_hooked.base_fee_lamports == fb_baseline.base_fee_lamports

    no_token_model = ComputeUnitCost()
    assert no_token_model.breakdown(action_hooked, 0).total_lamports == fb_baseline.total_lamports
