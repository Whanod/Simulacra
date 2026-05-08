"""Live engine introspection — events, market state, agent state."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.core.market import LiquidityPool, PricedMarket
from defi_sim.engine.json import to_jsonable
from defi_sim.engine.world import World

from defi_sim_api import schemas, state
from defi_sim_api.backend.serialization import agent_timeline_from_rounds, events_to_list, filter_events, round_snapshot_to_dict

router = APIRouter(prefix="/simulations", tags=["engine introspection"])


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


# ── Events ────────────────────────────────────────────────────────────────

@router.get(
    "/{simulation_id}/events",
    response_model=schemas.EventResponse,
    summary="Get event history from a live engine",
)
def get_events(
    simulation_id: str,
    event_type: str | None = None,
    agent_id: str | None = None,
    round: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> schemas.EventResponse:
    entry = _get_entry(simulation_id)
    history = filter_events(
        events_to_list(entry.event_bus.history),
        event_type=event_type,
        agent_id=agent_id,
        round_number=round,
        limit=limit,
        offset=offset,
    )
    return schemas.EventResponse(
        events=history
    )


# ── Market state ──────────────────────────────────────────────────────────

@router.get(
    "/{simulation_id}/markets",
    response_model=schemas.AllMarketStatesResponse,
    summary="Get all market states from a live engine",
)
def get_all_market_states(simulation_id: str) -> schemas.AllMarketStatesResponse:
    entry = _get_entry(simulation_id)
    market = entry.engine._market
    if isinstance(market, World):
        states = {
            name: to_jsonable(m.get_state(), include_type_tags=True)
            for name, m in market.markets.items()
        }
    else:
        states = {"default": to_jsonable(market.get_state(), include_type_tags=True)}
    return schemas.AllMarketStatesResponse(simulation_id=simulation_id, states=states)


@router.get(
    "/{simulation_id}/markets/{market_name}",
    response_model=schemas.MarketStateResponse,
    summary="Get a specific market's state",
)
def get_market_state(simulation_id: str, market_name: str) -> schemas.MarketStateResponse:
    entry = _get_entry(simulation_id)
    market = entry.engine._market
    if isinstance(market, World):
        child = market.markets.get(market_name)
        if child is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Market {market_name!r} not found. Available: {list(market.markets)}",
            )
        state_data = to_jsonable(child.get_state(), include_type_tags=True)
    elif market_name == "default":
        state_data = to_jsonable(market.get_state(), include_type_tags=True)
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Single-market engine only has 'default'. Got {market_name!r}",
        )
    return schemas.MarketStateResponse(
        simulation_id=simulation_id,
        market_name=market_name,
        state=state_data,
    )


@router.get(
    "/{simulation_id}/markets/{market_name}/prices",
    response_model=dict[str, object],
    summary="Get prices from a PricedMarket",
)
def get_market_prices(simulation_id: str, market_name: str) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    m = _resolve_market(entry, market_name)
    if not isinstance(m, PricedMarket):
        raise HTTPException(status_code=400, detail=f"Market {market_name!r} does not implement PricedMarket")
    return {"prices": to_jsonable(m.get_prices(), include_type_tags=False)}


@router.get(
    "/{simulation_id}/markets/{market_name}/lp",
    response_model=dict[str, object],
    summary="Get LP state and positions from a LiquidityPool market",
)
def get_lp_state(simulation_id: str, market_name: str) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    m = _resolve_market(entry, market_name)
    if not isinstance(m, LiquidityPool):
        raise HTTPException(status_code=400, detail=f"Market {market_name!r} does not implement LiquidityPool")
    return {
        "lp_state": to_jsonable(m.get_lp_state(), include_type_tags=False),
        "positions": [to_jsonable(p, include_type_tags=False) for p in m.get_all_lp_positions()],
    }


def _resolve_market(entry: state.EngineEntry, market_name: str):
    market = entry.engine._market
    if isinstance(market, World):
        child = market.markets.get(market_name)
        if child is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Market {market_name!r} not found",
            )
        return child
    if market_name == "default":
        return market
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Single-market engine only has 'default'. Got {market_name!r}",
    )


# ── Agent state ───────────────────────────────────────────────────────────

@router.get(
    "/{simulation_id}/agents",
    response_model=schemas.AllAgentStatesResponse,
    summary="Get all agent states from a live engine",
)
def get_all_agent_states(simulation_id: str) -> schemas.AllAgentStatesResponse:
    entry = _get_entry(simulation_id)
    agents = {}
    for agent in entry.engine._agents:
        s = agent.state
        agents[s.agent_id] = schemas.AgentStateResponse(
            agent_id=s.agent_id,
            balances=to_jsonable(s.balances, include_type_tags=False),
            cumulative_volume=s.cumulative_volume,
            realized_pnl=s.realized_pnl,
        )
    return schemas.AllAgentStatesResponse(simulation_id=simulation_id, agents=agents)


@router.get(
    "/{simulation_id}/agents/{agent_id}",
    response_model=schemas.AgentStateResponse,
    summary="Get a specific agent's state",
)
def get_agent_state(simulation_id: str, agent_id: str) -> schemas.AgentStateResponse:
    entry = _get_entry(simulation_id)
    for agent in entry.engine._agents:
        if str(agent.agent_id) == agent_id:
            s = agent.state
            return schemas.AgentStateResponse(
                agent_id=s.agent_id,
                balances=to_jsonable(s.balances, include_type_tags=False),
                cumulative_volume=s.cumulative_volume,
                realized_pnl=s.realized_pnl,
            )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Agent {agent_id!r} not found",
    )


@router.get(
    "/{simulation_id}/agents/{agent_id}/timeline",
    response_model=dict[str, object],
    summary="Get one live agent's state across recorded rounds",
)
def get_agent_timeline(
    simulation_id: str,
    agent_id: str,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    snapshots = [
        round_snapshot_to_dict(snapshot)
        for snapshot in entry.engine._snapshots
        if (start is None or snapshot.round >= start) and (end is None or snapshot.round <= end)
    ]
    snapshots = snapshots[offset : offset + limit]
    return {
        "simulation_id": simulation_id,
        "run_id": entry.run_id,
        "agent_id": agent_id,
        "timeline": agent_timeline_from_rounds(snapshots, agent_id),
    }
