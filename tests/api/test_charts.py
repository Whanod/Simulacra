"""Chart generation endpoint tests."""

from __future__ import annotations


SAMPLE_DATA = [
    {"agent": "A", "score": 10, "metric": 1.0, "x": 1, "y": 1, "val": 5},
    {"agent": "A", "score": 12, "metric": 2.0, "x": 1, "y": 2, "val": 8},
    {"agent": "B", "score": 20, "metric": 3.0, "x": 2, "y": 1, "val": 3},
    {"agent": "B", "score": 18, "metric": 4.0, "x": 2, "y": 2, "val": 9},
]


class TestLeaderboard:
    def test_returns_plotly_chart(self, client):
        resp = client.post("/charts/leaderboard", json={
            "data": SAMPLE_DATA,
            "group_col": "agent",
            "score_col": "score",
            "title": "Test Leaderboard",
        })
        assert resp.status_code == 200
        chart = resp.json()["chart"]
        assert "data" in chart
        assert "layout" in chart

    def test_custom_title(self, client):
        resp = client.post("/charts/leaderboard", json={
            "data": SAMPLE_DATA,
            "group_col": "agent",
            "score_col": "score",
            "title": "My Custom Title",
        })
        assert resp.status_code == 200
        layout = resp.json()["chart"]["layout"]
        assert layout["title"]["text"] == "My Custom Title"


class TestBoxPlot:
    def test_returns_plotly_chart(self, client):
        resp = client.post("/charts/box-plot", json={
            "data": SAMPLE_DATA,
            "group_col": "agent",
            "metric_col": "metric",
        })
        assert resp.status_code == 200
        chart = resp.json()["chart"]
        assert "data" in chart
        assert len(chart["data"]) == 2  # Two groups


class TestTimeSeries:
    def test_simple_series(self, client):
        resp = client.post("/charts/time-series", json={
            "series": [1.0, 2.0, 3.0, 4.0, 5.0],
        })
        assert resp.status_code == 200
        chart = resp.json()["chart"]
        assert "data" in chart

    def test_with_confidence_bands(self, client):
        resp = client.post("/charts/time-series", json={
            "series": [1.0, 2.0, 3.0],
            "ci_low": [0.5, 1.5, 2.5],
            "ci_high": [1.5, 2.5, 3.5],
            "title": "Convergence",
            "y_label": "KL Divergence",
        })
        assert resp.status_code == 200
        chart = resp.json()["chart"]
        assert len(chart["data"]) == 2  # CI band + mean line


class TestHeatmap:
    def test_returns_plotly_chart(self, client):
        resp = client.post("/charts/heatmap", json={
            "data": SAMPLE_DATA,
            "x_col": "x",
            "y_col": "y",
            "value_col": "val",
            "title": "Parameter Grid",
        })
        assert resp.status_code == 200
        chart = resp.json()["chart"]
        assert "data" in chart
        assert chart["data"][0]["type"] == "heatmap"
