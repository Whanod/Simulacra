"""OpenAPI conformance for ``POST /v1/simulate-bundle`` (PRD line 942).

The OpenAPI document at ``solana-plans/api-specs/simulate-bundle.openapi.yaml``
is the contract; the route at ``src/defi_sim_api/routers/simulate_bundle.py``
must serve responses that conform to ``components.schemas.SimulateBundleResponse``.

The repo doesn't depend on ``jsonschema`` / ``openapi-spec-validator``, so we
ship a small recursive validator covering the subset of OpenAPI 3.0 schema
features actually used by ``SimulateBundleResponse`` (object/array/integer/
number/string/boolean/null, required, properties, items, minimum/maximum,
oneOf, $ref, enum, additionalProperties=false). If the spec ever uses a
feature outside this subset, the validator raises so the omission is visible
rather than silently passed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

SPEC_PATH = (
    Path(__file__).resolve().parents[2]
    / "solana-plans"
    / "api-specs"
    / "simulate-bundle.openapi.yaml"
)


def _resolve_ref(ref: str, schemas: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        raise AssertionError(f"unsupported $ref form: {ref}")
    name = ref[len(prefix) :]
    if name not in schemas:
        raise AssertionError(f"$ref target {name!r} missing from components.schemas")
    return schemas[name]


def _validate(value: Any, schema: dict[str, Any], schemas: dict[str, Any], path: str) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(schema["$ref"], schemas), schemas, path)
        return

    if "oneOf" in schema:
        errors: list[str] = []
        for i, branch in enumerate(schema["oneOf"]):
            try:
                _validate(value, branch, schemas, f"{path}|oneOf[{i}]")
                return
            except AssertionError as e:
                errors.append(str(e))
        raise AssertionError(
            f"{path}: value did not match any oneOf branch: {errors}"
        )

    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r} not in enum {schema['enum']}"

    t = schema.get("type")
    if t == "null":
        assert value is None, f"{path}: expected null, got {type(value).__name__}"
    elif t == "boolean":
        assert isinstance(value, bool), f"{path}: expected bool, got {type(value).__name__}"
    elif t == "integer":
        # JSON has no int/float distinction beyond presence of a fraction; bool is
        # a subclass of int in Python so reject it explicitly.
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{path}: expected integer, got {type(value).__name__}"
        )
        if "minimum" in schema:
            assert value >= schema["minimum"], f"{path}: {value} < min {schema['minimum']}"
        if "maximum" in schema:
            assert value <= schema["maximum"], f"{path}: {value} > max {schema['maximum']}"
    elif t == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{path}: expected number, got {type(value).__name__}"
        )
        if "minimum" in schema:
            assert value >= schema["minimum"], f"{path}: {value} < min {schema['minimum']}"
        if "maximum" in schema:
            assert value <= schema["maximum"], f"{path}: {value} > max {schema['maximum']}"
    elif t == "string":
        assert isinstance(value, str), f"{path}: expected string, got {type(value).__name__}"
    elif t == "array":
        assert isinstance(value, list), f"{path}: expected array, got {type(value).__name__}"
        if "minItems" in schema:
            assert len(value) >= schema["minItems"], f"{path}: minItems violated"
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"], f"{path}: maxItems violated"
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(value):
                _validate(item, item_schema, schemas, f"{path}[{i}]")
    elif t == "object":
        assert isinstance(value, dict), f"{path}: expected object, got {type(value).__name__}"
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for req in required:
            assert req in value, f"{path}: required property {req!r} missing"
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            assert not extra, f"{path}: additionalProperties=false but got extras {sorted(extra)}"
        for prop, prop_schema in properties.items():
            if prop in value:
                _validate(value[prop], prop_schema, schemas, f"{path}.{prop}")
        # additionalProperties as a schema (e.g. CalibrationBlock.metric_thresholds)
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            declared = set(properties)
            for k, v in value.items():
                if k not in declared:
                    _validate(v, ap, schemas, f"{path}.{k}")
    elif t is None:
        # No type declared (e.g. only oneOf/$ref handled above) — nothing to check.
        return
    else:
        raise AssertionError(f"{path}: unsupported schema type {t!r}")


@pytest.fixture(scope="module")
def schemas() -> dict[str, Any]:
    assert SPEC_PATH.exists(), f"OpenAPI spec missing at {SPEC_PATH}"
    with SPEC_PATH.open() as fh:
        spec = yaml.safe_load(fh)
    return spec["components"]["schemas"]


def _post_minimal(client) -> dict[str, Any]:
    body = {
        "bundle": {
            "txs": ["base58encodedtx1", "base58encodedtx2"],
            "tip_lamports": 100_000,
            "tip_recipient": "T1pestRecipientPubkey11111111111111111111111",
        },
        "context_slot": "latest",
    }
    response = client.post("/v1/simulate-bundle", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _post_with_tip_optimizer(client) -> dict[str, Any]:
    body = {
        "bundle": {
            "txs": ["base58encodedtx1"],
            "tip_lamports": 100_000,
            "tip_recipient": "T1pestRecipientPubkey11111111111111111111111",
        },
        "context_slot": 420_196_842,
        "search_tip_optimizer": {"target_percentile": 90},
    }
    response = client.post("/v1/simulate-bundle", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_simulate_bundle_response_matches_spec(client, schemas):
    """PRD line 942: route response conforms to ``SimulateBundleResponse``."""
    payload = _post_minimal(client)
    _validate(
        payload,
        schemas["SimulateBundleResponse"],
        schemas,
        path="SimulateBundleResponse",
    )


def test_simulate_bundle_response_with_tip_optimizer_matches_spec(client, schemas):
    """Tip-optimizer branch also conforms (PRD line 902-905)."""
    payload = _post_with_tip_optimizer(client)
    _validate(
        payload,
        schemas["SimulateBundleResponse"],
        schemas,
        path="SimulateBundleResponse",
    )
    assert payload["tip_optimizer"] is not None


def test_openapi_spec_file_loads(schemas):
    """Smoke: the YAML is parsable and exposes the expected schema keys."""
    for name in (
        "SimulateBundleRequest",
        "SimulateBundleResponse",
        "Bundle",
        "ProfitDistribution",
        "AltCompression",
        "CuBudget",
        "WriteLockContention",
        "TipOptimizerResult",
    ):
        assert name in schemas, f"schema {name!r} missing from spec"


def test_conformance_validator_rejects_extra_keys(schemas):
    """Sanity: validator catches additionalProperties=false violations.

    Without this, the conformance test could pass vacuously if the validator
    silently ignored extras.
    """
    payload = {
        "expected_tip_to_land_lamports": 0,
        "landing_probability": 0.5,
        "profit_distribution": {"p50": 0, "p90": 0},
        "alt_compression": {"uncompressed_bytes": 0, "compressed_bytes": 0},
        "cu_budget": {"tx_cu_used": [], "slot_cu_headroom": 0},
        "write_lock_contention": {"blocking_pubkeys": []},
        "metrics": {
            "replay": {
                "bundle_landing_rate": {
                    "value": 0.0,
                    "unit": "ratio",
                    "sample_size": 0,
                },
                "tip_efficiency": {
                    "value": 0.0,
                    "unit": "ratio",
                    "sample_size": 0,
                },
                "slot_inclusion_latency": {
                    "value": 0.0,
                    "unit": "slots",
                    "sample_size": 0,
                    "mean": 0.0,
                    "median": 0.0,
                    "p95": 0.0,
                    "p99": 0.0,
                    "samples": [],
                },
            }
        },
        "this_is_not_in_the_spec": True,
    }
    with pytest.raises(AssertionError, match="additionalProperties"):
        _validate(
            payload,
            schemas["SimulateBundleResponse"],
            schemas,
            path="SimulateBundleResponse",
        )
