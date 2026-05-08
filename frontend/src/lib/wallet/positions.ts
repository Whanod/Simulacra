import {
  LAMPORTS_PER_SOL,
  PublicKey,
  type Connection,
  type ParsedAccountData,
} from "@solana/web3.js";
import {
  SPL_TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
} from "@/lib/solana/programIds";

const SPL_TOKEN_PROGRAM_KEY = new PublicKey(SPL_TOKEN_PROGRAM_ID);
const TOKEN_2022_PROGRAM_KEY = new PublicKey(TOKEN_2022_PROGRAM_ID);
const KNOWN_POSITION_PROTOCOLS = "Whirlpool / Meteora DLMM";

export type WalletPositionKind =
  | "native_balance"
  | "token_account"
  | "lp_position_candidate";

export interface WalletPosition {
  id: string;
  kind: WalletPositionKind;
  protocol: string;
  label: string;
  account: string;
  mint?: string;
  amount: string;
  rawAmount: string;
  decimals?: number;
  programId?: string;
}

export interface WalletReadOnlyState {
  owner: string;
  updatedAt: string;
  accountsScanned: number;
  positions: WalletPosition[];
  errors: string[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function parsedTokenInfo(data: ParsedAccountData): Record<string, unknown> | null {
  const parsed = asRecord(data.parsed);
  return asRecord(parsed?.info);
}

function isParsedAccountData(data: unknown): data is ParsedAccountData {
  return Boolean(data && typeof data === "object" && "parsed" in data);
}

function tokenAmountInfo(info: Record<string, unknown>): Record<string, unknown> | null {
  return asRecord(info.tokenAmount);
}

function displayTokenAmount(amount: Record<string, unknown>): string {
  return (
    readString(amount.uiAmountString) ??
    readString(amount.amount) ??
    String(readNumber(amount.uiAmount) ?? 0)
  );
}

function isLikelyLpPositionToken(amount: Record<string, unknown>): boolean {
  return readNumber(amount.decimals) === 0 && readString(amount.amount) === "1";
}

function formatSol(lamports: number): string {
  return `${(lamports / LAMPORTS_PER_SOL).toLocaleString("en-US", {
    maximumFractionDigits: 6,
  })} SOL`;
}

function errorMessage(source: string, reason: unknown): string {
  return `${source}: ${reason instanceof Error ? reason.message : String(reason)}`;
}

async function loadTokenAccounts(
  connection: Connection,
  owner: PublicKey,
  programId: PublicKey,
  protocolLabel: string,
): Promise<WalletPosition[]> {
  const response = await connection.getParsedTokenAccountsByOwner(owner, {
    programId,
  });

  return response.value.flatMap(({ pubkey, account }) => {
    const data = account.data;
    if (!isParsedAccountData(data)) return [];

    const info = parsedTokenInfo(data);
    if (!info) return [];

    const amount = tokenAmountInfo(info);
    if (!amount) return [];

    const rawAmount = readString(amount.amount) ?? "0";
    if (rawAmount === "0") return [];

    const mint = readString(info.mint);
    const isLpCandidate = isLikelyLpPositionToken(amount);

    return [
      {
        id: pubkey.toBase58(),
        kind: isLpCandidate ? "lp_position_candidate" : "token_account",
        protocol: isLpCandidate ? KNOWN_POSITION_PROTOCOLS : protocolLabel,
        label: isLpCandidate ? "Position NFT candidate" : "Token account",
        account: pubkey.toBase58(),
        mint,
        amount: displayTokenAmount(amount),
        rawAmount,
        decimals: readNumber(amount.decimals),
        programId: programId.toBase58(),
      },
    ];
  });
}

export async function fetchWalletReadOnlyState(
  connection: Connection,
  owner: PublicKey,
): Promise<WalletReadOnlyState> {
  const ownerAddress = owner.toBase58();
  const errors: string[] = [];
  const positions: WalletPosition[] = [];

  const [balanceResult, splResult, token2022Result] = await Promise.allSettled([
    connection.getBalance(owner, "confirmed"),
    loadTokenAccounts(connection, owner, SPL_TOKEN_PROGRAM_KEY, "SPL Token"),
    loadTokenAccounts(connection, owner, TOKEN_2022_PROGRAM_KEY, "Token-2022"),
  ]);

  if (balanceResult.status === "fulfilled") {
    positions.push({
      id: `${ownerAddress}:sol`,
      kind: "native_balance",
      protocol: "Native SOL",
      label: "SOL balance",
      account: ownerAddress,
      amount: formatSol(balanceResult.value),
      rawAmount: String(balanceResult.value),
      decimals: 9,
    });
  } else {
    errors.push(errorMessage("SOL balance", balanceResult.reason));
  }

  if (splResult.status === "fulfilled") {
    positions.push(...splResult.value);
  } else {
    errors.push(errorMessage("SPL accounts", splResult.reason));
  }

  if (token2022Result.status === "fulfilled") {
    positions.push(...token2022Result.value);
  } else {
    errors.push(errorMessage("Token-2022 accounts", token2022Result.reason));
  }

  const accountPositions = positions.filter((position) => position.kind !== "native_balance");

  return {
    owner: ownerAddress,
    updatedAt: new Date().toISOString(),
    accountsScanned: accountPositions.length + 1,
    positions: positions.sort((a, b) => {
      const rank = (position: WalletPosition) =>
        position.kind === "native_balance" ? 0 : position.kind === "lp_position_candidate" ? 1 : 2;
      return rank(a) - rank(b) || a.protocol.localeCompare(b.protocol);
    }),
    errors,
  };
}
