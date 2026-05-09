// Minimal TypeScript sample for `POST /v1/simulate-bundle`.
//
// Uses the global `fetch` (Node 18+, browsers, Bun, Deno). No client
// generator required for the basic happy-path; for typed responses
// generate a client with `openapi-typescript simulate-bundle.openapi.yaml`.
//
// Usage:
//   DEFI_SIM_API_KEY=... npx tsx simulate_bundle.ts

const API_URL = process.env.DEFI_SIM_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.DEFI_SIM_API_KEY;

interface SimulateBundleRequest {
  bundle: {
    txs: string[];
    tip_lamports: number;
    tip_recipient: string;
  };
  context_slot: number | "latest";
  fork_spec?: unknown;
  search_tip_optimizer?: { target_percentile: number };
}

export async function simulateBundle(
  req: SimulateBundleRequest,
): Promise<unknown> {
  if (!API_KEY) throw new Error("DEFI_SIM_API_KEY not set");

  const resp = await fetch(`${API_URL}/v1/simulate-bundle`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(req),
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  }
  return resp.json();
}

if (require.main === module) {
  simulateBundle({
    bundle: {
      txs: ["base58encodedtx1", "base58encodedtx2"],
      tip_lamports: 100_000,
      tip_recipient: "T1pestRecipientPubkey11111111111111111111111",
    },
    context_slot: "latest",
  })
    .then((r) => console.log(JSON.stringify(r, null, 2)))
    .catch((e) => {
      console.error(e);
      process.exit(1);
    });
}
