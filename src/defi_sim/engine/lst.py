"""LST exchange-rate advancement (US-007, PRD line 571).

A liquid-staking token's redemption rate against SOL drifts each epoch as the
underlying validators accrue staking rewards (and occasionally lose to
slashing). ``advance_lst_rate`` mutates an LST ``TokenSpec`` in place using
its ``ExchangeRateDriftSpec``: a deterministic baseline drift plus an
optional per-epoch Gaussian noise term.

The engine calls this from the epoch-boundary hook in
``SimulationEngine._execute_round`` and emits an ``LST_RATE_UPDATED`` event
with ``{epoch, token_id, new_rate, delta}`` for downstream consumers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from defi_sim.engine.specs import TokenSpec


def advance_lst_rate(
    lst_token: TokenSpec,
    epoch: int,
    rng: Any,
) -> tuple[Decimal, Decimal]:
    """Advance ``lst_token.exchange_rate_to_sol`` by one epoch.

    Returns ``(new_rate, delta)``. Caller is responsible for emitting any
    ``LST_RATE_UPDATED`` event; this helper only mutates the spec.

    Raises ``ValueError`` if the token has no exchange rate or no drift spec.
    """

    drift = lst_token.exchange_rate_drift
    if drift is None:
        raise ValueError(
            f"advance_lst_rate: token {lst_token.id!r} has no exchange_rate_drift"
        )
    if lst_token.exchange_rate_to_sol is None:
        raise ValueError(
            f"advance_lst_rate: token {lst_token.id!r} has no exchange_rate_to_sol"
        )

    baseline = float(drift.drift_per_epoch)
    sigma = float(drift.volatility_per_epoch)
    noise = float(rng.normal(0.0, sigma)) if sigma > 0.0 else 0.0
    factor = Decimal(str(1.0 + baseline + noise))

    old_rate = lst_token.exchange_rate_to_sol
    new_rate = old_rate * factor
    delta = new_rate - old_rate
    lst_token.exchange_rate_to_sol = new_rate
    return new_rate, delta
