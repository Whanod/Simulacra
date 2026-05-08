"""Regression tests for US-008's JSON-RPC translation layer."""

from __future__ import annotations

from typing import Any

from defi_sim_api.routers import simulate_bundle


def _fake_bundle_response() -> simulate_bundle.SimulateBundleResponse:
    return simulate_bundle.SimulateBundleResponse(
        expected_tip_to_land_lamports=95_000,
        landing_probability=0.87,
        profit_distribution=simulate_bundle.ProfitDistributionModel(
            p50=200_000,
            p90=50_000,
        ),
        alt_compression=simulate_bundle.AltCompressionModel(
            uncompressed_bytes=1500,
            compressed_bytes=800,
        ),
        cu_budget=simulate_bundle.CuBudgetModel(
            tx_cu_used=[200_000],
            slot_cu_headroom=47_800_000,
        ),
        write_lock_contention=simulate_bundle.WriteLockContentionModel(),
        tip_optimizer=None,
        calibration=None,
        metrics=simulate_bundle.MetricsBlock(
            replay=simulate_bundle.ReplayMetricsBlock(
                bundle_landing_rate={
                    "value": 0.87,
                    "unit": "ratio",
                    "sample_size": 100,
                },
                tip_efficiency={
                    "value": 0.5,
                    "unit": "ratio",
                    "sample_size": 1,
                },
                slot_inclusion_latency={
                    "value": 0,
                    "unit": "slots",
                    "sample_size": 1,
                    "mean": 0,
                    "median": 0,
                    "p95": 0,
                    "p99": 0,
                    "samples": [0],
                },
            )
        ),
    )


def test_simulate_transaction_calls_simulate_bundle_handler(client, monkeypatch):
    calls: list[simulate_bundle.SimulateBundleRequest] = []

    def fake_internal(
        request: simulate_bundle.SimulateBundleRequest,
    ) -> simulate_bundle.SimulateBundleResponse:
        calls.append(request)
        return _fake_bundle_response()

    monkeypatch.setattr(simulate_bundle, "simulate_bundle_internal", fake_internal)

    response = client.post(
        "/solana-rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [
                "base58encodedtx",
                {
                    "contextSlot": 250_000_000,
                    "tipLamports": 123_000,
                    "tipRecipient": "TipRecipient111111111111111111111111111111",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert "error" not in body
    assert body["id"] == 1
    assert body["result"]["context"]["slot"] == 250_000_000
    assert body["result"]["value"]["err"] is None
    assert body["result"]["value"]["unitsConsumed"] == 200_000

    assert len(calls) == 1
    delegated = calls[0]
    assert delegated.bundle.txs == ["base58encodedtx"]
    assert delegated.bundle.tip_lamports == 123_000
    assert delegated.bundle.tip_recipient == "TipRecipient111111111111111111111111111111"
    assert delegated.context_slot == 250_000_000
