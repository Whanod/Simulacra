# Calibration corpus

Curated slot fixtures and per-metric error thresholds used by the
`calibration` CI lane (per [`README.md`](../../README.md) "CI lanes" table)
to assert model-vs-mainnet error stays within budget. The active plan sources
are [`../phase-2.md`](../phase-2.md), the future
`solana-plans/phase-2-entry-gate.md` sign-off file, and
[`../../docs/CALIBRATION_CORPUS.md`](../../docs/CALIBRATION_CORPUS.md).
`PRD.md` mirrors `../phase-2.md` with completion checkboxes preserved, so use it
when assessing Phase 2 progress.

The corpus has two modes:

- **Development fixtures:** synthetic or recent-data fixtures used for parsers,
  hydrators, fork-loader plumbing, and CI wiring. They do not support
  mainnet-calibrated claims.
- **Calibration fixtures:** real as-of-slot data with independently checked
  expected metrics. Only these can drive model-vs-mainnet thresholds and
  calibration claims.

The forked-state lane (20 min, `@pytest.mark.forked_state`) covers
per-protocol regressions separately so calibration drift does **not**
block fork-mode bugfixes — keep this folder scoped to model-vs-mainnet
error only.

## Layout

```
solana-plans/calibration/
├── README.md              # this file
├── thresholds.yaml        # per-metric error bands
└── corpus/
    └── <slot>/
        ├── manifest.yaml                         # hand-filled expected metrics
        ├── block.json.gz                         # minimized getBlock proof
        ├── program_accounts-<program_id>.json    # per-protocol fixtures
        └── checksums.txt                         # optional SHA-256/artifact URIs
```

Large raw RPC payloads live in artifact storage under checksum-addressed
URIs; only minimized proof fixtures, manifests, and optional checksums are
committed to git so PR diffs stay reviewable. Current synthetic fixtures may
not need a `checksums.txt` file.

## How to add a slot

1. **Choose the slot purpose.** Mark it as either a development fixture or a
   calibration fixture. Development fixtures can be synthetic or recent-data
   pulls. Calibration fixtures must be recent-slot data captured while the
   slot is still inside provider retention, with independently-checked
   expected metrics. Each calibration fixture is tagged with the stress
   category it covers (see `docs/CALIBRATION_CORPUS.md`).

2. **Verify data access.** Development fixtures may use Helius RPC pulls of latest state or hand-crafted layout-faithful bytes if the manifest says so. Calibration fixtures come from the snapshotter capturing a slot — pick a captured slot for the targeted stress category defined in `docs/CALIBRATION_CORPUS.md`.

3. **Pull or author the slot once.** Run the one-off corpus tool for real pulls;
   there is **no** runtime CLI for ingesting arbitrary slot ranges. This is an
   authoring tool only:

   ```bash
   python tools/cache_corpus_slot.py \
       --slot <SLOT> \
       --programs <COMMA_SEPARATED_PROGRAM_IDS> \
       --historical-backend <BACKEND> \
       --out solana-plans/calibration/corpus/
   ```

   This writes `block.json.gz`, one
   `program_accounts-<program_id>.json[.gz]` per requested program, a
   placeholder `manifest.yaml`, and optional `checksums.txt` into
   `corpus/<slot>/`. Synthetic fixtures can be generated directly by tests or
   fixture builders, but their manifests must make the synthetic status clear.

4. **Hand-fill the manifest.** Open
   `corpus/<slot>/manifest.yaml` and fill in the `expected:` block with
   the values that tests will compare against (e.g. `tx_count`,
   `pool_reserves`, `pool_tick_current_index`). For calibration fixtures, these
   are real ground-truth metric values and must be independently sourceable. For
   development fixtures, these are parser/fork-loader expectations and must not
   be treated as mainnet truth.

