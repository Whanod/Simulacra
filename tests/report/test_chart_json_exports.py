from __future__ import annotations

import json

import pandas as pd

from defi_sim.report.charts import (
    figure_to_json,
    figure_to_plotly_dict,
    heatmap,
    heatmap_dict,
    leaderboard,
    leaderboard_json,
)


def test_chart_helpers_expose_in_memory_plotly_payloads():
    df = pd.DataFrame(
        [
            {"agent": "alice", "score": 10, "x": "low", "y": "tight", "value": 1.2},
            {"agent": "bob", "score": 15, "x": "high", "y": "tight", "value": 2.4},
        ]
    )

    leader = leaderboard(df, "agent", "score")
    leader_dict = figure_to_plotly_dict(leader)
    leader_json = figure_to_json(leader)
    grid_dict = heatmap_dict(df, "x", "y", "value")

    assert leader_dict["data"][0]["type"] == "bar"
    assert json.loads(leader_json)["data"][0]["type"] == "bar"
    assert grid_dict["data"][0]["type"] == "heatmap"


def test_chart_builder_specific_json_wrapper_returns_plotly_json():
    df = pd.DataFrame(
        [
            {"agent": "alice", "score": 10},
            {"agent": "bob", "score": 15},
        ]
    )

    payload = json.loads(leaderboard_json(df, "agent", "score"))
    assert payload["data"][0]["type"] == "bar"
