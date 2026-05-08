"""``GET /v1/calibration/corpus`` tests (PRD US-004 line 787).

The endpoint backs the ``/calibration`` dashboard's per-corpus-slot scoreboard,
threshold table, last-run timestamp per slot, and per-metric trend marker.
"""

from __future__ import annotations

from defi_sim_solana.replay.slot_client import clear_slot_cache

CORPUS_SLOT = 160_000_001


def test_corpus_endpoint_lists_committed_slots(client) -> None:
    response = client.get("/v1/calibration/corpus")
    assert response.status_code == 200, response.text
    body = response.json()

    assert "corpus_root" in body
    assert "thresholds_yaml" in body

    slots = body["slots"]
    assert isinstance(slots, list) and slots, "corpus directory has fixtures committed"
    slot_numbers = [s["slot"] for s in slots]
    assert CORPUS_SLOT in slot_numbers
    # Sorted ascending so the dashboard renders deterministic ordering.
    assert slot_numbers == sorted(slot_numbers)

    sample = next(s for s in slots if s["slot"] == CORPUS_SLOT)
    assert isinstance(sample["programs"], list) and sample["programs"]
    assert isinstance(sample["expected"], dict)
    assert sample["last_run"] is None
    assert sample["trend"] == []
    assert sample["run_count"] == 0


def test_corpus_endpoint_exposes_threshold_table(client) -> None:
    response = client.get("/v1/calibration/corpus")
    assert response.status_code == 200, response.text
    body = response.json()
    thresholds = body["thresholds"]
    assert isinstance(thresholds, list) and thresholds
    metrics = {row["metric"] for row in thresholds}
    # The PRD-required threshold metrics must all surface so the dashboard
    # can show them per slot (PRD line 770-779).
    assert {"pool_price", "lp_balance", "liquidations_triggered"}.issubset(metrics)
    for row in thresholds:
        # Exactly one of the two bound fields is set per row.
        rel = row["threshold_relative"]
        absolute = row["threshold_absolute"]
        assert (rel is None) ^ (absolute is None)


def test_corpus_endpoint_attaches_last_run_after_replay(client) -> None:
    """After a replay against a corpus slot, the dashboard reflects the run.

    Validates the "last-run timestamp per slot" deliverable on PRD line 787.
    """

    clear_slot_cache()
    replay_resp = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert replay_resp.status_code == 200, replay_resp.text
    run_id = replay_resp.json()["run_id"]

    response = client.get("/v1/calibration/corpus")
    assert response.status_code == 200, response.text
    body = response.json()
    sample = next(s for s in body["slots"] if s["slot"] == CORPUS_SLOT)
    assert sample["run_count"] >= 1
    assert sample["last_run"] is not None
    assert sample["last_run"]["run_id"] == run_id
    assert isinstance(sample["last_run"]["created_at"], str)
    # Replay artifacts now persist replay_diff; with only one run the dashboard
    # has a latest error but no prior point to compare against.
    trends = {row["metric"]: row for row in sample["trend"]}
    assert trends["tips_paid"] == {
        "metric": "tips_paid",
        "latest_abs_error": 0.0,
        "delta": None,
        "direction": "no_history",
    }
    assert "bundle_landing_rate" in trends
