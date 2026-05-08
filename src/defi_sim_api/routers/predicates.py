"""Predicate builder and evaluation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from defi_sim.core.types import (
    AndPredicate,
    NotPredicate,
    OrPredicate,
    Predicate,
    ThresholdPredicate,
)

from defi_sim_api.schemas import PredicateEvalRequest, PredicateEvalResponse, PredicateSchema

router = APIRouter(prefix="/predicates", tags=["predicates"])


def _build_predicate(schema: PredicateSchema) -> Predicate:
    if schema.type == "threshold":
        return ThresholdPredicate(
            field=schema.params.get("field", ""),
            source=schema.params.get("source", "market"),
            op=schema.params.get("op", "<"),
            threshold=schema.params.get("threshold", 0),
        )
    if schema.type == "and":
        if not schema.children:
            raise HTTPException(status_code=422, detail="'and' predicate requires children")
        return AndPredicate(children=tuple(_build_predicate(c) for c in schema.children))
    if schema.type == "or":
        if not schema.children:
            raise HTTPException(status_code=422, detail="'or' predicate requires children")
        return OrPredicate(children=tuple(_build_predicate(c) for c in schema.children))
    if schema.type == "not":
        if schema.child is None:
            raise HTTPException(status_code=422, detail="'not' predicate requires child")
        return NotPredicate(child=_build_predicate(schema.child))
    raise HTTPException(status_code=422, detail=f"Unknown predicate type: {schema.type!r}")


@router.post(
    "/build",
    response_model=dict[str, object],
    summary="Build and serialize a predicate from a schema",
)
def build_predicate(body: PredicateSchema) -> dict[str, object]:
    pred = _build_predicate(body)
    return {"predicate": pred.to_dict()}


@router.post(
    "/evaluate",
    response_model=PredicateEvalResponse,
    summary="Evaluate a predicate against market and agent state",
)
def evaluate_predicate(body: PredicateEvalRequest) -> PredicateEvalResponse:
    pred = _build_predicate(body.predicate)
    result = pred.evaluate(body.market_state, body.agent_state)
    return PredicateEvalResponse(result=result)
