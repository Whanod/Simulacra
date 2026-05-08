"""Oracle staleness events + per-slot cost roll-up (PRD US-006 line 494).

Two helpers:

* :func:`make_oracle_stale_event` — constructs the canonical
  ``OracleStaleEvent`` payload referenced by PRD line 495 as
  ``OracleStaleEvent(slot, oracle_id, last_update_slot)``. It returns an
  ordinary :class:`Event` carrying ``EventType.ORACLE_STALE`` so it
  flows through the existing :class:`EventBus` without a parallel event
  pipeline.

* :func:`oracle_costs_per_slot` — aggregates per-slot oracle update costs
  for run metrics. The data sources are the recorded oracle objects
  themselves (CU + lamport cost) joined against the slots at which each
  oracle was pulled / republished. Surface chosen to be cheap to compute
  off the simulation history and to avoid dragging a metrics framework
  into the engine for what is fundamentally a per-slot sum.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from defi_sim.engine.events import Event, EventType
from defi_sim.engine.oracles.source import PullOracle, PushOracle


@dataclass(frozen=True)
class OracleSlotCost:
    """Per-slot cost line surfaced in run metrics (PRD line 496).

    ``cu`` is the compute-unit cost paid by consumers for pull-mode
    updates included in their txs that slot; ``lamports`` is the matching
    flat lamport cost (consumers for pull-mode, oracle operators for
    push-mode — see ``operator_lamports``).
    """

    slot: int
    cu: int
    lamports: int
    operator_lamports: int


def make_oracle_stale_event(
    *,
    round: int,
    timestamp: int | float,
    slot: int,
    oracle_id: str,
    last_update_slot: int | None,
    run_id: str | None = None,
) -> Event:
    """Build an ``EventType.ORACLE_STALE`` event with the PRD-spec payload.

    ``last_update_slot`` may be ``None`` when a pull-mode oracle has
    never been pulled — callers receive that as ``None`` in the event
    data so downstream consumers can distinguish "never updated" from
    "updated at slot 0".
    """
    return Event(
        type=EventType.ORACLE_STALE,
        round=round,
        timestamp=timestamp,
        run_id=run_id,
        data={
            "slot": int(slot),
            "oracle_id": oracle_id,
            "last_update_slot": (
                None if last_update_slot is None else int(last_update_slot)
            ),
        },
    )


def oracle_costs_per_slot(
    *,
    pull_oracle_pulls: Mapping[str, Iterable[int]],
    pull_oracles: Mapping[str, PullOracle],
    push_oracles: Mapping[str, PushOracle] | None = None,
    push_slot_window: tuple[int, int] | None = None,
) -> list[OracleSlotCost]:
    """Roll cost-per-slot lines for a run.

    Inputs are the slots at which each pull oracle was pulled (typically
    derived from emitted ``OracleUpdateAction`` instances) and the oracle
    instances themselves (so we can read each oracle's CU + lamport
    cost). Push oracles are optional; when supplied with a slot window we
    surface the operator-paid lamport cost at each cadence boundary
    (consumers don't pay for push updates, but operators do — and the
    metric line should still show that throughput).

    Returns a slot-sorted list of :class:`OracleSlotCost`, one entry per
    slot that incurred cost.
    """
    by_slot: dict[int, dict[str, int]] = {}

    def _bump(slot: int, key: str, amount: int) -> None:
        if amount == 0:
            return
        by_slot.setdefault(slot, {"cu": 0, "lamports": 0, "operator_lamports": 0})
        by_slot[slot][key] += amount

    for oracle_id, pulled_slots in pull_oracle_pulls.items():
        oracle = pull_oracles.get(oracle_id)
        if oracle is None:
            continue
        for slot in pulled_slots:
            _bump(int(slot), "cu", oracle.update_cu_cost)
            _bump(int(slot), "lamports", oracle.update_lamport_cost)

    if push_oracles and push_slot_window is not None:
        start, end = push_slot_window
        for oracle in push_oracles.values():
            cadence = oracle.update_cadence_slots
            first_update = ((start + cadence - 1) // cadence) * cadence
            for slot in range(first_update, end, cadence):
                _bump(slot, "operator_lamports", oracle.update_cost_lamports)

    return [
        OracleSlotCost(
            slot=slot,
            cu=cells["cu"],
            lamports=cells["lamports"],
            operator_lamports=cells["operator_lamports"],
        )
        for slot, cells in sorted(by_slot.items())
    ]


__all__ = [
    "OracleSlotCost",
    "make_oracle_stale_event",
    "oracle_costs_per_slot",
]
