"""SimulationConfig with sensible defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Callable

from defi_sim.core.agent import InformationFilter
from defi_sim.core.clock import Clock
from defi_sim.core.types import FIXED_POINT, NumericMode, RoundSnapshot, TokenId
from defi_sim.engine.execution import DirectExecution, ExecutionModel
from defi_sim.engine.parameters import ParameterStore


class CancellationToken:
    """Thread-safe cancellation primitive for long-running simulations."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason: str | None = None

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            self._reason = reason
            self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        if not self._event.is_set():
            return None
        return self._reason or "cancelled"


@dataclass
class SimulationConfig:
    """Bundles common simulation parameters with sensible defaults."""
    num_rounds: int = 200
    snapshot_interval: int = 10
    seed: int = 42
    clock: Clock | None = None
    default_fee_model: Any = None  # FeeModel | None
    execution_model: ExecutionModel | None = None
    feeds: list[Any] | None = None  # list of multi-token feed aggregators (PRD US-006)
    information_filter: InformationFilter | None = None
    belief_provider: Callable[..., dict[TokenId, int] | None] | None = None
    visible_agents_provider: Callable[..., list[Any] | None] | None = None
    parameters: ParameterStore | None = None
    emission_schedule: Any = None  # EmissionSchedule | None
    reward_distributor: Any = None  # RewardDistributor | None
    # LST tokens whose exchange_rate_to_sol should be advanced at each
    # epoch boundary using their ExchangeRateDriftSpec. Each entry must be
    # a TokenSpec with both `exchange_rate_to_sol` and `exchange_rate_drift`
    # set (US-007, PRD line 571). The engine mutates these in place.
    lst_tokens: list[Any] | None = None  # list[TokenSpec] | None

    # AddressLookupTable seeds (US-009, PRD line 676). Materialized into
    # ``engine.alts: dict[AltId, AddressLookupTable]`` on init so
    # VersionedTransactions can reference them by id.
    alts: list[Any] | None = None  # list[AddressLookupTable] | None

    numeric_mode: NumericMode = field(default_factory=lambda: FIXED_POINT)

    early_stop: Callable[[RoundSnapshot], bool] | None = None
    stop_reason_fn: Callable[[RoundSnapshot], str] | None = None
    cancel_token: CancellationToken | None = None

    snapshot_callback: Callable[[RoundSnapshot], None] | None = None
    retain_snapshots: bool = True
    progress_callback: Callable[[int, int], None] | None = None

    def __post_init__(self) -> None:
        if self.execution_model is None:
            self.execution_model = DirectExecution()
