"""Forkable ``Market`` / seedable ``Agent`` contracts (PRD US-003 line 447).

A forkable ``Market`` subclass declares a ``state_hydrator`` class attribute
and implements ``from_initial_state`` so the materializer (PRD line 543) can
construct it from already-parsed :class:`InitialStateFragment`s. Likewise an
agent type that participates in fork-state seeding implements
``seed_from_fragments`` so the materializer can populate per-agent wallet /
position state from ``owner``-keyed fragments.

These are independent mixin ABCs. They do **not** modify the existing
:class:`defi_sim.core.market.Market` or :class:`defi_sim.core.agent.Agent`
bases — non-forkable markets and non-seedable agents remain untouched.
Phase 3 protocols and wallet-shaped agent types adopt these contracts as
they ship; the framework lands ahead of any concrete adopter so the
2.3a → 2.3b carve-out from the PRD holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from defi_sim.core.types import NumericMode
    from defi_sim.engine.initial_state import InitialStateFragment
    from defi_sim.engine.parameters import ParameterStore
    from defi_sim.engine.state_hydrator import StateHydrator

__all__ = ["ForkableMarket", "SeedableAgent"]


class ForkableMarket(ABC):
    """Mixin contract for ``Market`` subclasses constructible from a fork snapshot.

    Concrete subclasses set a ``state_hydrator`` class attribute (the
    :class:`StateHydrator` parser for this protocol's program) and
    implement :meth:`from_initial_state`. The materializer pre-filters
    fragments to those whose ``protocol_model`` matches the market before
    invoking the factory — pool-shaped markets consume ``"pool"`` fragments;
    lending-shaped markets consume ``"lending_reserve"`` +
    ``"lending_position"`` fragments.
    """

    state_hydrator: ClassVar["StateHydrator"]

    @classmethod
    @abstractmethod
    def from_initial_state(
        cls,
        fragments: list["InitialStateFragment"],
        *,
        parameters: "ParameterStore",
        numeric_mode: "NumericMode",
    ) -> "ForkableMarket":
        """Construct a runtime market from parsed fragments.

        ``fragments`` is pre-filtered to those whose ``protocol_model``
        matches this market. The factory should not perform binary
        decoding — that already happened in :class:`StateHydrator`.
        """


class SeedableAgent(ABC):
    """Mixin contract for agent types that consume wallet/position fragments.

    Implementations populate this agent's wallet and position state from
    fragments whose ``owner`` matches the agent's pubkey. The materializer
    routes only the relevant ``"wallet_balance"`` / ``"wallet_position"``
    (and per-user ``"lending_position"`` / ``"perp_position"``) fragments
    for this owner.
    """

    @abstractmethod
    def seed_from_fragments(
        self,
        fragments: list["InitialStateFragment"],
    ) -> None:
        """Populate this agent's state from owner-scoped fragments.

        Fragments arrive pre-filtered to this agent's owner pubkey.
        """
