"""Oracle abstractions for Solana-fidelity price feeds.

PRD US-006 step 1.8b complete: ``OracleSource`` is the only price-source
ABC. The legacy ``PriceFeed`` ABC and the ``LegacyFeedAsOracle`` shim
have been deleted; multi-token feed aggregators in
``defi_sim.engine.feeds`` project per-token oracle views via
``oracle_for(token)``.
"""

from defi_sim.engine.oracles.metrics import (
    OracleSlotCost,
    make_oracle_stale_event,
    oracle_costs_per_slot,
)
from defi_sim.engine.oracles.presets import (
    pyth_lazer_solusdc,
    pyth_pull_solusdc,
    switchboard_on_demand_solusdc,
)
from defi_sim.engine.oracles.source import (
    OracleSource,
    OracleUpdateAction,
    PullOracle,
    PushOracle,
    passes_confidence_gate,
)

__all__ = [
    "OracleSlotCost",
    "OracleSource",
    "OracleUpdateAction",
    "PullOracle",
    "PushOracle",
    "make_oracle_stale_event",
    "oracle_costs_per_slot",
    "passes_confidence_gate",
    "pyth_lazer_solusdc",
    "pyth_pull_solusdc",
    "switchboard_on_demand_solusdc",
]
