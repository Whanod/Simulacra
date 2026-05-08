import { describe, expect, test } from "vitest";
import { PublicKey } from "@solana/web3.js";

import {
  SOLANA_TOKEN_PROGRAM_IDS,
  SPL_TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
} from "./programIds";

describe("Solana program IDs", () => {
  test("keeps wallet production and mock token constants canonical", () => {
    expect(SPL_TOKEN_PROGRAM_ID).toBe(
      "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    );
    expect(TOKEN_2022_PROGRAM_ID).toBe(
      "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    );
    expect(SOLANA_TOKEN_PROGRAM_IDS).toEqual({
      splToken: SPL_TOKEN_PROGRAM_ID,
      token2022: TOKEN_2022_PROGRAM_ID,
    });
    expect(new PublicKey(TOKEN_2022_PROGRAM_ID).toBase58()).toBe(
      TOKEN_2022_PROGRAM_ID,
    );
  });
});
