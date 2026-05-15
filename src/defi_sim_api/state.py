"""In-memory store for live simulation engines.

A production deployment would swap this for Redis / a database — but for the
local-first design of defi-sim an in-memory dict is the right default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from defi_sim.engine.config import CancellationToken
from defi_sim.engine.events import EventBus
from defi_sim.engine.simulation import SimulationEngine


@dataclass
class EngineEntry:
    engine: SimulationEngine
    cancel_token: CancellationToken
    event_bus: EventBus
    run_id: str
    spec: dict[str, object] = field(default_factory=dict)
    completion_event_emitted: bool = False
    # Privy DID of the user who built this engine (None for open-mode /
    # API-key writes). Used by the simulations router to gate
    # step/cancel/snapshot against cross-owner access.
    owner_id: str | None = None


_engines: dict[str, EngineEntry] = {}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def store(sim_id: str, entry: EngineEntry) -> None:
    _engines[sim_id] = entry


def get(sim_id: str) -> EngineEntry | None:
    return _engines.get(sim_id)


def remove(sim_id: str) -> EngineEntry | None:
    return _engines.pop(sim_id, None)


def list_ids() -> list[str]:
    return list(_engines.keys())
