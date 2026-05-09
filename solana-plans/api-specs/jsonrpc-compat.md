# Solana JSON-RPC Compatibility

Phase 2 US-008 exposes a Solana-shaped JSON-RPC endpoint at
`POST /solana-rpc`. The endpoint is a compatibility adapter for existing
Solana tooling that expects JSON-RPC request and response envelopes. It is
read-only: transaction simulation delegates to the US-005 bundle simulator, and
all read methods use committed corpus fixtures or in-process simulator state.

The endpoint accepts a single JSON-RPC request object:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "getSlot",
  "params": []
}
```

Successful responses use the standard envelope:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": 250000000
}
```

## Supported Methods

| Method | Params | Result | Data source |
| --- | --- | --- | --- |
| `simulateTransaction` | `[tx, options?]` | Solana `simulateTransaction`-style `context` plus `value.err`, `value.logs`, `value.accounts`, `value.unitsConsumed`, and `value.returnData`. | Translates the single transaction into a one-tx `SimulateBundleRequest` and calls `simulate_bundle_internal`. |
| `getSlot` | `[]` | Integer slot. | Current simulated/replayed slot, otherwise highest committed corpus slot, otherwise `0`. |
| `getLatestBlockhash` | `[]` | `{ context, value: { blockhash, lastValidBlockHeight } }`. | Current slot corpus blockhash when available, otherwise deterministic simulator fallback. |
| `getRecentBlockhash` | `[]` | `{ context, value: { blockhash, feeCalculator } }`. | Same blockhash source as `getLatestBlockhash`; `lamportsPerSignature` is currently `5000`. |
| `getSignaturesForAddress` | `[pubkey, options?]` | Array of signature summaries with `signature`, `slot`, `err`, `memo`, `blockTime`, and `confirmationStatus`. | Cached slots plus committed corpus block fixtures. |
| `getTransaction` | `[signature, options?]` | Cached transaction object with `slot` and `blockTime`, or `null`. | Cached slots plus committed corpus block fixtures. |
| `getAccountInfo` | `[pubkey, options?]` | `{ context, value }`, where `value` is a Solana account object or `null`. | Current slot committed program-account fixtures. |
| `getProgramAccounts` | `[program_id, options?]` | Array of `{ pubkey, account }`, or `{ context, value }` when `withContext` is `true`. | Current slot committed program-account fixtures. |

## Method Details

### `simulateTransaction`

`simulateTransaction(tx, options?)` accepts a non-empty transaction string and an
optional object. The adapter understands these defi-sim extensions:

| Option | Default | Meaning |
| --- | --- | --- |
| `contextSlot` | `"latest"` | Forwarded as the bundle simulator context slot. Integer values also update the adapter's current slot. |
| `tipLamports` | `0` | Jito tip lamports for the one-transaction bundle. |
| `tipRecipient` | `JsonRpcSimulateTransaction11111111111111111111111` | Tip recipient for the one-transaction bundle. |

The response currently reports `err: null`, simulator log lines, `accounts:
null`, `returnData: null`, and `unitsConsumed` from the delegated bundle
simulation result.

### Account Reads

`getAccountInfo` and `getProgramAccounts` return base64-encoded account data in
the Solana account shape:

```json
{
  "data": ["BASE64_ACCOUNT_BYTES", "base64"],
  "executable": false,
  "lamports": 70407360,
  "owner": "Program1111111111111111111111111111111111",
  "rentEpoch": 0,
  "space": 512
}
```

`getProgramAccounts` supports these filters:

| Filter | Behavior |
| --- | --- |
| `{ "dataSize": n }` | Keeps accounts whose byte length equals `n`. |
| `{ "memcmp": { "offset": n, "bytes": "...", "encoding": "base58" } }` | Compares account bytes at `offset`. `encoding` may be `base58`, `base64`, or `bytes`; omitted encoding defaults to `base58`. |

Unsupported filters return JSON-RPC invalid-params errors.

## Unsupported Methods

The compatibility surface is intentionally not a full Solana RPC node. Methods
outside the supported table return JSON-RPC `MethodNotFound` (`-32601`).

Write or signing-shaped methods return the same code with a read-only
explanation:

| Method family | Behavior |
| --- | --- |
| `sendTransaction` | `MethodNotFound`; defi-sim is read-only and does not send transactions. |
| `sendRawTransaction` | Same read-only error. |
| `signMessage`, `signTransaction`, `signAllTransactions`, and other method names starting with `sign` | Same read-only error. |

Known Solana RPC methods that are not implemented yet, such as
`getSignatureStatuses`, also return `MethodNotFound`.

## Optional Yellowstone-Style gRPC

Clients that prefer streaming can use the optional protobuf contract in
`solana-plans/api-specs/proto/yellowstone_compat.proto`. The Python runtime
adapter lives in `src/defi_sim_api/grpc/yellowstone_compat.py` and registers a
read-only `defi_sim.solana.yellowstone.v1.YellowstoneCompat` service with three
server-streaming methods:

| Method | Response stream | Data source |
| --- | --- | --- |
| `SubscribeSlotUpdates` | `SlotUpdate` with slot, blockhash, and block time. | Committed corpus block fixtures. |
| `SubscribeAccountUpdates` | `AccountUpdate` with slot, pubkey, owner, lamports, and raw account bytes. | Committed corpus program-account fixtures. |
| `SubscribeTransactionUpdates` | `TransactionUpdate` with slot, signature, and raw transaction JSON bytes. | Committed corpus block fixtures. |

The gRPC surface is intentionally read-only and does not implement Yellowstone
archive ingestion or any transaction forwarding path. Operators enable it by
installing the `solana-rpc` extra, creating a grpcio server with
`create_yellowstone_compat_server()`, binding a port, and starting the server.

## Errors

| Code | Meaning |
| --- | --- |
| `-32600` | Invalid JSON-RPC envelope. |
| `-32601` | Unsupported method, including all write/signing methods. |
| `-32602` | Invalid method params. |
| `-32000` | Internal simulation error surfaced from the delegated backend. |

## Verification

Focused API coverage lives in:

- `tests/api/test_jsonrpc_compat.py`
- `tests/api/test_jsonrpc_uses_2_6_backend.py`
- `tests/api/test_yellowstone_compat_grpc.py`
- `tests/api/test_jsonrpc_conformance.py`

Client-level conformance tests for `solana-py` and `@solana/web3.js` are
opt-in through `RUN_SOLANA_RPC_CONFORMANCE=1` and should run in the dedicated
compatibility lane.
