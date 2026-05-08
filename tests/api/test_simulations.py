"""Simulation lifecycle endpoint tests."""

from __future__ import annotations

from defi_sim.core.types import SwapAction
from defi_sim.engine.gas import DEFAULT_CU_LIMITS, ComputeUnitCost
from defi_sim_api.backend.templates import experiment_templates

from tests.api.conftest import CFAMM_SPEC, WORLD_SPEC


def test_full_run_solana_template(client):
    """Solana template runs end-to-end; per-action cost matches the real Solana fee formula."""
    template = next(
        t for t in experiment_templates() if t["template_id"] == "solana-sandwich-stress"
    )
    spec = {**template["base_spec"], "num_rounds": 3}

    assert spec["execution"]["type"] == "solana_like"
    assert spec["execution"]["gas_model"]["type"] == "compute_unit"

    resp = client.post("/simulations/run", json=spec)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["num_rounds_executed"] == 3

    cu_cost = ComputeUnitCost()

    baseline = SwapAction(agent_id="x")
    assert cu_cost.cost(baseline, 0) == 5_000

    cu_limit = DEFAULT_CU_LIMITS[SwapAction]
    priced = SwapAction(
        agent_id="x",
        compute_unit_limit=cu_limit,
        compute_unit_price_micro_lamports=1_000_000,
    )
    assert cu_cost.cost(priced, 0) == 5_000 + cu_limit

    multi_signer = SwapAction(agent_id="x", num_required_signatures=3)
    assert cu_cost.cost(multi_signer, 0) == 15_000

    breakdown = cu_cost.breakdown(priced, 0)
    assert breakdown.base_fee_lamports == 5_000
    assert breakdown.base_fee_burned_lamports == 2_500
    assert breakdown.base_fee_to_validator_lamports == 2_500
    assert breakdown.priority_fee_lamports == cu_limit
    assert breakdown.total_lamports == 5_000 + cu_limit


def test_solana_template_emits_slot_skipped_events(client):
    """Solana template under skip_rate=0.1 emits at least one slot_skipped event over 1000 slots.

    With p=0.1 over 1000 slots, P(zero skips) = 0.9**1000 ≈ 1.7e-46 — effectively
    impossible — so the assertion is robust regardless of seed.
    """
    template = next(
        t for t in experiment_templates() if t["template_id"] == "solana-sandwich-stress"
    )
    spec = {
        **template["base_spec"],
        "clock": {
            "type": "solana_slot",
            "params": {
                "slot_duration_seconds": 0.4,
                "epoch_length_slots": 432_000,
                "skip_rate": 0.1,
            },
        },
        "num_rounds": 1000,
        "snapshot_interval": 100,
    }

    resp = client.post("/simulations/run", json=spec)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    events_resp = client.get(
        f"/runs/{run_id}/events",
        params={"event_type": "SLOT_SKIPPED", "limit": 5000},
    )
    assert events_resp.status_code == 200, events_resp.text
    skipped = events_resp.json()["events"]
    assert len(skipped) >= 1, f"expected ≥1 SLOT_SKIPPED event, got {len(skipped)}"


# ── POST /simulations/run ────────────────────────────────────────────────

class TestRunSimulation:
    def test_run_cfamm_returns_result(self, client):
        resp = client.post("/simulations/run", json=CFAMM_SPEC)
        assert resp.status_code == 200
        body = resp.json()
        result = body["result"]
        assert result["num_rounds"] == 5
        assert result["num_rounds_executed"] == 5
        assert result["seed"] == 42
        assert "noise-1" in result["agent_final_states"]

    def test_run_world_spec(self, client):
        resp = client.post("/simulations/run", json=WORLD_SPEC)
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["num_rounds_executed"] == 3
        snapshots = result["round_snapshots"]
        assert snapshots[0]["all_market_states"] is not None

    def test_run_with_fee_model(self, client):
        spec = {
            **CFAMM_SPEC,
            "market": {
                **CFAMM_SPEC["market"],
                "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
            },
        }
        resp = client.post("/simulations/run", json=spec)
        assert resp.status_code == 200

    def test_run_with_execution_model(self, client):
        spec = {
            **CFAMM_SPEC,
            "execution": {
                "type": "batch",
                "ordering": {"type": "random"},
                "gas_model": {"type": "fixed", "params": {"cost_per_action": 5}},
            },
        }
        resp = client.post("/simulations/run", json=spec)
        assert resp.status_code == 200

    def test_run_with_clock(self, client):
        spec = {
            **CFAMM_SPEC,
            "clock": {"type": "block", "params": {"block_time": 12, "epoch_length": 5}},
        }
        resp = client.post("/simulations/run", json=spec)
        assert resp.status_code == 200
        result = resp.json()["result"]
        # With block_time=12, round 1 timestamp should be 12
        assert result["round_snapshots"][0]["timestamp"] == 12

    def test_run_invalid_spec_returns_422(self, client):
        resp = client.post("/simulations/run", json={"not": "valid"})
        assert resp.status_code == 422


