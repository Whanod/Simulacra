"""Tests for the experiment template catalog."""

from __future__ import annotations

import logging

from defi_sim_api.backend.templates import (
    LEGACY_TEMPLATE_ALIASES,
    experiment_templates,
    find_template,
    resolve_template_id,
)


def _iter_token_lists(spec: dict) -> list[list[dict]]:
    market = spec.get("market", {})
    if market.get("type") == "world":
        return [m.get("tokens", []) for m in market.get("markets", {}).values() if m.get("tokens")]
    return [market.get("tokens", [])]


def test_solana_templates_use_sol_usdc() -> None:
    templates = experiment_templates()
    assert templates, "expected at least one template"

    for template in templates:
        for tokens in _iter_token_lists(template["base_spec"]):
            by_id = {t["id"]: t for t in tokens}
            assert "SOL" in by_id, f"{template['template_id']} missing SOL token"
            assert "USDC" in by_id, f"{template['template_id']} missing USDC token"
            assert by_id["SOL"]["decimals"] == 9, f"{template['template_id']} SOL decimals must be 9"
            assert by_id["USDC"]["decimals"] == 6, f"{template['template_id']} USDC decimals must be 6"
            assert "YES" not in by_id and "NO" not in by_id, (
                f"{template['template_id']} still references YES/NO prediction-market tokens"
            )


def test_templates_collateral_token_is_usdc_when_supported() -> None:
    for template in experiment_templates():
        market = template["base_spec"].get("market", {})
        if market.get("type") == "cfamm":
            assert market["params"]["collateral_token"] == "USDC", (
                f"{template['template_id']} cfamm should quote in USDC"
            )
        elif market.get("type") == "world":
            for sub in market.get("markets", {}).values():
                if sub.get("type") == "cfamm":
                    assert sub["params"]["collateral_token"] == "USDC"


def test_template_editable_fields_do_not_reference_legacy_collateral() -> None:
    for template in experiment_templates():
        for path in template["editable_fields"]:
            assert ".COLLATERAL" not in path, (
                f"{template['template_id']} editable field {path} still references legacy COLLATERAL"
            )


def test_template_ids_are_solana_named() -> None:
    expected = {
        "whirlpool-fee-tuning",
        "solana-sandwich-stress",
        "dlmm-bin-sustainability",
        "raydium-vs-whirlpool-arb",
    }
    actual = {t["template_id"] for t in experiment_templates()}
    assert expected.issubset(actual), (
        f"missing Solana-named templates: {expected - actual} (got {actual})"
    )


def test_each_template_has_synthetic_mode_flag() -> None:
    """Every template must explicitly declare ``synthetic_mode``.

    Templates whose underlying math + state has been replaced with a real
    on-chain capture (per ``solana-plans/synthetic-mode-tracker.md``) flip
    to ``False``; the rest stay ``True`` until their blockers ship.
    """
    templates = experiment_templates()
    assert templates, "expected at least one template"
    for template in templates:
        flag = template.get("synthetic_mode")
        assert isinstance(flag, bool), (
            f"{template['template_id']} must declare a boolean synthetic_mode"
        )


def test_each_template_names_its_math_model() -> None:
    allowed = {"l2_norm_cfamm", "clob", "xy_k", "clmm", "dlmm"}
    for template in experiment_templates():
        if template.get("synthetic_mode") is True:
            model = template.get("synthetic_math_model")
            assert model in allowed, (
                f"{template['template_id']} synthetic_math_model={model!r} not in {allowed}"
            )
            assert model is not None, (
                f"{template['template_id']} flagged synthetic but no synthetic_math_model named"
            )
        else:
            # Real-state templates can either omit ``synthetic_math_model`` or
            # explicitly set it to ``None`` — both are valid; they must NOT
            # claim a synthetic math model when they're running real math.
            model = template.get("synthetic_math_model")
            assert model is None, (
                f"{template['template_id']} is synthetic_mode=False but still names "
                f"a synthetic math model {model!r}"
            )


def test_each_template_has_non_transferable_conclusions() -> None:
    for template in experiment_templates():
        conclusions = template.get("non_transferable_conclusions")
        assert isinstance(conclusions, list) and conclusions, (
            f"{template['template_id']} must declare a non-empty non_transferable_conclusions list"
        )
        for entry in conclusions:
            assert isinstance(entry, str) and entry.strip(), (
                f"{template['template_id']} non_transferable_conclusions entries must be non-empty strings"
            )


def test_get_templates_endpoint_includes_synthetic_mode(client) -> None:
    response = client.get("/templates/experiments")
    assert response.status_code == 200
    payload = response.json()
    templates = payload["templates"]
    assert templates, "endpoint returned no templates"
    for template in templates:
        assert isinstance(template["synthetic_mode"], bool), (
            f"{template['template_id']} missing synthetic_mode in API response"
        )
        if template["synthetic_mode"]:
            assert template["synthetic_math_model"] in {
                "l2_norm_cfamm",
                "clob",
                "xy_k",
                "clmm",
                "dlmm",
            }
        else:
            assert template["synthetic_math_model"] is None
        conclusions = template["non_transferable_conclusions"]
        assert isinstance(conclusions, list) and conclusions
        assert all(isinstance(c, str) and c for c in conclusions)


def test_legacy_aliases_resolve() -> None:
    """Old chain-neutral template IDs resolve to the new Solana-named canonicals."""
    assert LEGACY_TEMPLATE_ALIASES == {
        "amm-fee-tuning": "whirlpool-fee-tuning",
        "mev-stress-test": "solana-sandwich-stress",
        "lp-sustainability": "dlmm-bin-sustainability",
        "cross-market-arbitrage": "raydium-vs-whirlpool-arb",
    }
    canonical_ids = {t["template_id"] for t in experiment_templates()}
    for alias, canonical in LEGACY_TEMPLATE_ALIASES.items():
        assert canonical in canonical_ids, (
            f"alias {alias!r} resolves to {canonical!r} but no such template exists"
        )
        assert resolve_template_id(alias) == canonical


def test_resolve_template_id_passes_unknown_through() -> None:
    """Unknown IDs return as-is so callers can decide whether to 404."""
    assert resolve_template_id("not-a-real-template") == "not-a-real-template"
    assert resolve_template_id("whirlpool-fee-tuning") == "whirlpool-fee-tuning"


def test_resolve_template_id_logs_deprecation_warning(
    caplog: "logging.LogCaptureFixture",
) -> None:
    with caplog.at_level(logging.WARNING, logger="defi_sim_api.backend.templates"):
        resolve_template_id("amm-fee-tuning")
    assert any(
        "deprecated alias" in record.message and "amm-fee-tuning" in record.message
        for record in caplog.records
    )


def test_find_template_resolves_alias_to_canonical() -> None:
    via_alias = find_template("amm-fee-tuning")
    via_canonical = find_template("whirlpool-fee-tuning")
    assert via_alias is not None
    assert via_canonical is not None
    assert via_alias["template_id"] == "whirlpool-fee-tuning"
    assert via_alias == via_canonical
    assert find_template("not-a-real-template") is None


def test_loading_template_produces_runnable_spec(client) -> None:
    for template in experiment_templates():
        spec = template["base_spec"]
        response = client.post("/simulations/build", json=spec)
        assert response.status_code == 201, (
            f"{template['template_id']} failed to build: {response.status_code} {response.text}"
        )
        sim_id = response.json()["simulation_id"]
        step = client.post(f"/simulations/{sim_id}/step")
        assert step.status_code == 200, (
            f"{template['template_id']} failed to step: {step.status_code} {step.text}"
        )
