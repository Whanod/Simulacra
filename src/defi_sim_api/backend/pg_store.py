"""Postgres-backed implementation of the ArtifactStore protocol.

Sole implementation since Phase 5; the legacy SQLite + filesystem store was
retired and ``defi_sim_api.backend.store.get_artifact_store`` always returns
an instance of this class.

Design notes:

* Spec, summary, result, round snapshots — JSONB on their parent row.
* Events — typed columns for the six hot fields named in the migration plan,
  remainder in ``data JSONB``. We do *not* strip the promoted fields from
  ``data`` so :meth:`get_run_events` is a byte-identical round-trip with the
  legacy ``events.json`` blob; that costs disk for now but lets the Phase 1
  golden suite assert parity. Phase 2+ can revisit.
* Named-snapshot blobs — the legacy interface hands us raw bytes (msgpack),
  but the new schema column is JSONB. We wrap as ``{"_b64": "..."}`` so the
  bytes round-trip without changing the protocol; Phase 2 will swap to a
  JSON-native shape and drop the envelope.
* Bulk event inserts use ``COPY`` via :meth:`psycopg.Cursor.copy`. Tested at
  ~3k events/run today; psycopg3 ``COPY`` handles 10× that with headroom.
"""

from __future__ import annotations

import base64
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from defi_sim.engine.json import BIGINT_MARKER
from defi_sim_api.backend import db as db_module


# Nine hot event fields promoted to typed columns; everything else lives in data.
# Kept duplicated inside ``data`` JSONB so :meth:`get_run_events` is byte-equal
# with the legacy events.json — Phase 2+ may stop duplicating.
_PROMOTED_EVENT_FIELDS = (
    "agent_id",
    "action_type",
    "asset",
    "amount",
    "price",
    "gas_cost",
    "execution_cost",
    "succeeded",
    "correlation_id",
)


# Columns each ``update_*`` method is allowed to set via **fields. Anything
# not in this list is rejected before SQL is composed — prevents accidental
# injection via attacker-controlled keys reaching the f-string.
_UPDATE_RUN_COLUMNS = frozenset({
    "status", "seed", "market_type", "current_round",
    "simulation_id", "source", "source_run_id", "source_snapshot_id",
    "summary",
})
_UPDATE_SWEEP_COLUMNS = frozenset({"status", "summary", "spec", "rows"})
_UPDATE_REPORT_COLUMNS = frozenset({"status", "manifest"})

# Run statuses that mean "no more events will arrive" — when a run reaches
# one of these, round_metrics is materialised so Phase 3 chart endpoints can
# read pre-aggregated rows instead of scanning events on every request.
# Mirrors the statuses the routers actually write: ``completed`` (sync +
# live), ``cancelled`` (router/simulations.py:140), ``deleted`` (user
# removed a still-running engine; events captured so far are valid). No
# caller writes ``failed`` or ``error`` today; left out until they do.
_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "deleted"})

# Sentinel agent_id reserved for the whole-market round_metrics rollup row
# (Phase 3 INSERTs it). A user-supplied agent_id matching this value would
# collide with the rollup PK on (run_id, round, agent_id), so
# :meth:`_bulk_insert_events` raises before letting it land. Kept readable
# rather than namespacing into ``\x00``-prefix territory because Postgres
# TEXT rejects null bytes via libpq.
ROLLUP_AGENT_ID = "__defi_sim_rollup__"


# Whitelist of round_metrics columns exposed via /runs/{id}/metrics/{metric}.
# Keeps the f-string in :meth:`PostgresArtifactStore.query_round_metrics` safe
# from injection and pins the contract — adding a metric is an explicit code
# change rather than an accidental SELECT against an unknown column.
_QUERYABLE_METRICS = frozenset({
    "volume",
    "num_actions",
    "num_failed",
    "gas_spent",
})


def _validate_update_columns(fields: dict, allowed: frozenset[str], scope: str) -> None:
    bad = set(fields) - allowed
    if bad:
        raise ValueError(
            f"{scope}: refusing to update unknown columns {sorted(bad)}; "
            f"allowed: {sorted(allowed)}"
        )


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _sanitize_for_jsonb(value: Any) -> Any:
    """Recursively coerce values into shapes Postgres JSONB accepts.

    JSONB rejects ``NaN`` / ``+Inf`` / ``-Inf`` outright (JSON spec doesn't
    permit them, and Postgres enforces this on cast). The engine can emit
    such floats for degenerate trades; replacing them with ``None`` is the
    least-surprising preservation choice — callers can distinguish missing
    from numeric via downstream nullability.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, Decimal):
        # JSONB stores numbers natively; Decimal is fine but be explicit.
        return value if value.is_finite() else None
    if isinstance(value, dict):
        return {k: _sanitize_for_jsonb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_jsonb(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_jsonb(item) for item in value]
    return value


def _safe_json(value: Any) -> Json:
    """Sanitise then wrap for JSONB. Use at every persistence call site."""
    return Json(_sanitize_for_jsonb(value))


def _coerce_numeric(value: Any) -> Any:
    """Coerce a value into something psycopg can bind to NUMERIC.

    Python floats round-trip through ``Decimal(repr(value))`` which preserves
    the canonical IEEE-754 representation (``0.1 + 0.2`` stays
    ``0.30000000000000004``). NaN/Inf become ``None`` because NUMERIC has no
    representation for them.

    Engine-side serialisation wraps Python ints > ``JS_SAFE_INTEGER`` (2**53-1)
    as ``{BIGINT_MARKER: "<digits>"}`` so they survive JS round-tripping
    (see ``engine/json.py:62-63``). When such a value lands here — most
    visibly in token-base-unit fee amounts (lamports, gwei) — we unwrap it
    back into a Python int so psycopg can bind it. Without this branch the
    dict falls through to ``return value`` and the bind blows up with a
    NUMERIC type error.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(repr(value))
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, dict):
        digits = value.get(BIGINT_MARKER)
        if isinstance(digits, str):
            try:
                return int(digits)
            except ValueError:
                return None
        return None
    if isinstance(value, (int, str)):
        return value
    return value


