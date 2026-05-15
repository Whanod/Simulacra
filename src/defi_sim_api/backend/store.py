"""Durable artifact storage for runs, sweeps, snapshots, and reports.

Defines the :class:`ArtifactStore` Protocol and a tiny factory that builds a
process-singleton :class:`~defi_sim_api.backend.pg_store.PostgresArtifactStore`.
The Postgres backend is the only supported implementation; the legacy
SQLite + filesystem ``LocalArtifactStore`` was retired in Phase 5 of the
migration.
"""

from __future__ import annotations

import os
from typing import Any, Protocol


STORE_BACKEND_ENV = "DEFI_SIM_STORE_BACKEND"


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

    def query_run_events(
        self,
        run_id: str,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        round_number: int | None = None,
        from_round: int | None = None,
        to_round: int | None = None,
        correlation_id: str | None = None,
        cursor: int | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        ...

    def query_round_metrics(
        self,
        run_id: str,
        metric: str,
        *,
        agent_id: str | None = None,
        from_round: int | None = None,
        to_round: int | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def aggregate_round_metrics(
        self,
        run_ids: list[str],
        metric: str,
        *,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def summarize_run_events(self, run_id: str) -> list[dict[str, Any]]:
        ...

    def query_fee_history(self, run_id: str) -> list[dict[str, dict[str, float]]]:
        ...

    def query_overview_result_slices(self, run_id: str) -> dict[str, Any]:
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


_STORE: ArtifactStore | None = None


def _build_store() -> ArtifactStore:
    """Build a Postgres-backed store.

    ``DEFI_SIM_STORE_BACKEND`` is accepted for backwards compatibility with
    deploy configs that already set it, but ``postgres`` is the only valid
    value.
    """
    backend = (os.environ.get(STORE_BACKEND_ENV) or "postgres").strip().lower()
    if backend != "postgres":
        raise ValueError(
            f"Unsupported {STORE_BACKEND_ENV}={backend!r}; only 'postgres' is "
            "supported (LocalArtifactStore was retired in Phase 5)."
        )
    from defi_sim_api.backend.pg_store import PostgresArtifactStore

    return PostgresArtifactStore()


def get_artifact_store() -> ArtifactStore:
    global _STORE
    if _STORE is None:
        _STORE = _build_store()
    return _STORE


def reset_artifact_store() -> None:
    global _STORE
    if _STORE is not None:
        close = getattr(_STORE, "close", None)
        if callable(close):
            close()
    _STORE = None