# ── POST /simulations/build + step lifecycle ─────────────────────────────

class TestEngineLifecycle:
    def test_build_creates_engine(self, client):
        resp = client.post("/simulations/build", json=CFAMM_SPEC)
        assert resp.status_code == 201
        body = resp.json()
        assert "simulation_id" in body
        assert body["current_round"] == 0
        assert body["is_complete"] is False

    def test_step_advances_one_round(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        step_resp = client.post(f"/simulations/{sim_id}/step")
        assert step_resp.status_code == 200
        body = step_resp.json()
        assert body["round"] == 1
        assert body["is_complete"] is False

    def test_step_all_rounds_to_completion(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        for i in range(1, 6):
            resp = client.post(f"/simulations/{sim_id}/step")
            assert resp.status_code == 200
            assert resp.json()["round"] == i

        # Engine should now be complete
        resp = client.post(f"/simulations/{sim_id}/step")
        assert resp.status_code == 409

    def test_status_reflects_progress(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        status = client.get(f"/simulations/{sim_id}/status").json()
        assert status["current_round"] == 0
        assert status["is_complete"] is False

        client.post(f"/simulations/{sim_id}/step")
        status = client.get(f"/simulations/{sim_id}/status").json()
        assert status["current_round"] == 1

    def test_cancel_sets_cancelled_flag(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        cancel_resp = client.post(f"/simulations/{sim_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["cancelled"] is True

        status = client.get(f"/simulations/{sim_id}/status").json()
        assert status["cancelled"] is True

    def test_delete_removes_engine(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        del_resp = client.delete(f"/simulations/{sim_id}")
        assert del_resp.status_code == 204

        status_resp = client.get(f"/simulations/{sim_id}/status")
        assert status_resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/simulations/does_not_exist")
        assert resp.status_code == 404

    def test_list_simulations(self, client):
        assert client.get("/simulations").json() == []

        build1 = client.post("/simulations/build", json=CFAMM_SPEC).json()
        build2 = client.post("/simulations/build", json=CFAMM_SPEC).json()

        ids = client.get("/simulations").json()
        assert build1["simulation_id"] in ids
        assert build2["simulation_id"] in ids


# ── 404 on unknown simulation ────────────────────────────────────────────

class TestNotFound:
    def test_step_unknown_id(self, client):
        resp = client.post("/simulations/unknown/step")
        assert resp.status_code == 404

    def test_status_unknown_id(self, client):
        resp = client.get("/simulations/unknown/status")
        assert resp.status_code == 404

    def test_cancel_unknown_id(self, client):
        resp = client.post("/simulations/unknown/cancel")
        assert resp.status_code == 404

    def test_snapshot_unknown_id(self, client):
        resp = client.post("/simulations/unknown/snapshot")
        assert resp.status_code == 404

    def test_restore_unknown_id(self, client):
        resp = client.post(
            "/simulations/unknown/restore",
            json={"snapshot_bytes_hex": "deadbeef"},
        )
        assert resp.status_code == 404


# ── Snapshot / restore ────────────────────────────────────────────────────

class TestSnapshotRestore:
    def test_snapshot_returns_hex(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        snap_resp = client.post(f"/simulations/{sim_id}/snapshot")
        assert snap_resp.status_code == 200
        hex_str = snap_resp.json()["snapshot_bytes_hex"]
        assert len(hex_str) > 0
        # Should be valid hex
        bytes.fromhex(hex_str)

    def test_snapshot_restore_roundtrip(self, client):
        build_resp = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id = build_resp.json()["simulation_id"]

        # Step twice
        client.post(f"/simulations/{sim_id}/step")
        client.post(f"/simulations/{sim_id}/step")
        assert client.get(f"/simulations/{sim_id}/status").json()["current_round"] == 2

        # Snapshot
        snap_hex = client.post(f"/simulations/{sim_id}/snapshot").json()["snapshot_bytes_hex"]

        # Build a fresh engine and restore
        build2 = client.post("/simulations/build", json=CFAMM_SPEC)
        sim_id2 = build2.json()["simulation_id"]

        restore_resp = client.post(
            f"/simulations/{sim_id2}/restore",
            json={"snapshot_bytes_hex": snap_hex},
        )
        assert restore_resp.status_code == 200
        assert restore_resp.json()["restored"] is True
        assert restore_resp.json()["current_round"] == 2
