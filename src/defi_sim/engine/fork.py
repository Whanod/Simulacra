"""Fork configuration: chain-reorg risk (US-014) and protocol-state forks (US-003).

Two distinct concepts share this module because they share a name in the PRD.
They are not interchangeable:

* :class:`ChainReorgForkSpec` (PRD US-014 line 1109) — runtime knob consulted by
  :class:`~defi_sim.engine.execution.ExecutionModel` per slot. Decides whether
  to mark a fork point and reorg the last ``d`` slots
  (``d <= max_reorg_depth_slots``). Was named ``ForkSpec`` until US-003
  introduced the second concept; renamed for disambiguation.

* :class:`ForkSpec` (PRD US-003 line 468) — declarative request to materialize
  a ``World`` from on-chain state at a specific slot via :class:`ForkLoader`.
  Names the protocols (and optionally specific accounts and wallet overlays)
  to hydrate. Has nothing to do with chain reorgs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from defi_sim.engine.state_hydrator import Pubkey

__all__ = ["ChainReorgForkSpec", "ForkSpec", "ProtocolForkRequest"]


@dataclass
class ChainReorgForkSpec:
    fork_probability_per_slot: float = 0.0
    max_reorg_depth_slots: int = 5
    seed: int | None = None


@dataclass
class ProtocolForkRequest:
    """One protocol's hydration request inside a :class:`ForkSpec`.

    ``protocol_model`` keys into the :class:`ProtocolModelRegistry` consulted
    by :class:`ForkLoader`; ``account_pubkey_allowlist`` (when set) restricts
    the fork to specific accounts under that program — useful for forking
    only the SOL/USDC pool out of all 100k+ Whirlpool accounts.
    """

    protocol_model: str
    account_pubkey_allowlist: list[Pubkey] | None = None


@dataclass
class ForkSpec:
    """Declarative request to fork a ``World`` from on-chain state at ``slot``.

    Consumed by :class:`ForkLoader` (PRD US-003 line 483) to produce an
    :class:`~defi_sim.engine.initial_state.InitialState` value object, which
    ``materialize_fork`` (PRD line 543) then turns into a runtime ``World``.

    The cache key (PRD line 526) hashes this spec plus the participating
    hydrators' ``schema_version`` values, so identical fork requests reuse
    parsed state across runs while parser-bug fixes invalidate stale entries.
    """

    slot: int
    protocols: list[ProtocolForkRequest] = field(default_factory=list)
    include_wallet_accounts: list[Pubkey] | None = None
