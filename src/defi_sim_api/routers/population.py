"""Population builder endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.agents.population import PopulationBuilder, PopulationConfig
from defi_sim.core.types import Token
from defi_sim.engine.json import to_jsonable

from defi_sim_api.schemas import PopulationBuildRequest, PopulationBuildResponse

router = APIRouter(prefix="/population", tags=["population"])


@router.post(
    "/build",
    response_model=PopulationBuildResponse,
    summary="Build an agent population from a declarative mix config",
)
def build_population(body: PopulationBuildRequest) -> PopulationBuildResponse:
    try:
        config = PopulationConfig(
            mix=body.mix,
            total_agents=body.total_agents,
            default_collateral=body.default_collateral,
            role_params=body.role_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    token = Token(
        id=body.collateral_token.id,
        symbol=body.collateral_token.symbol,
        decimals=body.collateral_token.decimals,
    )

    import numpy as np

    rng = np.random.default_rng(body.seed)
    agents = PopulationBuilder.build(config, collateral_token=token, rng=rng)

    agent_dicts = []
    for agent in agents:
        agent_dicts.append({
            "agent_id": agent.agent_id,
            "type": agent.__class__.__name__,
            "role": to_jsonable(agent.state.role, include_type_tags=False),
            "balances": to_jsonable(agent.state.balances, include_type_tags=False),
        })
    return PopulationBuildResponse(agents=agent_dicts)


@router.get(
    "/roles",
    response_model=list[str],
    summary="List available population roles",
)
def list_roles() -> list[str]:
    return sorted(PopulationBuilder._factories.keys())
