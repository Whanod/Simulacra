"use client";

import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import { useStudioStore } from "@/lib/state/useStudioStore";
import Modal from "@/components/feedback/Modal";
import Card from "@/components/ui/Card";
import InfoTooltip from "@/components/ui/InfoTooltip";
import Skeleton from "@/components/feedback/Skeleton";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  type AgentGroup,
  type CostModel,
  type ExecutionModel,
  type FeeModel,
  type FeedType,
  type InfoFilter,
  type MarketTokenSpec,
  type OrderingModel,
  type RunSpec,
  type ValidatorSetEntry,
  type WorldMarketBlock,
  type WorldMarketLink,
} from "@/lib/types";
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import WorldBuilder, {
  type MarketBlock,
  type MarketLink,
} from "@/features/builder/WorldBuilder";
import AgentGroupsDesigner from "@/features/builder/AgentGroupsDesigner";
import { RegistrySelect } from "@/features/builder/RegistrySelect";
// Side-effect import: wires the special-editor plugin registry used
// by nested SchemaForms (e.g. per-group agent param editors and the
// world-markets-graph plugin).
import "@/components/schema/registerSpecialEditors";
import { RawSpecEditor } from "@/components/schema/RawSpecEditor";
import {
  draftFromApiSpec,
  draftToApiSpec,
} from "@/lib/api/adapters/drafts";
import type { SimulationDraft } from "@/lib/types/drafts";
import {
  simulationService,
  isInteractiveBuild,
} from "@/lib/services/simulationService";
import {
  calibrationService,
  type CalibrationCorpusSlot,
} from "@/lib/services/calibrationService";
import {
  replayService,
  type ReplayCounterfactualSpec,
  type ReplayResult,
} from "@/lib/services/replayService";
import type { SimTemplate } from "@/lib/api/adapters/templates";
import { specFromApi, type ApiRunSpec } from "@/lib/api/adapters/runs";
import SyntheticBadge from "@/components/SyntheticBadge";
import { useAsync } from "@/lib/hooks/useAsync";
import { useRegistryContract } from "@/lib/hooks/useRegistryContract";
import { ApiError } from "@/lib/api/errors";
import type { RegTab } from "@/lib/types/registry";
import { chainIdiomFromSpec, useChainIdiom } from "@/lib/hooks/useChainIdiom";
import { useDataTheme } from "@/lib/hooks/useDataTheme";

const chainBlockTimeDefault = (exec: string): number =>
  chainIdiomFromSpec({
    execution: { model: exec === "solana" ? "solana_like" : exec },
  }).time_default;

const chainEpochDefault = (exec: string): number =>
  chainIdiomFromSpec({
    execution: { model: exec === "solana" ? "solana_like" : exec },
  }).epoch_default;

// Known SPL mint → symbol labels for the Protocol variables dropdowns.
// Captures only contain SOL/USDC pairs today; extend as the corpus grows.
const KNOWN_MINT_SYMBOLS: Record<string, string> = {
  So11111111111111111111111111111111111111112: "SOL",
  EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: "USDC",
};

function mintLabel(mint: string): string {
  const sym = KNOWN_MINT_SYMBOLS[mint];
  return sym ? `${mint.slice(0, 4)}…${mint.slice(-4)} — ${sym}` : mint;
}

const WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc";

interface WhirlpoolCorpusEntry {
  slot: number;
  pubkey: string;
  tokenMintA: string;
  tokenMintB: string;
  tokenVaultA?: string;
  tokenVaultB?: string;
  pairLabel: string;
}

function whirlpoolEntriesFromCorpus(
  slots: CalibrationCorpusSlot[],
): WhirlpoolCorpusEntry[] {
  const out: WhirlpoolCorpusEntry[] = [];
  for (const slot of slots) {
    const expected = slot.expected as Record<string, unknown> | undefined;
    const wp = expected?.whirlpool as Record<string, unknown> | undefined;
    if (!wp) {
      // Skip slots that aren't whirlpool captures.
      if (!slot.programs.includes(WHIRLPOOL_PROGRAM)) continue;
      continue;
    }
    const pubkey = typeof wp.pubkey === "string" ? wp.pubkey : null;
    const tokenMintA =
      typeof wp.token_mint_a === "string" ? wp.token_mint_a : null;
    const tokenMintB =
      typeof wp.token_mint_b === "string" ? wp.token_mint_b : null;
    if (!pubkey || !tokenMintA || !tokenMintB) continue;
    const symA = KNOWN_MINT_SYMBOLS[tokenMintA] ?? tokenMintA.slice(0, 4);
    const symB = KNOWN_MINT_SYMBOLS[tokenMintB] ?? tokenMintB.slice(0, 4);
    out.push({
      slot: slot.slot,
      pubkey,
      tokenMintA,
      tokenMintB,
      tokenVaultA:
        typeof wp.token_vault_a === "string" ? wp.token_vault_a : undefined,
      tokenVaultB:
        typeof wp.token_vault_b === "string" ? wp.token_vault_b : undefined,
      pairLabel: `${symA}/${symB}`,
    });
  }
  return out;
}

const REGISTRY_SEED_KEY = "builder-seed-registry";
const SWEEP_SEED_KEY = "builder-seed-params";

