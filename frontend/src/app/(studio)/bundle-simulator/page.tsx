"use client";

import { useEffect, useMemo, useState } from "react";
import { useWallet } from "@solana/wallet-adapter-react";
import Topbar from "@/components/shell/Topbar";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import { ReplayMetricsGrid, type ReplayMetricKey } from "@/components/charts/replay";
import { toToastMessage } from "@/lib/api/errors";
import {
  bundleSimulatorService,
  type BundleForkSpecRequest,
  type BundleSimulatorRequest,
  type BundleSimulatorResponse,
  type CalibrationBlock,
  type CalibrationThreshold,
} from "@/lib/services/bundleSimulatorService";

const DEFAULT_TIP_RECIPIENT = "T1pestRecipientPubkey11111111111111111111111";
const DEFAULT_CONTEXT_SLOT = "420196842";
const MAX_SLIDER_TIP = 250_000;
const BUNDLE_REPLAY_METRIC_KEYS: ReplayMetricKey[] = [
  "bundle_landing_rate",
  "tip_efficiency",
  "slot_inclusion_latency",
];

const SAMPLE_TXS = [
  "base58encodedtx1",
  "base58encodedtx2",
];

const numberFormatter = new Intl.NumberFormat("en-US");

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return numberFormatter.format(value);
}

