"""``ForkExecution`` (PRD US-003 line 595).

A :class:`SolanaLikeExecution` preset that carries *only* fork metadata
for telemetry / cost-model selection. It deliberately does **not** own
protocol state — that lives on the :class:`World` / per-protocol
:class:`Market` constructed by :func:`materialize_fork`.

Regression invariant (PRD line 716): ``ForkExecution`` exposes neither
``state``/``_state`` nor ``initial_state``/``world``. Anyone needing to
inspect fork state should reach for ``HydratedFork`` or the engine's
``World``.
"""

from __future__ import annotations

from defi_sim.engine.execution import SolanaLikeExecution

__all__ = ["ForkExecution"]


class ForkExecution(SolanaLikeExecution):
    """Solana-like scheduler tagged with the fork's start slot for telemetry.

    Does NOT own protocol state — that lives on the Market / World built by
    :func:`materialize_fork`. Constructed alongside that World by the
    run-builder.
    """

    def __init__(self, *, start_slot: int, **kwargs):
        super().__init__(**kwargs)
        self.start_slot = int(start_slot)
