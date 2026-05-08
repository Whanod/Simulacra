"""Order book introspection for CLOB markets."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.world import World
from defi_sim.markets.clob import ClobMarket

from defi_sim_api import schemas, state

router = APIRouter(prefix="/simulations", tags=["orderbook"])


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


def _find_clob(entry: state.EngineEntry, market_name: str | None) -> ClobMarket:
    market = entry.engine._market
    if isinstance(market, World):
        if market_name is None:
            # Find the first CLOB market
            for name, m in market.markets.items():
                if isinstance(m, ClobMarket):
                    return m
            raise HTTPException(status_code=404, detail="No CLOB market found in world")
        child = market.markets.get(market_name)
        if child is None:
            raise HTTPException(status_code=404, detail=f"Market {market_name!r} not found")
        if not isinstance(child, ClobMarket):
            raise HTTPException(status_code=400, detail=f"Market {market_name!r} is not a CLOB")
        return child
    if not isinstance(market, ClobMarket):
        raise HTTPException(status_code=400, detail="Engine market is not a CLOB")
    return market


@router.get(
    "/{simulation_id}/orderbook",
    response_model=schemas.OrderBookResponse,
    summary="Get order book state for all pairs in a CLOB market",
)
def get_orderbook(
    simulation_id: str,
    market_name: str | None = None,
) -> schemas.OrderBookResponse:
    entry = _get_entry(simulation_id)
    clob = _find_clob(entry, market_name)
    books = {}
    for (base, quote), book in clob._books.items():
        key = f"{base}:{quote}"
        books[key] = book.to_dict()
    return schemas.OrderBookResponse(simulation_id=simulation_id, books=books)
