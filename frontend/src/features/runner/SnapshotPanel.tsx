"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useToast } from "@/components/feedback/ToastProvider";
import { useStudioStore } from "@/lib/state/useStudioStore";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import {
  simulationService,
  type Snapshot,
} from "@/lib/services/simulationService";
import { runnerService } from "@/lib/services/runnerService";
import { toToastMessage } from "@/lib/api/errors";

interface SnapshotPanelProps {
  runId: string;
  currentRound: number;
  simulationId: string | null;
}

export default function SnapshotPanel({
  runId,
  currentRound,
  simulationId,
}: SnapshotPanelProps) {
  const router = useRouter();
  const { showToast } = useToast();
  const { setInteractiveEngine } = useStudioStore();
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [snapName, setSnapName] = useState("");
  const [creating, setCreating] = useState(false);
  const [forking, setForking] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await simulationService.getSnapshots(runId);
      setSnapshots(list);
    } catch (err) {
      setError(toToastMessage(err));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const createSnapshot = useCallback(async () => {
    if (!simulationId) {
      showToast("No live engine to snapshot", "error");
      return;
    }
    if (creating) return;
    setCreating(true);
    try {
      const name = snapName || `snapshot-r${currentRound}`;
      await simulationService.createSnapshot(simulationId, currentRound, name);
      showToast(`Snapshot "${name}" created at round ${currentRound}`, "success");
      setSnapName("");
      setShowCreate(false);
      await reload();
    } catch (err) {
      showToast(`Snapshot failed: ${toToastMessage(err)}`, "error");
    } finally {
      setCreating(false);
    }
  }, [simulationId, snapName, currentRound, creating, reload, showToast]);

  const branchFromSnapshot = useCallback(
    async (snap: Snapshot) => {
      if (forking) return;
      setForking(snap.id);
      try {
        const result = await runnerService.forkFromSnapshot(snap.id);
        setInteractiveEngine(result.runId, result.simulationId);
        showToast(
          `Forked from "${snap.name}" — opening new run`,
          "success",
        );
        router.push(`/runner/${result.runId}`);
      } catch (err) {
        showToast(`Fork failed: ${toToastMessage(err)}`, "error");
      } finally {
        setForking(null);
      }
    },
    [forking, setInteractiveEngine, router, showToast],
  );

  return (
    <Card
      title="Snapshots & Branching"
      actions={
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => setShowCreate(true)}
          disabled={!simulationId}
        >
          + Snapshot
        </button>
      }
    >
      {showCreate && (
        <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "end" }}>
          <div className="form-group" style={{ flex: 1, margin: 0 }}>
            <label>Name</label>
            <input
              type="text"
              placeholder={`snapshot-r${currentRound}`}
              value={snapName}
              onChange={(e) => setSnapName(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={createSnapshot}
            disabled={creating}
          >
            {creating ? "Saving…" : "Create"}
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => setShowCreate(false)}
            disabled={creating}
          >
            Cancel
          </button>
        </div>
      )}

      {loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <Skeleton height={32} />
          <Skeleton height={32} />
        </div>
      )}

      {!loading && error && (
        <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{error}</p>
      )}

      {!loading && !error && snapshots.length === 0 && (
        <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>No snapshots yet.</p>
      )}

      {!loading && !error && snapshots.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {snapshots.map((snap) => (
            <div
              key={snap.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "8px 12px",
                background: "var(--bg-2)",
                borderRadius: "var(--radius)",
                border: "1px solid var(--border)",
              }}
            >
              <span className="mono" style={{ fontSize: ".78rem", color: "var(--accent)" }}>
                R{snap.round}
              </span>
              <span style={{ fontSize: ".85rem", flex: 1 }}>{snap.name}</span>
              {snap.parentSnapshotId && <Badge variant="purple">branched</Badge>}
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => branchFromSnapshot(snap)}
                title="Branch from this snapshot"
                disabled={forking === snap.id}
              >
                {forking === snap.id ? "Forking…" : "Branch"}
              </button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
