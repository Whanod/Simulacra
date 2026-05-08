"""``materialize_fork`` pipeline + :class:`HydratedFork` (PRD US-003 line 543).

Turns the parsed :class:`InitialState` value object produced by
:class:`ForkLoader` into runtime objects: a populated :class:`World`,
per-owner :class:`AgentStateSeed` overlays, and an oracle
:class:`PriceFeedRegistry`. Materialization is deliberately separate from
loading — the loader is a pure parser whose output is cacheable, while
materialization constructs mutable runtime objects that are not.

Routing rule per PRD line 585-593:

* ``"pool"`` / ``"lending_reserve"`` / ``"perp_market"`` (and per-protocol
  position fragments) — passed to the protocol's
  :class:`ForkableMarket` factory ``from_initial_state``.
* ``"oracle_price"`` — folded into a :class:`PriceFeedRegistry`.
* ``"wallet_balance"`` / ``"wallet_position"`` — grouped by ``owner`` into
  :class:`AgentStateSeed` overlays so the engine's wallet-tracking agents
  can pick them up at construction time.

``PriceFeedRegistry`` and ``AgentStateSeed`` are intentionally minimal
placeholders here. They carry the routed fragments without yet committing
to a runtime shape — the first :class:`SeedableAgent` adopter (and the
Pyth/Switchboard decoder, when it lands) will refine the surface. Until
then, materialization is loud about preserving every routed fragment so
downstream calibration cannot silently drop state.

Explicit non-goals (PRD US-003 line 650)
----------------------------------------
Materialization inherits the same hard limits as the loader (see
:mod:`defi_sim.engine.fork_loader`):

* No sysvar replication.
* No unrelated programs.
* No full-account-index walk.
* No ledger replay.
* Hydrate enough state to make the modeled protocols' math correct, and
  nothing more.

Anything outside that envelope belongs to a different subsystem
(``ReplayExecution`` for ledger replay, the full validator for sysvars).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from defi_sim.engine.initial_state import (
    FragmentKind,
    InitialState,
    InitialStateFragment,
)
from defi_sim.engine.state_hydrator import Pubkey
from defi_sim.engine.world import World

if TYPE_CHECKING:
    from defi_sim.core.types import NumericMode
    from defi_sim.engine.fork_loader import ProtocolModelRegistry
    from defi_sim.engine.forkable import ForkableMarket
    from defi_sim.engine.parameters import ParameterStore

__all__ = [
    "AgentStateSeed",
    "HydratedFork",
    "PriceFeedRegistry",
    "materialize_fork",
]


_MARKET_FRAGMENT_KINDS: frozenset[FragmentKind] = frozenset(
    {
        "pool",
        "lending_reserve",
        "lending_position",
        "perp_market",
        "perp_position",
    }
)
_WALLET_FRAGMENT_KINDS: frozenset[FragmentKind] = frozenset(
    {"wallet_balance", "wallet_position"}
)


@dataclass(frozen=True)
class AgentStateSeed:
    """Owner-scoped wallet/position fragments awaiting agent seeding.

    Carries the raw fragments grouped by ``owner`` pubkey. A
    :class:`SeedableAgent` constructed for this owner consumes
    ``fragments`` via its ``seed_from_fragments`` hook.
    """

    owner: Pubkey
    fragments: tuple[InitialStateFragment, ...]


@dataclass(frozen=True)
class PriceFeedRegistry:
    """Container for ``oracle_price`` fragments.

    Minimal placeholder pending the Pyth/Switchboard decoder. The
    :meth:`from_fragments` factory preserves every routed fragment so
    materialization remains lossless until a richer feed shape lands.
    """

    fragments: tuple[InitialStateFragment, ...] = ()

    @classmethod
    def from_fragments(
        cls, fragments: list[InitialStateFragment]
    ) -> "PriceFeedRegistry":
        return cls(fragments=tuple(fragments))


@dataclass(frozen=True)
class HydratedFork:
    """Runtime objects produced from an :class:`InitialState`.

    ``world`` carries one named :class:`Market` per protocol model that
    contributed market-shaped fragments. ``agent_seeds`` is keyed by
    owner pubkey so the run-builder can match seeds to wallet-tracking
    agents. ``price_feeds`` carries every ``oracle_price`` fragment.
    ``start_slot`` mirrors :attr:`InitialState.slot` for telemetry and
    for ``ForkExecution`` to tag scheduler events with.
    """

    world: World
    agent_seeds: dict[Pubkey, AgentStateSeed]
    price_feeds: PriceFeedRegistry
    start_slot: int


def materialize_fork(
    initial: InitialState,
    registry: "ProtocolModelRegistry",
    *,
    parameters: "ParameterStore",
    numeric_mode: "NumericMode",
) -> HydratedFork:
    """Construct runtime objects from a parsed :class:`InitialState`.

    Walks ``initial.protocols()`` in deterministic order, partitions each
    protocol's fragments by kind, and routes them to the appropriate
    runtime owner. Protocols with no market-shaped fragments are skipped
    silently (an oracle-only protocol entry does not need a market).
    """
    world = World()
    for protocol_model in initial.protocols():
        protocol_fragments = initial.by_protocol(protocol_model)
        market_fragments = [
            f for f in protocol_fragments if f.kind in _MARKET_FRAGMENT_KINDS
        ]
        if not market_fragments:
            continue
        model_cls = registry.lookup(protocol_model)
        market = model_cls.from_initial_state(
            market_fragments,
            parameters=parameters,
            numeric_mode=numeric_mode,
        )
        world.add_market(protocol_model, market)

    price_feeds = PriceFeedRegistry.from_fragments(
        initial.by_kind("oracle_price")
    )
    wallet_fragments: list[InitialStateFragment] = []
    for kind in _WALLET_FRAGMENT_KINDS:
        wallet_fragments.extend(initial.by_kind(kind))
    agent_seeds = _group_wallet_fragments_by_owner(wallet_fragments)
    return HydratedFork(
        world=world,
        agent_seeds=agent_seeds,
        price_feeds=price_feeds,
        start_slot=initial.slot,
    )


def _group_wallet_fragments_by_owner(
    fragments: list[InitialStateFragment],
) -> dict[Pubkey, AgentStateSeed]:
    """Group wallet fragments by ``owner``; skip fragments without an owner."""
    grouped: dict[Pubkey, list[InitialStateFragment]] = {}
    for f in fragments:
        if f.owner is None:
            continue
        grouped.setdefault(f.owner, []).append(f)
    return {
        owner: AgentStateSeed(owner=owner, fragments=tuple(frags))
        for owner, frags in grouped.items()
    }
