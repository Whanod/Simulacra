"""Registry / catalog endpoints — expose available types to the frontend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.specs import (
    EntityMetadata,
    RunSpec,
    _CATEGORY_TABLES,
    iter_registry_categories,
)

from defi_sim_api.schemas import (
    RegistryCategoryDefinition,
    RegistryContractResponse,
    RegistryEntityDefinition,
    RunSpecSchema,
    SpecValidationResponse,
)

router = APIRouter(prefix="/registry", tags=["registry"])


# Contract version stamped on every enriched response (BE-006 tightens
# this into a contract test). Bumping this value signals to the
# frontend that a new contract shape has shipped — the frontend's
# SUPPORTED_CONTRACT_VERSION must be updated in lockstep.
REGISTRY_CONTRACT_VERSION = "v2"


# Presentation metadata used when an entity is registered without an
# EntityMetadata object. Keeps category labels stable across the
# legacy-vs-enriched transition.
_CATEGORY_LABELS: dict[str, str] = {
    "markets": "Markets",
    "agents": "Agents",
    "clocks": "Clocks",
    "orderings": "Ordering",
    "gas_models": "Cost Models",
    "fee_models": "Fee Models",
    "feeds": "Feeds",
    "execution_models": "Execution",
    "information_filters": "Information",
}

_CATEGORY_KEY_PREFIX: dict[str, str] = {
    "markets": "reg-markets",
    "agents": "reg-agents",
    "clocks": "reg-clocks",
    "orderings": "reg-ordering",
    "gas_models": "reg-gas",
    "fee_models": "reg-fees",
    "feeds": "reg-feeds",
    "execution_models": "reg-exec",
    "information_filters": "reg-information",
}


def _title_case(value: str) -> str:
    return " ".join(
        word[:1].upper() + word[1:] for word in value.replace("_", " ").split() if word
    )


def _entity_from_metadata(
    category: str, spec_type: str, meta: EntityMetadata | None
) -> RegistryEntityDefinition:
    if meta is None:
        return RegistryEntityDefinition(
            category=category,
            type=spec_type,
            label=_title_case(spec_type),
        )
    return RegistryEntityDefinition(
        category=category,
        type=spec_type,
        label=meta.label,
        description=meta.description,
        badges=list(meta.badges) if meta.badges else None,
        builder_supported=meta.builder_supported,
        schema=meta.schema,
        ui_schema=meta.ui_schema,
        defaults=meta.defaults,
        examples=list(meta.examples) if meta.examples else None,
        metadata=meta.metadata,
    )


def _build_category_definition(category: str) -> RegistryCategoryDefinition:
    tables = _CATEGORY_TABLES[category]
    factories, meta_table = tables
    entities = [
        _entity_from_metadata(category, spec_type, meta_table.get(spec_type))
        for spec_type in sorted(factories.keys())
    ]
    order = iter_registry_categories().index(category)
    return RegistryCategoryDefinition(
        key=_CATEGORY_KEY_PREFIX.get(category, f"reg-{category}"),
        label=_CATEGORY_LABELS.get(category, _title_case(category)),
        order=order,
        entities=entities,
    )


def _build_contract_response() -> RegistryContractResponse:
    return RegistryContractResponse(
        contract_version=REGISTRY_CONTRACT_VERSION,
        categories=[_build_category_definition(cat) for cat in iter_registry_categories()],
    )


@router.get(
    "",
    summary="List all registry categories and their registered types",
)
def list_all() -> dict:
    """Return the enriched v2 registry contract."""
    return _build_contract_response().model_dump(by_alias=True, exclude_none=True)


@router.get(
    "/{category}",
    summary="List registered types for a category",
)
def list_category(category: str) -> dict:
    if category not in _CATEGORY_TABLES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown registry category {category!r}. Available: {sorted(_CATEGORY_TABLES)}",
        )
    return _build_category_definition(category).model_dump(by_alias=True, exclude_none=True)


@router.post(
    "/validate",
    response_model=SpecValidationResponse,
    summary="Validate a RunSpec without executing it",
)
def validate_spec(body: RunSpecSchema) -> SpecValidationResponse:
    errors: list[str] = []
    try:
        spec = RunSpec.from_dict(body.model_dump(exclude_none=True))
    except Exception as exc:
        errors.append(str(exc))
        return SpecValidationResponse(valid=False, errors=errors)

    from defi_sim.engine.specs import build_agents, build_market, build_simulation_config

    try:
        build_market(spec.market)
        build_agents(spec.agents)
        build_simulation_config(spec)
    except Exception as exc:
        errors.append(str(exc))

    return SpecValidationResponse(valid=len(errors) == 0, errors=errors)
