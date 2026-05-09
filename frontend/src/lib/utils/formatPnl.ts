import type { ApiRunResult } from "@/lib/api/adapters/runs";
import type { RunSpec } from "@/lib/types/simulations";

const DEFAULT_DENOM = "units";

// Decimals for common Solana SPL tokens — used as a last-resort scale when
// the spec doesn't carry tokens[] but the result references one of these
// symbols by id. Add more as needed.
const KNOWN_DECIMALS: Record<string, number> = {
  USDC: 6,
  USDT: 6,
  USDH: 6,
  PYUSD: 6,
  SOL: 9,
  WSOL: 9,
  MSOL: 9,
  BSOL: 9,
  JITOSOL: 9,
  ETH: 18,
  WETH: 18,
};

const NATIVE_IDS = new Set(["SOL", "WSOL"]);

export interface PnlDenom {
  symbol: string;
  decimals: number;
  known: boolean;
}

function fromTokensList(
  tokens: NonNullable<RunSpec["market"]["tokens"]>,
  preferId?: string | null,
): PnlDenom | null {
  if (preferId) {
    const tok =
      tokens.find((t) => t.id === preferId) ??
      tokens.find((t) => t.symbol === preferId);
    if (tok) {
      return { symbol: tok.symbol, decimals: tok.decimals ?? 0, known: true };
    }
  }
  const nonNative = tokens.filter((t) => !t.native);
  const quote =
    nonNative.length === 1 ? nonNative[0] : tokens[tokens.length - 1];
  if (!quote) return null;
  return { symbol: quote.symbol, decimals: quote.decimals ?? 0, known: true };
}

/**
 * Pick the conventional quote/denom token id from a result's agent balances.
 * Heuristic: prefer the most common non-native token id across agents.
 */
function denomIdFromResult(result?: ApiRunResult | null): string | null {
  if (!result?.agent_final_states) return null;
  const counts: Record<string, number> = {};
  for (const state of Object.values(result.agent_final_states)) {
    const balances = state?.balances;
    if (!balances) continue;
    for (const id of Object.keys(balances)) {
      if (NATIVE_IDS.has(id.toUpperCase())) continue;
      counts[id] = (counts[id] ?? 0) + 1;
    }
  }
  const entries = Object.entries(counts);
  if (entries.length === 0) return null;
  entries.sort((a, b) => b[1] - a[1]);
  return entries[0][0];
}

export function resolvePnlDenom(
  market?: RunSpec["market"] | null,
  result?: ApiRunResult | null,
): PnlDenom {
  const tokens = market?.tokens ?? [];

  const wp = market?.whirlpool_params;
  if (wp?.token_b_id || wp?.token_b_symbol) {
    const fromList = fromTokensList(tokens, wp.token_b_id ?? wp.token_b_symbol);
    if (fromList) return fromList;
    const symbol = wp.token_b_symbol ?? wp.token_b_id!;
    return {
      symbol,
      decimals: KNOWN_DECIMALS[symbol.toUpperCase()] ?? market?.token_decimals ?? 0,
      known: true,
    };
  }

  const collateralId = market?.collateral_token_id;
  if (collateralId && collateralId.toUpperCase() !== "COLLATERAL") {
    const fromList = fromTokensList(tokens, collateralId);
    if (fromList) return fromList;
    return {
      symbol: collateralId,
      decimals:
        KNOWN_DECIMALS[collateralId.toUpperCase()] ?? market?.token_decimals ?? 0,
      known: true,
    };
  }

  if (tokens.length > 0) {
    const fromList = fromTokensList(tokens);
    if (fromList) return fromList;
  }

  const fromResultId = denomIdFromResult(result);
  if (fromResultId) {
    const fromList = fromTokensList(tokens, fromResultId);
    if (fromList) return fromList;
    return {
      symbol: fromResultId,
      decimals:
        KNOWN_DECIMALS[fromResultId.toUpperCase()] ?? market?.token_decimals ?? 0,
      known: true,
    };
  }

  return { symbol: DEFAULT_DENOM, decimals: 0, known: false };
}

export function pnlDenom(
  market?: RunSpec["market"] | null,
  result?: ApiRunResult | null,
): string {
  return resolvePnlDenom(market, result).symbol;
}

function formatScaled(value: number, decimals: number): string {
  if (decimals <= 0) return Math.round(value).toLocaleString();
  const scaled = value / Math.pow(10, decimals);
  const abs = Math.abs(scaled);
  const fractionDigits = abs >= 1000 ? 0 : abs >= 1 ? 2 : 4;
  return scaled.toLocaleString(undefined, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

export function formatPnl(
  value: number | null | undefined,
  market?: RunSpec["market"] | null,
  options: { signed?: boolean; result?: ApiRunResult | null } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const { signed = true, result } = options;
  const { symbol, decimals } = resolvePnlDenom(market, result);
  const sign = signed && value >= 0 ? "+" : "";
  return `${sign}${formatScaled(value, decimals)} ${symbol}`;
}

/**
 * Look up a token's decimals from the spec's tokens[] list, falling back to
 * the hard-coded common-token table, then to `market.token_decimals`, then 0.
 */
export function tokenDecimals(
  tokenId: string,
  market?: RunSpec["market"] | null,
): number {
  const tokens = market?.tokens ?? [];
  const tok =
    tokens.find((t) => t.id === tokenId) ??
    tokens.find((t) => t.symbol === tokenId);
  if (tok?.decimals !== undefined) return tok.decimals;
  const known = KNOWN_DECIMALS[tokenId.toUpperCase()];
  if (known !== undefined) return known;
  return market?.token_decimals ?? 0;
}

export function tokenSymbol(
  tokenId: string,
  market?: RunSpec["market"] | null,
): string {
  const tokens = market?.tokens ?? [];
  const tok =
    tokens.find((t) => t.id === tokenId) ??
    tokens.find((t) => t.symbol === tokenId);
  return tok?.symbol ?? tokenId;
}

/** Format a scaled token amount, e.g. `1,234.56 USDC`. */
export function formatTokenAmount(
  value: number | null | undefined,
  tokenId: string,
  market?: RunSpec["market"] | null,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const decimals = tokenDecimals(tokenId, market);
  const symbol = tokenSymbol(tokenId, market);
  return `${formatScaled(value, decimals)} ${symbol}`;
}

/** Multi-line breakdown of a balances dict — one token per line. */
export function formatBalancesBreakdown(
  balances: Record<string, number> | undefined,
  market?: RunSpec["market"] | null,
): string {
  if (!balances) return "";
  const entries = Object.entries(balances)
    .filter(([, v]) => typeof v === "number" && v !== 0)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  if (entries.length === 0) return "";
  return entries.map(([id, v]) => formatTokenAmount(v, id, market)).join("\n");
}