5. **Move large payloads to artifact storage.** If any committed file is
   too large to diff comfortably (rule of thumb: > a few hundred KB
   gzipped), upload it to artifact storage at the checksum-addressed URI
   recorded in `checksums.txt` and remove it from git. The committed layout
   must remain reviewable.

6. **Add a calibration test when the fixture is real.** Drop a
   `tests/calibration/test_calibration_<event>.py` that loads the
   fixture, runs `ReplayExecution`, and asserts each metric error is
   within its threshold. Mark every test with
   `@pytest.mark.calibration` so it routes to the calibration lane. Synthetic
   fixtures belong in parser, forked-state, or integration tests, not in
   model-vs-mainnet calibration tests.

7. **Open the PR.** The reviewer checks: (a) the manifest's expected
   values are independently sourceable, (b) committed payloads pass
   `checksums.txt` if present, (c) no live RPC traffic is added to a
   non-`@pytest.mark.calibration` test path.

## How to run locally

The calibration suite uses only committed fixtures + artifact-storage
payloads — it must **never** touch live RPC at test time:

```bash
pytest -m calibration                              # full calibration lane
pytest tests/calibration/                          # explicit path
pytest tests/calibration/test_threshold_loading.py # threshold loader only
```

Target wall-clock: under 30 minutes locally with the committed corpus.
If a calibration test hits RPC at runtime, that is a bug — the
fixture pull belongs in `tools/cache_corpus_slot.py`, not the test path.

The default `unit+integration` lane skips both calibration and
forked-state markers:

```bash
pytest -m "not forked_state and not calibration"
```

## How to update thresholds

Per-metric error bands live in
[`thresholds.yaml`](./thresholds.yaml). Each row is one
`ReplayDiff` metric (see
`src/defi_sim/engine/replay_execution.py::ReplayDiff._METRICS`) plus
**exactly one** of:

* `threshold_relative` — fractional error band, applied as
  `abs(predicted - actual) / max(abs(actual), epsilon) <= threshold_relative`.
  Use for ratio-shaped metrics (prices, balances, volume).
* `threshold_absolute` — absolute error band, applied as
  `abs(predicted - actual) <= threshold_absolute`. Use for
  count-shaped metrics where relative error is undefined at zero
  (e.g. `liquidations_triggered`).

The loader is `src/defi_sim/calibration/thresholds.py`:
`load_thresholds()` parses the file and validates that every row pins
exactly one bound; `flag_breaches()` runs the comparison against a
`ReplayDiff.per_metric_error()` mapping; `expected_metric_keys()`
enforces vocabulary alignment with the engine's emitted metrics so
new/removed metrics surface as test failures rather than silent drift.

Tightening a threshold (good direction):

1. Edit the relevant row in `thresholds.yaml`.
2. Run `pytest -m calibration` and confirm every corpus slot still
   passes; any breach is a real regression to investigate, not a reason
   to back the threshold off.
3. Note the rationale in the PR description.

Loosening a threshold:

1. Open an issue first describing what regressed and why a tighter band
   is no longer attainable. Loose thresholds are fine if visible, but they
   should never silently widen.
2. Update the row, re-run the lane, link the issue from the PR.

Adding a metric:

1. Land the new metric on `ReplayDiff._METRICS`; the metric must appear
   in `RunSnapshot` for `flag_breaches` to consume it.
2. Add a corresponding row in `thresholds.yaml`. The
   `test_threshold_metric_keys_match_run_snapshot_keys` pin will fail until
   both sides match.
3. Update the manifest `expected:` block in each corpus slot to include
   the new metric's ground-truth value.

## Claims Gate

Until the full US-004 DoD is met - real calibration corpus curated, CI job
under 30 min, studio overlay live, `/calibration` dashboard live, all tests
passing - there is **no "mainnet-calibrated" claim**. Replay runs whose decoded
coverage is incomplete, or whose fixture source is synthetic, are marked
`synthetic_or_partial_replay` and surface no calibration band.
