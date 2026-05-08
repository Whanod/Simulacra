"""Export endpoint tests."""

from __future__ import annotations

import io
import json


SAMPLE_DATA = [
    {"agent": "A", "score": 10, "metric": 1.5},
    {"agent": "B", "score": 20, "metric": 2.5},
    {"agent": "C", "score": 15, "metric": 3.5},
]


class TestCSVExport:
    def test_export_csv(self, client):
        resp = client.post("/export/csv", json={"data": SAMPLE_DATA})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        lines = resp.text.strip().split("\n")
        assert lines[0] == "agent,score,metric"
        assert len(lines) == 4  # header + 3 rows

    def test_export_csv_with_fields(self, client):
        resp = client.post("/export/csv", json={"data": SAMPLE_DATA, "fields": ["agent", "score"]})
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert lines[0] == "agent,score"


class TestJSONExport:
    def test_export_json(self, client):
        resp = client.post("/export/json", json={"data": SAMPLE_DATA})
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 3
        assert data[0]["agent"] == "A"

    def test_export_json_with_fields(self, client):
        resp = client.post("/export/json", json={"data": SAMPLE_DATA, "fields": ["metric"]})
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert list(data[0].keys()) == ["metric"]


class TestParquetExport:
    def test_export_parquet(self, client):
        resp = client.post("/export/parquet", json={"data": SAMPLE_DATA})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        # Parquet files start with "PAR1" magic bytes
        assert resp.content[:4] == b"PAR1"

    def test_export_parquet_roundtrip(self, client):
        import pandas as pd

        resp = client.post("/export/parquet", json={"data": SAMPLE_DATA})
        df = pd.read_parquet(io.BytesIO(resp.content))
        assert len(df) == 3
        assert list(df.columns) == ["agent", "score", "metric"]
