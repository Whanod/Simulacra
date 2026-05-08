"""Single-run API facade for web and service integrations."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from defi_sim.core.types import Numeric, SimulationResult
from defi_sim.engine.config import CancellationToken
from defi_sim.engine.events import EventBus
from defi_sim.engine.oracles.presets import (
    pyth_lazer_solusdc,
    pyth_pull_solusdc,
    switchboard_on_demand_solusdc,
)
from defi_sim.engine.oracles.source import PullOracle
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.specs import RunSpec, build_agents, build_market, build_simulation_config


_ORACLE_PRESET_FACTORIES: dict[str, Callable[..., PullOracle]] = {
    "pyth_pull": pyth_pull_solusdc,
    "pyth_lazer": pyth_lazer_solusdc,
    "switchboard_on_demand": switchboard_on_demand_solusdc,
}


def build_engine(
    spec: RunSpec | Mapping[str, Any],
    *,
    event_bus: EventBus | None = None,
    cancel_token: CancellationToken | None = None,
) -> SimulationEngine:
    run_spec = spec if isinstance(spec, RunSpec) else RunSpec.from_dict(spec)
    # PRD US-012 line 947: synthesize ``Validator`` agent specs from the
    # builder-supplied ``execution.params.validator_set`` so the frontend's
    # Validator Set panel actually reaches the engine. The frontend writes
    # one entry per validator with pubkey/client/stake/share fields; here
    # they become first-class AgentSpec entries appended to ``run_spec.agents``
    # ahead of ``build_agents`` so the existing factory pipeline handles
    # construction. This is additive — runs without a validator_set are
    # untouched.
    _expand_validator_set_into_agents(run_spec)
    market = build_market(run_spec.market)
    agents = build_agents(run_spec.agents)
    config = build_simulation_config(run_spec, cancel_token=cancel_token)
    engine = SimulationEngine(market=market, agents=agents, config=config, event_bus=event_bus)
    _register_oracle_preset(run_spec, engine, config.feeds or [])
    return engine


def _expand_validator_set_into_agents(run_spec: RunSpec) -> None:
    execution = run_spec.execution
    if execution is None:
        return
    params = execution.params or {}
    validator_set = params.pop("validator_set", None)
    if not validator_set:
        return
    from defi_sim.engine.specs import AgentSpec
    existing_ids = {
        getattr(a, "agent_id", None) if not isinstance(a, Mapping) else a.get("agent_id")
        for a in run_spec.agents
    }
    for index, entry in enumerate(validator_set):
        if not isinstance(entry, Mapping):
            continue
        pubkey = entry.get("pubkey") or f"validator-{index}"
        agent_id = entry.get("agent_id") or pubkey
        if agent_id in existing_ids:
            # Preserve any explicit validator agent the spec already
            # supplied — UI-derived entries do not overwrite them.
            continue
        existing_ids.add(agent_id)
        params_payload = {
            "pubkey": pubkey,
            "client": entry.get("client", "jito_solana"),
            "stake_pool_share": entry.get("stake_pool_share", 0.05),
            "stake_pool_address": entry.get("stake_pool_address"),
            "stake_lamports": int(entry.get("stake_lamports", 0)),
            "commission_pct": entry.get("commission_pct", 0.05),
        }
        run_spec.agents.append(
            AgentSpec(type="validator", agent_id=agent_id, params=params_payload)
        )


def _register_oracle_preset(
    run_spec: RunSpec,
    engine: SimulationEngine,
    feeds: list[Any],
) -> None:
    """Map ``execution.params.oracle_preset`` to a ``PullOracle`` and attach it
    to the engine (PRD US-006 line 484-497).

    The frontend's oracle picker writes one of ``{"none", "pyth_pull",
    "pyth_lazer", "switchboard_on_demand"}`` into ``execution.params``; this
    helper resolves the preset factory, sources its truth price from the
    first ``RunSpec.feeds`` entry that publishes ``SOL`` at slot 0, and
    registers the resulting ``PullOracle`` so per-slot oracle cost / staleness
    telemetry actually fires for runs submitted via the API.
    """
    execution = run_spec.execution
    if execution is None:
        return
    params = execution.params or {}
    preset_name = params.get("oracle_preset")
    if not preset_name or preset_name == "none":
        return
    factory = _ORACLE_PRESET_FACTORIES.get(preset_name)
    if factory is None:
        raise ValueError(
            f"unknown oracle_preset: {preset_name!r} "
            f"(expected one of {sorted(_ORACLE_PRESET_FACTORIES)})"
        )
    price_token = "SOL"
    price_source = _build_oracle_price_source(feeds, price_token, preset_name)
    engine.register_oracle(factory(price_source=price_source, initial_pull_slot=0))


def _build_oracle_price_source(
    feeds: list[Any],
    token_id: str,
    preset_name: str,
) -> Callable[[int], Numeric]:
    # PRD US-006 step 1.8b: feed aggregators expose per-token oracle
    # views via ``oracle_for(token)``. ``HistoricalFeed`` returns 0 for
    # unknown tokens rather than raising, so probe with ``> 0`` to
    # distinguish "feed has token" from "feed silently returned a
    # sentinel zero".
    for feed in feeds:
        try:
            oracle = feed.oracle_for(token_id)
            value, _ = oracle.price_at(0)
        except Exception:
            continue
        if value is None or value <= 0:
            continue
        return lambda slot, o=oracle: o.price_at(int(slot))[0]
    raise ValueError(
        f"oracle_preset={preset_name!r} requires a price feed publishing "
        f"token {token_id!r} in run_spec.feeds"
    )


def run_simulation(
    spec: RunSpec | Mapping[str, Any],
    *,
    event_bus: EventBus | None = None,
    cancel_token: CancellationToken | None = None,
) -> SimulationResult:
    """Build a market, agents, and config from a RunSpec and execute one run."""
    engine = build_engine(spec, event_bus=event_bus, cancel_token=cancel_token)
    return engine.run()