function formatLamports(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${numberFormatter.format(value)} lamports`;
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function parseContextSlot(value: string): "latest" | number {
  const trimmed = value.trim();
  if (trimmed === "" || trimmed.toLowerCase() === "latest") return "latest";
  const slot = Number(trimmed.replaceAll("_", ""));
  if (!Number.isInteger(slot) || slot < 0) {
    throw new Error("Context slot must be latest or a non-negative integer.");
  }
  return slot;
}

function walletForkSpecFor(
  walletPubkey: string | null,
  contextSlot: string,
): BundleForkSpecRequest | null {
  if (!walletPubkey) return null;
  try {
    const slot = parseContextSlot(contextSlot);
    if (slot === "latest") return null;
    return {
      slot,
      protocols: [],
      include_wallet_accounts: [walletPubkey],
    };
  } catch {
    return null;
  }
}

function parseTxInput(value: string): string[] {
  const trimmed = value.trim();
  if (!trimmed) throw new Error("Paste at least one serialized transaction.");

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    const parsed = JSON.parse(trimmed) as unknown;
    if (Array.isArray(parsed)) {
      const txs = parsed.filter((item): item is string => typeof item === "string");
      if (txs.length === 0) throw new Error("JSON array must contain transaction strings.");
      return txs;
    }
    if (parsed && typeof parsed === "object") {
      const obj = parsed as Record<string, unknown>;
      const bundle = obj.bundle && typeof obj.bundle === "object"
        ? (obj.bundle as Record<string, unknown>)
        : obj;
      if (Array.isArray(bundle.txs)) {
        const txs = bundle.txs.filter((item): item is string => typeof item === "string");
        if (txs.length > 0) return txs;
      }
    }
    throw new Error("JSON input must be an array, a bundle object, or a full API request.");
  }

  return trimmed
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function thresholdText(threshold: CalibrationThreshold | undefined): string | null {
  if (!threshold) return null;
  if (typeof threshold.relative === "number") {
    return `±${(threshold.relative * 100).toFixed(2)}%`;
  }
  if (typeof threshold.absolute === "number") {
    return `±${threshold.absolute}`;
  }
  return null;
}

function CalibrationPill({
  calibration,
  metric,
}: {
  calibration: CalibrationBlock | null | undefined;
  metric?: string;
}) {
  if (!calibration) {
    return (
      <span
        className="bundle-calibration-pill muted"
        data-testid="bundle-calibration-band"
      >
        No corpus band for this slot
      </span>
    );
  }

  const threshold = metric ? calibration.metric_thresholds[metric] : undefined;
  const label = thresholdText(threshold);
  return (
    <span
      className="bundle-calibration-pill"
      data-testid="bundle-calibration-band"
      data-metric={metric ?? "unmapped"}
    >
      {label ? `${metric}: ${label}` : "Corpus covered, no metric band"}
    </span>
  );
}

function NumericRow({
  label,
  value,
  calibration,
  metric,
}: {
  label: string;
  value: string;
  calibration: CalibrationBlock | null | undefined;
  metric?: string;
}) {
  return (
    <div className="bundle-metric-row" data-testid="bundle-numeric-output">
      <span>{label}</span>
      <strong>{value}</strong>
      <CalibrationPill calibration={calibration} metric={metric} />
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="bundle-json-block">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function BundleSimulatorPage() {
  const { connected, publicKey } = useWallet();
  const [txInput, setTxInput] = useState(JSON.stringify(SAMPLE_TXS, null, 2));
  const [tipLamports, setTipLamports] = useState(100_000);
  const [tipRecipient, setTipRecipient] = useState(DEFAULT_TIP_RECIPIENT);
  const [contextSlot, setContextSlot] = useState(DEFAULT_CONTEXT_SLOT);
  const [walletForkEnabled, setWalletForkEnabled] = useState(false);
  const [optimizerEnabled, setOptimizerEnabled] = useState(true);
  const [targetPercentile, setTargetPercentile] = useState(90);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BundleSimulatorResponse | null>(null);
  const [lastRequest, setLastRequest] = useState<BundleSimulatorRequest | null>(null);
  const walletPubkey = connected && publicKey ? publicKey.toBase58() : null;
  const walletForkSpec = useMemo(
    () => walletForkSpecFor(walletPubkey, contextSlot),
    [walletPubkey, contextSlot],
  );
  const activeWalletForkSpec = walletForkEnabled ? walletForkSpec : null;

  useEffect(() => {
    if (!walletPubkey) setWalletForkEnabled(false);
  }, [walletPubkey]);

  const requestPreview = useMemo<BundleSimulatorRequest | null>(() => {
    try {
      return {
        bundle: {
          txs: parseTxInput(txInput),
          tip_lamports: tipLamports,
          tip_recipient: tipRecipient.trim(),
        },
        context_slot: parseContextSlot(contextSlot),
        fork_spec: activeWalletForkSpec,
        search_tip_optimizer: optimizerEnabled
          ? { target_percentile: targetPercentile }
          : null,
      };
    } catch {
      return null;
    }
  }, [
    txInput,
    tipLamports,
    tipRecipient,
    contextSlot,
    activeWalletForkSpec,
    optimizerEnabled,
    targetPercentile,
  ]);

  const minTipToLand =
    result?.tip_optimizer?.minimum_tip_lamports ??
    result?.expected_tip_to_land_lamports ??
    0;
  const sliderMax = Math.max(MAX_SLIDER_TIP, tipLamports, minTipToLand * 2, 1);
  const tipPercent = Math.min(100, Math.max(0, (tipLamports / sliderMax) * 100));
  const minTipPercent = Math.min(100, Math.max(0, (minTipToLand / sliderMax) * 100));

  async function handleSubmit() {
    setError(null);
    let request: BundleSimulatorRequest;
    try {
      request = {
        bundle: {
          txs: parseTxInput(txInput),
          tip_lamports: tipLamports,
          tip_recipient: tipRecipient.trim(),
        },
        context_slot: parseContextSlot(contextSlot),
        fork_spec: null,
        search_tip_optimizer: optimizerEnabled
          ? { target_percentile: targetPercentile }
          : null,
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid bundle input.");
      return;
    }

    if (walletForkEnabled && !walletForkSpec) {
      setError("Wallet forks require a connected wallet and a numeric context slot.");
      return;
    }

    setLoading(true);
    request = {
      ...request,
      fork_spec: walletForkEnabled ? walletForkSpec : null,
    };
    setLastRequest(request);
    try {
      const response = await bundleSimulatorService.simulate(request);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(toToastMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Topbar
        title="Bundle Simulator"
        spec={{ execution: { model: "solana_like" } }}
      />

      <div id="content" className="fade-in" data-testid="bundle-simulator-page">
        <div className="bundle-simulator-layout">
          <Card
            title="Bundle request"
            badge={<Badge variant="blue">POST /v1/simulate-bundle</Badge>}
          >
            <div className="form-group">
              <label htmlFor="bundle-txs">Bundle JSON or base58/base64 txs</label>
              <textarea
                id="bundle-txs"
                data-testid="bundle-paste-box"
                value={txInput}
                onChange={(event) => setTxInput(event.target.value)}
                spellCheck={false}
                rows={9}
                className="bundle-textarea mono"
              />
              <div className="hint">
                Accepts a JSON array, a bundle object with <span className="mono">txs</span>,
                a full API request, or whitespace-separated serialized transactions.
              </div>
            </div>

            <div className="grid-2">
              <div className="form-group">
                <label htmlFor="bundle-context-slot">Context slot</label>
                <input
                  id="bundle-context-slot"
                  data-testid="bundle-context-slot"
                  value={contextSlot}
                  onChange={(event) => setContextSlot(event.target.value)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="bundle-tip-recipient">Tip recipient</label>
                <input
                  id="bundle-tip-recipient"
                  data-testid="bundle-tip-recipient"
                  value={tipRecipient}
                  onChange={(event) => setTipRecipient(event.target.value)}
                />
              </div>
            </div>

            <div className="bundle-wallet-fork" data-testid="wallet-fork-control">
              <div>
                <div className="bundle-section-title">Wallet fork</div>
                <div className="hint">
                  {walletPubkey
                    ? `Fork slot ${walletForkSpec?.slot ?? "N"} with ${walletPubkey.slice(0, 4)}...${walletPubkey.slice(-4)}`
                    : "Connect a wallet in the header to fork with positions."}
                </div>
              </div>
              <button
                className={`btn btn-secondary btn-sm${walletForkEnabled ? " wallet-fork-active" : ""}`}
                data-testid="wallet-fork-button"
                type="button"
                disabled={!walletForkSpec}
                onClick={() => setWalletForkEnabled((enabled) => !enabled)}
              >
                {walletForkEnabled ? "Wallet fork on" : "Fork with my positions"}
              </button>
            </div>
            {walletForkEnabled && walletForkSpec ? (
              <div
                className="bundle-preview compact"
                data-testid="wallet-fork-spec-preview"
              >
                <JsonBlock value={walletForkSpec} />
              </div>
            ) : null}

            <div className="form-group">
              <label htmlFor="bundle-tip-slider">Jito tip</label>
              <div className="bundle-tip-control">
                <input
                  id="bundle-tip-slider"
                  data-testid="bundle-tip-slider"
                  type="range"
                  min={0}
                  max={sliderMax}
                  step={1_000}
                  value={tipLamports}
                  onChange={(event) => setTipLamports(Number(event.target.value))}
                  style={{ accentColor: "var(--accent)", width: "100%" }}
                />
                <input
                  data-testid="bundle-tip-input"
                  type="number"
                  min={0}
                  step={1_000}
                  value={tipLamports}
                  onChange={(event) => setTipLamports(Math.max(0, Number(event.target.value)))}
                />
              </div>
              <div className="bundle-tip-track" aria-hidden="true">
                <span className="bundle-tip-fill" style={{ width: `${tipPercent}%` }} />
                <span
                  className="bundle-tip-marker"
                  style={{ left: `${minTipPercent}%` }}
                  title={`Minimum to land: ${formatLamports(minTipToLand)}`}
                />
              </div>
              <div className="hint">
                Minimum to land: <strong>{formatLamports(minTipToLand)}</strong>
              </div>
            </div>

            <div className="bundle-optimizer-row">
              <label className="bundle-toggle">
                <input
                  data-testid="bundle-tip-optimizer-toggle"
                  type="checkbox"
                  checked={optimizerEnabled}
                  onChange={(event) => setOptimizerEnabled(event.target.checked)}
                />
                Tip optimizer
              </label>
              <label className="bundle-percentile">
                Target percentile
                <input
                  data-testid="bundle-target-percentile"
                  type="number"
                  min={1}
                  max={99}
                  value={targetPercentile}
                  disabled={!optimizerEnabled}
                  onChange={(event) =>
                    setTargetPercentile(
                      Math.max(1, Math.min(99, Number(event.target.value))),
                    )
                  }
                />
              </label>
            </div>

            {error && (
              <div className="bundle-error" data-testid="bundle-error">
                {error}
              </div>
            )}

            <div className="bundle-actions">
              <button
                className="btn btn-primary cta-primary"
                data-testid="bundle-run-button"
                onClick={handleSubmit}
                disabled={loading}
              >
                {loading ? "Running..." : "Run bundle"}
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => {
                  setTxInput(JSON.stringify(SAMPLE_TXS, null, 2));
                  setTipLamports(100_000);
                  setContextSlot(DEFAULT_CONTEXT_SLOT);
                  setOptimizerEnabled(true);
                  setTargetPercentile(90);
                }}
                type="button"
              >
                Load sample
              </button>
            </div>

            <div className="bundle-preview" data-testid="bundle-request-preview">
              <div className="bundle-section-title">Request preview</div>
              {requestPreview ? (
                <JsonBlock value={requestPreview} />
              ) : (
                <p className="hint">Fix the input above to preview the API request.</p>
              )}
            </div>
          </Card>

          <Card
            title="Simulation result"
            badge={
              result?.calibration ? (
                <Badge variant="green">Corpus covered</Badge>
              ) : (
                <Badge variant="yellow">Uncalibrated slot</Badge>
              )
            }
          >
            {!result ? (
              <div className="bundle-empty-state" data-testid="bundle-empty-result">
                Run a bundle to populate landing probability, tip, profit, CU,
                ALT, write-lock, metrics, and calibration fields from the API.
              </div>
            ) : (
              <div data-testid="bundle-result-panel">
                <div className="bundle-result-summary">
                  <div>
                    <span>Landing probability</span>
                    <strong data-testid="bundle-landing-probability">
                      {formatPercent(result.landing_probability)}
                    </strong>
                    <CalibrationPill
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                  </div>
                  <div>
                    <span>Expected tip to land</span>
                    <strong>{formatLamports(result.expected_tip_to_land_lamports)}</strong>
                    <CalibrationPill
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                  </div>
                </div>

                <div className="bundle-panel">
                  <div className="bundle-section-title">Profit distribution</div>
                  <NumericRow
                    label="p10"
                    value={formatLamports(result.profit_distribution.p10)}
                    calibration={result.calibration}
                    metric="total_volume"
                  />
                  <NumericRow
                    label="p50"
                    value={formatLamports(result.profit_distribution.p50)}
                    calibration={result.calibration}
                    metric="total_volume"
                  />
                  <NumericRow
                    label="p75"
                    value={formatLamports(result.profit_distribution.p75)}
                    calibration={result.calibration}
                    metric="total_volume"
                  />
                  <NumericRow
                    label="p90"
                    value={formatLamports(result.profit_distribution.p90)}
                    calibration={result.calibration}
                    metric="total_volume"
                  />
                  <NumericRow
                    label="p99"
                    value={formatLamports(result.profit_distribution.p99)}
                    calibration={result.calibration}
                    metric="total_volume"
                  />
                </div>

                <div className="bundle-panel">
                  <div className="bundle-section-title">ALT compression</div>
                  <NumericRow
                    label="Uncompressed bytes"
                    value={formatNumber(result.alt_compression.uncompressed_bytes)}
                    calibration={result.calibration}
                  />
                  <NumericRow
                    label="Compressed bytes"
                    value={formatNumber(result.alt_compression.compressed_bytes)}
                    calibration={result.calibration}
                  />
                  <NumericRow
                    label="Used ALTs"
                    value={formatNumber(result.alt_compression.used_alts?.length ?? 0)}
                    calibration={result.calibration}
                  />
                </div>

                <div className="bundle-panel">
                  <div className="bundle-section-title">CU budget</div>
                  {result.cu_budget.tx_cu_used.map((cu, index) => (
                    <NumericRow
                      key={`${index}-${cu}`}
                      label={`Tx ${index + 1} CU`}
                      value={formatNumber(cu)}
                      calibration={result.calibration}
                    />
                  ))}
                  <NumericRow
                    label="Slot CU headroom"
                    value={formatNumber(result.cu_budget.slot_cu_headroom)}
                    calibration={result.calibration}
                  />
                  <div className="bundle-meta-row">
                    <span>Slot full</span>
                    <strong>{result.cu_budget.slot_full ? "yes" : "no"}</strong>
                  </div>
                </div>

                <div className="bundle-panel">
                  <div className="bundle-section-title">Write-lock contention</div>
                  <NumericRow
                    label="Contended locks"
                    value={formatNumber(result.write_lock_contention.contended_lock_count ?? 0)}
                    calibration={result.calibration}
                  />
                  <NumericRow
                    label="Relaxed locks"
                    value={formatNumber(result.write_lock_contention.relaxed_lock_count ?? 0)}
                    calibration={result.calibration}
                  />
                  <div className="bundle-lock-list">
                    {result.write_lock_contention.blocking_pubkeys.length === 0
                      ? "No blocking pubkeys returned."
                      : result.write_lock_contention.blocking_pubkeys.join(", ")}
                  </div>
                </div>

                {result.tip_optimizer && (
                  <div className="bundle-panel" data-testid="bundle-tip-optimizer">
                    <div className="bundle-section-title">Tip optimizer</div>
                    <NumericRow
                      label="Target percentile"
                      value={`${result.tip_optimizer.target_percentile}`}
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                    <NumericRow
                      label="Minimum tip"
                      value={formatLamports(result.tip_optimizer.minimum_tip_lamports)}
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                    <NumericRow
                      label="Safety margin"
                      value={formatLamports(result.tip_optimizer.safety_margin_lamports)}
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                    <NumericRow
                      label="Priority-fee quote"
                      value={formatLamports(result.tip_optimizer.priority_fee_quote_lamports)}
                      calibration={result.calibration}
                      metric="tips_paid"
                    />
                  </div>
                )}

                <div className="bundle-panel">
                  <div className="bundle-section-title">Replay metrics</div>
                  <NumericRow
                    label="Bundle landing rate"
                    value={`${formatPercent(result.metrics.replay.bundle_landing_rate.value)} (${result.metrics.replay.bundle_landing_rate.sample_size} samples)`}
                    calibration={result.calibration}
                    metric="tips_paid"
                  />
                  <NumericRow
                    label="Tip efficiency"
                    value={`${formatNumber(result.metrics.replay.tip_efficiency.value)} ${result.metrics.replay.tip_efficiency.unit}`}
                    calibration={result.calibration}
                    metric="tips_paid"
                  />
                  <NumericRow
                    label="Slot inclusion latency"
                    value={`${formatNumber(result.metrics.replay.slot_inclusion_latency.value)} ${result.metrics.replay.slot_inclusion_latency.unit}`}
                    calibration={result.calibration}
                  />
                  <NumericRow
                    label="Latency p95"
                    value={`${formatNumber(result.metrics.replay.slot_inclusion_latency.p95)} ${result.metrics.replay.slot_inclusion_latency.unit}`}
                    calibration={result.calibration}
                  />
                  <div style={{ marginTop: 14 }}>
                    <ReplayMetricsGrid
                      metrics={result.metrics.replay}
                      metricKeys={BUNDLE_REPLAY_METRIC_KEYS}
                    />
                  </div>
                </div>

                <div className="bundle-panel" data-testid="bundle-api-response">
                  <div className="bundle-section-title">Exact API response</div>
                  <JsonBlock value={result} />
                </div>

                {lastRequest && (
                  <div className="bundle-panel">
                    <div className="bundle-section-title">Last submitted request</div>
                    <JsonBlock value={lastRequest} />
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
