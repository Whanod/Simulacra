# API specs

Canonical OpenAPI 3.0.3 contracts for the defi-sim HTTP surface.
The spec is the source of truth: both the backend
(`src/defi_sim_api/routers/`) and the frontend client are validated
against the schemas here, and OpenAPI conformance tests live next to
each route. Active planning context lives in
[`../phase-2.md`](../phase-2.md) US-005. `PRD.md` mirrors that Phase 2 plan
with completion checkboxes preserved.

The default Studio + Builder endpoints (run create / artifact fetch)
are owned by FastAPI's auto-generated `/docs`; this folder hosts only
the **stable, integrator-facing** specs that bots, dashboards, and
paper-trading clients consume directly.

## Layout

```
solana-plans/api-specs/
├── README.md                        # this file
├── simulate-bundle.openapi.yaml     # POST /v1/simulate-bundle (Phase 2 US-005)
└── samples/
    ├── simulate_bundle.py           # Python (stdlib only)
    ├── simulate_bundle.ts           # TypeScript (global fetch)
    └── simulate_bundle.rs           # Rust (reqwest + tokio)
```

## Specs

### `simulate-bundle.openapi.yaml`

`POST /v1/simulate-bundle` — accepts a Solana bundle (transactions +
Jito tip) plus a context slot and optional fork spec, returns expected
tip-to-land, landing probability, profit distribution, ALT compression,
CU-budget headroom, write-lock contention, an optional tip-optimizer
recommendation, and an optional calibration block when the corpus
covers the slot.

- Backend route: `src/defi_sim_api/routers/simulate_bundle.py`.
- UI client: `frontend/src/app/(studio)/bundle-simulator/`.
- Conformance test:
  `tests/api/test_openapi_conformance.py::test_simulate_bundle_response_matches_spec`.

## Quickstart

The spec ships three request examples (`minimal`, `with_tip_optimizer`,
`with_fork`); pull them straight from the spec for copy-paste use.

### `curl`

```bash
curl -X POST http://localhost:8000/v1/simulate-bundle \
  -H "Authorization: Bearer $DEFI_SIM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "bundle": {
      "txs": ["base58encodedtx1", "base58encodedtx2"],
      "tip_lamports": 100000,
      "tip_recipient": "T1pestRecipientPubkey11111111111111111111111"
    },
    "context_slot": "latest"
  }'
```

The matched API key id is echoed in the `X-API-Key-Id` response header for
support reference.

### Authentication

- **Programmatic clients** — `Authorization: Bearer <API_KEY>` (the
  `apiKeyBearer` security scheme). Keys are hashed at rest; provision
  via the Studio "API keys" page.
- **UI clients** — the existing Studio session cookie (the
  `sessionCookie` scheme). Programmatic integrators should not depend
  on the cookie path.

## Code samples

Minimal happy-path samples live under [`samples/`](./samples). Each is
single-file, uses each language's standard HTTP client (no defi-sim-specific
package), and demonstrates the auth header and JSON body.

| Language   | File                                                 | Run                                          |
| ---------- | ---------------------------------------------------- | -------------------------------------------- |
| Python     | [`samples/simulate_bundle.py`](./samples/simulate_bundle.py) | `python samples/simulate_bundle.py` (stdlib) |
| TypeScript | [`samples/simulate_bundle.ts`](./samples/simulate_bundle.ts) | `npx tsx samples/simulate_bundle.ts`         |
| Rust       | [`samples/simulate_bundle.rs`](./samples/simulate_bundle.rs) | drop into a Cargo project; deps in file head |

Set `DEFI_SIM_API_KEY` (and optionally `DEFI_SIM_API_URL`) before
running.

## Generating clients

The spec is plain OpenAPI 3.0.3, so any conformant generator works.
Reference toolchains:

| Language   | Suggested generator                                    | Example invocation                                                                 |
| ---------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| Python     | [`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client) | `openapi-python-client generate --path simulate-bundle.openapi.yaml`               |
| TypeScript | [`openapi-typescript`](https://github.com/openapi-ts/openapi-typescript)               | `npx openapi-typescript simulate-bundle.openapi.yaml -o src/api/simulate-bundle.ts` |
| Rust       | [`openapi-generator`](https://openapi-generator.tech/) | `openapi-generator generate -i simulate-bundle.openapi.yaml -g rust -o ./client`   |

Hand-rolled clients are also fine; the spec is small and the response
shape is stable per the conformance test.

## Adding a new spec

1. Drop the new `<endpoint>.openapi.yaml` next to this README.
2. Pin `openapi: 3.0.3` for tooling compatibility (3.1 is supported by
   most generators but a few of the older Rust ones still lag).
3. Reference the corresponding `solana-plans/phase-*.md` story in the
   `info.description` block so a contributor reading the spec can find the
   design intent.
4. Add a row to the **Specs** table above and a conformance test under
   `tests/api/test_openapi_conformance.py`.

## Versioning

The HTTP path is versioned (`/v1/...`); the spec's `info.version`
tracks the schema and bumps independently when fields are added or
deprecated. Breaking changes ship as `/v2/...` with a parallel spec
file; integrators pin to a path version, not a spec version.