interface BuilderSeed {
  category: RegTab;
  type: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeSeedToken(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

function parseQuerySeed(value: string | null): BuilderSeed | null {
  if (!value) return null;
  const [category, ...rest] = value.split(":");
  if (rest.length === 0) return null;
  return {
    category: category as RegTab,
    type: rest.join(":"),
  };
}

function loadRegistrySeedFromStorage(): BuilderSeed | null {
  try {
    const raw = sessionStorage.getItem(REGISTRY_SEED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return null;
    if (typeof parsed.category !== "string" || typeof parsed.type !== "string") return null;
    return { category: parsed.category as RegTab, type: parsed.type };
  } catch {
    return null;
  }
}

function loadSweepSeedParamsFromStorage(): Record<string, number> | null {
  try {
    const raw = sessionStorage.getItem(SWEEP_SEED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return null;
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
    }
    return Object.keys(out).length > 0 ? out : null;
  } catch {
    return null;
  }
}

// ── Syntax highlighting for JSON ─────────────────────────
function syntaxHighlight(json: string): string {
  return json.replace(
    /("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "num";
      if (/^"/.test(match)) cls = /:$/.test(match) ? "key" : "str";
      else if (/true|false/.test(match)) cls = "bool";
      else if (/null/.test(match)) cls = "null";
      return `<span class="${cls}">${match}</span>`;
    },
  );
}

// ═════════════════════════════════════════════════════════
// BUILDER PAGE
// ═════════════════════════════════════════════════════════
export default function BuilderPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { showToast } = useToast();
  const { setInteractiveEngine } = useStudioStore();
  const { contract: registryContract } = useRegistryContract();
  const consumedSeedRef = useRef<string | null>(null);

  // ── Step state (template picker vs form) ──────────────
  const [step, setStep] = useState<"pick" | "form">("pick");

  // ── Builder form state ────────────────────────────────
  const [simName, setSimName] = useState("cfamm-noise-test");
  const [seed, setSeed] = useState(42);
  const [numRounds, setNumRounds] = useState(200);
  const [snapshotInterval, setSnapshotInterval] = useState(10);
  const [numericMode, setNumericMode] = useState<"FIXED_POINT" | "FLOAT_MODE">("FIXED_POINT");

  const [bClock, setBClock] = useState("block");
  const [blockTime, setBlockTime] = useState(1);
  const [epochLength, setEpochLength] = useState(1);
  const [skipRate, setSkipRate] = useState(0);

  const [bMarket, setBMarket] = useState("cfamm");
  const [numAssets, setNumAssets] = useState(4);
  const [initialLiquidity, setInitialLiquidity] = useState(1_000_000);
  const [tokenDecimals, setTokenDecimals] = useState(9);
  const [worldBlocks, setWorldBlocks] = useState<MarketBlock[]>([]);
  const [worldLinks, setWorldLinks] = useState<MarketLink[]>([]);

  // Whirlpool-only protocol variables. Round-tripped into market.params
  // on the wire; backend rejects whirlpool specs without corpus_slot +
  // pool_pubkey (defi_sim/engine/specs.py whirlpool guard).
  const [wpCorpusSlot, setWpCorpusSlot] = useState<number | null>(null);
  const [wpPoolPubkey, setWpPoolPubkey] = useState("");
  const [wpPoolAccountId, setWpPoolAccountId] = useState("");
  const [wpTokenAId, setWpTokenAId] = useState("");
  const [wpTokenBId, setWpTokenBId] = useState("");
  const [wpTokenASymbol, setWpTokenASymbol] = useState("");
  const [wpTokenBSymbol, setWpTokenBSymbol] = useState("");

  const [bFee, setBFee] = useState("flat");
  const [feeRate, setFeeRate] = useState(30);

  const [bExec, setBExec] = useState("direct");
  const [bOrdering, setBOrdering] = useState("fifo");
  const [bGas, setBGas] = useState("zero");
  const [bScheduler, setBScheduler] = useState("serial");
  const [bInfo, setBInfo] = useState("full");

  // US-002: Solana compute budget (per-tx / per-slot / per-writable-account
  // CU caps). Defaults match current mainnet; only forwarded to the backend
  // when execution is solana and the user changes them off the preset.
  const [cbPreset, setCbPreset] = useState("current");
  const [cbPerSlot, setCbPerSlot] = useState(60_000_000);
  const [cbPerTx, setCbPerTx] = useState(1_400_000);
  const [cbPerAccount, setCbPerAccount] = useState(12_000_000);

  // US-004: Solana submission-path priors. Defaults match SubmissionPathPriors
  // dataclass — Jito relayer is calibrated against the 2026-05-05 mainnet
  // bundle corpus; RPC and TPU/QUIC defaults are conservative starting points
  // (their drops aren't visible in finalized chain state).
  const [spRpc, setSpRpc] = useState(0.85);
  const [spTpuQuic, setSpTpuQuic] = useState(0.95);
  const [spJito, setSpJito] = useState(0.78);
  const [spCongestionPenalty, setSpCongestionPenalty] = useState(0.005);
  const [spCalibratedAt, setSpCalibratedAt] = useState<string | null>(
    "2026-05-05",
  );

  // US-006: Solana oracle picker. One-click choices map to the Python
  // presets in `defi_sim.engine.oracles.presets`. Default `none` keeps
  // the legacy "no oracle attached" behaviour for non-oracle markets.
  const [bOraclePreset, setBOraclePreset] = useState<
    "none" | "pyth_pull" | "pyth_lazer" | "switchboard_on_demand"
  >("none");

  // US-007: per-token extension panel. Holds the token list with
  // standard/LST/transfer-hook fields editable from the builder. Empty
  // by default; populated from `s.market.tokens` when a template is
  // applied or auto-seeded with [SOL, USDC] when the user picks the
  // Solana execution model.
  const [bTokens, setBTokens] = useState<MarketTokenSpec[]>([]);

  // US-010 PRD line 747: priority-fee market tuning (advanced). Defaults
  // mirror `PriorityFeeMarketSpec`: 150 slot rolling window, 30 slot EWMA
  // half-life, 1 µ-lamport floor, 5% percentile-shift event threshold.
  // Forwarded to the backend as `execution.params.priority_fee_market`
  // when execution is solana.
  const [pfmWindowSlots, setPfmWindowSlots] = useState(150);
  const [pfmEwmaHalfLife, setPfmEwmaHalfLife] = useState(30);
  const [pfmFloor, setPfmFloor] = useState(1);
  const [pfmThreshold, setPfmThreshold] = useState(0.05);

  // US-014 PRD line 1125: adversarial conditions. Defaults mirror
  // `ForkSpec`: 0% fork probability (off), 5-slot max reorg depth.
  // Forwarded to the backend as `execution.params.fork_spec` when
  // execution is solana.
  const [forkProbability, setForkProbability] = useState(0);
  const [forkMaxReorgDepth, setForkMaxReorgDepth] = useState(5);

  // Lighthouse — pre-roll seeds the priority-fee distribution before
  // slot 0 so the JitoSearcher gets a realistic percentile target on
  // its first sandwich attempt. Off by default; lighthouse template
  // turns it on. Round-tripped to backend as
  // `execution.params.priority_fee_market.pre_roll`.
  const [preRollEnabled, setPreRollEnabled] = useState(false);
  const [preRollSlots, setPreRollSlots] = useState(200);
  const [preRollAccounts, setPreRollAccounts] = useState<string[]>([]);
  const [preRollCuPriceMin, setPreRollCuPriceMin] = useState(1_000);
  const [preRollCuPriceMax, setPreRollCuPriceMax] = useState(50_000);
  const [preRollObsPerSlot, setPreRollObsPerSlot] = useState(2);
  const [preRollSeed, setPreRollSeed] = useState(1337);

  // Lighthouse — bundle auction config. Off by default; lighthouse
  // turns it on with a calibrated tip-quote curve. Round-tripped as
  // `execution.params.bundle_auction`.
  const [bundleAuctionEnabled, setBundleAuctionEnabled] = useState(false);
  const [bundleMaxBundlesPerSlot, setBundleMaxBundlesPerSlot] = useState(5);
  const [bundleStakePoolShare, setBundleStakePoolShare] = useState(0.05);
  const [bundleTipQuoteCurvePath, setBundleTipQuoteCurvePath] = useState("");

  // Lighthouse — Address Lookup Tables. Top-level on the spec
  // (`spec.alts`); jito_searcher agents reference them by id.
  const [bAlts, setBAlts] = useState<
    Array<{ id: string; entries: string[] }>
  >([]);

  // Lighthouse — auction visibility / cost-token config. Default
  // empty so non-lighthouse runs don't acquire spurious values.
  const [costToken, setCostToken] = useState("");
  const [visibleRoles, setVisibleRoles] = useState<string[]>([]);

  // Registry-driven role list for the visible_roles chip-picker. Pulls
  // every entity type under the agents category so power users can
  // toggle any backend agent role without remembering its string.
  const registryAgentTypes = useMemo(() => {
    if (!registryContract) return [] as string[];
    const types: string[] = [];
    for (const cat of registryContract.categories) {
      if (cat.key !== "agents" && cat.key !== "reg-agents") continue;
      for (const ent of cat.entities) types.push(ent.type);
    }
    return Array.from(new Set(types)).sort();
  }, [registryContract]);

  // US-003 PRD line 636: "Fork mainnet at slot N" affordance. Distinct
  // from the adversarial `fork_spec` above — this configures initial
  // state hydration from a real mainnet slot via `ForkExecution` /
  // `materialize_fork`. Forwarded to the backend as
  // `execution.params.fork_mainnet` when execution is solana and the
  // user has enabled the panel. Currently UX-only; backend wiring lands
  // alongside `build_forked_engine`.
  const [forkMainnetEnabled, setForkMainnetEnabled] = useState(false);
  const [forkMainnetSlot, setForkMainnetSlot] = useState<number | "now">(
    "now",
  );
  // PRD line 638: lists every protocol with a `state_hydrator`. Phase 3
  // hydrators land progressively — `available: false` entries render
  // disabled with the dependency note from the same bullet.
  const FORK_MAINNET_PROTOCOLS: ReadonlyArray<{
    id: string;
    label: string;
    available: boolean;
  }> = [
    { id: "whirlpool", label: "Whirlpool (Orca)", available: true },
    { id: "dlmm", label: "DLMM (Meteora)", available: false },
    { id: "marginfi", label: "MarginFi", available: false },
    { id: "raydium_clmm", label: "Raydium CLMM", available: false },
    { id: "pyth_pull_sol", label: "Pyth Pull (SOL/USD)", available: false },
  ];
  const [forkMainnetProtocols, setForkMainnetProtocols] = useState<string[]>([
    "whirlpool",
  ]);
  const [forkMainnetWallet, setForkMainnetWallet] = useState("");

  // US-012 PRD line 974: validator set surfaced under the Solana
  // execution panel. Default seed is one Jito-Solana validator at 100%
  // stake — matches the engine's default LeaderSchedule (specs.py:1320)
  // and lets users add Jito-Solana / vanilla validators with custom
  // stake-pool revenue shares without dropping into the raw-spec editor.
  const [bValidators, setBValidators] = useState<ValidatorSetEntry[]>(() => [
    {
      pubkey: "validator-1",
      client: "jito_solana",
      stake_lamports: 1_000_000_000,
      stake_pool_share: 0.05,
      stake_pool_address: "",
      commission_pct: 0.05,
    },
  ]);

  const builderSpecLike = {
    execution: { model: bExec === "solana" ? "solana_like" : bExec },
  };
  const idiom = useChainIdiom(builderSpecLike);
  useDataTheme(builderSpecLike);

  const [totalAgents, setTotalAgents] = useState(40);
  const [defaultCollateral, setDefaultCollateral] = useState(100_000);
  // US-012: agent groups are the single source of truth for the
  // population. Seeded with a default built-in mix so the initial
  // experience matches the old fixed-role builder — users can add,
  // remove, and edit any registry agent type from here on.
  const [groups, setGroups] = useState<AgentGroup[]>(() => [
    { id: "g-noise", type: "noise", weight: 40, params: {} },
    { id: "g-informed", type: "informed", weight: 20, params: {} },
    { id: "g-arb", type: "arbitrageur", weight: 15, params: {} },
    { id: "g-manip", type: "manipulator", weight: 5, params: {} },
    { id: "g-plp", type: "passive_lp", weight: 15, params: {} },
    { id: "g-rlp", type: "rebalancing_lp", weight: 5, params: {} },
  ]);

  const [bFeed, setBFeed] = useState("stochastic");
  const [drift, setDrift] = useState(0.0001);
  const [volatility, setVolatility] = useState(0.02);
  const [initialPrice, setInitialPrice] = useState(1.0);

  const [rewardDist, setRewardDist] = useState("None");
  const [emissionSched, setEmissionSched] = useState("None");

  // ── Replay slot range form (PRD US-002 line 335) ──────
  // Minimal wiring to the POST /v1/replay endpoint. Detailed UX
  // (counterfactual editor, diff viewer) lives under Phase 2.5 UX;
  // this surface only proves the API is callable from the builder.
  const [replaySlotStart, setReplaySlotStart] = useState(160_000_001);
  const [replaySlotEnd, setReplaySlotEnd] = useState(160_000_001);
  const [replayTipBundleId, setReplayTipBundleId] = useState("");
  const [replayTipNewLamports, setReplayTipNewLamports] = useState(0);
  const [replaySubmitting, setReplaySubmitting] = useState(false);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  const handleSubmitReplay = useCallback(async () => {
    setReplaySubmitting(true);
    setReplayError(null);
    setReplayResult(null);
    const counterfactuals: ReplayCounterfactualSpec[] = replayTipBundleId.trim()
      ? [
          {
            kind: "TipReplaceCounterfactual",
            params: {
              target_bundle_id: replayTipBundleId.trim(),
              new_tip_lamports: replayTipNewLamports,
            },
          },
        ]
      : [];
    try {
      const res = await replayService.submitReplay({
        slotStart: replaySlotStart,
        slotEnd: replaySlotEnd,
        counterfactuals,
      });
      setReplayResult(res);
      showToast(`Replay submitted (run ${res.runId})`, "success");
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : (err as Error).message;
      setReplayError(msg);
      showToast(`Replay failed: ${msg}`, "error");
    } finally {
      setReplaySubmitting(false);
    }
  }, [
    replaySlotStart,
    replaySlotEnd,
    replayTipBundleId,
    replayTipNewLamports,
    showToast,
  ]);

  // ── Modal + side panel state ──────────────────────────
  const [specModalOpen, setSpecModalOpen] = useState(false);
  const [sidePanelOpen, setSidePanelOpen] = useState(false);

  // ── Raw-spec fallback editor (US-014) ─────────────────
  // editorMode === "raw" swaps the structured two-column layout for a
  // full JSON textarea backed by the generic draft model. rawDraft is
  // seeded from the current structured spec on entry and is used as
  // the source of truth for action buttons while raw mode is active.
  const [editorMode, setEditorMode] = useState<"structured" | "raw">(
    "structured",
  );
  const [rawDraft, setRawDraft] = useState<SimulationDraft | null>(null);

  // ── Validation / submission state ─────────────────────
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [isValidating, setIsValidating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // ── Structured editor: section navigator state ────────
  const [activeSection, setActiveSection] = useState<
    | "general"
    | "clock"
    | "market"
    | "protocol_variables"
    | "fee"
    | "execution"
    | "agents"
    | "feeds"
    | "replay"
    | "rewards"
  >("general");
  const [previewTab, setPreviewTab] = useState<"summary" | "issues" | "json">(
    "summary",
  );
  const [secSearch, setSecSearch] = useState("");

  // ── Templates (from backend) ──────────────────────────
  const templatesState = useAsync<SimTemplate[]>(
    () => simulationService.getTemplates(),
    [],
  );

  // ── Corpus (for Protocol variables dropdowns) ─────────
  // Backs the whirlpool slot/pubkey/token pickers. Loads lazily; the
  // pane shows a "loading" hint while it resolves.
  const corpusState = useAsync(() => calibrationService.getCorpus(), []);
  const whirlpoolEntries = useMemo<WhirlpoolCorpusEntry[]>(
    () => whirlpoolEntriesFromCorpus(corpusState.data?.slots ?? []),
    [corpusState.data],
  );

  // The `token_a_id` / `token_b_id` dropdowns are populated with mint
  // pubkeys from the corpus, but templates (e.g. lighthouse) seed them
  // with symbols like "SOL"/"USDC" — those don't match any option, so
  // the select falls back to "— select —". Once the corpus resolves,
  // snap each id to a valid mint: prefer the mint of the currently
  // selected slot, otherwise the first mint in the corpus.
  useEffect(() => {
    if (whirlpoolEntries.length === 0) return;
    const slotEntry =
      wpCorpusSlot !== null
        ? whirlpoolEntries.find((e) => e.slot === wpCorpusSlot)
        : undefined;
    const tokenAMints = new Set(whirlpoolEntries.map((e) => e.tokenMintA));
    const tokenBMints = new Set(whirlpoolEntries.map((e) => e.tokenMintB));
    if (!tokenAMints.has(wpTokenAId)) {
      const fallback = slotEntry?.tokenMintA ?? whirlpoolEntries[0].tokenMintA;
      setWpTokenAId(fallback);
      const sym = KNOWN_MINT_SYMBOLS[fallback];
      if (sym && !wpTokenASymbol) setWpTokenASymbol(sym);
    }
    if (!tokenBMints.has(wpTokenBId)) {
      const fallback = slotEntry?.tokenMintB ?? whirlpoolEntries[0].tokenMintB;
      setWpTokenBId(fallback);
      const sym = KNOWN_MINT_SYMBOLS[fallback];
      if (sym && !wpTokenBSymbol) setWpTokenBSymbol(sym);
    }
  }, [
    whirlpoolEntries,
    wpCorpusSlot,
    wpTokenAId,
    wpTokenBId,
    wpTokenASymbol,
    wpTokenBSymbol,
  ]);

  // Consume ?template=<id> so dashboard featured cards can deep-link
  // straight into the builder with the template applied. Guarded so
  // we only fire once per id, even if templates re-resolve.
  const consumedTemplateRef = useRef<string | null>(null);

  const applyRegistrySeed = useCallback(
    (seedSelection: BuilderSeed): boolean => {
      const category = seedSelection.category;
      const type = normalizeSeedToken(seedSelection.type);
      let applied = true;

      switch (category) {
        case "reg-markets":
          if (type === "cfamm" || type === "clob" || type === "world") {
            setBMarket(type);
          } else {
            applied = false;
          }
          break;
        case "reg-agents": {
          // US-012: seed the population with a single group of the
          // selected agent type. This works for both built-in and
          // unknown registry types because groups carry an open
          // `type` string and SchemaForm fetches metadata by type.
          const seededType = type === "lp" ? "passive_lp" : type;
          setGroups([
            {
              id: `g-${seededType}`,
              type: seededType,
              weight: 100,
              params: {},
            },
          ]);
          break;
        }
        case "reg-clocks":
          if (type === "variable_block") setBClock("variable");
          else if (type === "solana_slot") {
            setBClock("solana_slot");
            setBlockTime(0.4);
            setEpochLength(432_000);
            setSkipRate(0);
          } else setBClock("block");
          break;
        case "reg-ordering":
          setBOrdering(type as OrderingModel);
          break;
        case "reg-gas":
          setBGas(type as CostModel);
          break;
        case "reg-feeds":
          setBFeed(type as FeedType);
          break;
        case "reg-fees":
          setBFee(type as FeeModel);
          break;
        case "reg-exec": {
          const execMap: Record<string, ExecutionModel> = {
            direct: "direct",
            batch: "batch",
            solana_like: "solana",
          };
          const exec = execMap[type];
          if (exec) {
            setBExec(exec);
            if (exec === "solana") {
              setBOrdering("priority");
              setBGas("compute_unit");
              setBScheduler("priority");
              if (bClock === "block") {
                setBClock("solana_slot");
                setBlockTime(0.4);
                setEpochLength(432_000);
                setSkipRate(0);
              }
            }
          } else {
            applied = false;
          }
          break;
        }
        case "reg-information": {
          const infoMap: Record<string, InfoFilter> = {
            full_transparency: "full",
            delayed_information: "delayed",
          };
          const info = infoMap[type];
          if (info) {
            setBInfo(info);
          } else {
            applied = false;
          }
          break;
        }
        default:
          applied = false;
      }

      if (applied) {
        setStep("form");
      }
      return applied;
    },
    [],
  );

  const applySweepSeedParams = useCallback((params: Record<string, number>): string[] => {
    const applied: string[] = [];
    for (const [rawKey, value] of Object.entries(params)) {
      switch (rawKey) {
        case "num_rounds":
        case "config.num_rounds":
          setNumRounds(Math.round(value));
          applied.push(rawKey);
          break;
        case "snapshot_interval":
        case "config.snapshot_interval":
          setSnapshotInterval(Math.round(value));
          applied.push(rawKey);
          break;
        case "seed":
        case "config.seed":
          setSeed(Math.round(value));
          applied.push(rawKey);
          break;
        case "initial_liquidity":
        case "market.initial_liquidity":
        case "market.params.initial_liquidity":
          setInitialLiquidity(Math.round(value));
          applied.push(rawKey);
          break;
        case "token_decimals":
        case "market.token_decimals":
          setTokenDecimals(Math.round(value));
          applied.push(rawKey);
          break;
        case "fee_model.rate_bps":
        case "rate_bps":
        case "fee_rate_bps":
        case "market.fee_model.params.trade_fee_bps":
          setFeeRate(Math.round(value));
          applied.push(rawKey);
          break;
        case "default_collateral":
        case "agents.default_collateral":
        case "agents[0].initial_balances.COLLATERAL":
          setDefaultCollateral(value);
          applied.push(rawKey);
          break;
      }
    }
    if (applied.length > 0) {
      setStep("form");
    }
    return applied;
  }, []);

  useEffect(() => {
    const querySeed = parseQuerySeed(searchParams.get("seed"));
    const rawSeedKey = JSON.stringify({
      query: querySeed,
      registry: typeof window === "undefined" ? null : sessionStorage.getItem(REGISTRY_SEED_KEY),
      sweep: typeof window === "undefined" ? null : sessionStorage.getItem(SWEEP_SEED_KEY),
    });
    if (consumedSeedRef.current === rawSeedKey) return;

    const stagedRegistrySeed = querySeed ?? loadRegistrySeedFromStorage();
    const stagedSweepParams = loadSweepSeedParamsFromStorage();
    let seeded = false;

    if (stagedRegistrySeed) {
      seeded = applyRegistrySeed(stagedRegistrySeed) || seeded;
      sessionStorage.removeItem(REGISTRY_SEED_KEY);
    }

    if (stagedSweepParams) {
      const appliedKeys = applySweepSeedParams(stagedSweepParams);
      if (appliedKeys.length > 0) {
        seeded = true;
        showToast(`Applied ${appliedKeys.length} sweep parameter${appliedKeys.length === 1 ? "" : "s"} to builder`, "success");
      }
      sessionStorage.removeItem(SWEEP_SEED_KEY);
    }

    if (seeded && stagedRegistrySeed) {
      showToast(`Applied ${stagedRegistrySeed.type} preset to builder`, "success");
    }

    consumedSeedRef.current = rawSeedKey;
  }, [applyRegistrySeed, applySweepSeedParams, searchParams, showToast]);

  // ── Exec auto-configure ───────────────────────────────
  const handleExecChange = useCallback((v: string) => {
    setBExec(v);
    if (v === "solana") {
      setBOrdering("priority");
      setBGas("compute_unit");
      setBScheduler("priority");
      // US-001: when switching to solana_like, default the clock to
      // SolanaSlotClock if the user hasn't picked a non-default clock.
      setBClock((cur) => {
        if (cur === "block") {
          setBlockTime(0.4);
          setEpochLength(432_000);
          setSkipRate(0);
          return "solana_slot";
        }
        return cur;
      });
      // US-007: seed the token-extensions panel with Solana defaults
      // (SOL/USDC) when no tokens have been configured yet. Mirrors
      // `default_tokens_for_execution("solana_like")` in the backend.
      setBTokens((cur) =>
        cur.length === 0
          ? [
              { id: "SOL", symbol: "SOL", decimals: 9, standard: "native" },
              { id: "USDC", symbol: "USDC", decimals: 6, standard: "spl" },
            ]
          : cur,
      );
    }
  }, []);

  // Sync block_time and epoch_length to the active chain's defaults
  // (Solana: 0.4s slot time, 432_000 slot epoch; neutral: 1s round, 1 epoch)
  // but only when the current value matches the *previous* chain default,
  // so a custom user-entered value is preserved.
  const prevExecRef = useRef(bExec);
  useEffect(() => {
    if (prevExecRef.current === bExec) return;
    const prevBlockDefault = chainBlockTimeDefault(prevExecRef.current);
    const nextBlockDefault = chainBlockTimeDefault(bExec);
    setBlockTime((cur) => (cur === prevBlockDefault ? nextBlockDefault : cur));
    const prevEpochDefault = chainEpochDefault(prevExecRef.current);
    const nextEpochDefault = chainEpochDefault(bExec);
    setEpochLength((cur) => (cur === prevEpochDefault ? nextEpochDefault : cur));
    // Seed sensible Solana defaults for the protocol-variables panel
    // on entry, and tear them down on exit. Only overrides the empty
    // off-state values so a user's edits (or template-applied values)
    // survive the transition.
    if (bExec === "solana" && prevExecRef.current !== "solana") {
      setCostToken((cur) => (cur === "" ? "USDC" : cur));
      setBundleAuctionEnabled(true);
      setPreRollEnabled(true);
    } else if (prevExecRef.current === "solana" && bExec !== "solana") {
      setCostToken((cur) => (cur === "USDC" ? "" : cur));
      setBundleAuctionEnabled(false);
      setPreRollEnabled(false);
    }
    prevExecRef.current = bExec;
  }, [bExec]);

  const handleGroupsChange = useCallback((next: AgentGroup[]) => {
    setGroups(next);
  }, []);

  const weightSum = groups.reduce((a, g) => a + (g.weight || 0), 0);

  // ── WorldBuilder bridge ───────────────────────────────
  const handleWorldSpecChange = useCallback(
    (blocks: MarketBlock[], links: MarketLink[]) => {
      setWorldBlocks(blocks);
      setWorldLinks(links);
    },
    [],
  );

  // ── Build spec ────────────────────────────────────────
  const buildSpec = useCallback((): RunSpec => {
    // Derive a legacy `mix` block from groups so read paths that
    // still inspect `spec.agents.mix` (e.g. heatmaps, compare) don't
    // go blank. Unknown agent types land under their own key via the
    // index signature on AgentMix.
    const totalWeight = groups.reduce((s, g) => s + (g.weight || 0), 0);
    const mixShares: Record<string, number> = {};
    if (totalWeight > 0) {
      for (const group of groups) {
        const share = (group.weight || 0) / totalWeight;
        mixShares[group.type] = (mixShares[group.type] || 0) + share;
      }
    }
    const mix: RunSpec["agents"]["mix"] = {
      noise: mixShares.noise ?? 0,
      informed: mixShares.informed ?? 0,
      arbitrageur: mixShares.arbitrageur ?? 0,
      manipulator: mixShares.manipulator ?? 0,
      passive_lp: mixShares.passive_lp ?? 0,
      rebalancing_lp: mixShares.rebalancing_lp ?? 0,
    };
    for (const [key, value] of Object.entries(mixShares)) {
      if (!(key in mix)) mix[key] = value;
    }

    const worldMarkets: WorldMarketBlock[] = worldBlocks.map((b) => ({
      id: b.id,
      type: b.type,
      label: b.label,
      tokens: b.tokens,
    }));
    const worldLinksOut: WorldMarketLink[] = worldLinks.map((l) => ({
      from: l.from,
      to: l.to,
      token: l.token,
    }));

    const whirlpoolParams =
      bMarket === "whirlpool"
        ? {
            ...(wpCorpusSlot !== null ? { corpus_slot: wpCorpusSlot } : {}),
            ...(wpPoolPubkey ? { pool_pubkey: wpPoolPubkey } : {}),
            ...(wpPoolAccountId ? { pool_account_id: wpPoolAccountId } : {}),
            ...(wpTokenAId ? { token_a_id: wpTokenAId } : {}),
            ...(wpTokenBId ? { token_b_id: wpTokenBId } : {}),
            ...(wpTokenASymbol ? { token_a_symbol: wpTokenASymbol } : {}),
            ...(wpTokenBSymbol ? { token_b_symbol: wpTokenBSymbol } : {}),
          }
        : null;

    return {
      market: {
        type: bMarket as RunSpec["market"]["type"],
        num_assets: numAssets,
        initial_liquidity: initialLiquidity,
        token_decimals: tokenDecimals,
        ...(bTokens.length > 0 ? { tokens: bTokens } : {}),
        ...(whirlpoolParams && Object.keys(whirlpoolParams).length > 0
          ? { whirlpool_params: whirlpoolParams }
          : {}),
      },
      world:
        bMarket === "world" && worldMarkets.length > 0
          ? { markets: worldMarkets, links: worldLinksOut }
          : undefined,
      clock: {
        type: bClock as RunSpec["clock"]["type"],
        block_time: blockTime,
        epoch_length: epochLength,
        ...(bClock === "solana_slot" ? { skip_rate: skipRate } : {}),
      },
      execution: {
        model: bExec as RunSpec["execution"]["model"],
        ordering: bOrdering as RunSpec["execution"]["ordering"],
        cost_model: bGas as RunSpec["execution"]["cost_model"],
        scheduler: bScheduler as RunSpec["execution"]["scheduler"],
        ...(bExec === "solana"
          ? {
              compute_budget: {
                preset: cbPreset,
                per_slot: cbPerSlot,
                per_tx: cbPerTx,
                per_writable_account: cbPerAccount,
              },
              submission_priors: {
                rpc_landing_prob_baseline: spRpc,
                tpu_quic_landing_prob_baseline: spTpuQuic,
                jito_relayer_landing_prob_baseline: spJito,
                congestion_penalty_per_pct_full: spCongestionPenalty,
                calibrated_at: spCalibratedAt,
              },
              oracle_preset: bOraclePreset,
              priority_fee_market: {
                window_slots: pfmWindowSlots,
                ewma_half_life_slots: pfmEwmaHalfLife,
                floor_micro_lamports: pfmFloor,
                update_event_threshold: pfmThreshold,
                ...(preRollEnabled
                  ? {
                      pre_roll: {
                        slots: preRollSlots,
                        // Fall back to the Whirlpool pool pubkey so the
                        // JitoSearcher gets a sensible percentile target
                        // on slot 0 even if the user hasn't pasted an
                        // explicit account list.
                        accounts:
                          preRollAccounts.length > 0
                            ? preRollAccounts
                            : wpPoolPubkey
                              ? [wpPoolPubkey]
                              : [],
                        cu_price_min: preRollCuPriceMin,
                        cu_price_max: preRollCuPriceMax,
                        observations_per_slot: preRollObsPerSlot,
                        seed: preRollSeed,
                      },
                    }
                  : {}),
              },
              ...(bundleAuctionEnabled
                ? {
                    bundle_auction: {
                      max_bundles_per_slot: bundleMaxBundlesPerSlot,
                      jito_stake_pool_share: bundleStakePoolShare,
                      ...(bundleTipQuoteCurvePath
                        ? { tip_quote_curve_path: bundleTipQuoteCurvePath }
                        : {}),
                    },
                  }
                : {}),
              ...(costToken ? { cost_token: costToken } : {}),
              ...(visibleRoles.length > 0
                ? { visible_roles: visibleRoles }
                : {}),
              fork_spec: {
                fork_probability_per_slot: forkProbability,
                max_reorg_depth_slots: forkMaxReorgDepth,
              },
              validator_set: bValidators.map((v) => ({
                pubkey: v.pubkey,
                client: v.client,
                stake_lamports: v.stake_lamports,
                stake_pool_share: v.stake_pool_share,
                stake_pool_address:
                  v.stake_pool_address && v.stake_pool_address.length > 0
                    ? v.stake_pool_address
                    : null,
                commission_pct: v.commission_pct ?? 0.05,
              })),
            }
          : {}),
      },
      fee_model: {
        type: bFee as RunSpec["fee_model"]["type"],
        rate_bps: feeRate,
      },
      agents: {
        total: totalAgents,
        mix,
        default_collateral: defaultCollateral,
        groups,
      },
      feeds: [
        {
          type: bFeed,
          process: bFeed === "mean_revert" ? "ou" : bFeed === "jump" ? "merton" : "gbm",
          drift,
          volatility,
          initial_price: initialPrice,
        },
      ],
      config: {
        num_rounds: numRounds,
        snapshot_interval: snapshotInterval,
        seed,
        numeric_mode: numericMode,
        information_filter: bInfo === "full" ? "full_transparency" : "delayed",
      },
      ...(bAlts.length > 0 ? { alts: bAlts } : {}),
    };
  }, [
    bMarket, numAssets, initialLiquidity, tokenDecimals, bTokens,
    wpCorpusSlot, wpPoolPubkey, wpPoolAccountId, wpTokenAId, wpTokenBId,
    wpTokenASymbol, wpTokenBSymbol,
    worldBlocks, worldLinks,
    bClock, blockTime, epochLength, skipRate,
    bExec, bOrdering, bGas, bScheduler, cbPreset, cbPerSlot, cbPerTx, cbPerAccount,
    spRpc, spTpuQuic, spJito, spCongestionPenalty, spCalibratedAt,
    bOraclePreset,
    pfmWindowSlots, pfmEwmaHalfLife, pfmFloor, pfmThreshold,
    preRollEnabled, preRollSlots, preRollAccounts,
    preRollCuPriceMin, preRollCuPriceMax, preRollObsPerSlot, preRollSeed,
    bundleAuctionEnabled, bundleMaxBundlesPerSlot,
    bundleStakePoolShare, bundleTipQuoteCurvePath,
    costToken, visibleRoles, bAlts,
    forkProbability, forkMaxReorgDepth,
    bValidators,
    bFee, feeRate,
    totalAgents, groups, defaultCollateral,
    bFeed, drift, volatility, initialPrice,
    numRounds, snapshotInterval, seed, numericMode, bInfo,
  ]);

  // ── Effective spec (US-014) ───────────────────────────
  // Action buttons and the live preview read from `effectiveSpec`
  // rather than `buildSpec` directly. In raw mode, the raw draft is
  // the source of truth and round-trips through the draft model so
  // unknown backend fields survive.
  const effectiveSpec = useCallback((): RunSpec => {
    if (editorMode === "raw" && rawDraft) {
      return draftToApiSpec(rawDraft) as unknown as RunSpec;
    }
    return buildSpec();
  }, [editorMode, rawDraft, buildSpec]);

  // In raw mode, `effectiveSpec()` is already a backend-shaped JSON
  // spec (the raw draft is seeded from `tpl.rawSpec`/`buildSpec` and
  // round-tripped through `draftToApiSpec`). Feeding it back through
  // `specToApi` would re-run the frontend→backend converter on a
  // body that's already in backend shape and crash on missing
  // frontend-shape paths (e.g., the lighthouse template has no
  // top-level `clock` block, so `clockToApi` would dereference
  // `undefined.type`). Pass the raw body through verbatim instead.
  const prebuiltApiBody = useCallback((): Record<string, unknown> | undefined => {
    if (editorMode === "raw" && rawDraft) {
      return draftToApiSpec(rawDraft) as Record<string, unknown>;
    }
    return undefined;
  }, [editorMode, rawDraft]);

  const specJson = useMemo(
    () => JSON.stringify(effectiveSpec(), null, 2),
    [effectiveSpec],
  );

  // Clear validation errors when the spec changes.
  useEffect(() => {
    if (validationErrors.length > 0) setValidationErrors([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specJson]);

  // ── Apply a backend spec back into structured form state ──
  // Used both by the template picker and by the raw-spec fallback
  // editor (US-014) when returning to structured mode. Covers every
  // field `buildSpec` consumes so the round trip is as complete as
  // the structured form can represent. Unknown backend categories and
  // per-block fields are not representable here and are dropped;
  // power users who need them should stay in raw mode until submit.
  const applySpecToForm = useCallback((s: Partial<RunSpec>) => {
    // The raw-spec round trip lands here in *backend* shape (e.g.
    // `execution.ordering = {type: "priority"}` rather than the string
    // `"priority"`). `asTypeString` peels the `{type}` wrapper so the
    // structured state stays as plain strings.
    const asTypeString = (v: unknown): string | undefined => {
      if (typeof v === "string") return v;
      if (
        v &&
        typeof v === "object" &&
        "type" in (v as Record<string, unknown>)
      ) {
        const t = (v as { type?: unknown }).type;
        if (typeof t === "string") return t;
      }
      return undefined;
    };
    const sx = s as Record<string, unknown>;
    const execX = (s.execution ?? sx.execution) as
      | (Record<string, unknown> & {
          model?: unknown;
          ordering?: unknown;
          cost_model?: unknown;
          gas_model?: unknown;
          scheduler?: unknown;
        })
      | undefined;
    if (s.market) {
      const marketType = asTypeString(s.market.type);
      if (marketType) setBMarket(marketType);
      if (typeof s.market.num_assets === "number") setNumAssets(s.market.num_assets);
      // Backend nests initial_liquidity under market.params; check both
      // shapes so a raw-spec round trip doesn't blank the field.
      const marketParams = (s.market as Record<string, unknown>).params as
        | Record<string, unknown>
        | undefined;
      if (typeof s.market.initial_liquidity === "number")
        setInitialLiquidity(s.market.initial_liquidity);
      else if (typeof marketParams?.initial_liquidity === "number")
        setInitialLiquidity(marketParams.initial_liquidity as number);
      if (typeof s.market.token_decimals === "number")
        setTokenDecimals(s.market.token_decimals);
      // US-007: hydrate the token-extensions panel from preserved tokens
      // so a loaded template's standard/LST/hook fields are editable.
      if (Array.isArray(s.market.tokens) && s.market.tokens.length > 0) {
        setBTokens(s.market.tokens.map((t) => ({ ...t })));
      }
      // Hydrate whirlpool protocol variables (Protocol variables tab).
      // Read from the typed `whirlpool_params` first, then fall back to
      // raw `market.params` so a backend-shaped round trip also lands.
      const wp =
        (s.market as { whirlpool_params?: Record<string, unknown> })
          .whirlpool_params ?? marketParams;
      if (wp) {
        if (typeof wp.corpus_slot === "number") setWpCorpusSlot(wp.corpus_slot);
        if (typeof wp.pool_pubkey === "string") setWpPoolPubkey(wp.pool_pubkey);
        if (typeof wp.pool_account_id === "string")
          setWpPoolAccountId(wp.pool_account_id);
        if (typeof wp.token_a_id === "string") setWpTokenAId(wp.token_a_id);
        if (typeof wp.token_b_id === "string") setWpTokenBId(wp.token_b_id);
        if (typeof wp.token_a_symbol === "string")
          setWpTokenASymbol(wp.token_a_symbol);
        if (typeof wp.token_b_symbol === "string")
          setWpTokenBSymbol(wp.token_b_symbol);
      }
      // Backend stores fee_model under market.fee_model; prefer the
      // top-level one when present (frontend shape) and fall back to
      // the nested one otherwise.
      const nestedFee = (s.market as Record<string, unknown>).fee_model as
        | Record<string, unknown>
        | undefined;
      if (!s.fee_model && nestedFee) {
        const t = asTypeString(nestedFee.type);
        if (t) setBFee(t);
        const params = nestedFee.params as Record<string, unknown> | undefined;
        const bps = params?.trade_fee_bps;
        if (typeof bps === "number") setFeeRate(bps);
      }
    }
    if (s.world && Array.isArray(s.world.markets)) {
      // Raw-spec blocks don't carry canvas positions; lay them out
      // in a deterministic row so the graph renders when switching
      // back to structured mode.
      setWorldBlocks(
        s.world.markets.map((m, idx) => ({
          id: m.id,
          type: m.type,
          label: m.label,
          tokens: m.tokens,
          x: 60 + idx * 180,
          y: 40,
        })),
      );
      setWorldLinks(
        Array.isArray(s.world.links)
          ? s.world.links.map((l) => ({ from: l.from, to: l.to, token: l.token }))
          : [],
      );
    }
    if (s.clock) {
      const clockType = asTypeString(s.clock.type);
      if (clockType)
        setBClock(clockType as RunSpec["clock"]["type"]);
      if (typeof s.clock.block_time === "number") setBlockTime(s.clock.block_time);
      if (typeof s.clock.epoch_length === "number") setEpochLength(s.clock.epoch_length);
      if (typeof s.clock.skip_rate === "number") setSkipRate(s.clock.skip_rate);
    }
    if (execX) {
      // Backend uses `execution.type`; frontend uses `execution.model`.
      const execModel =
        asTypeString(execX.model) ??
        asTypeString(execX.type) ??
        undefined;
      if (execModel) {
        // Map backend's solana_like → frontend's solana so RegistrySelect
        // stays in sync with the value the user actually picked.
        setBExec(execModel === "solana_like" ? "solana" : execModel);
      }
      const ord = asTypeString(execX.ordering);
      if (ord) setBOrdering(ord as RunSpec["execution"]["ordering"]);
      // Backend nests under `gas_model: { type }`; frontend uses
      // `cost_model` directly.
      const cm =
        asTypeString(execX.cost_model) ?? asTypeString(execX.gas_model);
      if (cm) setBGas(cm as RunSpec["execution"]["cost_model"]);
      const sched = asTypeString(execX.scheduler);
      if (sched === "serial" || sched === "priority") {
        setBScheduler(sched);
      }
      // US-010 PRD line 747: hydrate priority-fee market tuning when
      // present so a loaded template / round-tripped run preserves the
      // advanced settings.
      const pfm = execX.priority_fee_market as
        | RunSpec["execution"]["priority_fee_market"]
        | undefined;
      if (pfm) {
        setPfmWindowSlots(pfm.window_slots);
        setPfmEwmaHalfLife(pfm.ewma_half_life_slots);
        setPfmFloor(pfm.floor_micro_lamports);
        setPfmThreshold(pfm.update_event_threshold);
        if (pfm.pre_roll) {
          setPreRollEnabled(true);
          setPreRollSlots(pfm.pre_roll.slots);
          setPreRollAccounts(pfm.pre_roll.accounts);
          setPreRollCuPriceMin(pfm.pre_roll.cu_price_min);
          setPreRollCuPriceMax(pfm.pre_roll.cu_price_max);
          setPreRollObsPerSlot(pfm.pre_roll.observations_per_slot);
          setPreRollSeed(pfm.pre_roll.seed);
        }
      }
      // Lighthouse — bundle auction config.
      const ba = execX.bundle_auction as
        | RunSpec["execution"]["bundle_auction"]
        | undefined;
      if (ba) {
        setBundleAuctionEnabled(true);
        setBundleMaxBundlesPerSlot(ba.max_bundles_per_slot);
        setBundleStakePoolShare(ba.jito_stake_pool_share);
        if (typeof ba.tip_quote_curve_path === "string")
          setBundleTipQuoteCurvePath(ba.tip_quote_curve_path);
      }
      const ct = execX.cost_token;
      if (typeof ct === "string" && ct) setCostToken(ct);
      const vr = execX.visible_roles;
      if (Array.isArray(vr))
        setVisibleRoles(
          (vr as unknown[]).filter((s): s is string => typeof s === "string"),
        );
      // US-014 PRD line 1125: hydrate adversarial fork settings.
      const fs = execX.fork_spec as
        | RunSpec["execution"]["fork_spec"]
        | undefined;
      if (fs) {
        setForkProbability(fs.fork_probability_per_slot);
        setForkMaxReorgDepth(fs.max_reorg_depth_slots);
      }
      // US-012 PRD line 974: hydrate the validator-set panel from a
      // loaded template / round-tripped run so per-validator client and
      // revenue-share fields survive the trip through the raw-spec form.
      const vs = execX.validator_set as
        | RunSpec["execution"]["validator_set"]
        | undefined;
      if (vs && vs.length > 0) {
        setBValidators(
          vs.map((v) => ({
            pubkey: v.pubkey,
            client: v.client,
            stake_lamports: v.stake_lamports,
            stake_pool_share: v.stake_pool_share,
            stake_pool_address: v.stake_pool_address ?? "",
            commission_pct: v.commission_pct ?? 0.05,
          })),
        );
      }
    }
    if (s.fee_model) {
      const feeType = asTypeString(s.fee_model.type);
      if (feeType) setBFee(feeType as RunSpec["fee_model"]["type"]);
      if (typeof s.fee_model.rate_bps === "number") setFeeRate(s.fee_model.rate_bps);
    }
    // Frontend agents is an object {total, mix, groups}; backend agents
    // is an array of agent specs. Tolerate both so a raw-spec round
    // trip preserves the population.
    if (Array.isArray(s.agents)) {
      // Preserve per-agent params + initial_balances through the
      // structured round trip (lighthouse template needs this so
      // jito_searcher.tip_curve, swap_noise.frequency, per-agent
      // balances etc. all survive). Coalesce only when the
      // (type, params, balances) tuple is identical across agents.
      const arr = s.agents as Array<{
        type?: unknown;
        agent_id?: unknown;
        params?: Record<string, unknown>;
        initial_balances?: Record<string, number>;
      }>;
      const next: AgentGroup[] = [];
      const firstId = new Map<string, string | undefined>();
      for (const a of arr) {
        const t = asTypeString(a?.type);
        if (!t) continue;
        const params = (a.params ?? {}) as Record<string, unknown>;
        const balances = (a.initial_balances ?? {}) as Record<string, number>;
        const agentId = typeof a.agent_id === "string" ? a.agent_id : undefined;
        const existing = next.find(
          (g) =>
            g.type === t &&
            JSON.stringify(g.params) === JSON.stringify(params) &&
            JSON.stringify(g.initialBalances ?? {}) === JSON.stringify(balances),
        );
        if (existing) {
          existing.count = (existing.count ?? 1) + 1;
        } else {
          const id = `g-${t}-${next.length}`;
          next.push({
            id,
            type: t,
            weight: 0,
            count: 1,
            params,
            initialBalances: balances,
            agentIdPrefix: agentId,
          });
          firstId.set(id, agentId);
        }
      }
      for (const g of next) {
        if ((g.count ?? 1) > 1) {
          const first = firstId.get(g.id);
          if (typeof first === "string") {
            const stripped = first.replace(/-\d+$/, "");
            g.agentIdPrefix = stripped.length > 0 ? stripped : undefined;
          }
        }
      }
      // Mirror count → weight as a percentage share (sum ≈ 100%) so
      // the slider has a meaningful value and the weight-sum summary
      // doesn't show a misleading "9%" when there are nine agents.
      // specToApi still prefers count when present, so populations
      // round-trip exactly until the user touches the slider (which
      // clears count and lets the percentage drive distribution).
      const totalCount = next.reduce((s, g) => s + (g.count ?? 1), 0);
      if (totalCount > 0) {
        for (const g of next) {
          if (typeof g.count === "number") {
            g.weight = Math.round((g.count / totalCount) * 100);
          }
        }
      }
      if (next.length > 0) setGroups(next);
      if (arr.length > 0) setTotalAgents(arr.length);
    } else if (s.agents) {
      if (typeof s.agents.total === "number") setTotalAgents(s.agents.total);
      if (typeof s.agents.default_collateral === "number")
        setDefaultCollateral(s.agents.default_collateral);
      if (s.agents.groups && s.agents.groups.length > 0) {
        setGroups(s.agents.groups.map((g) => ({ ...g })));
      } else if (s.agents.mix) {
        // Backwards compat: templates still arrive with a mix block.
        // Map it into groups with one entry per non-zero key so the
        // dynamic designer can edit it.
        const next: AgentGroup[] = [];
        for (const [type, share] of Object.entries(s.agents.mix)) {
          const weight = Math.round((share as number) * 100);
          if (weight > 0) next.push({ id: `g-${type}`, type, weight, params: {} });
        }
        if (next.length > 0) setGroups(next);
      }
    }
    if (Array.isArray(s.feeds) && s.feeds.length > 0) {
      const feed = s.feeds[0];
      const feedType = asTypeString(feed.type);
      if (feedType) setBFeed(feedType as FeedType);
      if (typeof feed.drift === "number") setDrift(feed.drift);
      if (typeof feed.volatility === "number") setVolatility(feed.volatility);
      if (typeof feed.initial_price === "number") setInitialPrice(feed.initial_price);
    }
    if (s.config) {
      if (typeof s.config.num_rounds === "number") setNumRounds(s.config.num_rounds);
      if (typeof s.config.snapshot_interval === "number")
        setSnapshotInterval(s.config.snapshot_interval);
      if (typeof s.config.seed === "number") setSeed(s.config.seed);
      if (s.config.numeric_mode === "FIXED_POINT" || s.config.numeric_mode === "FLOAT_MODE")
        setNumericMode(s.config.numeric_mode);
      if (s.config.information_filter === "full_transparency") setBInfo("full");
      else if (s.config.information_filter === "delayed_information") setBInfo("delayed");
    } else {
      // Backend stores num_rounds/snapshot_interval/seed at the top
      // level. Pull them in when the structured `config` block is
      // absent so a raw round trip doesn't reset run length and seed.
      if (typeof sx.num_rounds === "number") setNumRounds(sx.num_rounds);
      if (typeof sx.snapshot_interval === "number")
        setSnapshotInterval(sx.snapshot_interval);
      if (typeof sx.seed === "number") setSeed(sx.seed);
    }
    // Lighthouse — top-level ALTs.
    const altsRaw = (s as { alts?: unknown }).alts ?? sx.alts;
    if (Array.isArray(altsRaw)) {
      const next: Array<{ id: string; entries: string[] }> = [];
      for (const item of altsRaw) {
        if (!item || typeof item !== "object") continue;
        const r = item as Record<string, unknown>;
        if (typeof r.id !== "string") continue;
        const entries = Array.isArray(r.entries)
          ? (r.entries as unknown[]).filter(
              (e): e is string => typeof e === "string",
            )
          : [];
        next.push({ id: r.id, entries });
      }
      if (next.length > 0) setBAlts(next);
    }
  }, []);

  // ── Auto-apply template from query param ──────────────
  // Defined as a function-declaration-equivalent below so it can
  // close over `applyTemplate` (declared next). The effect runs once
  // templates resolve.

  // ── Apply template ────────────────────────────────────
  const applyTemplate = useCallback(
    (tpl: SimTemplate) => {
      // Always land in structured mode. Prefer the verbatim base_spec
      // when it's available — the structured form (post WS-2 plumbing)
      // round-trips lighthouse-grade fields (alts, jito_searcher
      // tip_curve, execution.params.bundle_auction, pre_roll, …)
      // through specFromApi → applySpecToForm. The slim `tpl.spec`
      // (built by templateFromApi) is the legacy fallback.
      const rawSpec = tpl.rawSpec;
      const hydrated: Partial<RunSpec> = rawSpec
        ? specFromApi(rawSpec as ApiRunSpec)
        : tpl.spec;
      applySpecToForm(hydrated);
      setSimName(tpl.id);
      setRawDraft(null);
      setEditorMode("structured");
      const lossyNote = tpl.requiresRawSpec
        ? " — advanced fields (ALTs, tip curves, …) are dropped; switch to Raw JSON if you need them"
        : "";
      showToast(`Template "${tpl.name}" applied${lossyNote}`, "success");
      setStep("form");
    },
    [applySpecToForm, showToast],
  );

  useEffect(() => {
    const queryTemplateId = searchParams.get("template");
    if (!queryTemplateId) return;
    if (consumedTemplateRef.current === queryTemplateId) return;
    const templates = templatesState.data;
    if (!templates) return;
    const match = templates.find((t) => t.id === queryTemplateId);
    if (!match) return;
    consumedTemplateRef.current = queryTemplateId;
    applyTemplate(match);
  }, [searchParams, templatesState.data, applyTemplate]);

  // ── Raw-mode switch handlers (US-014) ─────────────────
  const enterRawMode = useCallback(() => {
    const spec = buildSpec() as unknown as Record<string, unknown>;
    const draft = draftFromApiSpec(spec, { name: simName });
    setRawDraft(draft);
    setEditorMode("raw");
    setValidationErrors([]);
  }, [buildSpec, simName]);

  const exitRawMode = useCallback(() => {
    if (rawDraft) {
      const spec = draftToApiSpec(rawDraft) as unknown as RunSpec;
      applySpecToForm(spec);
    }
    setRawDraft(null);
    setEditorMode("structured");
    setValidationErrors([]);
  }, [rawDraft, applySpecToForm]);

  // ── Validation ────────────────────────────────────────
  const runValidation = useCallback(
    async (
      spec: RunSpec,
      prebuiltApiSpec?: Record<string, unknown>,
    ): Promise<string[]> => {
      const errors: string[] = [];
      // Local guards that depend on structured state are only
      // meaningful when the structured form is the source of truth.
      // In raw mode the user can freely restructure the spec, so we
      // defer to backend validation instead of blocking on local
      // assumptions about the shape.
      if (editorMode === "structured") {
        if (groups.length === 0) errors.push("Add at least one agent group");
        if (weightSum <= 0)
          errors.push("At least one agent group must have a non-zero weight");
        if (numRounds <= 0) errors.push("Rounds must be > 0");
        if (totalAgents <= 0) errors.push("Total agents must be > 0");
        if (
          spec.market?.type === "world" &&
          (!spec.world || spec.world.markets.length === 0)
        ) {
          errors.push("World market type requires at least one market block");
        }
      }
      if (errors.length > 0) return errors;

      try {
        const res = await simulationService.validateSpec(spec, { prebuiltApiSpec });
        if (!res.valid) return res.errors.length > 0 ? res.errors : ["Spec rejected by backend"];
        return [];
      } catch (err) {
        if (err instanceof ApiError) return [err.message];
        return [err instanceof Error ? err.message : "Validation request failed"];
      }
    },
    [editorMode, groups.length, weightSum, numRounds, totalAgents],
  );

  const handleValidate = useCallback(async () => {
    setIsValidating(true);
    try {
      const errors = await runValidation(effectiveSpec(), prebuiltApiBody());
      setValidationErrors(errors);
      if (errors.length === 0) {
        showToast("Spec is valid", "success");
      } else {
        showToast(`Validation failed: ${errors.length} issue(s)`, "error");
      }
    } finally {
      setIsValidating(false);
    }
  }, [effectiveSpec, prebuiltApiBody, runValidation, showToast]);

  // ── Build & Run (sync) ────────────────────────────────
  const handleBuildAndRun = useCallback(async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    try {
      const spec = effectiveSpec();
      const apiBody = prebuiltApiBody();
      const errors = await runValidation(spec, apiBody);
      if (errors.length > 0) {
        setValidationErrors(errors);
        showToast(`Cannot build: ${errors.length} validation issue(s)`, "error");
        return;
      }
      showToast("Building simulation…", "info");
      const result = await simulationService.buildSpec(spec, {
        mode: "sync",
        prebuiltApiSpec: apiBody,
      });
      showToast("Simulation complete", "success");
      router.push(`/results/${result.runId}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Build failed";
      setValidationErrors([msg]);
      showToast(`Build failed: ${msg}`, "error");
    } finally {
      setIsSubmitting(false);
    }
  }, [effectiveSpec, prebuiltApiBody, runValidation, showToast, router, isSubmitting]);

  // ── Build & Open Runner (interactive) ─────────────────
  const handleBuildAndOpenRunner = useCallback(async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    try {
      const spec = effectiveSpec();
      const apiBody = prebuiltApiBody();
      const errors = await runValidation(spec, apiBody);
      if (errors.length > 0) {
        setValidationErrors(errors);
        showToast(`Cannot build: ${errors.length} validation issue(s)`, "error");
        return;
      }
      showToast("Building live engine…", "info");
      const result = await simulationService.buildSpec(spec, {
        mode: "interactive",
        prebuiltApiSpec: apiBody,
      });
      if (!isInteractiveBuild(result)) {
        throw new Error("Backend returned a sync result for interactive build");
      }
      setInteractiveEngine(result.runId, result.simulationId);
      showToast("Engine ready — opening runner", "success");
      router.push(`/runner/${result.runId}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Build failed";
      setValidationErrors([msg]);
      showToast(`Build failed: ${msg}`, "error");
    } finally {
      setIsSubmitting(false);
    }
  }, [effectiveSpec, prebuiltApiBody, runValidation, showToast, router, setInteractiveEngine, isSubmitting]);

  // ═════════════════════════════════════════════════════
  // RENDER
  // ═════════════════════════════════════════════════════

  // Per-section issues drive sidebar badges + Issues tab.
  const orderingValid = ["fifo", "random", "priority", "sandwich", "block_builder"].includes(
    bOrdering,
  );
  const issuesBySection: Record<string, string[]> = {};
  const pushIssue = (section: string, msg: string) => {
    (issuesBySection[section] ??= []).push(msg);
  };
  if (groups.length === 0) pushIssue("agents", "Add at least one agent group");
  if (weightSum !== 100 && groups.length > 0)
    pushIssue("agents", `Group weights sum to ${weightSum}% (engine will normalize)`);
  if (!orderingValid) pushIssue("execution", "Ordering value not recognized");
  if (numRounds <= 0) pushIssue("general", `${idiom.rounds_label} must be > 0`);
  if (totalAgents <= 0) pushIssue("agents", "Total agents must be > 0");
  if (bMarket === "world" && worldBlocks.length === 0)
    pushIssue("market", "World market needs at least one block");
  if (bMarket === "whirlpool") {
    if (wpCorpusSlot === null)
      pushIssue("protocol_variables", "Corpus slot is required");
    if (!wpPoolPubkey)
      pushIssue("protocol_variables", "Pool pubkey is required");
  }
  if (
    bExec === "solana" &&
    bundleAuctionEnabled &&
    !bundleTipQuoteCurvePath &&
    groups.some((g) => g.type === "jito_searcher")
  ) {
    pushIssue(
      "protocol_variables",
      "Bundle auction is enabled with a jito_searcher present but no tip-quote curve path is configured — searcher tips will fall back to the floor",
    );
  }
  if (validationErrors.length > 0) {
    for (const err of validationErrors) pushIssue("general", err);
  }
  const totalIssues = Object.values(issuesBySection).reduce((s, x) => s + x.length, 0);

  type SectionId =
    | "general"
    | "clock"
    | "market"
    | "protocol_variables"
    | "fee"
    | "execution"
    | "agents"
    | "feeds"
    | "replay"
    | "rewards";

  const sectionMeta: Array<{
    id: SectionId;
    icon: string;
    title: string;
    sub: string;
    fields: number;
  }> = [
    { id: "general", icon: "G", title: "General", sub: "Name · seed · rounds", fields: 5 },
    {
      id: "clock",
      icon: "T",
      title: "Clock",
      sub: bClock === "solana_slot" ? "Slot · epoch · skip" : "Block time · epoch",
      fields: bClock === "solana_slot" ? 4 : 3,
    },
    {
      id: "market",
      icon: "M",
      title: "Market",
      sub: bMarket === "world" ? "World graph · tokens" : "Pool · tokens · liquidity",
      fields: 4,
    },
    {
      id: "protocol_variables",
      icon: "V",
      title: "Protocol variables",
      sub:
        bMarket === "whirlpool"
          ? `Corpus ${wpCorpusSlot ?? "—"} · pool ${wpPoolPubkey ? wpPoolPubkey.slice(0, 4) + "…" : "—"}`
          : "—",
      fields: bMarket === "whirlpool" ? 7 : 0,
    },
    { id: "fee", icon: "F", title: "Fee model", sub: "Type · rate", fields: 2 },
    {
      id: "execution",
      icon: "E",
      title: "Execution",
      sub: bExec === "solana" ? "Solana pipeline" : "Ordering · scheduler",
      fields: 5,
    },
    {
      id: "agents",
      icon: "A",
      title: "Agent population",
      sub: `${groups.length} group${groups.length === 1 ? "" : "s"} · ${totalAgents} agents`,
      fields: groups.length + 2,
    },
    { id: "feeds", icon: "P", title: "Price feeds", sub: `${bFeed} · μ ${drift}`, fields: 4 },
    {
      id: "replay",
      icon: "R",
      title: "Replay range",
      sub: `${replaySlotStart} → ${replaySlotEnd}`,
      fields: 4,
    },
    {
      id: "rewards",
      icon: "I",
      title: "Incentives",
      sub: `${rewardDist} · ${emissionSched}`,
      fields: 2,
    },
  ];

  const sectionsFiltered = secSearch.trim()
    ? sectionMeta.filter((s) =>
        (s.title + " " + s.sub).toLowerCase().includes(secSearch.toLowerCase().trim()),
      )
    : sectionMeta;

  // ── Per-section editor renderers ─────────────────────────
  const renderGeneral = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>General</h2>
          <p>Identifies this run and controls deterministic randomness, length, and snapshot cadence.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Identity & schedule</h3>
        <div className="form-row">
          <div className="form-group">
            <label>Simulation name<InfoTooltip text="Display name only — used in the run list. Doesn't affect engine output." /></label>
            <input
              type="text"
              placeholder="my-cfamm-sim"
              value={simName}
              onChange={(e) => setSimName(e.target.value)}
            />
          </div>
          <div className="form-group" data-editable-field="seed">
            <label>Seed<InfoTooltip text="Master RNG seed. Same seed + same spec = bit-identical run." /></label>
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(parseInt(e.target.value) || 0)}
            />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group" data-editable-field="num_rounds">
            <label>{idiom.rounds_label}<InfoTooltip text="Total simulation rounds (slots/blocks) to step. Higher = longer run, more output." /></label>
            <input
              type="number"
              aria-label={idiom.rounds_label}
              value={numRounds}
              onChange={(e) => setNumRounds(parseInt(e.target.value) || 0)}
            />
          </div>
          <div className="form-group" data-editable-field="snapshot_interval">
            <label>Snapshot interval<InfoTooltip text="Rounds between full state snapshots written to results. Lower = finer time resolution, larger output." /></label>
            <input
              type="number"
              aria-label="Snapshot Interval"
              value={snapshotInterval}
              onChange={(e) => setSnapshotInterval(parseInt(e.target.value) || 0)}
            />
          </div>
          <div className="form-group">
            <label>Numeric mode<InfoTooltip text="FIXED_POINT mirrors on-chain integer math (recommended for fidelity). FLOAT_MODE is faster but drifts vs. mainnet." /></label>
            <select
              value={numericMode}
              onChange={(e) =>
                setNumericMode(e.target.value as "FIXED_POINT" | "FLOAT_MODE")
              }
            >
              <option value="FIXED_POINT">FIXED_POINT</option>
              <option value="FLOAT_MODE">FLOAT_MODE</option>
            </select>
          </div>
        </div>
      </div>
    </>
  );

  const renderClock = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Clock</h2>
          <p>
            {bClock === "solana_slot"
              ? "Solana slot timing — controls how simulation rounds map to wall-clock time."
              : "Block timing controls how simulation rounds map to wall-clock time."}
          </p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Slot model</h3>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="clock-type">Type<InfoTooltip text="Pacing model. solana_slot uses Solana's 400ms slot. constant uses fixed wall-clock; variable samples gaps from a distribution." /></label>
            <RegistrySelect
              id="clock-type"
              category="clocks"
              value={bClock}
              onChange={(t) => {
                if (t === "solana_slot" && bClock !== "solana_slot") {
                  setBlockTime(0.4);
                  setEpochLength(432_000);
                  setSkipRate(0);
                }
                setBClock(t);
              }}
              aliasFromBackend={{ variable_block: "variable" }}
              aliasToBackend={{ variable: "variable_block" }}
            />
          </div>
          <div className="form-group">
            <label>
              {bClock === "solana_slot" ? "Slot duration (s)" : idiom.time_label}
              <InfoTooltip text="Real seconds each round represents. Solana mainnet ≈ 0.4s/slot. Affects oracle drift, EWMA windows, time-based fees." />
            </label>
            <input
              type="number"
              step="any"
              value={blockTime}
              onChange={(e) => setBlockTime(parseFloat(e.target.value) || 0)}
            />
          </div>
          <div className="form-group">
            <label>{bClock === "solana_slot" ? "Epoch (slots)" : idiom.epoch_label}<InfoTooltip text="Slots per epoch. Solana mainnet uses 432,000. Drives stake/leader rotations and per-epoch token drift." /></label>
            <input
              type="number"
              value={epochLength}
              onChange={(e) => setEpochLength(parseInt(e.target.value) || 0)}
            />
          </div>
          {bClock === "solana_slot" && (
            <div className="form-group">
              <label htmlFor="clock-skip-rate">Skip rate<InfoTooltip text="Fraction of slots where no leader produces a block. 0 = perfect uptime; ~0.05 ≈ historical mainnet." /></label>
              <input
                id="clock-skip-rate"
                type="number"
                step="0.01"
                min={0}
                max={1}
                value={skipRate}
                onChange={(e) =>
                  setSkipRate(Math.min(1, Math.max(0, parseFloat(e.target.value) || 0)))
                }
              />
            </div>
          )}
        </div>
      </div>
    </>
  );

  const renderMarket = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Market</h2>
          <p>The AMM under test. Tokens and liquidity live here.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Pool</h3>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="market-type">Market type<InfoTooltip text="AMM family under test. Whirlpool uses real captured pool state; world is the chain-neutral CFAMM." /></label>
            <RegistrySelect
              id="market-type"
              aria-label="Market Type"
              category="markets"
              value={bMarket}
              onChange={setBMarket}
            />
          </div>
          <div className="form-group">
            <label># of assets<InfoTooltip text="Number of distinct tokens in the market. Whirlpool is fixed at 2." /></label>
            <input
              type="number"
              value={numAssets}
              min={2}
              max={20}
              onChange={(e) => setNumAssets(parseInt(e.target.value) || 2)}
            />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Initial liquidity<InfoTooltip text="Starting liquidity (in token-A units). Ignored when a corpus snapshot already pins real liquidity." /></label>
            <input
              type="number"
              value={initialLiquidity}
              onChange={(e) => setInitialLiquidity(parseInt(e.target.value) || 0)}
            />
          </div>
          <div className="form-group">
            <label>Token decimals<InfoTooltip text="Decimals for each token. Affects amount scaling and fixed-point precision." /></label>
            <input
              type="number"
              value={tokenDecimals}
              min={0}
              max={18}
              onChange={(e) => setTokenDecimals(parseInt(e.target.value) || 0)}
            />
          </div>
        </div>
        {bMarket === "world" && (
          <div style={{ marginTop: 12 }}>
            <WorldBuilder onSpecChange={handleWorldSpecChange} />
          </div>
        )}
      </div>
    </>
  );

  const renderProtocolVariables = () => {
    if (bMarket !== "whirlpool") {
      return (
        <>
          <div className="bsec-pane-head">
            <div>
              <h2>Protocol variables</h2>
              <p>
                Protocol-specific state inputs surface here when the selected
                market type needs them.
              </p>
            </div>
          </div>
          <div className="bsec-card">
            <p className="bsec-empty">
              No protocol variables for <code>{bMarket}</code> markets. Switch
              to a Whirlpool template to hydrate from a captured slot.
            </p>
          </div>
        </>
      );
    }
    // Distinct option lists from the corpus, with friendly labels.
    const slotOptions = whirlpoolEntries.map((e) => ({
      value: e.slot,
      label: `${e.slot} — ${e.pairLabel}`,
    }));
    const pubkeySeen = new Set<string>();
    const pubkeyOptions: { value: string; label: string }[] = [];
    for (const e of whirlpoolEntries) {
      if (pubkeySeen.has(e.pubkey)) continue;
      pubkeySeen.add(e.pubkey);
      pubkeyOptions.push({
        value: e.pubkey,
        label: `${e.pubkey.slice(0, 6)}…${e.pubkey.slice(-4)} — ${e.pairLabel}`,
      });
    }
    const tokenAMints = new Set<string>();
    const tokenBMints = new Set<string>();
    for (const e of whirlpoolEntries) {
      tokenAMints.add(e.tokenMintA);
      tokenBMints.add(e.tokenMintB);
    }
    const tokenAOptions = Array.from(tokenAMints).map((m) => ({
      value: m,
      label: mintLabel(m),
    }));
    const tokenBOptions = Array.from(tokenBMints).map((m) => ({
      value: m,
      label: mintLabel(m),
    }));

    // Cascading auto-fill: when the user picks a slot, populate the
    // other fields from that slot's manifest. Symbols default to the
    // KNOWN_MINT_SYMBOLS map (still editable as text).
    const onSelectSlot = (raw: string) => {
      if (raw === "") {
        setWpCorpusSlot(null);
        return;
      }
      const slot = parseInt(raw, 10);
      if (Number.isNaN(slot)) return;
      setWpCorpusSlot(slot);
      const entry = whirlpoolEntries.find((e) => e.slot === slot);
      if (!entry) return;
      setWpPoolPubkey(entry.pubkey);
      setWpPoolAccountId(entry.pubkey);
      setWpTokenAId(entry.tokenMintA);
      setWpTokenBId(entry.tokenMintB);
      const symA = KNOWN_MINT_SYMBOLS[entry.tokenMintA];
      const symB = KNOWN_MINT_SYMBOLS[entry.tokenMintB];
      if (symA) setWpTokenASymbol(symA);
      if (symB) setWpTokenBSymbol(symB);
    };

    const corpusEmpty =
      !corpusState.loading && whirlpoolEntries.length === 0;

    return (
      <>
        <div className="bsec-pane-head">
          <div>
            <h2>Protocol variables</h2>
            <p>
              On-chain state hydration for the Orca Whirlpool. Pick a corpus
              slot and the pool pubkey, vaults, and token mints auto-fill
              from the captured manifest. Required by the engine — empty
              values are rejected.
            </p>
          </div>
        </div>
        {corpusState.loading && (
          <div className="bsec-card">
            <p className="bsec-empty">Loading captured corpus…</p>
          </div>
        )}
        {corpusState.error && (
          <div className="bsec-card">
            <p className="bsec-empty">
              Corpus load failed:{" "}
              {corpusState.error instanceof Error
                ? corpusState.error.message
                : "unknown error"}
            </p>
          </div>
        )}
        {corpusEmpty && (
          <div className="bsec-card">
            <p className="bsec-empty">
              No whirlpool captures under{" "}
              <code>solana-plans/calibration/corpus/</code>. Run{" "}
              <code>tools/cache_corpus_slot.py</code> to populate.
            </p>
          </div>
        )}
        <div className="bsec-card" data-editable-field="market.params">
          <h3>Whirlpool corpus</h3>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="wp-corpus-slot">Corpus slot<InfoTooltip text="Captured mainnet slot under solana-plans/calibration/corpus/. The pool, ticks, and vaults at that slot become the run's starting state." /></label>
              <select
                id="wp-corpus-slot"
                value={wpCorpusSlot ?? ""}
                onChange={(e) => onSelectSlot(e.target.value)}
              >
                <option value="">— select —</option>
                {slotOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label htmlFor="wp-pool-pubkey">Pool pubkey<InfoTooltip text="Whirlpool program account for this pair. Auto-fills from the chosen corpus slot." /></label>
              <select
                id="wp-pool-pubkey"
                value={wpPoolPubkey}
                onChange={(e) => setWpPoolPubkey(e.target.value)}
              >
                <option value="">— select —</option>
                {pubkeyOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="wp-pool-account-id">Pool account ID<InfoTooltip text="Stable identifier (often = pool pubkey) used to thread accounts through ALTs and bundles." /></label>
              <select
                id="wp-pool-account-id"
                value={wpPoolAccountId}
                onChange={(e) => setWpPoolAccountId(e.target.value)}
              >
                <option value="">— select —</option>
                {pubkeyOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
        <div className="bsec-card">
          <h3>Token mapping</h3>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="wp-token-a-id">Token A ID<InfoTooltip text="ID for token A used by the engine. Maps to a real SPL mint via the corpus manifest." /></label>
              <select
                id="wp-token-a-id"
                value={wpTokenAId}
                onChange={(e) => setWpTokenAId(e.target.value)}
              >
                <option value="">— select —</option>
                {tokenAOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label htmlFor="wp-token-a-symbol">Token A symbol<InfoTooltip text="Display symbol for token A. Cosmetic — doesn't affect engine math." /></label>
              <input
                id="wp-token-a-symbol"
                type="text"
                value={wpTokenASymbol}
                onChange={(e) => setWpTokenASymbol(e.target.value)}
              />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="wp-token-b-id">Token B ID<InfoTooltip text="ID for token B used by the engine. Maps to a real SPL mint via the corpus manifest." /></label>
              <select
                id="wp-token-b-id"
                value={wpTokenBId}
                onChange={(e) => setWpTokenBId(e.target.value)}
              >
                <option value="">— select —</option>
                {tokenBOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label htmlFor="wp-token-b-symbol">Token B symbol<InfoTooltip text="Display symbol for token B. Cosmetic — doesn't affect engine math." /></label>
              <input
                id="wp-token-b-symbol"
                type="text"
                value={wpTokenBSymbol}
                onChange={(e) => setWpTokenBSymbol(e.target.value)}
              />
            </div>
          </div>
        </div>

        {bExec === "solana" ? (
          <details style={{ marginTop: 8 }}>
            <summary
              style={{
                cursor: "pointer",
                padding: "8px 12px",
                fontWeight: 600,
                fontSize: "0.85rem",
                color: "var(--muted)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                background: "var(--bg-subtle, transparent)",
                marginBottom: 8,
                listStyle: "revert",
              }}
            >
              Advanced — bundle auction, priority-fee pre-roll, ALTs, cost &amp;
              visibility
            </summary>
            <div
              className="bsec-card"
              data-editable-field="execution.params.bundle_auction"
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 8,
                }}
              >
                <h3 style={{ margin: 0 }}>Bundle auction</h3>
                <label
                  style={{
                    fontSize: ".82rem",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={bundleAuctionEnabled}
                    onChange={(e) =>
                      setBundleAuctionEnabled(e.target.checked)
                    }
                  />
                  Enabled
                </label>
              </div>
              {bundleAuctionEnabled ? (
                <>
                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor="ba-max-bundles">
                        Max bundles per slot
                        <InfoTooltip text="Hard cap on bundles the Jito auction can land per slot. Mainnet today ≈ 5." />
                      </label>
                      <input
                        id="ba-max-bundles"
                        type="number"
                        min={1}
                        value={bundleMaxBundlesPerSlot}
                        onChange={(e) =>
                          setBundleMaxBundlesPerSlot(
                            parseInt(e.target.value) || 1,
                          )
                        }
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor="ba-stake-pool-share">
                        Jito stake-pool share
                        <InfoTooltip text="Fraction of bundle tip routed to stake pools (vs. kept by the validator). 0.05 ≈ default." />
                      </label>
                      <input
                        id="ba-stake-pool-share"
                        type="number"
                        step={0.01}
                        min={0}
                        max={1}
                        value={bundleStakePoolShare}
                        onChange={(e) =>
                          setBundleStakePoolShare(
                            parseFloat(e.target.value) || 0,
                          )
                        }
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor="ba-tip-curve-path">
                        Tip-quote curve path
                        <InfoTooltip text="YAML tip curve under solana-plans/calibration/. Maps tip amount → landing probability for the searcher." />
                      </label>
                      <input
                        id="ba-tip-curve-path"
                        type="text"
                        value={bundleTipQuoteCurvePath}
                        placeholder="solana-plans/calibration/jito_tip_curves.yaml"
                        onChange={(e) =>
                          setBundleTipQuoteCurvePath(e.target.value)
                        }
                      />
                      <p className="hint" style={{ marginTop: 4 }}>
                        Browse calibration files under{" "}
                        <code>solana-plans/calibration/</code>.
                      </p>
                    </div>
                  </div>
                </>
              ) : null}
            </div>

            <div
              className="bsec-card"
              data-editable-field="execution.params.priority_fee_market.pre_roll"
            >
              <details open={preRollEnabled}>
                <summary
                  style={{
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <h3 style={{ margin: 0, flex: 1 }}>
                    Priority-fee pre-roll
                  </h3>
                  <label
                    style={{
                      fontSize: ".82rem",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={preRollEnabled}
                      onChange={(e) => setPreRollEnabled(e.target.checked)}
                    />
                    Enabled
                  </label>
                </summary>
                {preRollEnabled ? (
                  <div style={{ marginTop: 10 }}>
                    <div className="form-row">
                      <div className="form-group">
                        <label htmlFor="pre-slots">Slots<InfoTooltip text="How many slots to pre-warm the priority-fee distribution before round 1. Without this the searcher quotes a degenerate floor." /></label>
                        <input
                          id="pre-slots"
                          type="number"
                          min={0}
                          value={preRollSlots}
                          onChange={(e) =>
                            setPreRollSlots(parseInt(e.target.value) || 0)
                          }
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="pre-obs">
                          Observations per slot
                          <InfoTooltip text="Synthetic CU-price observations seeded per slot during pre-roll. More obs = sharper distribution at run start." />
                        </label>
                        <input
                          id="pre-obs"
                          type="number"
                          min={1}
                          value={preRollObsPerSlot}
                          onChange={(e) =>
                            setPreRollObsPerSlot(
                              parseInt(e.target.value) || 1,
                            )
                          }
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="pre-seed">Seed<InfoTooltip text="Seed for the pre-roll RNG. Independent of the main run seed so calibration is reproducible." /></label>
                        <input
                          id="pre-seed"
                          type="number"
                          value={preRollSeed}
                          onChange={(e) =>
                            setPreRollSeed(parseInt(e.target.value) || 0)
                          }
                        />
                      </div>
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label htmlFor="pre-cu-min">CU price min (µ-lam)<InfoTooltip text="Floor of the seeded CU-price range, in micro-lamports per CU." /></label>
                        <input
                          id="pre-cu-min"
                          type="number"
                          min={0}
                          value={preRollCuPriceMin}
                          onChange={(e) =>
                            setPreRollCuPriceMin(
                              parseInt(e.target.value) || 0,
                            )
                          }
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="pre-cu-max">CU price max (µ-lam)<InfoTooltip text="Ceiling of the seeded CU-price range, in micro-lamports per CU." /></label>
                        <input
                          id="pre-cu-max"
                          type="number"
                          min={0}
                          value={preRollCuPriceMax}
                          onChange={(e) =>
                            setPreRollCuPriceMax(
                              parseInt(e.target.value) || 0,
                            )
                          }
                        />
                      </div>
                    </div>
                    <div className="form-group">
                      <label htmlFor="pre-accounts">Accounts<InfoTooltip text="Solana accounts whose per-account fee distributions get pre-warmed. Defaults to the pool pubkey when blank." /></label>
                      <textarea
                        id="pre-accounts"
                        rows={4}
                        value={preRollAccounts.join("\n")}
                        placeholder={
                          wpPoolPubkey ||
                          "One Solana account per line — the JitoSearcher samples these for the pre-slot priority-fee distribution."
                        }
                        onChange={(e) =>
                          setPreRollAccounts(
                            e.target.value
                              .split("\n")
                              .map((s) => s.trim())
                              .filter((s) => s.length > 0),
                          )
                        }
                      />
                      <p className="hint" style={{ marginTop: 4 }}>
                        Defaults to the Whirlpool pool pubkey when blank.
                      </p>
                    </div>
                  </div>
                ) : null}
              </details>
            </div>

            <div className="bsec-card" data-editable-field="alts">
              <h3>Address Lookup Tables</h3>
              {bAlts.length === 0 ? (
                <p className="hint" style={{ margin: 0, marginBottom: 6 }}>
                  No ALTs configured. Add one to compress the accounts a
                  jito_searcher bundle references.
                </p>
              ) : null}
              {bAlts.map((alt, idx) => (
                <div
                  key={`alt-${idx}`}
                  style={{
                    border: "1px solid var(--border)",
                    padding: 10,
                    borderRadius: "var(--radius)",
                    marginBottom: 8,
                  }}
                >
                  <div
                    style={{ display: "flex", gap: 8, alignItems: "center" }}
                  >
                    <input
                      type="text"
                      value={alt.id}
                      placeholder="alt-name"
                      style={{ flex: 1 }}
                      onChange={(e) => {
                        const id = e.target.value;
                        setBAlts((cur) =>
                          cur.map((a, i) => (i === idx ? { ...a, id } : a)),
                        );
                      }}
                    />
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        setBAlts((cur) => cur.filter((_, i) => i !== idx))
                      }
                    >
                      Remove
                    </button>
                  </div>
                  <textarea
                    rows={4}
                    value={alt.entries.join("\n")}
                    placeholder="One Solana account per line"
                    style={{ marginTop: 6 }}
                    onChange={(e) => {
                      const entries = e.target.value
                        .split("\n")
                        .map((s) => s.trim())
                        .filter((s) => s.length > 0);
                      setBAlts((cur) =>
                        cur.map((a, i) =>
                          i === idx ? { ...a, entries } : a,
                        ),
                      );
                    }}
                  />
                </div>
              ))}
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() =>
                  setBAlts((cur) => [
                    ...cur,
                    { id: `alt-${cur.length + 1}`, entries: [] },
                  ])
                }
              >
                + ALT
              </button>
            </div>

            <div className="bsec-card">
              <h3>Cost &amp; visibility</h3>
              <div className="form-row">
                <div
                  className="form-group"
                  data-editable-field="execution.params.cost_token"
                >
                  <label htmlFor="cost-token">Cost token<InfoTooltip text="Token used to score bundle EV (so MEV is denominated consistently). USDC for lighthouse runs." /></label>
                  <input
                    id="cost-token"
                    type="text"
                    value={costToken}
                    placeholder="USDC"
                    onChange={(e) => setCostToken(e.target.value)}
                  />
                  <p className="hint" style={{ marginTop: 4 }}>
                    Token id used to score bundle EV. Defaults to USDC on
                    lighthouse runs.
                  </p>
                </div>
                <div
                  className="form-group"
                  data-editable-field="execution.params.visible_roles"
                >
                  <label htmlFor="visible-roles">Visible roles<InfoTooltip text="Agent roles allowed to see the bundle book. Empty = all roles. Restricting models information asymmetry." /></label>
                  <div
                    id="visible-roles"
                    role="group"
                    aria-label="Visible roles"
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 6,
                      padding: 6,
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius)",
                      minHeight: 36,
                    }}
                  >
                    {registryAgentTypes.length === 0 ? (
                      <span className="hint" style={{ fontSize: ".8rem" }}>
                        Loading registry…
                      </span>
                    ) : (
                      registryAgentTypes.map((t) => {
                        const selected = visibleRoles.includes(t);
                        return (
                          <button
                            key={t}
                            type="button"
                            aria-pressed={selected}
                            onClick={() =>
                              setVisibleRoles((cur) =>
                                cur.includes(t)
                                  ? cur.filter((r) => r !== t)
                                  : [...cur, t],
                              )
                            }
                            style={{
                              fontSize: ".74rem",
                              padding: "3px 8px",
                              borderRadius: 999,
                              border: selected
                                ? "1px solid var(--accent)"
                                : "1px solid var(--border)",
                              background: selected
                                ? "var(--accent)"
                                : "var(--bg-2)",
                              color: selected ? "#fff" : "var(--text-1)",
                              cursor: "pointer",
                              fontFamily: "var(--font-mono, monospace)",
                            }}
                          >
                            {t}
                          </button>
                        );
                      })
                    )}
                  </div>
                  <p className="hint" style={{ marginTop: 4 }}>
                    Roles allowed to see the bundle book. Click chips to
                    toggle; empty selection means all roles are visible.
                  </p>
                </div>
              </div>
            </div>
          </details>
        ) : null}
      </>
    );
  };

  const renderFee = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Fee model</h2>
          <p>How swap fees are charged. Pairs with the Market section above.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Fee</h3>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="fee-type">Fee type<InfoTooltip text="Fee model registered in the engine. Whirlpool uses cfamm_fee with the pool's tier." /></label>
            <RegistrySelect
              id="fee-type"
              category="fee_models"
              value={bFee}
              onChange={setBFee}
            />
          </div>
          <div className="form-group">
            <label>Fee rate (bps)<InfoTooltip text="Pool fee in basis points (1 bp = 0.01%). Whirlpool SOL/USDC ≈ 5 bps." /></label>
            <input
              type="number"
              value={feeRate}
              onChange={(e) => setFeeRate(parseInt(e.target.value) || 0)}
            />
          </div>
        </div>
      </div>
    </>
  );

  const renderExecution = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Execution</h2>
          <p>How transactions get ordered and packed each {bExec === "solana" ? "slot" : "block"}.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Pipeline</h3>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="exec-model">Execution model<InfoTooltip text="Block/slot pipeline. solana models Solana's leader-driven slot loop; ethereum_like uses sealed blocks." /></label>
            <RegistrySelect
              id="exec-model"
              category="execution_models"
              value={bExec}
              onChange={handleExecChange}
              aliasFromBackend={{ solana_like: "solana" }}
              aliasToBackend={{ solana: "solana_like" }}
            />
          </div>
          <div className="form-group">
            <label htmlFor="ordering">Ordering<InfoTooltip text="How submitted txs get ordered within a slot. Priority sorts by fee bid; FIFO uses arrival order." /></label>
            <RegistrySelect
              id="ordering"
              category="orderings"
              value={bOrdering}
              onChange={(v) => setBOrdering(v as RunSpec["execution"]["ordering"])}
            />
          </div>
          <div className="form-group">
            <label htmlFor="info-filter">Information filter<InfoTooltip text="Information visible to agents each round. delayed = agents see one round behind; full = perfect info." /></label>
            <RegistrySelect
              id="info-filter"
              category="information_filters"
              value={bInfo}
              onChange={setBInfo}
              aliasFromBackend={{
                full_transparency: "full",
                delayed_information: "delayed",
              }}
              aliasToBackend={{
                full: "full_transparency",
                delayed: "delayed_information",
              }}
            />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="gas-model">{idiom.fee_label}<InfoTooltip text="Cost model for transaction execution. Solana uses CU-based pricing with a per-tx base fee." /></label>
            <RegistrySelect
              id="gas-model"
              category="gas_models"
              value={bGas}
              onChange={(v) => setBGas(v as RunSpec["execution"]["cost_model"])}
            />
          </div>
          <div className="form-group">
            <label htmlFor="scheduler">Scheduler<InfoTooltip text="Within a slot, run txs serially or in parallel under priority order. Priority models Solana's account-locking parallel scheduler." /></label>
            <select
              id="scheduler"
              data-testid="scheduler-select"
              value={bScheduler}
              onChange={(e) =>
                setBScheduler(
                  e.target.value as NonNullable<RunSpec["execution"]["scheduler"]>,
                )
              }
            >
              <option value="serial">Serial</option>
              <option value="priority">Priority (parallel)</option>
            </select>
          </div>
        </div>
      </div>

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="compute-budget-section">
          <h3>Compute budget</h3>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="cb-preset">Use historical preset<InfoTooltip text="current loads mainnet's compute-budget caps. custom unlocks the per-tx / per-slot / per-account fields below." /></label>
              <select
                id="cb-preset"
                value={cbPreset}
                onChange={(e) => {
                  const next = e.target.value;
                  setCbPreset(next);
                  if (next === "current") {
                    setCbPerSlot(60_000_000);
                    setCbPerTx(1_400_000);
                    setCbPerAccount(12_000_000);
                  }
                }}
              >
                <option value="current">current (mainnet)</option>
                <option value="custom">custom</option>
              </select>
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="cb-per-tx">Per-tx CU<InfoTooltip text="Hard cap on compute units a single tx can consume. Mainnet = 1.4M." /></label>
              <input
                id="cb-per-tx"
                type="number"
                min={0}
                value={cbPerTx}
                disabled={cbPreset === "current"}
                onChange={(e) => setCbPerTx(parseInt(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="cb-per-slot">Per-slot CU<InfoTooltip text="Hard cap on compute units the entire slot can consume. Mainnet = 60M." /></label>
              <input
                id="cb-per-slot"
                type="number"
                min={0}
                value={cbPerSlot}
                disabled={cbPreset === "current"}
                onChange={(e) => setCbPerSlot(parseInt(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="cb-per-account">Per-writable-account CU<InfoTooltip text="Hard cap on compute units that touch one writable account in a slot. Mainnet = 12M." /></label>
              <input
                id="cb-per-account"
                type="number"
                min={0}
                value={cbPerAccount}
                disabled={cbPreset === "current"}
                onChange={(e) => setCbPerAccount(parseInt(e.target.value) || 0)}
              />
            </div>
          </div>
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="oracle-preset-section">
          <h3>Oracle</h3>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="oracle-preset">Source preset<InfoTooltip text="Pre-wired SOL/USDC oracle. none disables oracle reads in agents." /></label>
              <select
                id="oracle-preset"
                value={bOraclePreset}
                onChange={(e) =>
                  setBOraclePreset(e.target.value as typeof bOraclePreset)
                }
              >
                <option value="none">none</option>
                <option value="pyth_pull">Pyth Pull (SOL/USDC)</option>
                <option value="pyth_lazer">Pyth Lazer (SOL/USDC)</option>
                <option value="switchboard_on_demand">
                  Switchboard On-Demand (SOL/USDC)
                </option>
              </select>
            </div>
          </div>
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="priority-fee-market-section">
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              Advanced: Priority fee market
            </summary>
            <div className="form-row" style={{ marginTop: ".5rem" }}>
              <div className="form-group">
                <label htmlFor="pfm-window-slots">Window (slots)<InfoTooltip text="Sliding window over which the priority-fee distribution is computed." /></label>
                <input
                  id="pfm-window-slots"
                  type="number"
                  min={1}
                  value={pfmWindowSlots}
                  onChange={(e) => setPfmWindowSlots(parseInt(e.target.value) || 0)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="pfm-half-life">EWMA half-life (slots)<InfoTooltip text="EWMA half-life inside the window. Lower = reacts faster to spikes; higher = smoother quotes." /></label>
                <input
                  id="pfm-half-life"
                  type="number"
                  min={1}
                  value={pfmEwmaHalfLife}
                  onChange={(e) => setPfmEwmaHalfLife(parseInt(e.target.value) || 0)}
                />
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label htmlFor="pfm-floor">Floor (µ-lamports)<InfoTooltip text="Minimum CU price the distribution will ever quote, in micro-lamports." /></label>
                <input
                  id="pfm-floor"
                  type="number"
                  min={0}
                  value={pfmFloor}
                  onChange={(e) => setPfmFloor(parseInt(e.target.value) || 0)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="pfm-threshold">Event threshold (relative)<InfoTooltip text="Relative jump (vs. EWMA) that counts as a 'fee event' in analytics. Doesn't affect engine math." /></label>
                <input
                  id="pfm-threshold"
                  type="number"
                  step={0.01}
                  min={0}
                  value={pfmThreshold}
                  onChange={(e) => setPfmThreshold(parseFloat(e.target.value) || 0)}
                />
              </div>
            </div>
          </details>
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="adversarial-conditions-section">
          <h3>Adversarial conditions</h3>
          <p className="bsec-card-help">
            Forces per-slot reorgs to stress-test bundle revert accounting. Default 0%
            leaves the engine on the canonical (no-fork) chain.
          </p>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="fork-probability">Fork probability / slot<InfoTooltip text="Per-slot probability the leader produces a fork. Stress-tests bundle revert accounting. 0 = canonical chain only." /></label>
              <input
                id="fork-probability"
                data-testid="fork-probability-input"
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={forkProbability}
                onChange={(e) => setForkProbability(parseFloat(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="fork-max-reorg-depth">Max reorg depth (slots)<InfoTooltip text="Cap on how many slots back a reorg can rewind. High values grow the replay buffer." /></label>
              <input
                id="fork-max-reorg-depth"
                data-testid="fork-max-reorg-depth-input"
                type="number"
                min={1}
                value={forkMaxReorgDepth}
                onChange={(e) => setForkMaxReorgDepth(parseInt(e.target.value) || 1)}
              />
            </div>
          </div>
          {forkMaxReorgDepth >= 50 && (
            <p style={{ fontSize: ".75rem", color: "var(--yellow)", margin: ".25rem 0 0" }}>
              High reorg depth grows the replay buffer proportionally — expect higher
              memory use.
            </p>
          )}
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="fork-mainnet-section">
          <h3>Fork mainnet</h3>
          <p className="bsec-card-help">
            Hydrate the simulation from real mainnet protocol state at a chosen slot.
            The engine pulls only the accounts owned by the protocols you select — no
            sysvar replication, no unrelated programs.
          </p>
          <div className="form-group">
            <label>
              <input
                type="checkbox"
                data-testid="fork-mainnet-enabled"
                checked={forkMainnetEnabled}
                onChange={(e) => setForkMainnetEnabled(e.target.checked)}
              />{" "}
              Fork mainnet at slot N
            </label>
          </div>
          {forkMainnetEnabled && (
            <>
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="fork-mainnet-slot">Slot<InfoTooltip text="Mainnet slot from which to hydrate protocol state. 'now' resolves at submit time." /></label>
                  <input
                    id="fork-mainnet-slot"
                    data-testid="fork-mainnet-slot-input"
                    type="number"
                    min={0}
                    value={forkMainnetSlot === "now" ? "" : forkMainnetSlot}
                    placeholder="e.g. 250000000"
                    onChange={(e) => {
                      const v = e.target.value;
                      setForkMainnetSlot(v === "" ? "now" : parseInt(v, 10) || 0);
                    }}
                  />
                </div>
                <div className="form-group">
                  <label>&nbsp;</label>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    data-testid="fork-mainnet-now"
                    onClick={() => setForkMainnetSlot("now")}
                  >
                    Use current slot ("now")
                  </button>
                </div>
              </div>
              <div className="form-group">
                <label>Protocols to include<InfoTooltip text="Which on-chain protocols to hydrate from the slot. Only protocols whose engine math has shipped can be forked." /></label>
                <div
                  data-testid="fork-mainnet-protocols"
                  style={{ display: "flex", flexDirection: "column", gap: ".25rem" }}
                >
                  {FORK_MAINNET_PROTOCOLS.map((p) => {
                    const checked = forkMainnetProtocols.includes(p.id);
                    return (
                      <label
                        key={p.id}
                        data-testid={`fork-mainnet-protocol-${p.id}`}
                        style={{ opacity: p.available ? 1 : 0.5 }}
                      >
                        <input
                          type="checkbox"
                          disabled={!p.available}
                          checked={checked && p.available}
                          onChange={(e) => {
                            setForkMainnetProtocols((cur) =>
                              e.target.checked
                                ? [...cur, p.id]
                                : cur.filter((x) => x !== p.id),
                            );
                          }}
                        />{" "}
                        {p.label}
                        {!p.available && (
                          <span
                            style={{
                              fontSize: ".75rem",
                              color: "var(--yellow)",
                              marginLeft: ".5rem",
                            }}
                          >
                            (unavailable)
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              </div>
              <div className="form-group">
                <label htmlFor="fork-mainnet-wallet">
                  Include positions owned by wallet (optional)
                  <InfoTooltip text="Wallet pubkey whose open positions also get hydrated alongside the selected protocols." />
                </label>
                <input
                  id="fork-mainnet-wallet"
                  data-testid="fork-mainnet-wallet-input"
                  type="text"
                  placeholder="Wallet pubkey (base58)"
                  value={forkMainnetWallet}
                  onChange={(e) => setForkMainnetWallet(e.target.value)}
                />
              </div>
              <p
                data-testid="fork-mainnet-dependency-note"
                className="bsec-card-help"
                style={{ margin: ".25rem 0 0" }}
              >
                Only protocols whose engine math has shipped can be forked from a
                captured slot.
              </p>
            </>
          )}
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="validator-set-section">
          <h3>Validator set</h3>
          <p className="bsec-card-help">
            Default seed: one Jito-Solana validator at 100% stake. Jito-Solana
            validators capture bundle tips minus the configured stake-pool share;
            vanilla validators forgo MEV revenue.
          </p>
          {bValidators.map((v, idx) => {
            const updateV = (patch: Partial<ValidatorSetEntry>) =>
              setBValidators((cur) => {
                const next = cur.slice();
                next[idx] = { ...next[idx], ...patch };
                return next;
              });
            const removeV = () =>
              setBValidators((cur) =>
                cur.length > 1 ? cur.filter((_, i) => i !== idx) : cur,
              );
            return (
              <div
                key={`${v.pubkey}-${idx}`}
                data-testid={`validator-row-${idx}`}
                className="bsec-subrow"
              >
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor={`val-pubkey-${idx}`}>Pubkey<InfoTooltip text="Validator identity pubkey (anything unique works for synthetic sets)." /></label>
                    <input
                      id={`val-pubkey-${idx}`}
                      data-testid={`val-pubkey-${idx}`}
                      type="text"
                      value={v.pubkey}
                      onChange={(e) => updateV({ pubkey: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor={`val-client-${idx}`}>Client<InfoTooltip text="Jito-Solana captures bundle tips minus stake-pool share; Vanilla validators forgo MEV revenue." /></label>
                    <select
                      id={`val-client-${idx}`}
                      data-testid={`val-client-${idx}`}
                      value={v.client}
                      onChange={(e) =>
                        updateV({
                          client: e.target.value as "jito_solana" | "vanilla",
                        })
                      }
                    >
                      <option value="jito_solana">Jito-Solana (MEV)</option>
                      <option value="vanilla">Vanilla (no MEV)</option>
                    </select>
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor={`val-stake-${idx}`}>Stake (lamports)<InfoTooltip text="Validator stake in lamports. Determines slot-leader probability per epoch." /></label>
                    <input
                      id={`val-stake-${idx}`}
                      data-testid={`val-stake-${idx}`}
                      type="number"
                      min={0}
                      value={v.stake_lamports}
                      onChange={(e) =>
                        updateV({ stake_lamports: parseInt(e.target.value) || 0 })
                      }
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor={`val-pool-share-${idx}`}>Stake-pool share<InfoTooltip text="Fraction of bundle tip the validator forwards to its stake pool (Jito-Solana only)." /></label>
                    <input
                      id={`val-pool-share-${idx}`}
                      data-testid={`val-pool-share-${idx}`}
                      type="number"
                      min={0}
                      max={1}
                      step={0.01}
                      value={v.stake_pool_share}
                      disabled={v.client !== "jito_solana"}
                      onChange={(e) =>
                        updateV({ stake_pool_share: parseFloat(e.target.value) || 0 })
                      }
                    />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor={`val-pool-addr-${idx}`}>
                      Stake-pool address (optional)
                      <InfoTooltip text="Stake pool to receive forwarded tips. Leave empty to keep tips at the validator." />
                    </label>
                    <input
                      id={`val-pool-addr-${idx}`}
                      data-testid={`val-pool-addr-${idx}`}
                      type="text"
                      placeholder="leave empty to keep tips"
                      value={v.stake_pool_address ?? ""}
                      onChange={(e) =>
                        updateV({ stake_pool_address: e.target.value })
                      }
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor={`val-commission-${idx}`}>Commission %<InfoTooltip text="Validator commission on inflation rewards (0–1)." /></label>
                    <input
                      id={`val-commission-${idx}`}
                      data-testid={`val-commission-${idx}`}
                      type="number"
                      min={0}
                      max={1}
                      step={0.01}
                      value={v.commission_pct ?? 0.05}
                      onChange={(e) =>
                        updateV({ commission_pct: parseFloat(e.target.value) || 0 })
                      }
                    />
                  </div>
                </div>
                {bValidators.length > 1 && (
                  <button
                    type="button"
                    data-testid={`val-remove-${idx}`}
                    onClick={removeV}
                    className="btn btn-secondary btn-sm"
                  >
                    Remove validator
                  </button>
                )}
              </div>
            );
          })}
          <button
            type="button"
            data-testid="val-add"
            className="btn btn-secondary btn-sm"
            onClick={() =>
              setBValidators((cur) => [
                ...cur,
                {
                  pubkey: `validator-${cur.length + 1}`,
                  client: "jito_solana",
                  stake_lamports: 1_000_000_000,
                  stake_pool_share: 0.05,
                  stake_pool_address: "",
                  commission_pct: 0.05,
                },
              ])
            }
          >
            + Add validator
          </button>
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="token-extensions-section">
          <h3>Token extensions</h3>
          {bTokens.length === 0 ? (
            <p className="bsec-card-help">
              No tokens configured yet. Apply a Solana template or switch the
              execution model to seed defaults (SOL/USDC).
            </p>
          ) : (
            bTokens.map((tok, idx) => {
              const updateTok = (patch: Partial<MarketTokenSpec>) =>
                setBTokens((cur) => {
                  const next = cur.slice();
                  next[idx] = { ...next[idx], ...patch };
                  return next;
                });
              const tokDrift = tok.exchange_rate_drift;
              const hook = tok.transfer_hook;
              return (
                <div
                  key={`${tok.id}-${idx}`}
                  data-testid={`token-extension-row-${tok.id}`}
                  className="bsec-subrow"
                >
                  <div style={{ fontWeight: 600, fontSize: ".875rem", marginBottom: ".25rem" }}>
                    {tok.symbol} ({tok.id}, decimals={tok.decimals})
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor={`token-${tok.id}-standard`}>Standard<InfoTooltip text="spl = legacy Token Program; spl_2022 = Token-2022 with extensions; native = wrapped SOL." /></label>
                      <select
                        id={`token-${tok.id}-standard`}
                        value={tok.standard ?? "spl"}
                        onChange={(e) =>
                          updateTok({
                            standard: e.target.value as MarketTokenSpec["standard"],
                          })
                        }
                      >
                        <option value="native">native</option>
                        <option value="spl">spl</option>
                        <option value="spl_2022">spl_2022</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label htmlFor={`token-${tok.id}-rate`}>Exchange rate to SOL (LST)<InfoTooltip text="For LSTs only — exchange rate to SOL (e.g. mSOL ≈ 1.18). Engine uses this to convert balances." /></label>
                      <input
                        id={`token-${tok.id}-rate`}
                        type="number"
                        step={0.0001}
                        min={0}
                        value={
                          typeof tok.exchange_rate_to_sol === "number"
                            ? tok.exchange_rate_to_sol
                            : typeof tok.exchange_rate_to_sol === "string"
                              ? Number(tok.exchange_rate_to_sol)
                              : ""
                        }
                        placeholder="(non-LST)"
                        onChange={(e) => {
                          const v = e.target.value;
                          updateTok({
                            exchange_rate_to_sol: v === "" ? null : parseFloat(v),
                          });
                        }}
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label htmlFor={`token-${tok.id}-drift`}>Drift per epoch<InfoTooltip text="Mean drift per epoch in the LST exchange rate (e.g. 0.0001 ≈ 0.01% / epoch)." /></label>
                      <input
                        id={`token-${tok.id}-drift`}
                        type="number"
                        step={0.00001}
                        value={tokDrift?.drift_per_epoch ?? ""}
                        placeholder="0.0001"
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === "") {
                            updateTok({ exchange_rate_drift: null });
                            return;
                          }
                          updateTok({
                            exchange_rate_drift: {
                              drift_per_epoch: parseFloat(v),
                              volatility_per_epoch: tokDrift?.volatility_per_epoch ?? 0,
                              ...(tokDrift?.seed !== undefined && tokDrift?.seed !== null
                                ? { seed: tokDrift.seed }
                                : {}),
                            },
                          });
                        }}
                      />
                    </div>
                    <div className="form-group">
                      <label htmlFor={`token-${tok.id}-volatility`}>Volatility per epoch<InfoTooltip text="Stdev per epoch on top of the drift. 0 = deterministic LST appreciation." /></label>
                      <input
                        id={`token-${tok.id}-volatility`}
                        type="number"
                        step={0.0001}
                        min={0}
                        value={tokDrift?.volatility_per_epoch ?? ""}
                        placeholder="0"
                        disabled={!tokDrift}
                        onChange={(e) => {
                          if (!tokDrift) return;
                          updateTok({
                            exchange_rate_drift: {
                              ...tokDrift,
                              volatility_per_epoch: parseFloat(e.target.value) || 0,
                            },
                          });
                        }}
                      />
                    </div>
                  </div>
                  {tok.standard === "spl_2022" && (
                    <>
                      <div className="form-row">
                        <div className="form-group">
                          <label htmlFor={`token-${tok.id}-hook-program`}>
                            Transfer hook program
                            <InfoTooltip text="Token-2022 transfer-hook program id, if any. Adds CU and lamport overhead per transfer." />
                          </label>
                          <input
                            id={`token-${tok.id}-hook-program`}
                            type="text"
                            value={hook?.program_id ?? ""}
                            placeholder="(no hook)"
                            onChange={(e) => {
                              const v = e.target.value.trim();
                              if (v === "") {
                                updateTok({ transfer_hook: null });
                                return;
                              }
                              updateTok({
                                transfer_hook: {
                                  program_id: v,
                                  additional_cu_per_transfer:
                                    hook?.additional_cu_per_transfer ?? 0,
                                  additional_lamports_per_transfer:
                                    hook?.additional_lamports_per_transfer ?? 0,
                                },
                              });
                            }}
                          />
                        </div>
                        <div className="form-group">
                          <label htmlFor={`token-${tok.id}-hook-cu`}>Extra CU / transfer<InfoTooltip text="Extra compute units the transfer hook charges per transfer." /></label>
                          <input
                            id={`token-${tok.id}-hook-cu`}
                            type="number"
                            min={0}
                            value={hook?.additional_cu_per_transfer ?? 0}
                            disabled={!hook}
                            onChange={(e) => {
                              if (!hook) return;
                              updateTok({
                                transfer_hook: {
                                  ...hook,
                                  additional_cu_per_transfer: parseInt(e.target.value) || 0,
                                },
                              });
                            }}
                          />
                        </div>
                        <div className="form-group">
                          <label htmlFor={`token-${tok.id}-hook-lamports`}>
                            Extra lamports / transfer
                            <InfoTooltip text="Extra lamports the transfer hook charges per transfer." />
                          </label>
                          <input
                            id={`token-${tok.id}-hook-lamports`}
                            type="number"
                            min={0}
                            value={hook?.additional_lamports_per_transfer ?? 0}
                            disabled={!hook}
                            onChange={(e) => {
                              if (!hook) return;
                              updateTok({
                                transfer_hook: {
                                  ...hook,
                                  additional_lamports_per_transfer:
                                    parseInt(e.target.value) || 0,
                                },
                              });
                            }}
                          />
                        </div>
                      </div>
                      <div className="form-row">
                        <div className="form-group">
                          <label htmlFor={`token-${tok.id}-confidential`}>
                            <input
                              id={`token-${tok.id}-confidential`}
                              type="checkbox"
                              checked={tok.confidential ?? false}
                              onChange={(e) =>
                                updateTok({ confidential: e.target.checked })
                              }
                            />{" "}
                            Confidential transfers (stub)
                          </label>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      {bExec === "solana" && (
        <div className="bsec-card" data-testid="submission-priors-section">
          <h3>Submission path priors</h3>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="sp-rpc">RPC landing prob<InfoTooltip text="Probability a tx submitted via plain RPC lands in a slot, before congestion adjustments." /></label>
              <input
                id="sp-rpc"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={spRpc}
                onChange={(e) => setSpRpc(parseFloat(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="sp-tpu-quic">TPU/QUIC landing prob<InfoTooltip text="Landing probability for direct TPU/QUIC submission to the leader." /></label>
              <input
                id="sp-tpu-quic"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={spTpuQuic}
                onChange={(e) => setSpTpuQuic(parseFloat(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="sp-jito">Jito relayer landing prob<InfoTooltip text="Landing probability for the Jito relayer path (bundles)." /></label>
              <input
                id="sp-jito"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={spJito}
                onChange={(e) => setSpJito(parseFloat(e.target.value) || 0)}
              />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label htmlFor="sp-congestion">Congestion penalty / %full<InfoTooltip text="Penalty subtracted from landing probability per percentage point of slot fullness." /></label>
              <input
                id="sp-congestion"
                type="number"
                min={0}
                step={0.001}
                value={spCongestionPenalty}
                onChange={(e) => setSpCongestionPenalty(parseFloat(e.target.value) || 0)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="sp-calibrated-at">Calibrated at (ISO)<InfoTooltip text="ISO timestamp when these priors were calibrated. Empty = synthetic / uncalibrated." /></label>
              <input
                id="sp-calibrated-at"
                type="text"
                placeholder="synthetic (leave empty)"
                value={spCalibratedAt ?? ""}
                onChange={(e) => {
                  const v = e.target.value.trim();
                  setSpCalibratedAt(v === "" ? null : v);
                }}
              />
            </div>
          </div>
        </div>
      )}
    </>
  );

  const renderAgents = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Agent population</h2>
          <p>Mix of bots running on top of the market. Weights normalize at build time.</p>
        </div>
      </div>
      {/* AgentGroupsDesigner brings its own Card chrome, so we render
          it directly without an outer .bsec-card to avoid nested
          panels. */}
      <div className="bsec-agents">
        <AgentGroupsDesigner
          groups={groups}
          onGroupsChange={handleGroupsChange}
          totalAgents={totalAgents}
          onTotalChange={setTotalAgents}
          defaultCollateral={defaultCollateral}
          onCollateralChange={setDefaultCollateral}
        />
      </div>
    </>
  );

  const renderFeeds = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Price feeds</h2>
          <p>External price stream that drives noise traders and oracle-style components.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Feed</h3>
        <div className="form-group">
          <label htmlFor="feed-type">Feed type<InfoTooltip text="Price source for noise/oracle agents. Brownian = synthetic GBM; replay = recorded series." /></label>
          <RegistrySelect
            id="feed-type"
            category="feeds"
            value={bFeed}
            onChange={setBFeed}
          />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Drift (μ)<InfoTooltip text="GBM mean drift parameter — average per-round price change in the synthetic feed." /></label>
            <input
              type="number"
              value={drift}
              step={0.0001}
              onChange={(e) => setDrift(parseFloat(e.target.value) || 0)}
            />
          </div>
          <div className="form-group">
            <label>Volatility (σ)<InfoTooltip text="GBM volatility parameter — stdev of per-round price shocks." /></label>
            <input
              type="number"
              value={volatility}
              step={0.001}
              onChange={(e) => setVolatility(parseFloat(e.target.value) || 0)}
            />
          </div>
          <div className="form-group">
            <label>Initial price<InfoTooltip text="Starting price quote at round 0." /></label>
            <input
              type="number"
              value={initialPrice}
              step={0.01}
              onChange={(e) => setInitialPrice(parseFloat(e.target.value) || 0)}
            />
          </div>
        </div>
      </div>
    </>
  );

  const renderReplay = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Replay range</h2>
          <p>
            Replay a range of mainnet slots through the engine, optionally with one
            parameter swapped (counterfactual).
          </p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Slot window</h3>
        <div className="form-row">
          <div className="form-group">
            <label>Slot start<InfoTooltip text="First mainnet slot to replay, inclusive." /></label>
            <input
              data-testid="replay-slot-start"
              type="number"
              value={replaySlotStart}
              onChange={(e) => setReplaySlotStart(parseInt(e.target.value, 10) || 0)}
            />
          </div>
          <div className="form-group">
            <label>Slot end<InfoTooltip text="Last mainnet slot to replay, inclusive." /></label>
            <input
              data-testid="replay-slot-end"
              type="number"
              value={replaySlotEnd}
              onChange={(e) => setReplaySlotEnd(parseInt(e.target.value, 10) || 0)}
            />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Tip-replace bundle id (optional)<InfoTooltip text="Bundle to swap a different tip into when replaying. Leave blank to replay verbatim." /></label>
            <input
              data-testid="replay-tip-bundle-id"
              type="text"
              value={replayTipBundleId}
              onChange={(e) => setReplayTipBundleId(e.target.value)}
              placeholder="leave blank for no counterfactual"
            />
          </div>
          <div className="form-group">
            <label>New tip (lamports)<InfoTooltip text="Counterfactual tip in lamports applied to the bundle above." /></label>
            <input
              data-testid="replay-tip-new-lamports"
              type="number"
              value={replayTipNewLamports}
              onChange={(e) =>
                setReplayTipNewLamports(parseInt(e.target.value, 10) || 0)
              }
            />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
          <button
            data-testid="replay-submit"
            className="btn btn-secondary"
            onClick={handleSubmitReplay}
            disabled={replaySubmitting || replaySlotEnd < replaySlotStart}
          >
            {replaySubmitting ? "Replaying…" : "Submit replay only"}
          </button>
          <span style={{ fontSize: ".8rem", color: "var(--text-2)" }}>
            Or use Build & Run below for a full scenario.
          </span>
        </div>
        {replayError && (
          <div
            data-testid="replay-error"
            style={{ marginTop: 8, color: "var(--red)", fontSize: ".85rem" }}
          >
            {replayError}
          </div>
        )}
        {replayResult && (
          <div
            data-testid="replay-result"
            style={{ marginTop: 8, fontSize: ".8rem", color: "var(--text-2)" }}
          >
            <div>
              <strong>run id:</strong> {replayResult.runId}
            </div>
            <div>
              <strong>slots loaded:</strong> {replayResult.slotsLoaded}
            </div>
            <div>
              <strong>decoded share:</strong>{" "}
              {(replayResult.decodedTransactionShare * 100).toFixed(2)}%
            </div>
            <div>
              <strong>eligible for calibration:</strong>{" "}
              {replayResult.eligibleForCalibration ? "yes" : "no"}
            </div>
            {replayResult.unsupportedProgramIds.length > 0 && (
              <div>
                <strong>unsupported programs:</strong>{" "}
                {replayResult.unsupportedProgramIds.join(", ")}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );

  const renderRewards = () => (
    <>
      <div className="bsec-pane-head">
        <div>
          <h2>Incentives & rewards</h2>
          <p>Optional emission schedule and reward distributor for LP / staker incentives.</p>
        </div>
      </div>
      <div className="bsec-card">
        <h3>Distribution</h3>
        <div className="form-row">
          <div className="form-group">
            <label>Reward distributor<InfoTooltip text="Algorithm that pays LP/staker rewards. None = no rewards distributed." /></label>
            <select
              value={rewardDist}
              onChange={(e) => setRewardDist(e.target.value)}
            >
              <option>None</option>
              <option>Pro-rata LP</option>
              <option>Stake-weighted</option>
              <option>Custom</option>
            </select>
          </div>
          <div className="form-group">
            <label>Emission schedule<InfoTooltip text="Curve for total reward emissions over time." /></label>
            <select
              value={emissionSched}
              onChange={(e) => setEmissionSched(e.target.value)}
            >
              <option>None</option>
              <option>Linear</option>
              <option>Exponential Decay</option>
            </select>
          </div>
        </div>
      </div>
    </>
  );

  const sectionRenderers: Record<SectionId, () => React.ReactNode> = {
    general: renderGeneral,
    clock: renderClock,
    market: renderMarket,
    protocol_variables: renderProtocolVariables,
    fee: renderFee,
    execution: renderExecution,
    agents: renderAgents,
    feeds: renderFeeds,
    replay: renderReplay,
    rewards: renderRewards,
  };

  // ── Preview pane renderers ───────────────────────────────
  // Defensive: state can briefly hold raw-spec objects ({type: "..."})
  // mid-round-trip. Coerce non-primitives to a readable string so the
  // summary pane never throws "Objects are not valid as a React child".
  const safe = (v: unknown): React.ReactNode => {
    if (v == null) return "—";
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
      return String(v);
    if (typeof v === "object" && "type" in (v as Record<string, unknown>)) {
      const t = (v as { type?: unknown }).type;
      if (typeof t === "string") return t;
    }
    try {
      return JSON.stringify(v);
    } catch {
      return "[unrenderable]";
    }
  };
  const summaryRow = (k: string, v: React.ReactNode) => (
    <div className="bsec-summary-row" key={String(k)}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
  const renderSummary = () => (
    <>
      <div className="bsec-summary-section">
        <h4>Run</h4>
        {summaryRow("Name", safe(simName))}
        {summaryRow("Seed", safe(seed))}
        {summaryRow(idiom.rounds_label, safe(numRounds))}
        {summaryRow("Numeric mode", safe(numericMode))}
      </div>
      <div className="bsec-summary-section">
        <h4>Market & fees</h4>
        {summaryRow("Pool", safe(bMarket))}
        {summaryRow("Liquidity", initialLiquidity.toLocaleString())}
        {summaryRow("Fee", `${safe(bFee)} ${(feeRate / 100).toFixed(2)}%`)}
        {summaryRow(
          bClock === "solana_slot" ? "Slot duration" : idiom.time_label,
          `${blockTime}s`,
        )}
      </div>
      <div className="bsec-summary-section">
        <h4>Agents</h4>
        {summaryRow("Total", safe(totalAgents))}
        {summaryRow("Groups", groups.length)}
        {summaryRow("Weight sum", `${weightSum}%`)}
        {groups.map((g) =>
          summaryRow(
            g.type,
            `${g.weight}% · ×${Math.round((totalAgents * g.weight) / 100)}`,
          ),
        )}
      </div>
      <div className="bsec-summary-section">
        <h4>Execution</h4>
        {summaryRow("Model", safe(bExec))}
        {summaryRow(
          "Ordering",
          orderingValid ? (
            safe(bOrdering)
          ) : (
            <span style={{ color: "var(--red)" }}>{safe(bOrdering)}</span>
          ),
        )}
        {summaryRow("Scheduler", safe(bScheduler))}
        {summaryRow("Filter", safe(bInfo))}
      </div>
      <div className="bsec-summary-section">
        <h4>Replay</h4>
        {summaryRow("Range", `${replaySlotStart} → ${replaySlotEnd}`)}
        {summaryRow("Counterfactual", replayTipBundleId.trim() ? "yes" : "no")}
      </div>
    </>
  );

  const renderIssues = () => {
    const items: Array<{ section: SectionId; msg: string; severity: "warn" | "err" }> = [];
    for (const [sectionId, msgs] of Object.entries(issuesBySection) as Array<
      [SectionId, string[]]
    >) {
      for (const msg of msgs) {
        const isErr =
          msg.toLowerCase().includes("must be") ||
          msg.toLowerCase().includes("at least") ||
          msg.toLowerCase().includes("not recognized") ||
          msg.toLowerCase().includes("requires");
        items.push({ section: sectionId, msg, severity: isErr ? "err" : "warn" });
      }
    }
    if (items.length === 0) {
      return (
        <div style={{ color: "var(--text-2)", textAlign: "center", padding: "30px 10px" }}>
          No issues — ready to build.
        </div>
      );
    }
    return (
      <>
        {items.map((it, i) => (
          <div
            key={`${it.section}-${i}`}
            className={`bsec-issue ${it.severity === "err" ? "err" : ""}`}
          >
            <span className="bsec-issue-icon">⚠</span>
            <div className="bsec-issue-body">
              <strong>{it.msg}</strong>
              <span>in {it.section}</span>
            </div>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setActiveSection(it.section)}
            >
              Open
            </button>
          </div>
        ))}
      </>
    );
  };

  const renderJsonPreview = () => (
    <div
      className="json-view bsec-json"
      dangerouslySetInnerHTML={{ __html: syntaxHighlight(specJson) }}
    />
  );

  // ── Action bar status pill ───────────────────────────────
  const actionDot =
    totalIssues === 0 ? "ok" : issuesBySection.general?.length ? "err" : "warn";
  const actionLabel =
    totalIssues === 0
      ? "Ready to build"
      : `${totalIssues} issue${totalIssues === 1 ? "" : "s"} — fix before run`;

  return (
    <>
      <Topbar
        title="New Simulation"
        spec={builderSpecLike}
        template={templatesState.data?.find((t) => t.id === simName) ?? null}
      />
      <div
        id="content"
        className="fade-in"
        style={
          step === "form" && !(editorMode === "raw" && rawDraft)
            ? { padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }
            : undefined
        }
      >
        {/* ════════ STEP 1: TEMPLATE PICKER ════════ */}
        {step === "pick" && (
          <Card title="Start from Template">
            {templatesState.loading && (
              <div className="grid-4" style={{ marginBottom: 16 }}>
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="card">
                    <Skeleton height={18} width="60%" />
                    <div style={{ marginTop: 8 }}>
                      <Skeleton height={12} />
                    </div>
                    <div style={{ marginTop: 6 }}>
                      <Skeleton height={12} width="80%" />
                    </div>
                  </div>
                ))}
              </div>
            )}
            {!templatesState.loading && templatesState.error != null && (
              <div style={{ marginBottom: 16 }}>
                <p style={{ color: "var(--red)", fontSize: ".85rem" }}>
                  Failed to load templates:{" "}
                  {templatesState.error instanceof Error
                    ? templatesState.error.message
                    : "unknown error"}
                </p>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={templatesState.refetch}
                  style={{ marginTop: 8 }}
                >
                  Retry
                </button>
              </div>
            )}
            {!templatesState.loading &&
              templatesState.error == null &&
              (templatesState.data?.length ?? 0) === 0 && (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem", marginBottom: 16 }}>
                  No experiment templates available from the backend.
                </p>
              )}
            {!templatesState.loading &&
              templatesState.error == null &&
              (templatesState.data?.length ?? 0) > 0 && (
                <div className="grid-4" style={{ marginBottom: 16 }}>
                  {templatesState
                    .data!.filter((tpl) => tpl.id === "solana-sandwich-lighthouse")
                    .map((tpl) => (
                    <div
                      key={tpl.id}
                      className="card"
                      data-testid="template-card"
                      data-template-id={tpl.id}
                      data-featured={tpl.featured ? "true" : "false"}
                      style={{
                        cursor: "pointer",
                        transition: "border-color .15s",
                        position: "relative",
                        ...(tpl.featured
                          ? {
                              borderColor: "var(--accent)",
                              boxShadow: "0 0 0 1px var(--accent)",
                            }
                          : {}),
                      }}
                      onClick={() => applyTemplate(tpl)}
                    >
                      {tpl.featured && (
                        <span
                          data-testid="template-featured-ribbon"
                          style={{
                            position: "absolute",
                            top: 8,
                            right: 8,
                            background: "var(--accent)",
                            color: "var(--bg-0)",
                            fontSize: ".7rem",
                            fontWeight: 700,
                            padding: "2px 8px",
                            borderRadius: 4,
                            letterSpacing: ".03em",
                            textTransform: "uppercase",
                          }}
                        >
                          Featured demo
                        </span>
                      )}
                      <h3 style={{ marginBottom: 4, fontSize: "1rem" }}>{tpl.name}</h3>
                      <p style={{ color: "var(--text-2)", fontSize: ".85rem", marginBottom: 8 }}>
                        {tpl.description}
                      </p>
                      <div
                        style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}
                      >
                        <span className="badge badge-blue">{tpl.category}</span>
                        <SyntheticBadge template={tpl} />
                      </div>
                      {tpl.id === "solana-sandwich-lighthouse" && (
                        <Link
                          href="/help/lighthouse-scenario"
                          data-testid="template-what-this-is-link"
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            display: "inline-block",
                            marginTop: 8,
                            fontSize: ".8rem",
                            color: "var(--accent)",
                            textDecoration: "underline",
                          }}
                        >
                          What this is →
                        </Link>
                      )}
                    </div>
                  ))}
                </div>
              )}
            <button className="btn btn-secondary" onClick={() => setStep("form")}>
              Start from Scratch
            </button>
          </Card>
        )}

        {/* ════════ STEP 2: BUILDER FORM ════════ */}
        {step === "form" && (
          <>
            <div className="bsec-strip">
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setStep("pick")}
              >
                Browse templates
              </button>
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-secondary btn-sm"
                data-testid="editor-mode-toggle"
                aria-pressed={editorMode === "raw"}
                onClick={editorMode === "raw" ? exitRawMode : enterRawMode}
              >
                {editorMode === "raw"
                  ? "Back to Structured Editing"
                  : "Edit as Raw JSON →"}
              </button>
            </div>

            {editorMode === "raw" && rawDraft ? (
              <div style={{ padding: 24, overflow: "auto" }}>
                <Card title="Raw Spec Editor">
                  <RawSpecEditor
                    draft={rawDraft}
                    onChange={setRawDraft}
                    validationErrors={validationErrors}
                  />
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      marginTop: 16,
                      flexWrap: "wrap",
                    }}
                  >
                    <button
                      className="btn btn-primary cta-primary"
                      style={{ flex: 1, minWidth: 140 }}
                      onClick={handleBuildAndRun}
                      disabled={isSubmitting}
                    >
                      {isSubmitting ? "Building…" : "Build & Run"}
                    </button>
                    <button
                      className="btn btn-secondary"
                      onClick={handleBuildAndOpenRunner}
                      disabled={isSubmitting}
                    >
                      Build & Open Runner
                    </button>
                    <button
                      className="btn btn-secondary"
                      onClick={handleValidate}
                      disabled={isValidating || isSubmitting}
                    >
                      {isValidating ? "Validating…" : "Validate Spec"}
                    </button>
                  </div>
                </Card>
              </div>
            ) : (
              <section className="bsec-shell">
                {/* SECTION NAV */}
                <nav className="bsec-nav">
                  <div className="bsec-nav-head">
                    <div className="bsec-search">
                      <span aria-hidden="true">⌕</span>
                      <input
                        value={secSearch}
                        onChange={(e) => setSecSearch(e.target.value)}
                        placeholder="Find any field…"
                        aria-label="Find any field"
                      />
                    </div>
                  </div>
                  <div className="bsec-nav-list">
                    {sectionsFiltered.map((s) => {
                      const sIssues = issuesBySection[s.id]?.length ?? 0;
                      const cls =
                        "bsec-link" + (activeSection === s.id ? " active" : "");
                      return (
                        <button
                          key={s.id}
                          type="button"
                          className={cls}
                          data-section={s.id}
                          onClick={() => setActiveSection(s.id)}
                        >
                          <span className="bsec-link-ico">{s.icon}</span>
                          <span className="bsec-link-lbl">
                            <span className="bsec-link-main">{s.title}</span>
                            <span className="bsec-link-sub">{s.sub}</span>
                          </span>
                          {sIssues > 0 ? (
                            <span className="bsec-badge bsec-badge-warn">{sIssues}</span>
                          ) : (
                            <span className="bsec-badge">{s.fields}</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  <div className="bsec-nav-foot">
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>Spec status</span>
                      <span>
                        {totalIssues === 0
                          ? "ready"
                          : `${totalIssues} issue${totalIssues === 1 ? "" : "s"}`}
                      </span>
                    </div>
                    <div className="bsec-progress">
                      <span style={{ width: `${Math.max(8, 100 - totalIssues * 8)}%` }} />
                    </div>
                  </div>
                </nav>

                {/* EDITOR */}
                <div className="bsec-editor">
                  {sectionRenderers[activeSection]()}
                </div>

                {/* PREVIEW */}
                <aside className="bsec-preview">
                  <div className="bsec-preview-tabs">
                    <button
                      type="button"
                      className={previewTab === "summary" ? "on" : ""}
                      onClick={() => setPreviewTab("summary")}
                    >
                      Summary
                    </button>
                    <button
                      type="button"
                      className={previewTab === "issues" ? "on" : ""}
                      onClick={() => setPreviewTab("issues")}
                    >
                      Issues{totalIssues > 0 ? ` (${totalIssues})` : ""}
                    </button>
                    <button
                      type="button"
                      className={previewTab === "json" ? "on" : ""}
                      onClick={() => setPreviewTab("json")}
                    >
                      Spec JSON
                    </button>
                  </div>
                  <div className="bsec-preview-body">
                    {previewTab === "summary" && renderSummary()}
                    {previewTab === "issues" && renderIssues()}
                    {previewTab === "json" && renderJsonPreview()}
                  </div>
                </aside>

                {/* ACTIONS */}
                <div className="bsec-actions">
                  <div className="bsec-actions-left">
                    <span className={`bsec-dot ${actionDot}`} />
                    <span>{actionLabel}</span>
                  </div>
                  <div className="bsec-actions-right">
                    <button
                      className="btn btn-secondary"
                      onClick={handleValidate}
                      disabled={isValidating || isSubmitting}
                    >
                      {isValidating ? "Validating…" : "Validate"}
                    </button>
                    <button
                      className="btn btn-secondary"
                      onClick={() => setSpecModalOpen(true)}
                    >
                      Preview Spec
                    </button>
                    <button
                      className="btn btn-secondary"
                      onClick={handleBuildAndOpenRunner}
                      disabled={isSubmitting}
                    >
                      Build & Open Runner
                    </button>
                    <button
                      className="btn btn-primary cta-primary"
                      onClick={handleBuildAndRun}
                      disabled={isSubmitting}
                      data-testid="builder-build-and-run"
                    >
                      {isSubmitting ? "Building…" : "Build & Run"}
                    </button>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </div>

      {/* ═══════════════ SPEC PREVIEW MODAL ═══════════════ */}
      <Modal
        open={specModalOpen}
        onClose={() => setSpecModalOpen(false)}
        title="RunSpec Preview"
        maxWidth={640}
        actions={
          <>
            <button
              className="btn btn-secondary"
              onClick={() => {
                navigator.clipboard?.writeText(specJson);
                showToast("Spec copied to clipboard", "success");
              }}
            >
              Copy JSON
            </button>
            <button
              className="btn btn-primary"
              onClick={() => setSpecModalOpen(false)}
            >
              Close
            </button>
          </>
        }
      >
        <div
          className="json-view"
          dangerouslySetInnerHTML={{ __html: syntaxHighlight(specJson) }}
        />
      </Modal>

      {/* Hidden validation errors list — keeps the existing test selector
          working while the new layout surfaces issues in the preview pane. */}
      {validationErrors.length > 0 && (
        <ul
          data-testid="builder-validation-errors"
          style={{ display: "none" }}
          aria-hidden="true"
        >
          {validationErrors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      )}
    </>
  );
}
