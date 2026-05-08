"""Durable artifact storage for runs, sweeps, snapshots, and reports."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


ARTIFACT_ROOT_ENV = "DEFI_SIM_ARTIFACT_ROOT"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactStore(Protocol):
    def create_run(
        self,
        run_id: str,
        *,
        spec: dict[str, Any],
        status: str,
        seed: int | None,
        market_type: str | None,
        source: str,
        simulation_id: str | None = None,
        source_run_id: str | None = None,
        source_snapshot_id: str | None = None,
        current_round: int = 0,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        ...

    def save_run_artifacts(
        self,
        run_id: str,
        *,
        spec: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        round_snapshots: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        ...

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        ...

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        ...

    def count_runs(self) -> int:
        ...

    def get_run_spec(self, run_id: str) -> dict[str, Any] | None:
        ...

    def get_run_result(self, run_id: str) -> dict[str, Any] | None:
        ...

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        ...

    def get_run_round(self, run_id: str, round_number: int) -> dict[str, Any] | None:
        ...

    def list_run_rounds(
        self,
        run_id: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        ...

    def create_named_snapshot(
        self,
        snapshot_id: str,
        *,
        run_id: str,
        round_number: int,
        label: str | None,
        blob: bytes,
        simulation_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def list_named_snapshots(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        ...

    def get_named_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        ...

    def get_named_snapshot_blob(self, snapshot_id: str) -> bytes | None:
        ...

    def create_sweep(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any],
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def update_sweep(self, sweep_id: str, **fields: Any) -> dict[str, Any]:
        ...

    def save_sweep_artifacts(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        ...

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        ...

    def get_sweep_spec(self, sweep_id: str) -> dict[str, Any] | None:
        ...

    def list_sweeps(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        ...

    def count_sweeps(self) -> int:
        ...

    def get_sweep_rows(self, sweep_id: str) -> list[dict[str, Any]]:
        ...

    def create_report(
        self,
        report_id: str,
        *,
        manifest: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        ...

    def update_report(self, report_id: str, **fields: Any) -> dict[str, Any]:
        ...

    def update_report_manifest(
        self, report_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        ...

    def delete_report(self, report_id: str) -> bool:
        ...

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        ...

    def list_reports(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        ...

    def count_reports(self) -> int:
        ...

    def get_report_manifest(self, report_id: str) -> dict[str, Any] | None:
        ...

    def save_report_bundle(self, report_id: str, bundle_bytes: bytes) -> str:
        ...

    def get_report_bundle_path(self, report_id: str) -> str | None:
        ...


class LocalArtifactStore:
    """SQLite metadata with filesystem-backed JSON and binary artifacts."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get(ARTIFACT_ROOT_ENV) or (Path.cwd() / ".defi_sim_artifacts"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.root / "artifacts.sqlite3"
        self._blobs_root = self.root / "blobs"
        self._blobs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._open_connection()
        self._init_db()

    def _open_connection(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        except sqlite3.OperationalError as exc:  # pragma: no cover - diagnostic path
            raise sqlite3.OperationalError(
                f"{exc}; db_path={self._db_path!s}; root={self.root!s}; env={os.environ.get(ARTIFACT_ROOT_ENV)!r}"
            ) from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    simulation_id TEXT,
                    source TEXT NOT NULL,
                    source_run_id TEXT,
                    source_snapshot_id TEXT,
                    status TEXT NOT NULL,
                    seed INTEGER,
                    market_type TEXT,
                    current_round INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    spec_path TEXT,
                    result_path TEXT,
                    events_path TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS round_snapshots (
                    run_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    PRIMARY KEY (run_id, round_number)
                );

                CREATE TABLE IF NOT EXISTS named_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_run_id TEXT,
                    simulation_id TEXT,
                    round_number INTEGER NOT NULL,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    blob_path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sweeps (
                    sweep_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    spec_path TEXT,
                    rows_path TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    bundle_path TEXT
                );
                """
            )

    def _write_json(self, path: Path, payload: Any) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
        return str(path)

    def _read_json(self, path_str: str | None) -> Any:
        if not path_str:
            return None
        path = Path(path_str)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_bytes(self, path: Path, payload: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path)

    def _read_bytes(self, path_str: str | None) -> bytes | None:
        if not path_str:
            return None
        path = Path(path_str)
        if not path.exists():
            return None
        return path.read_bytes()

    def _blob_path(self, *parts: str) -> Path:
        return self._blobs_root.joinpath(*parts)

    @staticmethod
    def _decode_summary(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        return json.loads(value)

    def create_run(
        self,
        run_id: str,
        *,
        spec: dict[str, Any],
        status: str,
        seed: int | None,
        market_type: str | None,
        source: str,
        simulation_id: str | None = None,
        source_run_id: str | None = None,
        source_snapshot_id: str | None = None,
        current_round: int = 0,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        spec_path = self._write_json(self._blob_path("runs", run_id, "spec.json"), spec)
        summary_json = json.dumps(summary or {})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, simulation_id, source, source_run_id, source_snapshot_id,
                    status, seed, market_type, current_round, created_at, updated_at,
                    spec_path, result_path, events_path, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    run_id,
                    simulation_id,
                    source,
                    source_run_id,
                    source_snapshot_id,
                    status,
                    seed,
                    market_type,
                    current_round,
                    now,
                    now,
                    spec_path,
                    summary_json,
                ),
            )
        return self.get_run(run_id) or {}

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_run(run_id) or {}
        updates: list[str] = []
        values: list[Any] = []
        if "summary" in fields:
            fields["summary_json"] = json.dumps(fields.pop("summary") or {})
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            values.append(value)
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(run_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE run_id = ?",
                values,
            )
        return self.get_run(run_id) or {}

    def save_run_artifacts(
        self,
        run_id: str,
        *,
        spec: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        round_snapshots: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if spec is not None:
            fields["spec_path"] = self._write_json(self._blob_path("runs", run_id, "spec.json"), spec)
        if result is not None:
            fields["result_path"] = self._write_json(self._blob_path("runs", run_id, "result.json"), result)
        if events is not None:
            fields["events_path"] = self._write_json(self._blob_path("runs", run_id, "events.json"), events)
        if summary is not None:
            fields["summary"] = summary
        if fields:
            self.update_run(run_id, **fields)

        if round_snapshots is not None:
            with self._lock, self._connect() as conn:
                for snapshot in round_snapshots:
                    round_number = int(snapshot["round"])
                    snapshot_path = self._write_json(
                        self._blob_path("runs", run_id, "rounds", f"{round_number}.json"),
                        snapshot,
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO round_snapshots (
                            run_id, round_number, created_at, snapshot_path
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (run_id, round_number, _utc_now(), snapshot_path),
                    )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "simulation_id": row["simulation_id"],
            "source": row["source"],
            "source_run_id": row["source_run_id"],
            "source_snapshot_id": row["source_snapshot_id"],
            "status": row["status"],
            "seed": row["seed"],
            "market_type": row["market_type"],
            "current_round": row["current_round"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": self._decode_summary(row["summary_json"]),
        }

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                ORDER BY created_at DESC, run_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self.get_run(row["run_id"]) for row in rows if row["run_id"]]

    def count_runs(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"] if row is not None else 0)

    def get_run_spec(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT spec_path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._read_json(row["spec_path"] if row is not None else None)

    def get_run_result(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT result_path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._read_json(row["result_path"] if row is not None else None)

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT events_path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        payload = self._read_json(row["events_path"] if row is not None else None)
        return payload if isinstance(payload, list) else []

    def get_run_round(self, run_id: str, round_number: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_path FROM round_snapshots
                WHERE run_id = ? AND round_number = ?
                """,
                (run_id, round_number),
            ).fetchone()
        return self._read_json(row["snapshot_path"] if row is not None else None)

    def list_run_rounds(
        self,
        run_id: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["run_id = ?"]
        values: list[Any] = [run_id]
        if start is not None:
            clauses.append("round_number >= ?")
            values.append(start)
        if end is not None:
            clauses.append("round_number <= ?")
            values.append(end)
        values.extend([limit, offset])
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT snapshot_path FROM round_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY round_number ASC
                LIMIT ? OFFSET ?
                """,
                values,
            ).fetchall()
        return [self._read_json(row["snapshot_path"]) for row in rows]

    def create_named_snapshot(
        self,
        snapshot_id: str,
        *,
        run_id: str,
        round_number: int,
        label: str | None,
        blob: bytes,
        simulation_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        blob_path = self._write_bytes(self._blob_path("snapshots", f"{snapshot_id}.bin"), blob)
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO named_snapshots (
                    snapshot_id, run_id, source_run_id, simulation_id,
                    round_number, label, created_at, blob_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
                    source_run_id,
                    simulation_id,
                    round_number,
                    label,
                    now,
                    blob_path,
                ),
            )
        return self.get_named_snapshot(snapshot_id) or {}

    def list_named_snapshots(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT snapshot_id FROM named_snapshots"
        values: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            values.append(run_id)
        query += " ORDER BY created_at DESC, snapshot_id DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self.get_named_snapshot(row["snapshot_id"]) for row in rows if row["snapshot_id"]]

    def get_named_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM named_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "snapshot_id": row["snapshot_id"],
            "run_id": row["run_id"],
            "source_run_id": row["source_run_id"],
            "simulation_id": row["simulation_id"],
            "round": row["round_number"],
            "label": row["label"],
            "created_at": row["created_at"],
        }

    def get_named_snapshot_blob(self, snapshot_id: str) -> bytes | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT blob_path FROM named_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        return self._read_bytes(row["blob_path"] if row is not None else None)

    def create_sweep(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any],
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        spec_path = self._write_json(self._blob_path("sweeps", sweep_id, "spec.json"), spec)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sweeps (
                    sweep_id, status, created_at, updated_at, spec_path, rows_path, summary_json
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (sweep_id, status, now, now, spec_path, json.dumps(summary or {})),
            )
        return self.get_sweep(sweep_id) or {}

    def update_sweep(self, sweep_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_sweep(sweep_id) or {}
        updates: list[str] = []
        values: list[Any] = []
        if "summary" in fields:
            fields["summary_json"] = json.dumps(fields.pop("summary") or {})
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            values.append(value)
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(sweep_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE sweeps SET {', '.join(updates)} WHERE sweep_id = ?",
                values,
            )
        return self.get_sweep(sweep_id) or {}

    def save_sweep_artifacts(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if spec is not None:
            fields["spec_path"] = self._write_json(self._blob_path("sweeps", sweep_id, "spec.json"), spec)
        if rows is not None:
            fields["rows_path"] = self._write_json(self._blob_path("sweeps", sweep_id, "rows.json"), rows)
        if summary is not None:
            fields["summary"] = summary
        if fields:
            self.update_sweep(sweep_id, **fields)

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sweeps WHERE sweep_id = ?", (sweep_id,)).fetchone()
        if row is None:
            return None
        return {
            "sweep_id": row["sweep_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": self._decode_summary(row["summary_json"]),
        }

    def get_sweep_spec(self, sweep_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT spec_path FROM sweeps WHERE sweep_id = ?", (sweep_id,)
            ).fetchone()
        return self._read_json(row["spec_path"] if row is not None else None)

    def list_sweeps(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sweep_id FROM sweeps
                ORDER BY created_at DESC, sweep_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            sweep = self.get_sweep(row["sweep_id"])
            if sweep is None:
                continue
            sweep["spec"] = self.get_sweep_spec(row["sweep_id"])
            out.append(sweep)
        return out

    def count_sweeps(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM sweeps").fetchone()
        return int(row["n"] if row is not None else 0)

    def get_sweep_rows(self, sweep_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT rows_path FROM sweeps WHERE sweep_id = ?", (sweep_id,)).fetchone()
        payload = self._read_json(row["rows_path"] if row is not None else None)
        return payload if isinstance(payload, list) else []

    def create_report(
        self,
        report_id: str,
        *,
        manifest: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        manifest_path = self._write_json(self._blob_path("reports", report_id, "manifest.json"), manifest)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reports (
                    report_id, status, created_at, updated_at, manifest_path, bundle_path
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (report_id, status, now, now, manifest_path),
            )
        return self.get_report(report_id) or {}

    def update_report(self, report_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_report(report_id) or {}
        updates: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            values.append(value)
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(report_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE reports SET {', '.join(updates)} WHERE report_id = ?",
                values,
            )
        return self.get_report(report_id) or {}

    def update_report_manifest(
        self, report_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Merge `patch` into the stored manifest JSON and persist it.

        Returns the new manifest, or None if the report does not exist.
        Also bumps `updated_at` on the SQL row.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT manifest_path FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
            if row is None or not row["manifest_path"]:
                return None
            manifest_path = Path(row["manifest_path"])
            current: dict[str, Any] = {}
            if manifest_path.exists():
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
            merged = {**current, **patch}
            self._write_json(manifest_path, merged)
            conn.execute(
                "UPDATE reports SET updated_at = ? WHERE report_id = ?",
                (_utc_now(), report_id),
            )
        return merged

    def delete_report(self, report_id: str) -> bool:
        """Best-effort delete of the report row plus its manifest and bundle files."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT manifest_path, bundle_path FROM reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if row is None:
                return False
            manifest_path = row["manifest_path"]
            bundle_path = row["bundle_path"]
            conn.execute("DELETE FROM reports WHERE report_id = ?", (report_id,))

        for path_str in (manifest_path, bundle_path):
            if not path_str:
                continue
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return True

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        return {
            "report_id": row["report_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "has_bundle": row["bundle_path"] is not None,
        }

    def list_reports(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT report_id FROM reports
                ORDER BY created_at DESC, report_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            report = self.get_report(row["report_id"])
            if report is None:
                continue
            report["manifest"] = self.get_report_manifest(row["report_id"])
            out.append(report)
        return out

    def count_reports(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()
        return int(row["n"] if row is not None else 0)

    def get_report_manifest(self, report_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT manifest_path FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        return self._read_json(row["manifest_path"] if row is not None else None)

    def save_report_bundle(self, report_id: str, bundle_bytes: bytes) -> str:
        bundle_path = self._write_bytes(self._blob_path("reports", report_id, "bundle.zip"), bundle_bytes)
        self.update_report(report_id, bundle_path=bundle_path, status="ready")
        return bundle_path

    def get_report_bundle_path(self, report_id: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT bundle_path FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        return row["bundle_path"]


_STORE: LocalArtifactStore | None = None


def get_artifact_store() -> LocalArtifactStore:
    global _STORE
    if _STORE is None:
        _STORE = LocalArtifactStore()
    return _STORE


def reset_artifact_store() -> None:
    global _STORE
    if _STORE is not None:
        _STORE.close()
    _STORE = None