class PostgresArtifactStore:
    """Postgres-backed artifact store. Lazily acquires :func:`db.get_pool`."""

    def __init__(self, pool: ConnectionPool | None = None) -> None:
        # If the caller passed a pool, they own its lifecycle and ``close()``
        # is a no-op on this store. Otherwise we use the process-wide pool
        # from ``db_module`` and ``close()`` tears it down — needed so tests
        # that swap ``DATABASE_URL`` between fixtures don't end up bound to
        # the prior pool.
        self._pool = pool
        self._owns_pool = pool is None

    # ── infra ────────────────────────────────────────────────────────────

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            self._pool = db_module.ensure_ready()
        return self._pool

    def close(self) -> None:
        if self._owns_pool:
            db_module.reset_pool()
        self._pool = None

    # ── runs ─────────────────────────────────────────────────────────────

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
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runs (
                        run_id, simulation_id, source, source_run_id, source_snapshot_id,
                        status, seed, market_type, current_round, spec, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        simulation_id      = EXCLUDED.simulation_id,
                        source             = EXCLUDED.source,
                        source_run_id      = EXCLUDED.source_run_id,
                        source_snapshot_id = EXCLUDED.source_snapshot_id,
                        status             = EXCLUDED.status,
                        seed               = EXCLUDED.seed,
                        market_type        = EXCLUDED.market_type,
                        current_round      = EXCLUDED.current_round,
                        spec               = EXCLUDED.spec,
                        summary            = EXCLUDED.summary,
                        updated_at         = now()
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
                        _safe_json(spec),
                        _safe_json(summary or {}),
                    ),
                )
            conn.commit()
        return self.get_run(run_id) or {}

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_run(run_id) or {}
        _validate_update_columns(fields, _UPDATE_RUN_COLUMNS, "update_run")
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key == "summary":
                sets.append("summary = %s")
                values.append(_safe_json(value or {}))
            else:
                sets.append(f"{key} = %s")
                values.append(value)
        sets.append("updated_at = now()")
        values.append(run_id)
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE runs SET {', '.join(sets)} WHERE run_id = %s",
                    values,
                )
                # Live-streaming runs land in their terminal status via
                # update_run rather than create_run (see persist_live_entry):
                # the events are already in the DB at this point, so this is
                # the call site that materialises round_metrics. Gating on the
                # *incoming* status (rather than re-reading runs.status inside
                # the aggregator) avoids the double-fire when
                # persist_live_entry does ``save_run_artifacts; update_run`` —
                # save_run_artifacts already aggregated once the run row's
                # status was committed terminal.
                #
                # Fees were materialised by the earlier ``save_run_artifacts``
                # call (persist_live_entry always saves with the engine's
                # final result before flipping status), so we don't re-run
                # ``_maybe_materialise_fees`` here — the fees table is
                # already populated and the in-memory fee_history is no
                # longer in scope on this code path.
                if fields.get("status") in _TERMINAL_STATUSES:
                    self._maybe_aggregate_round_metrics(cur, run_id)
            conn.commit()
        return self.get_run(run_id) or {}

    # Three metadata keys the Phase 4.5 sandwich tiles consume. Lifted into
    # their own column on the runs row in Phase 5.1 so the StressScore /
    # bundle counters keep their data when ``runs.result`` was retired.
    _SANDWICH_METADATA_KEYS = (
        "sandwich_bundles_landed",
        "sandwich_bundles_submitted",
        "sandwich_realized_ev_lamports",
    )

    @classmethod
    def _extract_sandwich_summary(cls, result: dict[str, Any]) -> dict[str, Any] | None:
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            return None
        picked = {k: metadata[k] for k in cls._SANDWICH_METADATA_KEYS if k in metadata}
        return picked or None

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
        # Phase 5.3 retired the monolithic ``result`` JSONB column. The
        # incoming ``result`` payload is shredded into typed columns on
        # ``runs`` (price_history, agent_final_states, derived_metrics,
        # replay_diff, sandwich_summary, metadata) plus the
        # ``round_snapshots`` / ``fees`` tables; ``get_run_result``
        # stitches the legacy shape back together when callers ask for it.
        # Missing slices land as SQL NULL (not JSONB ``null``) so readers
        # can ``IS NULL``-check the column without disambiguating the two.
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                row_updates: list[tuple[str, Any]] = []
                if spec is not None:
                    row_updates.append(("spec", _safe_json(spec)))
                if summary is not None:
                    row_updates.append(("summary", _safe_json(summary)))
                if result is not None:
                    metadata = result.get("metadata") if isinstance(result, dict) else None
                    derived = (
                        metadata.get("derived_metrics")
                        if isinstance(metadata, dict)
                        else None
                    )
                    row_updates.extend(
                        (col, _safe_json(value) if value is not None else None)
                        for col, value in (
                            ("price_history", result.get("price_history")),
                            ("agent_final_states", result.get("agent_final_states")),
                            ("derived_metrics", derived),
                            ("replay_diff", result.get("replay_diff")),
                            ("sandwich_summary", self._extract_sandwich_summary(result)),
                            ("metadata", metadata if isinstance(metadata, dict) else None),
                        )
                    )
                if row_updates:
                    sets = ", ".join(f"{name} = %s" for name, _ in row_updates)
                    cur.execute(
                        f"UPDATE runs SET {sets}, updated_at = now() WHERE run_id = %s",
                        [v for _, v in row_updates] + [run_id],
                    )

                if events is not None:
                    cur.execute("DELETE FROM events WHERE run_id = %s", (run_id,))
                    self._bulk_insert_events(cur, run_id, events)

                if round_snapshots is not None:
                    for snapshot in round_snapshots:
                        round_number = int(snapshot["round"])
                        cur.execute(
                            """
                            INSERT INTO round_snapshots (run_id, round_number, state)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (run_id, round_number) DO UPDATE
                              SET state = EXCLUDED.state,
                                  created_at = now()
                            """,
                            (run_id, round_number, _safe_json(snapshot)),
                        )

                if events is not None:
                    self._maybe_aggregate_round_metrics(cur, run_id)
                if result is not None:
                    self._maybe_materialise_fees(cur, run_id, result.get("fee_history"))
            conn.commit()

    @staticmethod
    def _maybe_aggregate_round_metrics(cur, run_id: str) -> None:
        """Materialise round_metrics from events when the run is terminal.

        Idempotent: deletes prior rows for this run before inserting. Skipped
        for live/in-progress runs because event tables are still being filled.
        Emits two row classes per round:

        * Per-agent rows (one per distinct ``agent_id`` at that round).
        * One whole-market rollup row tagged ``ROLLUP_AGENT_ID`` — Phase 3
          chart endpoints filter on this sentinel when no ``?agent=`` is
          supplied. Counts include events without an agent (e.g. system
          events). For action-typed metrics the rollup equals
          ``SUM`` over the per-agent rows because actions always carry an
          ``agent_id``; the two are kept consistent by deriving both from
          the same event set in the same transaction.

        Concurrency: two workers calling this for the same run race on the
        DELETE+INSERT pair. We deliberately do not ``SELECT … FOR UPDATE``
        because the aggregation is deterministic — both workers compute the
        same output from the same event set — and the worst case is one
        wasted DELETE+INSERT, which is cheaper than holding a row lock
        across the aggregation. If contention ever shows up in production,
        promote to ``INSERT … ON CONFLICT DO NOTHING`` against a serial
        ``aggregation_id`` and gate via an advisory lock.
        """
        cur.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        if not row or row[0] not in _TERMINAL_STATUSES:
            return
        cur.execute("DELETE FROM round_metrics WHERE run_id = %s", (run_id,))
        cur.execute(
            """
            INSERT INTO round_metrics
              (run_id, round, agent_id, num_actions, num_failed, volume, gas_spent)
            SELECT
              run_id,
              round,
              agent_id,
              COUNT(*) FILTER (WHERE type = 'ACTION_EXECUTED')::int AS num_actions,
              COUNT(*) FILTER (WHERE type = 'ACTION_FAILED')::int   AS num_failed,
              SUM(
                CASE
                  WHEN type = 'ACTION_EXECUTED'
                       AND jsonb_typeof(data #> '{result,volume}') = 'number'
                  THEN (data #>> '{result,volume}')::numeric
                  ELSE NULL
                END
              ) AS volume,
              -- gas_spent is gas on *succeeded* actions only. A failed action
              -- bills its CU/priority fee to the agent, but Phase 3 chart code
              -- treats it as overhead — exposing it here would conflate the
              -- two and bake the choice into every downstream consumer.
              SUM(gas_cost) FILTER (WHERE type = 'ACTION_EXECUTED') AS gas_spent
            FROM events
            WHERE run_id = %s AND agent_id IS NOT NULL
            GROUP BY run_id, round, agent_id
            """,
            (run_id,),
        )
        # Whole-market rollup. ``agent_id`` filter dropped so system events
        # contribute to counts; in practice actions always carry agent_id so
        # rollup totals equal the sum of per-agent rows at the same round.
        cur.execute(
            """
            INSERT INTO round_metrics
              (run_id, round, agent_id, num_actions, num_failed, volume, gas_spent)
            SELECT
              run_id,
              round,
              %s AS agent_id,
              COUNT(*) FILTER (WHERE type = 'ACTION_EXECUTED')::int AS num_actions,
              COUNT(*) FILTER (WHERE type = 'ACTION_FAILED')::int   AS num_failed,
              SUM(
                CASE
                  WHEN type = 'ACTION_EXECUTED'
                       AND jsonb_typeof(data #> '{result,volume}') = 'number'
                  THEN (data #>> '{result,volume}')::numeric
                  ELSE NULL
                END
              ) AS volume,
              SUM(gas_cost) FILTER (WHERE type = 'ACTION_EXECUTED') AS gas_spent
            FROM events
            WHERE run_id = %s
            GROUP BY run_id, round
            """,
            (ROLLUP_AGENT_ID, run_id),
        )

    @staticmethod
    def _maybe_materialise_fees(cur, run_id: str, fee_history: Any) -> None:
        """Materialise the ``fees`` table from a result's ``fee_history``.

        The engine maintains ``_fee_history`` as a list indexed by round
        whose entries are ``{destination: {token_id: amount}}`` (see
        ``engine/simulation.py:172``); there are no per-event ``FEE_*``
        events to aggregate, so the source is the in-memory result payload
        passed to :meth:`save_run_artifacts`. Phase 5.3 removed the
        previous DB read against ``runs.result`` (column retired); callers
        pass ``fee_history`` straight through.

        Persistence-time gate: :meth:`save_run_artifacts` is only ever
        called with a non-``None`` ``result`` once the engine has finished
        the run (see ``runtime.persist_live_entry`` / ``persist_sync_run``),
        so ``fee_history`` is always final by the time we reach this
        method. No status check needed.

        Idempotent: deletes prior rows for this run before inserting.
        ``executemany`` is appropriate — typical runs have a few thousand
        (round × destination × token) rows total, well below the threshold
        where ``COPY`` starts winning over a batched insert.
        """
        if not isinstance(fee_history, list):
            return

        rows: list[tuple[str, int, str, str, Any]] = []
        for round_number, splits in enumerate(fee_history):
            if not isinstance(splits, dict):
                continue
            for destination, tokens in splits.items():
                if not isinstance(destination, str) or not isinstance(tokens, dict):
                    continue
                for token_id, amount in tokens.items():
                    if not isinstance(token_id, str):
                        continue
                    coerced = _coerce_numeric(amount)
                    if coerced is None:
                        continue
                    rows.append((run_id, round_number, destination, token_id, coerced))

        cur.execute("DELETE FROM fees WHERE run_id = %s", (run_id,))
        if rows:
            cur.executemany(
                "INSERT INTO fees (run_id, round, destination, token_id, amount) "
                "VALUES (%s, %s, %s, %s, %s)",
                rows,
            )

    @staticmethod
    def _bulk_insert_events(cur, run_id: str, events: Iterable[dict[str, Any]]) -> None:
        # COPY beats executemany once event counts climb past a few hundred.
        # Per-row sanitisation: NaN/Inf floats become NULL (JSONB rejects
        # them); NUMERIC columns receive ``Decimal(repr(float))`` so the
        # IEEE-754 representation round-trips losslessly.
        copy_sql = (
            "COPY events ("
            "run_id, event_id, round, timestamp, type, agent_id, action_type, "
            "asset, amount, price, gas_cost, execution_cost, succeeded, "
            "correlation_id, data) FROM STDIN"
        )
        with cur.copy(copy_sql) as copy:
            for event in events:
                raw_data = event.get("data") or {}
                agent_id = raw_data.get("agent_id")
                if agent_id == ROLLUP_AGENT_ID:
                    # Catch the collision at write time rather than letting it
                    # explode in Phase 3 when the rollup INSERT trips the PK.
                    raise ValueError(
                        f"agent_id={ROLLUP_AGENT_ID!r} is reserved for the "
                        f"round_metrics rollup row; reject in spec validation "
                        f"or rename the agent (event_id={event.get('event_id')})"
                    )
                safe_data = _sanitize_for_jsonb(raw_data)
                timestamp = float(event["timestamp"])
                if not math.isfinite(timestamp):
                    timestamp = 0.0
                price_val = raw_data.get("price")
                if isinstance(price_val, float) and not math.isfinite(price_val):
                    price_val = None
                copy.write_row(
                    (
                        run_id,
                        int(event["event_id"]),
                        int(event["round"]),
                        timestamp,
                        str(event["type"]),
                        agent_id,
                        raw_data.get("action_type"),
                        raw_data.get("asset"),
                        _coerce_numeric(raw_data.get("amount")),
                        price_val,
                        _coerce_numeric(raw_data.get("gas_cost")),
                        _coerce_numeric(raw_data.get("execution_cost")),
                        raw_data.get("succeeded"),
                        raw_data.get("correlation_id"),
                        _safe_json(safe_data),
                    )
                )

    _RUN_COLUMNS = (
        "run_id, simulation_id, source, source_run_id, source_snapshot_id, "
        "status, seed, market_type, current_round, created_at, updated_at, summary"
    )

    @staticmethod
    def _run_row_to_dict(row: tuple) -> dict[str, Any]:
        return {
            "run_id": row[0],
            "simulation_id": row[1],
            "source": row[2],
            "source_run_id": row[3],
            "source_snapshot_id": row[4],
            "status": row[5],
            "seed": row[6],
            "market_type": row[7],
            "current_round": row[8],
            "created_at": _iso(row[9]),
            "updated_at": _iso(row[10]),
            "summary": row[11] or {},
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._RUN_COLUMNS} FROM runs WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        return self._run_row_to_dict(row) if row else None

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        # Single SELECT — the legacy SQLite store could afford N+1 because
        # every "connection" was a process-local pointer; pooled Postgres
        # over a network can't.
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self._RUN_COLUMNS} FROM runs
                    ORDER BY created_at DESC, run_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                return [self._run_row_to_dict(row) for row in cur.fetchall()]

    def count_runs(self) -> int:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM runs")
                return int(cur.fetchone()[0])

    def purge_runs(self) -> dict[str, int]:
        """Delete every run row + its events, snapshots, and named snapshots.

        Sweeps and reports left intact, matching the legacy semantics.
        Cascades on ``events``, ``round_metrics``, ``round_snapshots`` are
        defined in the schema; ``named_snapshots`` does not cascade so we
        clear it separately.
        """
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM runs")
                run_count = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM round_snapshots")
                round_count = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM named_snapshots")
                named_count = int(cur.fetchone()[0])
                cur.execute("DELETE FROM named_snapshots")
                cur.execute("DELETE FROM runs")
            conn.commit()
        return {
            "runs": run_count,
            "round_snapshots": round_count,
            "named_snapshots": named_count,
        }

    def get_run_spec(self, run_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT spec FROM runs WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
        return row[0] if row else None

    # Columns + summary keys ``get_run_result`` stitches into the legacy
    # ``result`` shape. Spelled out here so the composer's SQL stays explicit
    # and the test fixture matches the read path. ``num_rounds`` /
    # ``stopped_early`` / ``cancelled`` / ``stop_reason`` live on
    # ``runs.summary`` (populated by ``summarize_result`` at write time);
    # ``num_rounds_executed`` comes from ``runs.current_round``.
    # Keys the engine always emits at the top of ``SimulationResult``,
    # mirrored on ``runs.summary`` by :func:`summarize_result`. Composer
    # surfaces them unconditionally (preserving ``None``) so the wire
    # shape matches what golden captures pinned.
    _COMPOSED_RESULT_ALWAYS_KEYS = (
        "num_rounds",
        "stopped_early",
        "cancelled",
        "stop_reason",
    )
    # Replay-only keys ``persist_replay_run`` writes to summary so the
    # composer (and the legacy ``/runs/{id}/result`` endpoint) can keep
    # surfacing them after Phase 5.1 retired ``runs.result``.
    _COMPOSED_RESULT_OPTIONAL_KEYS = ("kind", "predicted")

    def get_run_result(self, run_id: str) -> dict[str, Any] | None:
        """Compose the legacy ``result`` shape from typed surfaces.

        Phase 5.1 split ``result`` into per-slice columns on ``runs`` and
        peer tables (``round_snapshots``, ``fees``). This method stitches
        the shape back together for callers that still want the legacy
        bundle — primarily the report ZIP export, the share-link payload,
        and the embed chart renderer. The overview view and the compare
        endpoint read typed columns directly (no composer).

        Returns ``None`` only when the run row itself is missing. Empty /
        null slices land as their legacy defaults (``price_history=[]``,
        ``round_snapshots=[]``, ``fee_history=[]``) so consumers can
        ``.get(...)`` without extra defensiveness. The composer's
        ``metadata`` slot is carried whole off the ``runs.metadata``
        column so engine-internal fields like ``parameter_state`` and
        ``fee_destination_balances`` round-trip for golden parity even
        though no live consumer reads them.
        """
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      seed, current_round, summary,
                      price_history, agent_final_states, replay_diff,
                      metadata
                    FROM runs WHERE run_id = %s
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                (
                    seed,
                    current_round,
                    summary,
                    price_history,
                    agent_final_states,
                    replay_diff,
                    metadata,
                ) = row
                cur.execute(
                    "SELECT state FROM round_snapshots WHERE run_id = %s ORDER BY round_number",
                    (run_id,),
                )
                round_snapshots = [snap_row[0] for snap_row in cur.fetchall()]

        summary = summary or {}
        result: dict[str, Any] = {
            # ``__type__`` marker mirrors the engine's
            # ``simulation_result_to_dict(include_type_tags=True)`` output
            # the legacy ``runs.result`` column carried. Some consumers
            # (e.g. the runner snapshot view in
            # ``frontend/.../runner/[runId]/page.tsx``) key off the nested
            # markers; the top-level one preserves golden byte-equality.
            "__type__": "SimulationResult",
            "price_history": price_history or [],
            "agent_final_states": agent_final_states or {},
            "round_snapshots": round_snapshots,
            "fee_history": self.query_fee_history(run_id),
            "num_rounds_executed": int(current_round or 0),
            "seed": seed,
        }
        for key in self._COMPOSED_RESULT_ALWAYS_KEYS:
            result[key] = summary.get(key)
        for key in self._COMPOSED_RESULT_OPTIONAL_KEYS:
            value = summary.get(key)
            if value is not None:
                result[key] = value
        if replay_diff is not None:
            result["replay_diff"] = replay_diff
        if isinstance(metadata, dict) and metadata:
            result["metadata"] = metadata
        return result

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_id, round, timestamp, type, data
                    FROM events WHERE run_id = %s
                    ORDER BY event_id
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "event_id": row[0],
                "run_id": run_id,
                "type": row[3],
                "round": row[1],
                "timestamp": row[2],
                "data": row[4] or {},
            }
            for row in rows
        ]

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
        # Server-side filter + cursor pagination, driven by the indexes in
        # schema.sql: events_run_round (round range), events_run_agent_round
        # (agent filters), events_run_type (type filter), events_run_correlation
        # (correlation_id partial). Cursor pagination uses the (run_id, event_id)
        # PK so it's free.
        clauses = ["run_id = %s"]
        params: list[Any] = [run_id]
        if event_type is not None:
            clauses.append("type = %s")
            params.append(event_type)
        if agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(agent_id)
        if round_number is not None:
            clauses.append("round = %s")
            params.append(round_number)
        if from_round is not None:
            clauses.append("round >= %s")
            params.append(from_round)
        if to_round is not None:
            clauses.append("round <= %s")
            params.append(to_round)
        if correlation_id is not None:
            clauses.append("correlation_id = %s")
            params.append(correlation_id)
        if cursor is not None:
            clauses.append("event_id > %s")
            params.append(cursor)
        params.extend([limit, offset])
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT event_id, round, timestamp, type, data
                    FROM events WHERE {' AND '.join(clauses)}
                    ORDER BY event_id
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = cur.fetchall()
        return [
            {
                "event_id": row[0],
                "run_id": run_id,
                "type": row[3],
                "round": row[1],
                "timestamp": row[2],
                "data": row[4] or {},
            }
            for row in rows
        ]

    def query_round_metrics(
        self,
        run_id: str,
        metric: str,
        *,
        agent_id: str | None = None,
        from_round: int | None = None,
        to_round: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``[{round, value}]`` for one metric column, ordered by round.

        ``agent_id=None`` selects the whole-market rollup (the
        :data:`ROLLUP_AGENT_ID` row that ``_maybe_aggregate_round_metrics``
        emits). Any user-named agent is queried directly. The router never
        exposes the sentinel literal to callers.
        """
        if metric not in _QUERYABLE_METRICS:
            raise ValueError(
                f"metric {metric!r} not exposed; allowed: {sorted(_QUERYABLE_METRICS)}"
            )
        clauses = ["run_id = %s"]
        params: list[Any] = [run_id]
        clauses.append("agent_id = %s")
        params.append(agent_id if agent_id is not None else ROLLUP_AGENT_ID)
        if from_round is not None:
            clauses.append("round >= %s")
            params.append(from_round)
        if to_round is not None:
            clauses.append("round <= %s")
            params.append(to_round)
        # ``metric`` is whitelisted above; safe to interpolate.
        sql = (
            f"SELECT round, {metric} FROM round_metrics "
            f"WHERE {' AND '.join(clauses)} ORDER BY round"
        )
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [
            {
                "round": row[0],
                # NUMERIC comes back as Decimal; JSON-serialise as float so
                # the FastAPI default encoder doesn't choke and chart callers
                # see the same shape they'd get from float columns.
                "value": float(row[1]) if isinstance(row[1], Decimal) else row[1],
            }
            for row in rows
        ]

    def aggregate_round_metrics(
        self,
        run_ids: list[str],
        metric: str,
        *,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Multi-run aggregation against ``round_metrics`` — one SQL roundtrip.

        Mirrors the cross-run query sketched in the migration plan: SUM the
        metric over each run's rounds, plus MAX(round) so callers can show
        a "how far did the run get" column. ``run_ids`` not present in the
        table simply don't appear in the result; callers can detect missing
        runs via set diff.
        """
        if metric not in _QUERYABLE_METRICS:
            raise ValueError(
                f"metric {metric!r} not exposed; allowed: {sorted(_QUERYABLE_METRICS)}"
            )
        if not run_ids:
            return []
        # WHERE run_id = ANY(...) lets us bind the list as one parameter
        # rather than building a variadic IN clause.
        target_agent = agent_id if agent_id is not None else ROLLUP_AGENT_ID
        sql = (
            f"SELECT run_id, SUM({metric})::numeric AS total, MAX(round) AS final_round "
            f"FROM round_metrics "
            f"WHERE run_id = ANY(%s) AND agent_id = %s "
            f"GROUP BY run_id"
        )
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_ids, target_agent))
                rows = {row[0]: row for row in cur.fetchall()}
        # Preserve caller's run order; missing runs surface as totals=None.
        out: list[dict[str, Any]] = []
        for run_id in run_ids:
            row = rows.get(run_id)
            if row is None:
                out.append({"run_id": run_id, "total": None, "final_round": None})
                continue
            total = row[1]
            out.append({
                "run_id": run_id,
                "total": float(total) if isinstance(total, Decimal) else total,
                "final_round": row[2],
            })
        return out

    def summarize_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Counts per event ``type`` for one run, ordered by type.

        One round trip; uses the ``events_run_type`` index. Drives the
        ``event_summary`` slice on :func:`/runs/{id}/views/overview`.
        """
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT type, COUNT(*)::int AS count
                    FROM events
                    WHERE run_id = %s
                    GROUP BY type
                    ORDER BY type
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()
        return [{"type": row[0], "count": row[1]} for row in rows]

    def query_fee_history(self, run_id: str) -> list[dict[str, dict[str, float]]]:
        """Reconstruct ``fee_history`` from the ``fees`` table.

        Returns a list indexed by round, where each entry is
        ``{destination: {token_id: amount}}`` — the engine's native
        ``_fee_history`` shape (``engine/simulation.py:172``). Rounds with
        no fee rows are emitted as empty dicts so the list index lines up
        with the round number.

        The list is bounded by ``runs.current_round`` (which equals
        ``num_rounds_executed`` for terminal runs — ``runtime.py:154,167``)
        rather than by ``MAX(round)`` from the ``fees`` table. If the final
        K rounds have no fees across any destination, bounding on
        ``MAX(round)`` would truncate them and decouple ``fee_history``'s
        length from ``price_history``'s — chart consumers iterate the array
        by index, so trailing alignment matters.

        ``NUMERIC`` values come back as ``Decimal``; JSON-serialised as
        ``float`` to match the legacy ``result.fee_history`` wire shape that
        ``chartDataFromResult`` / ``sumLpFeesForToken`` consume.
        """
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, current_round FROM runs WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return []
                status, current_round = row
                # Live / unmaterialised runs return ``[]`` — no truthful list
                # length exists until the engine finishes ``_record_round``
                # for every round, and the consumer can fall back to the
                # in-memory event stream while a run is still progressing.
                if status not in _TERMINAL_STATUSES:
                    return []
                num_rounds = int(current_round or 0)
                cur.execute(
                    "SELECT round, destination, token_id, amount FROM fees "
                    "WHERE run_id = %s ORDER BY round, destination, token_id",
                    (run_id,),
                )
                rows = cur.fetchall()
        # Terminal runs with no fees still emit ``[{}]*N`` to match the
        # engine's ``_fee_history`` shape — chart consumers iterate the
        # array alongside ``price_history`` and expect matching lengths.
        # A defensive max() guards against a stale ``current_round`` ever
        # lagging behind the materialised fees; should be unreachable.
        bound = max(num_rounds, (rows[-1][0] + 1) if rows else 0)
        if bound == 0:
            return []
        history: list[dict[str, dict[str, float]]] = [{} for _ in range(bound)]
        for round_number, destination, token_id, amount in rows:
            value = float(amount) if isinstance(amount, Decimal) else amount
            history[round_number].setdefault(destination, {})[token_id] = value
        return history

    # Typed columns on ``runs`` that hold the chart slices peeled off the
    # legacy ``result`` payload in Phase 5.1. Listed here so the overview
    # view's read path is a single SELECT against an explicit column list
    # rather than a JSONB pluck against ``runs.result``.
    _OVERVIEW_SLICE_COLUMNS = (
        "price_history",
        "agent_final_states",
        "derived_metrics",
        "replay_diff",
        "sandwich_summary",
        "current_round",  # supplants ``result.num_rounds_executed`` for terminal runs
    )

    def read_overview_typed_slices(self, run_id: str) -> dict[str, Any]:
        """Read the typed slices the overview view bundles in one round trip.

        Replaces the Phase 4.5 ``query_overview_result_slices`` JSONB plucks
        against ``runs.result``. Each slice now lives on its own column
        (added in Phase 5.1's ``save_run_artifacts`` dual-write), so the
        SELECT is a flat row read; columns missing from a particular run
        type (live runs without a result write, replay runs without
        sandwich activity) come back as ``None``.

        ``current_round`` doubles as ``num_rounds_executed`` for terminal
        runs — ``runtime.persist_sync_run`` and ``persist_live_entry`` both
        set it from ``result.num_rounds_executed``.
        """
        cols = ", ".join(self._OVERVIEW_SLICE_COLUMNS)
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {cols} FROM runs WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        if row is None:
            return {col: None for col in self._OVERVIEW_SLICE_COLUMNS}
        return dict(zip(self._OVERVIEW_SLICE_COLUMNS, row, strict=True))

    def query_round_snapshot_summaries(self, run_id: str) -> dict[str, Any]:
        """Pull per-round whirlpool + Solana telemetry from ``round_snapshots``.

        Phase 4.5 plucked these off ``result.round_snapshots[]`` — the same
        data is already in the ``round_snapshots`` table (populated by
        :meth:`save_run_artifacts` from the engine's ``round_snapshot_to_dict``
        serializer), so Phase 5.2 reads from there instead. Two JSONB
        aggregates in one SELECT:

        * ``whirlpool_snapshots``: ``[{round, whirlpool}, …]`` filtered to
          rows whose ``state.metrics.whirlpool`` is non-null. Drives the
          tick-crossing / active-L / fees-by-side charts.
        * ``snapshot_summaries``: ``[{round, current_slot, current_leader,
          bundle_outcomes, jito_searcher, replay}, …]`` over every row,
          regardless of telemetry presence. Drives the Solana slot, bundle
          outcome, Jito-searcher, and replay-metrics summaries.

        Both lists are ``None`` when the run has no snapshots (live runs
        before the first record, replay runs that didn't persist any).
        """
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      (
                        SELECT jsonb_agg(
                          jsonb_build_object(
                            'round', state -> 'round',
                            'whirlpool', state #> '{metrics,whirlpool}'
                          )
                          ORDER BY round_number
                        )
                        FROM round_snapshots
                        WHERE run_id = %(run_id)s
                          AND state #> '{metrics,whirlpool}' IS NOT NULL
                      ) AS whirlpool_snapshots,
                      (
                        SELECT jsonb_agg(
                          jsonb_build_object(
                            'round', state -> 'round',
                            'current_slot', state -> 'current_slot',
                            'current_leader', state -> 'current_leader',
                            'bundle_outcomes', state -> 'bundle_outcomes',
                            'jito_searcher', state #> '{metrics,jito_searcher}',
                            'replay', state #> '{metrics,replay}'
                          )
                          ORDER BY round_number
                        )
                        FROM round_snapshots
                        WHERE run_id = %(run_id)s
                      ) AS snapshot_summaries
                    """,
                    {"run_id": run_id},
                )
                row = cur.fetchone()
        if row is None:
            return {"whirlpool_snapshots": None, "snapshot_summaries": None}
        return {"whirlpool_snapshots": row[0], "snapshot_summaries": row[1]}

    def get_run_round(self, run_id: str, round_number: int) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT state FROM round_snapshots WHERE run_id = %s AND round_number = %s",
                    (run_id, round_number),
                )
                row = cur.fetchone()
        return row[0] if row else None

    def list_run_rounds(
        self,
        run_id: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["run_id = %s"]
        params: list[Any] = [run_id]
        if start is not None:
            clauses.append("round_number >= %s")
            params.append(start)
        if end is not None:
            clauses.append("round_number <= %s")
            params.append(end)
        params.extend([limit, offset])
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT state FROM round_snapshots
                    WHERE {' AND '.join(clauses)}
                    ORDER BY round_number ASC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                return [row[0] for row in cur.fetchall()]

    # ── named snapshots ──────────────────────────────────────────────────

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
        envelope = {"_b64": base64.b64encode(blob).decode("ascii")}
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO named_snapshots (
                        snapshot_id, run_id, source_run_id, simulation_id,
                        round_number, label, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_id) DO UPDATE SET
                        run_id        = EXCLUDED.run_id,
                        source_run_id = EXCLUDED.source_run_id,
                        simulation_id = EXCLUDED.simulation_id,
                        round_number  = EXCLUDED.round_number,
                        label         = EXCLUDED.label,
                        state         = EXCLUDED.state
                    """,
                    (
                        snapshot_id,
                        run_id,
                        source_run_id,
                        simulation_id,
                        round_number,
                        label,
                        _safe_json(envelope),
                    ),
                )
            conn.commit()
        return self.get_named_snapshot(snapshot_id) or {}

    _NAMED_SNAPSHOT_COLUMNS = (
        "snapshot_id, run_id, source_run_id, simulation_id, "
        "round_number, label, created_at"
    )

    @staticmethod
    def _named_snapshot_row_to_dict(row: tuple) -> dict[str, Any]:
        return {
            "snapshot_id": row[0],
            "run_id": row[1],
            "source_run_id": row[2],
            "simulation_id": row[3],
            "round": row[4],
            "label": row[5],
            "created_at": _iso(row[6]),
        }

    def list_named_snapshots(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        query = f"SELECT {self._NAMED_SNAPSHOT_COLUMNS} FROM named_snapshots"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = %s"
            params.append(run_id)
        query += " ORDER BY created_at DESC, snapshot_id DESC"
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [self._named_snapshot_row_to_dict(row) for row in cur.fetchall()]

    def get_named_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._NAMED_SNAPSHOT_COLUMNS} FROM named_snapshots "
                    "WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                row = cur.fetchone()
        return self._named_snapshot_row_to_dict(row) if row else None

    def get_named_snapshot_blob(self, snapshot_id: str) -> bytes | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT state FROM named_snapshots WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        envelope = row[0]
        encoded = envelope.get("_b64") if isinstance(envelope, dict) else None
        if not encoded:
            return None
        return base64.b64decode(encoded)

    # ── sweeps ───────────────────────────────────────────────────────────

    def create_sweep(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any],
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sweeps (sweep_id, status, spec, summary)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (sweep_id) DO UPDATE SET
                        status     = EXCLUDED.status,
                        spec       = EXCLUDED.spec,
                        summary    = EXCLUDED.summary,
                        updated_at = now()
                    """,
                    (sweep_id, status, _safe_json(spec), _safe_json(summary or {})),
                )
            conn.commit()
        return self.get_sweep(sweep_id) or {}

    def update_sweep(self, sweep_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_sweep(sweep_id) or {}
        _validate_update_columns(fields, _UPDATE_SWEEP_COLUMNS, "update_sweep")
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key == "summary":
                sets.append("summary = %s")
                values.append(_safe_json(value or {}))
            else:
                sets.append(f"{key} = %s")
                values.append(value)
        sets.append("updated_at = now()")
        values.append(sweep_id)
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE sweeps SET {', '.join(sets)} WHERE sweep_id = %s",
                    values,
                )
            conn.commit()
        return self.get_sweep(sweep_id) or {}

    def save_sweep_artifacts(
        self,
        sweep_id: str,
        *,
        spec: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if spec is not None:
            sets.append("spec = %s")
            params.append(_safe_json(spec))
        if rows is not None:
            sets.append("rows = %s")
            params.append(_safe_json(rows))
        if summary is not None:
            sets.append("summary = %s")
            params.append(_safe_json(summary))
        if not sets:
            return
        sets.append("updated_at = now()")
        params.append(sweep_id)
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE sweeps SET {', '.join(sets)} WHERE sweep_id = %s",
                    params,
                )
            conn.commit()

    def get_sweep(self, sweep_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sweep_id, status, created_at, updated_at, summary "
                    "FROM sweeps WHERE sweep_id = %s",
                    (sweep_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "sweep_id": row[0],
            "status": row[1],
            "created_at": _iso(row[2]),
            "updated_at": _iso(row[3]),
            "summary": row[4] or {},
        }

    def get_sweep_spec(self, sweep_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT spec FROM sweeps WHERE sweep_id = %s", (sweep_id,))
                row = cur.fetchone()
        return row[0] if row else None

    def list_sweeps(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT sweep_id, status, created_at, updated_at, summary, spec
                    FROM sweeps
                    ORDER BY created_at DESC, sweep_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()
        return [
            {
                "sweep_id": row[0],
                "status": row[1],
                "created_at": _iso(row[2]),
                "updated_at": _iso(row[3]),
                "summary": row[4] or {},
                "spec": row[5],
            }
            for row in rows
        ]

    def count_sweeps(self) -> int:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM sweeps")
                return int(cur.fetchone()[0])

    def get_sweep_rows(self, sweep_id: str) -> list[dict[str, Any]]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT rows FROM sweeps WHERE sweep_id = %s", (sweep_id,))
                row = cur.fetchone()
        payload = row[0] if row else None
        return payload if isinstance(payload, list) else []

    # ── reports ──────────────────────────────────────────────────────────
    #
    # Phase 3: bundles are generated on demand by the router from live SQL,
    # so we no longer persist ZIP bytes. The Phase 1 ``_bundle_b64`` envelope
    # is gone; manifests are now pure JSONB and never need stripping.

    def create_report(
        self,
        report_id: str,
        *,
        manifest: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reports (report_id, status, manifest)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (report_id) DO UPDATE SET
                        status     = EXCLUDED.status,
                        manifest   = EXCLUDED.manifest,
                        updated_at = now()
                    """,
                    (report_id, status, _safe_json(manifest)),
                )
            conn.commit()
        return self.get_report(report_id) or {}

    def update_report(self, report_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get_report(report_id) or {}
        _validate_update_columns(fields, _UPDATE_REPORT_COLUMNS, "update_report")
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key == "manifest":
                sets.append("manifest = %s")
                values.append(_safe_json(value or {}))
            else:
                sets.append(f"{key} = %s")
                values.append(value)
        sets.append("updated_at = now()")
        values.append(report_id)
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE reports SET {', '.join(sets)} WHERE report_id = %s",
                    values,
                )
            conn.commit()
        return self.get_report(report_id) or {}

    def update_report_manifest(
        self, report_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT manifest FROM reports WHERE report_id = %s FOR UPDATE",
                    (report_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                current = row[0] or {}
                merged = {**current, **patch}
                cur.execute(
                    "UPDATE reports SET manifest = %s, updated_at = now() WHERE report_id = %s",
                    (_safe_json(merged), report_id),
                )
            conn.commit()
        return merged

    def delete_report(self, report_id: str) -> bool:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reports WHERE report_id = %s", (report_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT report_id, status, created_at, updated_at, manifest "
                    "FROM reports WHERE report_id = %s",
                    (report_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "report_id": row[0],
            "status": row[1],
            "created_at": _iso(row[2]),
            "updated_at": _iso(row[3]),
        }

    def list_reports(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT report_id, status, created_at, updated_at, manifest
                    FROM reports
                    ORDER BY created_at DESC, report_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()
        return [
            {
                "report_id": row[0],
                "status": row[1],
                "created_at": _iso(row[2]),
                "updated_at": _iso(row[3]),
                "manifest": row[4] or {},
            }
            for row in rows
        ]

    def count_reports(self) -> int:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM reports")
                return int(cur.fetchone()[0])

    def get_report_manifest(self, report_id: str) -> dict[str, Any] | None:
        with self._get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT manifest FROM reports WHERE report_id = %s", (report_id,)
                )
                row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]


__all__ = ["PostgresArtifactStore"]
