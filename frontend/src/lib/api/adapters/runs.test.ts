import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  agentRowsFromResult,
  chartDataFromResult,
  fromApiEvent,
  fromApiEvents,
  fromApiRun,
  fromApiRuns,
  metricsFromResult,
  priorityFeeMarketChartFromEvents,
  specFromApi,
  specToApi,
  type ApiRun,
  type ApiRunResultResponse,
  type ApiRunsListResponse,
  type ApiRunEventsResponse,
} from "@/lib/api/adapters/runs";
import type { EvEntry } from "@/lib/types";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "..", "test", "fixtures", "api");

function loadFixture<T>(name: string): T {
  return JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8")) as T;
}

describe("runs adapter", () => {
  describe("fromApiRun", () => {
    it("maps a captured /runs/{id} payload to SimRun", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const run = fromApiRun(raw);

      expect(run.id).toBe(raw.run_id);
      expect(run.status).toBe("completed");
      expect(run.seed).toBe(42);
      // US-017: SimRun.market is now the raw backend type, not a
      // pretty-printed label.
      expect(run.market).toBe("cfamm");
      expect(run.currentRound).toBe(5);
      expect(run.totalRounds).toBe(5);
      expect(run.agents).toBe(1);
      expect(run.createdAt).toMatch(/^2026-/);
      expect(run.spec.config.num_rounds).toBe(5);
      expect(run.spec.config.seed).toBe(42);
      expect(run.spec.market.type).toBe("cfamm");
    });

    it("survives a minimal run with no summary or spec", () => {
      const run = fromApiRun({
        run_id: "bare",
        status: "live",
      });
      expect(run.id).toBe("bare");
      expect(run.status).toBe("running"); // live → running
      expect(run.seed).toBe(0);
      expect(run.currentRound).toBe(0);
      expect(run.totalRounds).toBe(0);
      expect(run.agents).toBe(0);
      expect(run.spec.config.num_rounds).toBe(0);
    });

    it("maps unknown backend statuses to 'running'", () => {
      expect(fromApiRun({ run_id: "x", status: "weird" }).status).toBe("running");
    });
  });

  describe("fromApiRuns", () => {
    it("maps a list response", () => {
      const list = loadFixture<ApiRunsListResponse>("runs_list.json");
      const runs = fromApiRuns(list.runs);
      expect(runs.length).toBe(list.runs.length);
      expect(runs[0].id).toBe(list.runs[0].run_id);
    });
  });

  describe("specFromApi / specToApi round-trip", () => {
    it("specFromApi derives market defaults from tokens", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      expect(spec.market.num_assets).toBe(2); // YES + NO
      expect(spec.market.token_decimals).toBe(9);
      expect(spec.market.initial_liquidity).toBe(1_000_000);
    });

    it("specToApi produces a CFAMM payload the backend accepts by shape", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      const api = specToApi(spec);

      expect(api.num_rounds).toBe(5);
      expect(api.seed).toBe(42);
      const market = api.market as { type: string; tokens: unknown[] };
      expect(market.type).toBe("cfamm");
      expect(market.tokens).toHaveLength(2);
      expect(api.agents).toBeInstanceOf(Array);
      expect(api.clock).toEqual({
        type: "block",
        params: { block_time: 1, epoch_length: 1 },
      });
      expect(api.execution).toEqual({
        type: "direct",
        params: {},
        ordering: { type: "fifo", params: {} },
        gas_model: { type: "zero", params: {} },
      });
      expect(api.default_fee_model).toEqual({
        type: "flat",
        params: { trade_fee_bps: 30 },
      });
      expect(api.information_filter).toEqual({
        type: "full_transparency",
        params: {},
      });
      expect(api.feeds).toEqual([
        {
          type: "stochastic",
          params: {
            process: "gbm",
            process_params: { mu: 0.0001, sigma: 0.02, initial: 1.0 },
            seed: 42,
          },
        },
      ]);
    });

    it("specToApi encodes a world market from spec.world", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.market.type = "world";
      spec.world = {
        markets: [
          { id: "m1", type: "cfamm", label: "amm", tokens: ["YES", "NO"] },
          { id: "m2", type: "clob", label: "book", tokens: ["BASE", "QUOTE"] },
        ],
        links: [{ from: "m1", to: "m2", token: "YES" }],
      };
      const api = specToApi(spec);
      const market = api.market as {
        type: string;
        markets: Record<string, { type: string }>;
      };
      expect(market.type).toBe("world");
      expect(Object.keys(market.markets).sort()).toEqual(["amm", "book"]);
      expect(market.markets.amm.type).toBe("cfamm");
      expect(market.markets.book.type).toBe("clob");
    });

    it("specToApi falls back to cfamm when world has no markets", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.market.type = "world";
      const api = specToApi(spec);
      const market = api.market as { type: string };
      expect(market.type).toBe("cfamm");
    });

    it("specToApi threads role_params onto each per-role agent's params", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.agents = {
        total: 2,
        mix: {
          noise: 0.5,
          informed: 0.5,
          arbitrageur: 0,
          manipulator: 0,
          passive_lp: 0,
          rebalancing_lp: 0,
        },
        default_collateral: 100_000,
        role_params: {
          noise: { tradeMin: 10, tradeMax: 200, frequency: 0.9 },
          informed: { conviction: 0.75, frequency: 0.1 },
        },
      };
      const api = specToApi(spec);
      const agents = api.agents as Array<{
        type: string;
        params: Record<string, unknown>;
      }>;
      expect(agents).toHaveLength(2);
      const noise = agents.find((a) => a.type === "noise");
      const informed = agents.find((a) => a.type === "informed");
      expect(noise?.params.trade_min).toBe(10);
      expect(noise?.params.trade_max).toBe(200);
      expect(noise?.params.frequency).toBe(0.9);
      expect(informed?.params.conviction).toBe(0.75);
      // Informed agents don't accept `frequency` — backend InformedParams
      // only has `conviction` / `trade_fraction` / `capital_limit`.
      expect(informed?.params.frequency).toBeUndefined();
    });

    it("specToApi maps builder runtime settings onto the backend schema", () => {
      const api = specToApi({
        market: {
          type: "cfamm",
          num_assets: 2,
          initial_liquidity: 1_000_000,
          token_decimals: 9,
        },
        clock: { type: "variable", block_time: 12, epoch_length: 5 },
        execution: {
          model: "direct",
          ordering: "priority",
          cost_model: "eip1559",
        },
        fee_model: { type: "dynamic", rate_bps: 45 },
        agents: {
          total: 10,
          mix: {
            noise: 0.6,
            informed: 0.4,
            arbitrageur: 0,
            manipulator: 0,
            passive_lp: 0,
            rebalancing_lp: 0,
          },
          default_collateral: 100_000,
        },
        feeds: [
          {
            type: "mean_revert",
            process: "mean_reversion",
            drift: 0.005,
            volatility: 0.15,
            initial_price: 1.2,
          },
        ],
        config: {
          num_rounds: 4,
          snapshot_interval: 1,
          seed: 7,
          numeric_mode: "FIXED_POINT",
          information_filter: "delayed_information",
        },
      });

      expect((api.agents as unknown[])).toHaveLength(10);
      expect(api.clock).toEqual({
        type: "variable_block",
        params: { timestamps: [12, 24, 36, 48], epoch_length: 5 },
      });
      expect(api.execution).toEqual({
        type: "direct",
        params: {},
        ordering: { type: "priority", params: {} },
        gas_model: {
          type: "eip1559",
          params: { base_fee: 1, target_actions_per_round: 50, adjustment_factor: 8 },
        },
      });
      expect(api.default_fee_model).toEqual({
        type: "dynamic",
        params: { base_bps: 45, max_bps: 135, volatility_multiplier: 2.0 },
      });
      expect(api.information_filter).toEqual({
        type: "delayed_information",
        params: {
          delays: {
            noise: 1,
            informed: 1,
            arbitrageur: 1,
            manipulator: 1,
            passive_lp: 1,
            rebalancing_lp: 1,
            lp: 1,
          },
        },
      });
      expect(api.feeds).toEqual([
        {
          type: "stochastic",
          params: {
            process: "mean_reversion",
            process_params: { mu: 0.005, sigma: 0.15, initial: 1.2, theta: 1.2, kappa: 0.1 },
            seed: 7,
          },
        },
      ]);
    });
  });

  describe("solana_slot clock", () => {
    it("specToApi emits solana_slot params (US-001)", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.clock = {
        type: "solana_slot",
        block_time: 0.4,
        epoch_length: 432_000,
        skip_rate: 0.05,
      };
      const api = specToApi(spec);
      expect(api.clock).toEqual({
        type: "solana_slot",
        params: {
          slot_duration_seconds: 0.4,
          epoch_length_slots: 432_000,
          skip_rate: 0.05,
        },
      });
    });

    it("specFromApi parses solana_slot params back into block_time/epoch_length/skip_rate", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        clock: {
          type: "solana_slot",
          params: {
            slot_duration_seconds: 0.4,
            epoch_length_slots: 432_000,
            skip_rate: 0.1,
          },
        },
      });
      expect(spec.clock).toEqual({
        type: "solana_slot",
        block_time: 0.4,
        epoch_length: 432_000,
        skip_rate: 0.1,
      });
    });
  });

  describe("solana compute_budget (US-002)", () => {
    it("specToApi emits execution.params.compute_budget when execution is solana", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "solana",
        compute_budget: {
          preset: "custom",
          per_slot: 30_000_000,
          per_tx: 700_000,
          per_writable_account: 6_000_000,
        },
      };
      const api = specToApi(spec);
      expect(api.execution).toMatchObject({
        type: "solana_like",
        params: {
          compute_budget: {
            per_slot: 30_000_000,
            per_tx: 700_000,
            per_writable_account: 6_000_000,
          },
        },
      });
    });

    it("specFromApi parses execution.params.compute_budget back into the spec", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        execution: {
          type: "solana_like",
          params: {
            compute_budget: {
              per_slot: 30_000_000,
              per_tx: 700_000,
              per_writable_account: 6_000_000,
            },
          },
          ordering: { type: "fifo" },
          gas_model: { type: "compute_unit" },
        },
      });
      expect(spec.execution.compute_budget).toEqual({
        preset: "custom",
        per_slot: 30_000_000,
        per_tx: 700_000,
        per_writable_account: 6_000_000,
      });
    });

    it("specToApi emits execution.params.priority_fee_market on solana (US-010 PRD line 747)", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "solana",
        priority_fee_market: {
          window_slots: 200,
          ewma_half_life_slots: 60,
          floor_micro_lamports: 7,
          update_event_threshold: 0.1,
        },
      };
      const api = specToApi(spec);
      expect(api.execution).toMatchObject({
        type: "solana_like",
        params: {
          priority_fee_market: {
            window_slots: 200,
            ewma_half_life_slots: 60,
            floor_micro_lamports: 7,
            update_event_threshold: 0.1,
          },
        },
      });
    });

    it("specFromApi parses execution.params.priority_fee_market back into the spec", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        execution: {
          type: "solana_like",
          params: {
            priority_fee_market: {
              window_slots: 300,
              ewma_half_life_slots: 75,
              floor_micro_lamports: 42,
              update_event_threshold: 0.2,
            },
          },
          ordering: { type: "fifo" },
          gas_model: { type: "compute_unit" },
        },
      });
      expect(spec.execution.priority_fee_market).toEqual({
        window_slots: 300,
        ewma_half_life_slots: 75,
        floor_micro_lamports: 42,
        update_event_threshold: 0.2,
      });
    });

    it("specToApi emits execution.params.fork_spec on solana (US-014 PRD line 1125)", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "solana",
        fork_spec: {
          fork_probability_per_slot: 0.1,
          max_reorg_depth_slots: 8,
        },
      };
      const api = specToApi(spec);
      expect(api.execution).toMatchObject({
        type: "solana_like",
        params: {
          fork_spec: {
            fork_probability_per_slot: 0.1,
            max_reorg_depth_slots: 8,
          },
        },
      });
    });

    it("specFromApi parses execution.params.fork_spec back into the spec", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        execution: {
          type: "solana_like",
          params: {
            fork_spec: {
              fork_probability_per_slot: 0.05,
              max_reorg_depth_slots: 3,
              seed: 7,
            },
          },
          ordering: { type: "fifo" },
          gas_model: { type: "compute_unit" },
        },
      });
      expect(spec.execution.fork_spec).toEqual({
        fork_probability_per_slot: 0.05,
        max_reorg_depth_slots: 3,
        seed: 7,
      });
    });

    it("specToApi omits fork_spec for non-solana execution", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "direct",
        fork_spec: {
          fork_probability_per_slot: 0.5,
          max_reorg_depth_slots: 4,
        },
      };
      const api = specToApi(spec);
      const exec = api.execution as { params?: Record<string, unknown> };
      expect(exec.params?.fork_spec).toBeUndefined();
    });

    it("specToApi omits priority_fee_market for non-solana execution", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "direct",
        priority_fee_market: {
          window_slots: 200,
          ewma_half_life_slots: 60,
          floor_micro_lamports: 7,
          update_event_threshold: 0.1,
        },
      };
      const api = specToApi(spec);
      const exec = api.execution as { params?: Record<string, unknown> };
      expect(exec.params?.priority_fee_market).toBeUndefined();
    });

    it("specToApi omits compute_budget for non-solana execution", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "direct",
        compute_budget: {
          preset: "custom",
          per_slot: 30_000_000,
          per_tx: 700_000,
          per_writable_account: 6_000_000,
        },
      };
      const api = specToApi(spec);
      const exec = api.execution as { params?: Record<string, unknown> };
      expect(exec.params?.compute_budget).toBeUndefined();
    });
  });

  describe("solana oracle_preset (US-006)", () => {
    it("specToApi forwards a named oracle preset on solana execution", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "solana",
        oracle_preset: "pyth_lazer",
      };
      const api = specToApi(spec);
      expect(api.execution).toMatchObject({
        type: "solana_like",
        params: { oracle_preset: "pyth_lazer" },
      });
    });

    it("specToApi omits oracle_preset when none / non-solana", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.execution = {
        ...spec.execution,
        model: "solana",
        oracle_preset: "none",
      };
      const apiNone = specToApi(spec);
      const execNone = apiNone.execution as { params?: Record<string, unknown> };
      expect(execNone.params?.oracle_preset).toBeUndefined();

      spec.execution = {
        ...spec.execution,
        model: "direct",
        oracle_preset: "pyth_pull",
      };
      const apiEth = specToApi(spec);
      const execEth = apiEth.execution as { params?: Record<string, unknown> };
      expect(execEth.params?.oracle_preset).toBeUndefined();
    });

    it("specFromApi parses oracle_preset back into the spec", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        execution: {
          type: "solana_like",
          params: { oracle_preset: "switchboard_on_demand" },
          ordering: { type: "fifo" },
          gas_model: { type: "compute_unit" },
        },
      });
      expect(spec.execution.oracle_preset).toBe("switchboard_on_demand");
    });

    it("specFromApi ignores unknown oracle_preset strings", () => {
      const spec = specFromApi({
        market: { type: "cfamm", tokens: [], params: {} },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
        execution: {
          type: "solana_like",
          params: { oracle_preset: "made_up_oracle" },
          ordering: { type: "fifo" },
          gas_model: { type: "compute_unit" },
        },
      });
      expect(spec.execution.oracle_preset).toBeUndefined();
    });
  });

  describe("token extensions (US-007)", () => {
    it("specFromApi reads standard / LST / transfer-hook fields off tokens", () => {
      const spec = specFromApi({
        market: {
          type: "cfamm",
          tokens: [
            { id: "SOL", symbol: "SOL", decimals: 9, standard: "native" },
            {
              id: "mSOL",
              symbol: "mSOL",
              decimals: 9,
              standard: "spl",
              exchange_rate_to_sol: 1.05,
              exchange_rate_drift: {
                drift_per_epoch: 0.0001,
                volatility_per_epoch: 0.001,
              },
            },
            {
              id: "USDC22",
              symbol: "USDC22",
              decimals: 6,
              standard: "spl_2022",
              transfer_hook: {
                program_id: "Hook111111111111111111111111111111111111111",
                additional_cu_per_transfer: 5_000,
                additional_lamports_per_transfer: 100,
              },
              confidential: true,
            },
          ],
          params: { initial_liquidity: 1_000_000 },
        },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
      });

      expect(spec.market.tokens?.length).toBe(3);
      expect(spec.market.tokens?.[0].standard).toBe("native");
      expect(spec.market.tokens?.[1].exchange_rate_to_sol).toBe(1.05);
      expect(spec.market.tokens?.[1].exchange_rate_drift).toEqual({
        drift_per_epoch: 0.0001,
        volatility_per_epoch: 0.001,
      });
      expect(spec.market.tokens?.[2].transfer_hook).toEqual({
        program_id: "Hook111111111111111111111111111111111111111",
        additional_cu_per_transfer: 5_000,
        additional_lamports_per_transfer: 100,
      });
      expect(spec.market.tokens?.[2].confidential).toBe(true);
    });

    it("specToApi forwards LST drift and transfer-hook fields verbatim", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.market.tokens = [
        {
          id: "mSOL",
          symbol: "mSOL",
          decimals: 9,
          standard: "spl",
          exchange_rate_to_sol: 1.07,
          exchange_rate_drift: {
            drift_per_epoch: 0.0002,
            volatility_per_epoch: 0,
          },
        },
        {
          id: "USDC22",
          symbol: "USDC22",
          decimals: 6,
          standard: "spl_2022",
          transfer_hook: {
            program_id: "Hook2222222222222222222222222222222222222222",
            additional_cu_per_transfer: 8_000,
            additional_lamports_per_transfer: 250,
          },
          confidential: false,
        },
      ];
      const api = specToApi(spec);
      const market = api.market as { tokens: Array<Record<string, unknown>> };
      expect(market.tokens[0]).toMatchObject({
        id: "mSOL",
        standard: "spl",
        exchange_rate_to_sol: 1.07,
        exchange_rate_drift: {
          drift_per_epoch: 0.0002,
          volatility_per_epoch: 0,
        },
      });
      expect(market.tokens[1]).toMatchObject({
        id: "USDC22",
        standard: "spl_2022",
        transfer_hook: {
          program_id: "Hook2222222222222222222222222222222222222222",
          additional_cu_per_transfer: 8_000,
          additional_lamports_per_transfer: 250,
        },
        confidential: false,
      });
    });

    it("specToApi omits extension fields on plain tokens", () => {
      const raw = loadFixture<ApiRun>("run_get.json");
      const spec = specFromApi(raw.spec);
      spec.market.tokens = [
        { id: "YES", symbol: "YES", decimals: 9 },
        { id: "NO", symbol: "NO", decimals: 9 },
      ];
      const api = specToApi(spec);
      const market = api.market as { tokens: Array<Record<string, unknown>> };
      expect(market.tokens[0]).toEqual({ id: "YES", symbol: "YES", decimals: 9 });
      expect(market.tokens[0]).not.toHaveProperty("exchange_rate_drift");
      expect(market.tokens[0]).not.toHaveProperty("transfer_hook");
      expect(market.tokens[0]).not.toHaveProperty("confidential");
    });

    it("specFromApi ignores malformed extension payloads", () => {
      const spec = specFromApi({
        market: {
          type: "cfamm",
          tokens: [
            {
              id: "SOL",
              symbol: "SOL",
              decimals: 9,
              exchange_rate_to_sol: null,
              exchange_rate_drift: null,
              transfer_hook: null,
            },
          ],
          params: {},
        },
        agents: [],
        num_rounds: 1,
        snapshot_interval: 1,
        seed: 1,
      });
      const tok = spec.market.tokens?.[0];
      expect(tok?.exchange_rate_to_sol).toBeUndefined();
      expect(tok?.exchange_rate_drift).toBeUndefined();
      expect(tok?.transfer_hook).toBeUndefined();
    });
  });

  describe("specFromApi", () => {
    it("parses backend runtime settings back into the frontend run spec", () => {
      const spec = specFromApi({
        market: {
          type: "cfamm",
          tokens: [
            { id: "YES", symbol: "YES", decimals: 9 },
            { id: "NO", symbol: "NO", decimals: 9 },
          ],
          params: { initial_liquidity: 1_000_000, collateral_token: "COLLATERAL" },
        },
        agents: [
          { type: "noise", agent_id: "noise-1" },
          { type: "informed", agent_id: "informed-1" },
        ],
        num_rounds: 9,
        snapshot_interval: 3,
        seed: 77,
        numeric_mode: "float",
        clock: { type: "block", params: { block_time: 12, epoch_length: 4 } },
        execution: {
          type: "solana_like",
          ordering: { type: "priority" },
          gas_model: { type: "compute_unit" },
        },
        default_fee_model: { type: "spread", params: { base_bps: 25 } },
        information_filter: { type: "delayed_information", params: { delays: { noise: 1 } } },
        feeds: [
          {
            type: "stochastic",
            params: {
              process: "jump_diffusion",
              process_params: { mu: 0.002, sigma: 0.11, initial: 1.4 },
            },
          },
        ],
      });

      expect(spec.clock).toEqual({ type: "block", block_time: 12, epoch_length: 4 });
      // execution.model normalizes the wire-only `solana_like` alias
      // back to the frontend canonical `solana` so RunSpec consumers
      // (executionToApi solana-only gates, builder bExec state) can
      // round-trip solana-only params without an extra translation.
      expect(spec.execution).toEqual({
        model: "solana",
        ordering: "priority",
        cost_model: "compute_unit",
      });
      expect(spec.fee_model).toEqual({ type: "spread", rate_bps: 25 });
      expect(spec.config.numeric_mode).toBe("FLOAT_MODE");
      expect(spec.config.information_filter).toBe("delayed_information");
      expect(spec.feeds).toEqual([
        {
          type: "jump",
          process: "jump_diffusion",
          drift: 0.002,
          volatility: 0.11,
          initial_price: 1.4,
        },
      ]);
    });

    it("preserves unknown market, execution, fee, information and feed types through round-trip", () => {
      const raw = {
        market: {
          type: "custom_market",
          tokens: [
            { id: "X", symbol: "X", decimals: 6 },
            { id: "Y", symbol: "Y", decimals: 6 },
          ],
          params: { initial_liquidity: 500_000, collateral_token: "COLLATERAL" },
        },
        agents: [{ type: "noise", agent_id: "noise-1" }],
        num_rounds: 3,
        snapshot_interval: 1,
        seed: 9,
        clock: { type: "block", params: { block_time: 1, epoch_length: 1 } },
        execution: {
          type: "arbitrum_like",
          ordering: { type: "fifo" },
          gas_model: { type: "zero" },
        },
        default_fee_model: { type: "exotic_fee", params: { base_bps: 17 } },
        information_filter: { type: "private_information", params: {} },
        feeds: [
          {
            type: "custom_feed",
            params: {
              process: "ou_with_jumps",
              process_params: { mu: 0.01, sigma: 0.05, initial: 2.5 },
            },
          },
        ],
      };

      const spec = specFromApi(raw);
      expect(spec.market.type).toBe("custom_market");
      expect(spec.execution.model).toBe("arbitrum_like");
      expect(spec.fee_model).toEqual({ type: "exotic_fee", rate_bps: 17 });
      expect(spec.config.information_filter).toBe("private_information");
      expect(spec.feeds[0]).toEqual({
        type: "custom_feed",
        process: "ou_with_jumps",
        drift: 0.01,
        volatility: 0.05,
        initial_price: 2.5,
      });

      const api = specToApi(spec);
      expect((api.market as { type: string }).type).toBe("custom_market");
      expect((api.execution as { type: string }).type).toBe("arbitrum_like");
      expect(api.default_fee_model).toEqual({
        type: "exotic_fee",
        params: { base_bps: 17 },
      });
      expect(api.information_filter).toEqual({
        type: "private_information",
        params: {},
      });
      const feedOut = (api.feeds as Array<{ type: string; params: { process: string; process_params: Record<string, number> } }>)[0];
      expect(feedOut.type).toBe("custom_feed");
      expect(feedOut.params.process).toBe("ou_with_jumps");
      expect(feedOut.params.process_params).toEqual({
        mu: 0.01,
        sigma: 0.05,
        initial: 2.5,
      });
    });
  });

  describe("run metadata labels (US-017: raw pass-through)", () => {
    it("surfaces raw backend identifiers for execution, ordering, fee, and feed", () => {
      const run = fromApiRun({
        run_id: "cfg-1",
        status: "completed",
        seed: 11,
        market_type: "cfamm",
        current_round: 4,
        created_at: "2026-04-13T00:00:00Z",
        spec: {
          market: {
            type: "cfamm",
            tokens: [
              { id: "YES", symbol: "YES", decimals: 9 },
              { id: "NO", symbol: "NO", decimals: 9 },
            ],
            params: { initial_liquidity: 1_000_000, collateral_token: "COLLATERAL" },
          },
          agents: [{ type: "noise", agent_id: "noise-1" }],
          execution: {
            type: "direct",
            ordering: { type: "priority" },
            gas_model: { type: "eip1559" },
          },
          default_fee_model: { type: "dynamic", params: { base_bps: 60 } },
          feeds: [{ type: "stochastic", params: { process: "mean_reversion" } }],
          num_rounds: 4,
          snapshot_interval: 1,
          seed: 11,
        },
        summary: { agent_count: 1, num_rounds: 4 },
      });

      // US-017: labels are the raw backend identifiers — no more
      // hardcoded pretty-casing. The dashboard/detail modal shows
      // whatever the backend shipped, preserving unknown types.
      expect(run.exec).toBe("direct");
      expect(run.ordering).toBe("priority");
      expect(run.fee).toBe("dynamic 60bps");
      expect(run.feed).toBe("mean_reversion");
    });

    it("passes unknown execution/fee/feed types through verbatim", () => {
      const run = fromApiRun({
        run_id: "cfg-2",
        status: "completed",
        seed: 1,
        market_type: "unknown_future_market",
        current_round: 1,
        spec: {
          market: {
            type: "unknown_future_market",
            tokens: [{ id: "A", symbol: "A", decimals: 6 }],
            params: { initial_liquidity: 1, collateral_token: "COLLATERAL" },
          },
          agents: [{ type: "noise", agent_id: "a" }],
          execution: {
            type: "future_execution_kind",
            ordering: { type: "mev_aware" },
            gas_model: { type: "future_gas" },
          },
          default_fee_model: { type: "novel_fee_kind", params: { base_bps: 9 } },
          feeds: [{ type: "exotic_feed" }],
          num_rounds: 1,
          snapshot_interval: 1,
          seed: 1,
        },
      });
      expect(run.market).toBe("unknown_future_market");
      expect(run.exec).toBe("future_execution_kind");
      expect(run.ordering).toBe("mev_aware");
      expect(run.fee).toBe("novel_fee_kind 9bps");
      expect(run.feed).toBe("exotic_feed");
    });
  });

  describe("agentRowsFromResult", () => {
    it("derives AgentRow[] from agent_final_states", () => {
      const { result } = loadFixture<ApiRunResultResponse>("run_result.json");
      const rows = agentRowsFromResult(result);
      expect(rows).toHaveLength(1);
      expect(rows[0].id).toBe(0);
      expect(rows[0].agentId).toBe("noise-1");
      expect(rows[0].role).toBe("noise");
      expect(rows[0].balance).toBeGreaterThan(0);
      expect(rows[0].volume).toBe(0);
      expect(rows[0].pnl).toBe(0);
      expect(rows[0].trades).toBe(0);
    });

    it("returns [] when result has no agents", () => {
      expect(agentRowsFromResult({})).toEqual([]);
    });

    it("falls back to the dict key when state.agent_id is missing", () => {
      const rows = agentRowsFromResult({
        agent_final_states: {
          "lp-7": {
            role: { name: "passive_lp" },
            balances: { COLLATERAL: 1000 },
            cumulative_volume: 5,
            realized_pnl: 1,
          },
        },
      });
      expect(rows[0].agentId).toBe("lp-7");
      expect(rows[0].role).toBe("lp");
    });
  });

  describe("metricsFromResult", () => {
    it("derives concrete metrics from the result payload", () => {
      const { result } = loadFixture<ApiRunResultResponse>("run_result.json");
      const metrics = metricsFromResult(result);
      expect(metrics.klDivergence).toBeNull();
      expect(metrics.maxDrawdown).toBe(0);
      expect(metrics.rollingVol).toBe(0);
      expect(metrics.twap).toBe(499_998_895);
      // No fee_history and no usable liquidity_history fallback → null.
      // The dashboard renders ``—`` for null instead of the misleading
      // 1.000 placeholder this previously emitted.
      expect(metrics.lpProfitability).toBeNull();
      // Composite still defined: a null lpProfitability is treated as
      // the neutral 1 by the scoring formula. (scoreMdd(0)=1 +
      // scoreLp(1)=0 + scoreRvol(0)=1) / 3 × 100 = 67.
      expect(metrics.compositeScore).toBe(67);
    });

    it("returns null instead of Infinity when starting liquidity is zero", () => {
      const metrics = metricsFromResult({
        price_history: [{ TKN: 100 }, { TKN: 110 }],
        liquidity_history: [0, 250],
      });
      expect(metrics.lpProfitability).toBeNull();
      expect(metrics.compositeScore).toBeLessThanOrEqual(100);
    });
  });

  describe("chartDataFromResult", () => {
    it("unpacks price_history into per-token series", () => {
      const { result } = loadFixture<ApiRunResultResponse>("run_result.json");
      const chart = chartDataFromResult(result);
      // CFAMM has YES and NO — 2 series
      expect(chart.priceData.length).toBe(2);
      expect(chart.priceLabels).toEqual(["YES", "NO"]);
      expect(chart.priceData[0].length).toBe(5);
      expect(chart.pnlData).toHaveLength(1); // one agent
      expect(chart.pnlColors).toHaveLength(1);
      // volume is derived from round_snapshots when volume_history is
      // absent — the fixture's single agent has cumulative_volume=0 at
      // every snapshot, so the synthesised series is all zeros.
      expect(chart.cumVol).toEqual([0, 0, 0, 0, 0]);
      // fee_history is absent from this fixture, so no cumulative series.
      expect(chart.fees).toEqual([]);
    });

    it("sums fee_history splits per round into a cumulative series", () => {
      const chart = chartDataFromResult({
        price_history: [{ TKN: 1 }, { TKN: 1 }, { TKN: 1 }],
        fee_history: [
          { lp: 10, protocol: 5 },
          {},
          { lp: 2, protocol: 1, burn: 1 },
        ],
        agent_final_states: {},
      });
      // Per-round totals: 15, 0, 4 → cumulative: 15, 15, 19.
      expect(chart.fees).toEqual([15, 15, 19]);
    });

    it("pivots fee_history into per-destination cumulative bands sorted by total", () => {
      const chart = chartDataFromResult({
        price_history: [{ TKN: 1 }, { TKN: 1 }, { TKN: 1 }],
        fee_history: [
          { lp: 10, protocol: 5 },
          {},
          { lp: 2, protocol: 1, burn: 1 },
        ],
        agent_final_states: {},
      });
      // Totals per destination: lp=12, protocol=6, burn=1 → sorted
      // lp > protocol > burn. Series are *unstacked* — each data[r]
      // is that destination's own cumulative value — so tooltips can
      // read a truthful per-destination number. The chart component
      // handles visual stacking.
      expect(chart.feesByDestination.map((s) => s.destination)).toEqual([
        "lp",
        "protocol",
        "burn",
      ]);
      expect(chart.feesByDestination[0].data).toEqual([10, 10, 12]);
      expect(chart.feesByDestination[1].data).toEqual([5, 5, 6]);
      expect(chart.feesByDestination[2].data).toEqual([0, 0, 1]);
    });

    it("sums token-keyed fee splits across tokens per destination", () => {
      // Token-aware shape (post Bug 1 fix): fee_history entries are
      // `{destination: {token: amount}}`. Summing across tokens assumes
      // a shared numeraire — acceptable for the scalar charts, while
      // the Fees by Destination chart keeps destination identity.
      const chart = chartDataFromResult({
        price_history: [{ TKN: 1 }, { TKN: 1 }],
        fee_history: [
          { lp: { USDC: 5, ETH: 2 }, protocol: { USDC: 3 } },
          { lp: { USDC: 1 } },
        ],
        agent_final_states: {},
      });
      expect(chart.fees).toEqual([10, 11]);
      const lp = chart.feesByDestination.find((s) => s.destination === "lp");
      expect(lp?.data).toEqual([7, 8]);
      const protocol = chart.feesByDestination.find((s) => s.destination === "protocol");
      expect(protocol?.data).toEqual([3, 3]);
    });

    it("decodes BigInt-marker fee values instead of skipping them", () => {
      // The backend wraps ints outside JS's safe range as
      // `{"__defi_sim_bigint__": "<digits>"}`; without decoding, those
      // values silently count as 0 and the charts look empty.
      const chart = chartDataFromResult({
        price_history: [{ TKN: 1 }, { TKN: 1 }],
        fee_history: [
          {
            lp: { __defi_sim_bigint__: "9000000000000000000" },
            protocol: 5,
          },
          { lp: 100, protocol: { __defi_sim_bigint__: "10" } },
        ],
        agent_final_states: {},
      });
      // Number-casting 9e18 is lossy, so use coarse thresholds — the
      // point is the chart no longer reads BigInt-marker objects as 0.
      expect(chart.fees[0]).toBeGreaterThan(8.9e18);
      const lpSeries = chart.feesByDestination.find((s) => s.destination === "lp");
      expect(lpSeries).toBeDefined();
      expect(lpSeries!.data[0]).toBeGreaterThan(8.9e18);
      const protocolSeries = chart.feesByDestination.find((s) => s.destination === "protocol");
      expect(protocolSeries).toBeDefined();
      // protocol per-round: 5, then encoded "10" → cumulative 5, 15.
      expect(protocolSeries!.data).toEqual([5, 15]);
    });

    it("returns empty fees when fee_history is missing", () => {
      const chart = chartDataFromResult({
        price_history: [{ TKN: 1 }],
        agent_final_states: {},
      });
      expect(chart.fees).toEqual([]);
      expect(chart.feesByDestination).toEqual([]);
    });

    it("filters world-run chart data by market name", () => {
      const result = {
        price_history: [
          {
            "amm:YES": 1,
            "amm:NO": 0.98,
            "book:YES": 1.01,
            "book:COLLATERAL": 1,
          },
          {
            "amm:YES": 1.1,
            "amm:NO": 0.95,
            "book:YES": 1.04,
            "book:COLLATERAL": 1,
          },
        ],
        round_snapshots: [
          {
            all_market_states: {
              amm: { total_liquidity: 1000 },
              book: { total_liquidity: 500 },
            },
          },
          {
            all_market_states: {
              amm: { total_liquidity: 1100 },
              book: { total_liquidity: 520 },
            },
          },
        ],
        agent_final_states: {},
      };
      const chart = chartDataFromResult(result, { market: "amm" });
      expect(chart.priceLabels).toEqual(["YES", "NO"]);
      expect(chart.priceData).toEqual([
        [1, 1.1],
        [0.98, 0.95],
      ]);
      expect(chart.liq).toEqual([1000, 1100]);
    });
  });

  describe("fromApiEvent / fromApiEvents", () => {
    const ALLOWED_CLASSES = new Set(["trade", "lp", "oracle", "reward", "fail"]);

    it("maps captured events to EvEntry[]", () => {
      const evs = loadFixture<ApiRunEventsResponse>("run_events.json");
      const mapped = fromApiEvents(evs.events);
      expect(mapped.length).toBe(evs.events.length);
      const start = mapped[0];
      expect(start.evType).toBe("SIMULATION_START");
      expect(start.round).toBe(0);
      // US-015: classification is hash-derived, not from a fixed map.
      // We only assert the class is one of the known CSS buckets.
      expect(ALLOWED_CLASSES.has(start.cls)).toBe(true);
      expect(start.detail).toContain("Engine initialized");
    });

    it("classifies every event type into one of the ev-type CSS buckets", () => {
      // US-015: the adapter no longer owns an authoritative event-type
      // coverage map. It must instead produce a stable, legal CSS
      // class for any event type — known or not — so unknown backend
      // events don't render with broken styling.
      const samples = [
        "ACTION_EXECUTED",
        "ACTION_FAILED",
        "LP_FEES_DISTRIBUTED",
        "ORACLE_UPDATE",
        "REWARD_DISTRIBUTED",
        "BACKEND_FUTURE_EVENT_KIND",
        "vendor_specific_type",
      ];
      for (const type of samples) {
        const ev = fromApiEvent({ type, round: 1 });
        expect(ALLOWED_CLASSES.has(ev.cls)).toBe(true);
      }
    });

    it("returns the same class for the same event type on every call", () => {
      // Stability is the practical requirement: charts, legends, and
      // event rows must not flicker between renders.
      expect(fromApiEvent({ type: "ACTION_EXECUTED" }).cls).toBe(
        fromApiEvent({ type: "ACTION_EXECUTED" }).cls,
      );
      expect(fromApiEvent({ type: "UNKNOWN_FUTURE" }).cls).toBe(
        fromApiEvent({ type: "UNKNOWN_FUTURE" }).cls,
      );
    });

    it("tolerates missing fields", () => {
      const ev = fromApiEvent({});
      expect(ev.round).toBe(0);
      expect(ev.evType).toBe("UNKNOWN");
      expect(ALLOWED_CLASSES.has(ev.cls)).toBe(true);
    });

    it("retains the raw data payload on EvEntry", () => {
      const ev = fromApiEvent({
        type: "PRIORITY_FEE_MARKET_UPDATED",
        round: 7,
        data: { account_id: "pool_A" },
      });
      expect(ev.data).toEqual({ account_id: "pool_A" });
    });
  });

  describe("priorityFeeMarketChartFromEvents (PRD US-010 line 748)", () => {
    // The percentile dict on PriorityFeeMarketUpdatedEvent is dict[int, int]
    // which the engine's `to_jsonable` serializes as a marker envelope
    // (`{__type__: "mapping", entries: [...]}`) since JSON keys must be
    // strings. The adapter must decode that envelope back to a usable map.
    function makeFeeUpdate(
      round: number,
      accountId: string,
      percentiles: Record<number, number>,
    ): EvEntry {
      return {
        round,
        evType: "PRIORITY_FEE_MARKET_UPDATED",
        cls: "trade",
        detail: "",
        data: {
          account_id: accountId,
          priority_fee_market_updated: {
            slot: round,
            account_id: accountId,
            percentiles: {
              __type__: "mapping",
              entries: Object.entries(percentiles).map(([k, v]) => ({
                key: Number(k),
                value: v,
              })),
            },
            previous_percentiles: null,
            threshold: 0.05,
          },
        },
      };
    }

    it("returns an empty chart when no fee-market events are present", () => {
      const chart = priorityFeeMarketChartFromEvents([
        {
          round: 1,
          evType: "ACTION_EXECUTED",
          cls: "trade",
          detail: "",
        } as EvEntry,
      ]);
      expect(chart.rounds).toEqual([]);
      expect(chart.series).toEqual([]);
      expect(chart.accounts).toEqual([]);
    });

    it("derives one series per (account, percentile) sorted by hottest pool", () => {
      const events: EvEntry[] = [
        makeFeeUpdate(1, "pool_A", { 25: 100, 50: 200, 75: 300, 90: 400, 99: 500 }),
        makeFeeUpdate(2, "pool_B", { 25: 10, 50: 20, 75: 30, 90: 40, 99: 50 }),
        makeFeeUpdate(3, "pool_A", { 25: 110, 50: 220, 75: 330, 90: 440, 99: 550 }),
      ];
      const chart = priorityFeeMarketChartFromEvents(events);
      expect(chart.accounts).toEqual(["pool_A", "pool_B"]);
      expect(chart.rounds).toEqual([1, 2, 3]);
      // 2 accounts × 5 percentiles = 10 series.
      expect(chart.series).toHaveLength(10);
      const a50 = chart.series.find(
        (s) => s.accountId === "pool_A" && s.percentile === 50,
      )!;
      expect(a50.data).toEqual([200, 200, 220]);
      const b50 = chart.series.find(
        (s) => s.accountId === "pool_B" && s.percentile === 50,
      )!;
      // pool_B's first update is at round 2 — round 1 carries NaN.
      expect(Number.isNaN(b50.data[0])).toBe(true);
      expect(b50.data[1]).toBe(20);
      // After round 2, pool_B has no further updates: carry-forward.
      expect(b50.data[2]).toBe(20);
    });

    it("caps series count via maxAccounts", () => {
      const events: EvEntry[] = [];
      for (let i = 0; i < 6; i++) {
        const id = `pool_${i}`;
        for (let r = 0; r < i + 1; r++) {
          events.push(makeFeeUpdate(r, id, { 50: 100 + r }));
        }
      }
      const chart = priorityFeeMarketChartFromEvents(events, {
        maxAccounts: 2,
      });
      expect(chart.accounts).toHaveLength(2);
      // The two hottest accounts (pool_5 and pool_4) have the most updates.
      expect(chart.accounts).toEqual(["pool_5", "pool_4"]);
    });
  });
});
