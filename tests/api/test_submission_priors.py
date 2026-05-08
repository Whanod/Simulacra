"""PRD US-004 line 383: integration test for the synthetic-calibration marker.

Loads a Solana-flavored experiment template via the API, runs it,
and asserts the resulting run metadata surfaces both the structured
``submission_priors`` block and the consumer-facing
``priors_calibrated_at`` marker. With the synthetic defaults the marker
must read ``"synthetic"`` and the inner ``calibrated_at`` field must be
``None``; calibration lands in Phase 2.1.
"""

from __future__ import annotations

from defi_sim_api.backend.templates import experiment_templates


def test_run_emits_synthetic_calibration_marker(client) -> None:
    template = next(
        t for t in experiment_templates() if t["template_id"] == "solana-sandwich-stress"
    )
    spec = {**template["base_spec"], "num_rounds": 2}
    assert spec["execution"]["type"] == "solana_like"

    resp = client.post("/simulations/run", json=spec)
    assert resp.status_code == 200, resp.text
    metadata = resp.json()["result"]["metadata"]

    assert "submission_priors" in metadata, (
        f"expected submission_priors in run metadata; got keys {sorted(metadata)}"
    )
    priors = metadata["submission_priors"]
    assert priors["calibrated_at"] is None
    assert metadata["priors_calibrated_at"] == "synthetic"
