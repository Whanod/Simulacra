"""DLMM (Meteora ``LbPair``) ``StateHydrator`` reference implementation
(PRD US-003 line 722 / 2.3c).

Parses Meteora DLMM ``LbPair`` account bytes into a typed
:class:`~defi_sim.engine.initial_state.InitialStateFragment` so the
framework's ``ForkLoader`` can route it to a ``Market`` at materialization
time, and so a hand-curated manifest in
``solana-plans/calibration/corpus/<slot>/manifest.yaml`` (added by 2.4)
will be able to validate the parser output offline.

This class is the **2.3c reference implementation** of
:class:`~defi_sim.engine.state_hydrator.StateHydrator`: it satisfies the
ABC so ``ForkLoader`` (PRD line 483) can drive it end-to-end without
duck-typing.

**Reserve proxy.** A DLMM ``LbPair`` does not store vault token amounts
directly — those live in two SPL Token reserve accounts referenced by
``reserve_x`` / ``reserve_y``. The 2.3-reference hydrator therefore emits
the canonical DLMM state ``(active_id, bin_step)`` as the "reserve proxy"
pair recorded under ``manifest.expected.pool_reserves`` — matching the
existing manifest schema's ``per-pool address -> [reserve_a, reserve_b]``
convention used by the Whirlpool reference impl. ``active_id`` selects
the active bin (price level) and ``bin_step`` is the price-discreteness
parameter; together they pin the pool's price state without requiring the
vault accounts. Future iterations that pull vault accounts can swap in
true token reserves without changing the manifest layout.

Account layout (Meteora DLMM ``LbPair`` struct, little-endian)::

    0   discriminator              [u8; 8]
    8   parameters                 StaticParameters (32)
    40  v_parameters               VariableParameters (32)
    72  bump_seed                  [u8; 1]
    73  bin_step_seed              [u8; 2]
    75  pair_type                  u8
    76  active_id                  i32
    80  bin_step                   u16
    ... (remaining fields are not needed for the reserve-proxy validation)

The minimum prefix needed is 82 bytes; longer payloads parse fine — extra
bytes are ignored.

The Anchor account discriminator for ``LbPair`` is the first 8 bytes of
``sha256("account:LbPair")``: ``21 0b 31 62 b5 65 b1 0d``. That value
narrows ``getProgramAccounts`` to ``LbPair`` accounts, excluding sibling
shapes (e.g. ``Position``, ``BinArray``) under the same program id.
"""

from __future__ import annotations

from dataclasses import dataclass

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import (
    AccountFilter,
    OracleId,
    StateHydrator,
)

__all__ = [
    "DLMM_LB_PAIR_DISCRIMINATOR",
    "DLMM_PROGRAM",
    "DlmmLbPairFragment",
    "DlmmStateHydrator",
]

DLMM_PROGRAM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
# sha256("account:LbPair")[:8]
DLMM_LB_PAIR_DISCRIMINATOR = b"\x21\x0b\x31\x62\xb5\x65\xb1\x0d"

_MIN_LB_PAIR_LEN = 82


@dataclass(frozen=True, slots=True)
class DlmmLbPairFragment:
    """Typed view of the parsed DLMM ``LbPair`` fields.

    ``(active_id, bin_step)`` is the DLMM-canonical reserve proxy that
    populates ``manifest.expected.pool_reserves[pubkey]``. This typed view
    is used by :meth:`DlmmStateHydrator.parse_lb_pair` and embedded as the
    ``payload`` of the ABC-shaped :class:`InitialStateFragment` returned
    by :meth:`DlmmStateHydrator.parse_account`.
    """

    pubkey: str
    active_id: int
    bin_step: int

    @property
    def reserve_proxy(self) -> tuple[int, int]:
        return (self.active_id, self.bin_step)


class DlmmStateHydrator(StateHydrator):
    """DLMM ``LbPair`` account parser (PRD line 722 / 2.3c reference impl)."""

    program_id: str = DLMM_PROGRAM
    schema_version: int = 1

    def account_filters(self) -> list[AccountFilter]:
        return [AccountFilter(discriminator=DLMM_LB_PAIR_DISCRIMINATOR)]

    def oracle_dependencies(self) -> list[OracleId]:
        return []

    def parse_lb_pair(self, pubkey: str, data: bytes) -> DlmmLbPairFragment:
        """Parse the raw account bytes into the typed ``LbPair`` view."""
        if len(data) < _MIN_LB_PAIR_LEN:
            raise ValueError(
                f"DLMM LbPair account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_MIN_LB_PAIR_LEN} to read active_id + bin_step."
            )
        active_id = int.from_bytes(data[76:80], "little", signed=True)
        bin_step = int.from_bytes(data[80:82], "little", signed=False)
        return DlmmLbPairFragment(
            pubkey=pubkey,
            active_id=active_id,
            bin_step=bin_step,
        )

    def parse_account(self, pubkey: str, data: bytes) -> InitialStateFragment:
        """Return an ABC-shaped fragment routed as ``kind="pool"``.

        Payload preserves the typed ``LbPair`` view (``active_id``,
        ``bin_step``, ``reserve_proxy``) so downstream consumers —
        ``materialize_fork`` and the manifest validator — can read either
        the raw fields or the DLMM reserve proxy without re-parsing.
        """
        pair = self.parse_lb_pair(pubkey, data)
        return InitialStateFragment(
            kind="pool",
            protocol_model="MeteoraDlmm",
            pubkey=pubkey,
            owner=None,
            payload={
                "active_id": pair.active_id,
                "bin_step": pair.bin_step,
                "reserve_proxy": list(pair.reserve_proxy),
            },
        )
