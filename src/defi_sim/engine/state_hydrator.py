"""StateHydrator contract for forkable Phase 3 protocol models.

PRD US-003 line 398. Each forkable Market subclass declares a
``state_hydrator: StateHydrator`` class attribute; the framework's
``ForkLoader`` (line 483) walks each protocol's hydrator against the
historical-account snapshots from PRD US-001 to produce an
``InitialState`` value object.

The ABC ships ahead of any concrete protocol parser inheriting from it —
the existing ``WhirlpoolStateHydrator`` (in ``defi_sim_solana.replay``)
is duck-typed today and will be retrofitted under 2.3b. The 2.3a framework
test exercises the contract via a fake hydrator so the abstraction can
ship without Whirlpool/DLMM in the same PR.

``schema_version`` participates in the fork cache key (line 526). When a
parser bug is fixed, bumping it invalidates every cached
``(slot, fork_spec)`` entry that uses this hydrator and forces re-parse on
next load — required so parser bugfixes propagate to dependent calibration
tests on next CI run rather than silently serving stale cached state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from defi_sim.engine.initial_state import InitialStateFragment

__all__ = ["AccountFilter", "OracleId", "StateHydrator"]


Pubkey = str
OracleId = str


@dataclass(frozen=True)
class AccountFilter:
    """Discriminator + optional pubkey allowlist filter for narrowing program accounts.

    The 8-byte Anchor discriminator selects which account *kind* under a
    program's ID space the hydrator wants (e.g., Whirlpool's ``Whirlpool``
    pool struct vs. ``Position`` accounts). ``pubkey_allowlist`` further
    narrows to a specific list of accounts when callers want to fork only
    specific pools.
    """

    discriminator: bytes | None = None
    pubkey_allowlist: tuple[Pubkey, ...] | None = None


class StateHydrator(ABC):
    """Per-protocol parser contract for selective protocol-state forks.

    Subclasses declare:

    * ``program_id`` — the Solana program owning this protocol's accounts.
    * ``schema_version`` — bumped when parser logic changes; participates
      in the fork cache key so parser bugfixes invalidate stale cache.

    and implement:

    * :meth:`account_filters` — discriminator/pubkey filters narrowing
      ``getProgramAccounts`` output.
    * :meth:`parse_account` — pure ``bytes -> fragment`` transform. Does
      NOT construct a ``Market`` — materialization is a separate step
      (see PRD line 543's ``materialize_fork`` pipeline).
    * :meth:`oracle_dependencies` — oracle account IDs this protocol
      reads; the loader pulls them automatically.
    """

    program_id: str
    schema_version: int

    @abstractmethod
    def account_filters(self) -> list[AccountFilter]:
        """Return discriminator + optional pubkey allowlist filters."""

    @abstractmethod
    def parse_account(self, pubkey: Pubkey, data: bytes) -> "InitialStateFragment":
        """Parse one account into a typed fragment.

        Pure function: bytes -> fragment. Does NOT construct a Market.
        """

    def oracle_dependencies(self) -> list[OracleId]:
        """Return oracle account IDs this protocol reads.

        Used by ``ForkLoader`` to add Pyth/Switchboard accounts to the
        fork. Defaults to no oracle dependencies.
        """
        return []
