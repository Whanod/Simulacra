"""Smoke tests for ``POST /v1/simulate-bundle`` (PRD US-005 line 879).

The full unit-test list at PRD lines 922-942 is gated on subsequent
iterations (auth, fork hydration, calibration block, OpenAPI conformance).
This file pins the route's surface — request/response shape, basic error
paths, and the tip-optimizer presence contract — so the contract doesn't drift
while those follow-ups land.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS

VALID_BUNDLE = {
    "txs": ["base58encodedtx1", "base58encodedtx2"],
    "tip_lamports": 100_000,
    "tip_recipient": "T1pestRecipientPubkey11111111111111111111111",
}
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _encode_base58(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return ("1" * leading_zeroes) + (encoded or "1")


def _encoded_transfer_tx(
    *,
    compute_unit_limit: int | None = None,
    use_alt: bool = False,
    encoding: str = "base64",
) -> tuple[str, str | None, int]:
    from solders.address_lookup_table_account import AddressLookupTableAccount
    from solders.compute_budget import set_compute_unit_limit
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message, MessageV0
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction, VersionedTransaction

    payer = Keypair()
    receiver = Keypair()
    instructions = []
    if compute_unit_limit is not None:
        instructions.append(set_compute_unit_limit(compute_unit_limit))
    instructions.append(
        transfer(
            TransferParams(
                from_pubkey=payer.pubkey(),
                to_pubkey=receiver.pubkey(),
                lamports=1,
            )
        )
    )
    if use_alt:
        table_key = Keypair().pubkey()
        lookup = AddressLookupTableAccount(table_key, [receiver.pubkey()])
        message = MessageV0.try_compile(
            payer.pubkey(),
            instructions,
            [lookup],
            Hash.default(),
        )
        raw = bytes(VersionedTransaction(message, [payer]))
        encoded = (
            _encode_base58(raw)
            if encoding == "base58"
            else base64.b64encode(raw).decode("ascii")
        )
        return encoded, str(table_key), len(raw)
    message = Message(instructions, payer.pubkey())
    raw = bytes(Transaction([payer], message, Hash.default()))
    encoded = (
        _encode_base58(raw)
        if encoding == "base58"
        else base64.b64encode(raw).decode("ascii")
    )
    return encoded, None, len(raw)


def _post(client, body: dict) -> object:
    return client.post("/v1/simulate-bundle", json=body)


def test_simulate_bundle_minimal_request_returns_full_response_shape(client):
    response = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": "latest"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # All seven response fields from PRD line 866-877 (calibration optional).
    for key in (
        "expected_tip_to_land_lamports",
        "landing_probability",
        "profit_distribution",
        "alt_compression",
        "cu_budget",
        "write_lock_contention",
    ):
        assert key in body, f"missing required field {key}"
    assert body["tip_optimizer"] is None
    assert body["calibration"] is None
    assert 0.0 <= body["landing_probability"] <= 1.0
    profit = body["profit_distribution"]
    assert (
        profit["p10"]
        <= profit["p50"]
        <= profit["p75"]
        <= profit["p90"]
        <= profit["p99"]
    )
    # CU budget shape: one entry per tx.
    assert body["cu_budget"]["tx_cu_used"] and len(
        body["cu_budget"]["tx_cu_used"]
    ) == len(VALID_BUNDLE["txs"])


def test_simulate_bundle_tip_optimizer_present_when_requested(client):
    response = _post(
        client,
        {
            "bundle": VALID_BUNDLE,
            "context_slot": 420_196_842,
            "search_tip_optimizer": {"target_percentile": 90},
        },
    )
    assert response.status_code == 200, response.text
    optimizer = response.json()["tip_optimizer"]
    assert optimizer is not None
    assert optimizer["target_percentile"] == 90
    # PRD line 905: Jito tip surfaced separately from the priority-fee quote.
    assert optimizer["minimum_tip_lamports"] >= MIN_BUNDLE_TIP_LAMPORTS
    assert "priority_fee_quote_lamports" in optimizer
    assert optimizer["safety_margin_lamports"] >= 0


def test_tip_optimizer_uses_decoded_bundle_lock_cohort(client):
    """PRD line 905: Jito tip quote uses the bundle write-lock cohort."""
    from defi_sim_api.routers import simulate_bundle as route

    tx, _, _ = _encoded_transfer_tx()
    analyses = route._analyze_bundle_transactions([tx])
    lock_set = route._bundle_lock_set(
        analyses,
        fallback_tip_recipient=VALID_BUNDLE["tip_recipient"],
    )
    assert VALID_BUNDLE["tip_recipient"] not in lock_set

    auction = route._BUNDLE_AUCTION
    prior = {key: list(value) for key, value in auction._tip_observations.items()}
    try:
        auction.clear_tip_observations()
        auction.observe_tip(lock_set, 750_000)
        response = _post(
            client,
            {
                "bundle": {**VALID_BUNDLE, "txs": [tx]},
                "context_slot": 420_196_842,
                "search_tip_optimizer": {"target_percentile": 90},
            },
        )
    finally:
        auction.clear_tip_observations()
        auction._tip_observations.update(prior)

    assert response.status_code == 200, response.text
    optimizer = response.json()["tip_optimizer"]
    assert optimizer["minimum_tip_lamports"] == 751_000


def test_simulate_bundle_rejects_below_floor_tip(client):
    """Below-floor tips fail closed: integrators learn the Jito constraint
    without consuming a 200 (PRD US-011 line 832)."""
    bad = dict(VALID_BUNDLE)
    bad["tip_lamports"] = MIN_BUNDLE_TIP_LAMPORTS - 1
    response = _post(client, {"bundle": bad, "context_slot": "latest"})
    assert response.status_code == 400


def test_simulate_bundle_rejects_invalid_context_slot_string(client):
    response = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": "now"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "tip,expected_landing",
    [
        # tip=0 is the explicit zero-tip branch (route accepts, formula short-circuits to 0).
        (0, 0.0),
        # tip == floor: ratio=1.0 -> 0.5 * 1.0 = 0.5.
        (MIN_BUNDLE_TIP_LAMPORTS, 0.5),
        # 2x floor: 1 - 0.5/2 = 0.75.
        (MIN_BUNDLE_TIP_LAMPORTS * 2, 0.75),
        # 10x floor (PRD-quoted "~95%" calibration anchor in the route docstring).
        (MIN_BUNDLE_TIP_LAMPORTS * 10, 0.95),
    ],
)
def test_simulate_bundle_returns_landing_probability(
    client, tip: int, expected_landing: float
):
    """PRD line 922: pin the landing-probability values at the documented
    saturating-curve anchor points (floor=50%, 10x floor=95%, zero-tip=0%).
    A future calibrated curve replacing ``_landing_probability`` will need
    to either reproduce these anchors or update both formula + test
    together — the contract is the curve shape, not just monotonicity.
    """
    response = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "tip_lamports": tip},
            "context_slot": "latest",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "landing_probability" in body
    assert body["landing_probability"] == pytest.approx(expected_landing, abs=1e-9)


def test_simulate_bundle_tip_optimizer_minimum_tip_above_target_percentile(client):
    """PRD line 924: with ``search_tip_optimizer.target_percentile``, the
    recommended ``minimum_tip_lamports`` must sit above the regular
    priority-fee quote at that percentile — Jito tip bidding is *additive*
    to CU-price guidance, not a substitute (PRD line 905). Pins the
    invariant ``minimum_tip > priority_fee_quote`` so a future calibrated
    tip-quote curve can replace the floor+margin stub without softening
    the additivity contract.
    """
    response = _post(
        client,
        {
            "bundle": VALID_BUNDLE,
            "context_slot": "latest",
            "search_tip_optimizer": {"target_percentile": 90},
        },
    )
    assert response.status_code == 200, response.text
    optimizer = response.json()["tip_optimizer"]
    assert optimizer is not None
    assert optimizer["minimum_tip_lamports"] > optimizer["priority_fee_quote_lamports"]
    # Jito floor must always be honored; safety margin must be strictly
    # positive so the tip is never tied to the auction floor.
    assert optimizer["minimum_tip_lamports"] >= MIN_BUNDLE_TIP_LAMPORTS
    assert optimizer["safety_margin_lamports"] > 0


def test_simulate_bundle_returns_alt_compression_for_alt_using_bundle(client):
    """PRD line 923: an ALT-using bundle gets an ``alt_compression`` block
    where ``compressed_bytes < uncompressed_bytes`` (the whole point of
    ALTs is to shave bytes off a tx by replacing 32-byte pubkeys with
    1-byte indexes), totals scale with tx count, ``used_alts`` is a list
    (decoder lands later — empty list is OK), and the response satisfies
    ``AltCompressionModel`` (both byte counts ``>= 0``). Pins the stub's
    contract so a future ALT-decoding implementation must keep the
    invariant ``compressed < uncompressed`` and per-tx scaling.
    """
    one_tx = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "txs": ["base58encodedtx1"]},
            "context_slot": "latest",
        },
    )
    five_tx = _post(
        client,
        {
            "bundle": {
                **VALID_BUNDLE,
                "txs": [f"base58encodedtx{i}" for i in range(5)],
            },
            "context_slot": "latest",
        },
    )
    assert one_tx.status_code == 200, one_tx.text
    assert five_tx.status_code == 200, five_tx.text
    one = one_tx.json()["alt_compression"]
    five = five_tx.json()["alt_compression"]
    # Shape: required keys + types per AltCompressionModel.
    for block in (one, five):
        assert isinstance(block["uncompressed_bytes"], int)
        assert isinstance(block["compressed_bytes"], int)
        assert isinstance(block["used_alts"], list)
        assert block["uncompressed_bytes"] >= 0
        assert block["compressed_bytes"] >= 0
        # Core invariant: compression actually shrinks the payload.
        assert block["compressed_bytes"] < block["uncompressed_bytes"]
    # Totals scale with tx count (proportional to per-tx stub).
    assert five["uncompressed_bytes"] == 5 * one["uncompressed_bytes"]
    assert five["compressed_bytes"] == 5 * one["compressed_bytes"]


def test_simulate_bundle_decodes_alt_usage_and_estimates_cu_from_tx_bytes(client):
    legacy_tx, _, legacy_len = _encoded_transfer_tx()
    alt_tx, alt_table, alt_len = _encoded_transfer_tx(
        compute_unit_limit=345_000,
        use_alt=True,
    )
    assert alt_table is not None

    response = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "txs": [legacy_tx, alt_tx]},
            "context_slot": "latest",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    compression = body["alt_compression"]
    assert compression["used_alts"] == [alt_table]
    assert compression["compressed_bytes"] == legacy_len + alt_len
    assert compression["uncompressed_bytes"] > compression["compressed_bytes"]
    assert compression["compressed_bytes"] != 2 * 800

    cu = body["cu_budget"]["tx_cu_used"]
    assert len(cu) == 2
    assert cu[0] != 200_000
    assert cu[1] == 345_000
    assert body["cu_budget"]["slot_cu_headroom"] == 48_000_000 - sum(cu)


def test_simulate_bundle_decodes_base58_transaction_payload(client):
    tx, _, tx_len = _encoded_transfer_tx(encoding="base58")

    response = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "txs": [tx]},
            "context_slot": "latest",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["alt_compression"]["compressed_bytes"] == tx_len
    assert body["alt_compression"]["uncompressed_bytes"] == tx_len
    assert body["cu_budget"]["tx_cu_used"] != [200_000]


def test_simulate_bundle_oversized_request_rejected(client):
    """PRD line 928: requests exceeding ~256 KB must be rejected with 413
    before the route does any engine work. Solana's per-tx ceiling is 1232
    raw bytes / ~1644 base64 chars, so a 5-tx bundle plus envelope is
    well under 64 KB; a 1 MB ``tip_recipient`` field is unambiguously a
    DoS-shaped payload, not legitimate traffic."""
    oversized_recipient = "X" * (1024 * 1024)
    response = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "tip_recipient": oversized_recipient},
            "context_slot": "latest",
        },
    )
    assert response.status_code == 413, response.text


def test_simulate_bundle_with_fork_spec_uses_forked_state(client):
    """PRD line 925: when ``fork_spec`` is provided, the route must hydrate
    protocol state via the injected ``ForkLoader`` (PRD US-003 line 483) and
    reflect the forked accounts in the response. Pins three contracts:

    * The route translates the request's ``fork_spec`` payload into a domain
      ``ForkSpec`` with matching slot/protocols/allowlist.
    * It calls ``ForkLoader.load(spec)`` exactly once per request that carries
      a ``fork_spec`` (no extra hydration when ``fork_spec`` is absent).
    * Forked-account pubkeys surface on the response under
      ``write_lock_contention.blocking_pubkeys`` — they are the simulator's
      candidate contention set until raw-tx decoding lands. A future
      replacement that hydrates the engine and decodes txs must keep this
      invariant: forked pubkeys are still locked accounts.
    """
    from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
    from defi_sim.engine.initial_state import InitialState, InitialStateFragment
    from defi_sim_api.routers import simulate_bundle as route

    pool_pubkey = "PoolPubkey1111111111111111111111111111111111"
    other_pubkey = "OtherPoolPubkey1111111111111111111111111111"

    class _FakeForkLoader:
        def __init__(self) -> None:
            self.calls: list[ForkSpec] = []

        def load(self, fork_spec: ForkSpec) -> InitialState:
            self.calls.append(fork_spec)
            state = InitialState(slot=fork_spec.slot)
            for pk in (pool_pubkey, other_pubkey):
                state.merge(
                    InitialStateFragment(
                        kind="pool",
                        protocol_model="Whirlpool",
                        pubkey=pk,
                        owner=None,
                        payload={"liquidity": 0},
                    )
                )
            return state

    fake = _FakeForkLoader()
    prior = route._FORK_LOADER
    route._FORK_LOADER = fake
    try:
        # First, baseline: no fork_spec -> no hydration call, no blocking_pubkeys.
        baseline = _post(
            client,
            {"bundle": VALID_BUNDLE, "context_slot": "latest"},
        )
        assert baseline.status_code == 200, baseline.text
        assert baseline.json()["write_lock_contention"]["blocking_pubkeys"] == []
        assert fake.calls == []

        # With fork_spec: route must call loader.load with a domain ForkSpec
        # that mirrors the request body.
        fork_payload = {
            "slot": 250_000_001,
            "protocols": [
                {
                    "protocol_model": "Whirlpool",
                    "account_pubkey_allowlist": [pool_pubkey],
                }
            ],
            "include_wallet_accounts": None,
        }
        forked = _post(
            client,
            {
                "bundle": VALID_BUNDLE,
                "context_slot": 250_000_001,
                "fork_spec": fork_payload,
            },
        )
        assert forked.status_code == 200, forked.text
        contention = forked.json()["write_lock_contention"]
        # Both forked-account pubkeys surface as blocking pubkeys.
        assert pool_pubkey in contention["blocking_pubkeys"]
        assert other_pubkey in contention["blocking_pubkeys"]
        assert contention["contended_lock_count"] == 2

        # Loader was called exactly once with the translated spec.
        assert len(fake.calls) == 1
        called_spec = fake.calls[0]
        assert isinstance(called_spec, ForkSpec)
        assert called_spec.slot == 250_000_001
        assert len(called_spec.protocols) == 1
        proto = called_spec.protocols[0]
        assert isinstance(proto, ProtocolForkRequest)
        assert proto.protocol_model == "Whirlpool"
        assert proto.account_pubkey_allowlist == [pool_pubkey]
        assert called_spec.include_wallet_accounts is None
    finally:
        route._FORK_LOADER = prior


def test_simulate_bundle_with_fork_spec_fails_closed_without_loader(client):
    from defi_sim_api.routers import simulate_bundle as route

    prior = route._FORK_LOADER
    route._FORK_LOADER = None
    try:
        response = _post(
            client,
            {
                "bundle": VALID_BUNDLE,
                "context_slot": 250_000_001,
                "fork_spec": {
                    "slot": 250_000_001,
                    "protocols": [
                        {
                            "protocol_model": "Whirlpool",
                            "account_pubkey_allowlist": [
                                "PoolPubkey1111111111111111111111111111111111"
                            ],
                        }
                    ],
                    "include_wallet_accounts": None,
                },
            },
        )
        assert response.status_code == 503, response.text
        assert "exact historical account-state hydration" in response.text
    finally:
        route._FORK_LOADER = prior


def test_simulate_bundle_with_unsupported_fork_loader_path_returns_503(client):
    from defi_sim.engine.fork import ForkSpec
    from defi_sim_api.routers import simulate_bundle as route

    class _UnsupportedForkLoader:
        def load(self, fork_spec: ForkSpec):
            raise NotImplementedError("wallet-account hydration not yet implemented")

    prior = route._FORK_LOADER
    route._FORK_LOADER = _UnsupportedForkLoader()
    try:
        response = _post(
            client,
            {
                "bundle": VALID_BUNDLE,
                "context_slot": 250_000_001,
                "fork_spec": {
                    "slot": 250_000_001,
                    "protocols": [
                        {
                            "protocol_model": "Whirlpool",
                            "account_pubkey_allowlist": None,
                        }
                    ],
                    "include_wallet_accounts": [
                        "WaLLet111111111111111111111111111111111111"
                    ],
                },
            },
        )
        assert response.status_code == 503, response.text
        assert "configured ForkLoader cannot satisfy" in response.text
        assert "wallet-account hydration" in response.text
    finally:
        route._FORK_LOADER = prior


def test_e2e_against_real_priority_fee_market(client):
    """PRD line 933: seed the route's ``PriorityFeeMarket`` singleton with a
    known distribution for ``tip_recipient``, post a bundle requesting the
    tip-optimizer at percentile 90, and assert the response's
    ``priority_fee_quote_lamports`` exactly matches the engine's
    ``PriorityFeeMarket.quote(tip_recipient, 90)``. Pins the contract that
    the route reads ``PriorityFeeMarket`` (PRD line 879) — a future
    re-implementation can swap the underlying source but the response must
    still reflect the engine's quote.
    """
    from defi_sim_api.routers import simulate_bundle as route

    market = route._PRIORITY_FEE_MARKET
    recipient = "SyntheticPfmRecipientPubkey1111111111111111111"
    # Snapshot+restore the singleton's per-account state so the seeded
    # distribution doesn't leak to other tests.
    prior_obs = market._observations.pop(recipient, None)
    prior_ewma = market._ewma_baseline.pop(recipient, None)
    try:
        # Known distribution: prices 100..1000 stepped by 100 (10 samples).
        # Engine's _percentile_of_sorted at p=90 picks
        #   idx = (90 * 9) // 100 = 8 -> sorted[8] = 900.
        for slot, price in enumerate(range(100, 1100, 100), start=1):
            market.observe(recipient, slot, price)
        expected_quote = market.quote(recipient, 90)
        assert expected_quote == 900, f"sanity: engine quote drifted ({expected_quote})"

        response = _post(
            client,
            {
                "bundle": {**VALID_BUNDLE, "tip_recipient": recipient},
                "context_slot": "latest",
                "search_tip_optimizer": {"target_percentile": 90},
            },
        )
        assert response.status_code == 200, response.text
        optimizer = response.json()["tip_optimizer"]
        assert optimizer is not None
        assert optimizer["priority_fee_quote_lamports"] == expected_quote
        # Additivity contract (PRD line 905) still holds with a real,
        # non-floor priority-fee quote.
        assert (
            optimizer["minimum_tip_lamports"] > optimizer["priority_fee_quote_lamports"]
        )
    finally:
        market._observations.pop(recipient, None)
        market._ewma_baseline.pop(recipient, None)
        if prior_obs is not None:
            market._observations[recipient] = prior_obs
        if prior_ewma is not None:
            market._ewma_baseline[recipient] = prior_ewma


def test_tip_optimizer_contended_whirlpool_pool_quotes_between_p75_and_p99(
    client,
):
    """PRD line 918: for a contended Whirlpool/SOL/USDC cohort, p90
    optimization recommends a Jito tip above the p75 quote and below p99.
    """
    from defi_sim.engine.bundle_auction import BundleAuction
    from defi_sim_api.routers import simulate_bundle as route

    pool = "Whirlpool/SOL/USDC"
    auction = BundleAuction()
    for tip in range(10_000, 1_020_000, 10_000):
        auction.observe_tip({pool}, tip)
    p75 = auction.tip_quote({pool}, 75)
    p99 = auction.tip_quote({pool}, 99)

    prior = route._BUNDLE_AUCTION
    route._BUNDLE_AUCTION = auction
    try:
        response = _post(
            client,
            {
                "bundle": {**VALID_BUNDLE, "tip_recipient": pool},
                "context_slot": "latest",
                "search_tip_optimizer": {"target_percentile": 90},
            },
        )
        assert response.status_code == 200, response.text
        optimizer = response.json()["tip_optimizer"]
        assert optimizer is not None
        minimum_tip = optimizer["minimum_tip_lamports"]
        assert minimum_tip > p75
        assert minimum_tip < p99
        assert minimum_tip == (
            auction.tip_quote({pool}, 90) + route._TIP_OPTIMIZER_SAFETY_MARGIN_LAMPORTS
        )
    finally:
        route._BUNDLE_AUCTION = prior


def test_simulate_bundle_response_includes_replay_metrics_subset(client):
    """PRD line 990: the bundle simulator response includes the relevant
    subset of replay metrics (landing rate, tip efficiency, slot inclusion
    latency) under ``metrics.replay``. Pins both presence and value contracts:

    * ``bundle_landing_rate.value`` reflects the response's
      ``landing_probability`` (matched at 100-sample resolution — the
      synthesized outcomes round to nearest 1%).
    * ``tip_efficiency.value`` equals ``tip / profit_p50`` from the same
      response (one-sample synthesis).
    * ``slot_inclusion_latency`` is well-formed: required percentile fields
      present, ``samples`` is a list, ``unit == 'slots'``.

    A future calibrated populator can replace the synthesis without breaking
    this contract — the keys and shape are what charts (PRD line 982)
    consume.
    """
    response = _post(
        client,
        {
            "bundle": VALID_BUNDLE,
            "context_slot": 420_196_842,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "metrics" in body
    assert "replay" in body["metrics"]
    replay = body["metrics"]["replay"]
    for key in ("bundle_landing_rate", "tip_efficiency", "slot_inclusion_latency"):
        assert key in replay, f"missing metrics.replay.{key}"

    # Landing rate matches the response's landing_probability at 1% resolution.
    landing_rate = replay["bundle_landing_rate"]
    assert landing_rate["unit"] == "ratio"
    assert landing_rate["sample_size"] == 100
    assert landing_rate["value"] == pytest.approx(
        round(body["landing_probability"] * 100) / 100, abs=1e-9
    )

    # Tip efficiency = tip / extracted_value (where extracted_value = p50).
    tip_eff = replay["tip_efficiency"]
    assert tip_eff["unit"] == "ratio"
    assert tip_eff["sample_size"] == 1
    expected_eff = VALID_BUNDLE["tip_lamports"] / body["profit_distribution"]["p50"]
    assert tip_eff["value"] == pytest.approx(expected_eff, rel=1e-9)

    # Slot inclusion latency: zero-latency single-sample stub, well-formed.
    latency = replay["slot_inclusion_latency"]
    assert latency["unit"] == "slots"
    assert latency["sample_size"] == 1
    for pct in ("mean", "median", "p95", "p99"):
        assert pct in latency
        assert latency[pct] == 0
    assert isinstance(latency["samples"], list)
    assert latency["samples"] == [0]


def test_simulate_bundle_replay_metrics_zero_tip_yields_zero_sample_tip_efficiency(
    client,
):
    """A zero-tip bundle has no meaningful tip-efficiency reading — the
    route surfaces the zero-sample sentinel for that calculator while still
    emitting the other two metrics. Pins the divide-by-zero short-circuit
    in ``_replay_metrics_block`` so a future calibrated populator can't
    accidentally regress to emitting 0.0/sample_size=1 (which charts would
    misread as "tip is 100% efficient")."""
    response = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "tip_lamports": 0},
            "context_slot": "latest",
        },
    )
    assert response.status_code == 200, response.text
    replay = response.json()["metrics"]["replay"]
    tip_eff = replay["tip_efficiency"]
    assert tip_eff["sample_size"] == 0
    assert tip_eff["value"] == 0.0
    # Other two metrics still surface.
    assert replay["bundle_landing_rate"]["sample_size"] == 100
    assert replay["slot_inclusion_latency"]["sample_size"] == 1


@pytest.mark.parametrize(
    "tip,expected_higher_landing",
    [
        (MIN_BUNDLE_TIP_LAMPORTS, MIN_BUNDLE_TIP_LAMPORTS * 100),
    ],
)
def test_simulate_bundle_landing_prob_monotone_in_tip(
    client, tip: int, expected_higher_landing: int
):
    """Landing probability rises with tip (stub heuristic, but the contract
    is monotone). Pins the curve direction so a future calibrated curve
    can replace the formula without breaking the contract."""
    low = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "tip_lamports": tip},
            "context_slot": "latest",
        },
    ).json()
    high = _post(
        client,
        {
            "bundle": {**VALID_BUNDLE, "tip_lamports": expected_higher_landing},
            "context_slot": "latest",
        },
    ).json()
    assert high["landing_probability"] >= low["landing_probability"]


def test_simulate_bundle_invalid_api_key_rejected(client, monkeypatch):
    """PRD line 927 / lines 881-884: when ``DEFI_SIM_API_KEYS`` is configured,
    a request without a valid bearer token must be rejected with 401, and a
    request with a matching key must succeed and surface the matched key id
    in ``X-API-Key-Id``. Pins three contracts:

    * Open mode (env unset) accepts unauthenticated requests so dev/test
      callers don't need to configure auth.
    * Configured mode rejects missing / wrong / malformed bearer tokens
      with 401 + ``WWW-Authenticate: Bearer``.
    * On success, the matched ``key_id`` is returned in the
      ``X-API-Key-Id`` header (the support-reference contract from line
      884) — clients only learn the key id; the plaintext key never echoes
      back.
    """
    from defi_sim_api.auth import API_KEYS_ENV, hash_api_key

    plaintext = "test-bundle-simulator-key-001"
    key_id = "kid_abc"

    # Sanity: open mode (no env) → request succeeds without auth.
    open_response = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": "latest"},
    )
    assert open_response.status_code == 200, open_response.text
    assert "x-api-key-id" not in {k.lower() for k in open_response.headers.keys()}

    # Configure the allowlist with one key.
    monkeypatch.setenv(API_KEYS_ENV, f"{key_id}:{hash_api_key(plaintext)}")

    # Missing bearer → 401.
    missing = client.post(
        "/v1/simulate-bundle",
        json={"bundle": VALID_BUNDLE, "context_slot": "latest"},
    )
    assert missing.status_code == 401, missing.text
    assert missing.headers.get("WWW-Authenticate") == "Bearer"

    # Wrong scheme → 401.
    basic_auth = client.post(
        "/v1/simulate-bundle",
        json={"bundle": VALID_BUNDLE, "context_slot": "latest"},
        headers={"Authorization": f"Basic {plaintext}"},
    )
    assert basic_auth.status_code == 401, basic_auth.text

    # Wrong key → 401.
    wrong_key = client.post(
        "/v1/simulate-bundle",
        json={"bundle": VALID_BUNDLE, "context_slot": "latest"},
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert wrong_key.status_code == 401, wrong_key.text

    # Valid key → 200 + X-API-Key-Id header echoes the key id (not the
    # plaintext).
    valid = client.post(
        "/v1/simulate-bundle",
        json={"bundle": VALID_BUNDLE, "context_slot": "latest"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert valid.status_code == 200, valid.text
    assert valid.headers.get("X-API-Key-Id") == key_id
    # The plaintext key must never echo back in headers.
    assert plaintext not in " ".join(valid.headers.values())


def test_simulate_bundle_development_corpus_slot_has_no_calibration_claim(client):
    """Committed corpus placeholders are development fixtures. They keep
    parser/fork paths deterministic, but must not produce calibrated bundle
    claims until a manifest explicitly marks real calibration evidence.
    """
    development = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": 420_196_842},
    )
    assert development.status_code == 200, development.text
    assert development.json()["calibration"] is None

    # Uncovered integer slot: no calibration claim.
    uncovered = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": 1},
    )
    assert uncovered.status_code == 200, uncovered.text
    assert uncovered.json()["calibration"] is None

    # "latest": no calibration claim either.
    latest = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": "latest"},
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["calibration"] is None


def test_simulate_bundle_marker_only_calibration_manifest_is_not_enough(
    client,
    monkeypatch,
    tmp_path,
):
    from defi_sim_api.routers import simulate_bundle as route

    slot = 260_000_000
    slot_dir = tmp_path / str(slot)
    slot_dir.mkdir(parents=True)
    (slot_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "fixture_kind: calibration",
                "calibrated: true",
                "mainnet_accuracy_claim: true",
                "calibration_source: provider-backed-fixture",
                f"slot: {slot}",
                "expected:",
                "  tx_count: 1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(route, "corpus_root", lambda: tmp_path)

    response = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": slot},
    )

    assert response.status_code == 200, response.text
    assert response.json()["calibration"] is None


def test_simulate_bundle_calibration_block_requires_real_manifest(
    client,
    monkeypatch,
    tmp_path,
):
    from defi_sim_api.routers import simulate_bundle as route

    slot = 260_000_000
    slot_dir = tmp_path / str(slot)
    slot_dir.mkdir(parents=True)
    (slot_dir / "bundle-proof.json").write_text(
        json.dumps(
            {
                "landing_probability": 0.72,
                "provenance": "provider export reviewed in PR",
                "raw_payload_sha256": "0123456789abcdef",
            }
        ),
        encoding="utf-8",
    )
    (slot_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "fixture_kind: calibration",
                "calibrated: true",
                "mainnet_accuracy_claim: true",
                "calibration_source: provider-backed-fixture",
                "calibration_provenance: provider export reviewed in PR",
                "artifact_paths:",
                "  - bundle-proof.json",
                f"slot: {slot}",
                "expected:",
                "  tx_count: 1",
                "  bundle_landing_probability: 0.72",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(route, "corpus_root", lambda: tmp_path)

    response = _post(
        client,
        {"bundle": VALID_BUNDLE, "context_slot": slot},
    )
    assert response.status_code == 200, response.text
    block = response.json()["calibration"]
    assert block is not None
    assert block["corpus_slot"] == slot
    assert isinstance(block["calibrated_at"], str) and block["calibrated_at"]
    thresholds = block["metric_thresholds"]
    assert isinstance(thresholds, dict) and thresholds, thresholds
    for metric, band in thresholds.items():
        rel = band.get("relative")
        abs_ = band.get("absolute")
        assert (rel is None) ^ (abs_ is None), (
            f"{metric!r} band must set exactly one of relative/absolute: {band!r}"
        )


def test_simulate_bundle_curl_style_authenticated_request_under_5s(client, monkeypatch):
    """PRD line 917: a documented curl-style POST with a bearer API key
    returns a well-formed bundle simulator response in under five seconds.

    Uses a raw JSON body rather than TestClient's ``json=`` helper to mirror
    ``curl -d @sample.json`` as closely as the in-process API test can.
    """
    from defi_sim_api.auth import API_KEYS_ENV, hash_api_key

    plaintext = "curl-style-bundle-key"
    key_id = "kid_curl"
    monkeypatch.setenv(API_KEYS_ENV, f"{key_id}:{hash_api_key(plaintext)}")
    payload = {"bundle": VALID_BUNDLE, "context_slot": "latest"}

    started = time.perf_counter()
    response = client.post(
        "/v1/simulate-bundle",
        content=json.dumps(payload),
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Content-Type": "application/json",
        },
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    assert elapsed < 5.0
    assert response.headers.get("X-API-Key-Id") == key_id
    body = response.json()
    for key in (
        "expected_tip_to_land_lamports",
        "landing_probability",
        "profit_distribution",
        "alt_compression",
        "cu_budget",
        "write_lock_contention",
        "metrics",
    ):
        assert key in body, f"missing required field {key}"
    assert 0.0 <= body["landing_probability"] <= 1.0
    assert len(body["cu_budget"]["tx_cu_used"]) == len(VALID_BUNDLE["txs"])


def test_simulate_bundle_does_not_log_raw_bundle_bytes(client):
    """PRD US-005 line 891 + ``docs/PRIVACY.md``: raw bundle bytes are not
    written to logs after the request completes. This regression test pins the
    contract by attaching a capture handler to the root logger, posting a
    bundle whose ``txs`` and ``tip_recipient`` carry uniquely identifiable
    canary strings, and asserting that no log record (formatted message or
    args) contains those canaries. Any future log statement that echoes the
    request body — anywhere on the request path — will trip this test.

    Scope: stdlib logging only. The PRIVACY policy also forbids writing the
    body to error-tracking sinks and cold storage; those have no in-process
    surface to assert against in unit tests.
    """
    import logging

    canary_tx_a = "PRIVACY_CANARY_TX_AAAA_8z3f1k9q"
    canary_tx_b = "PRIVACY_CANARY_TX_BBBB_8z3f1k9q"
    canary_recipient = "PRIVACY_CANARY_TIP_RECIPIENT_PUBKEY_xxxxxxxx"
    canaries = (canary_tx_a, canary_tx_b, canary_recipient)

    captured: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = CaptureHandler(level=logging.DEBUG)
    root = logging.getLogger()
    prior_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        bundle = {
            "txs": [canary_tx_a, canary_tx_b],
            "tip_lamports": 100_000,
            "tip_recipient": canary_recipient,
        }
        response = _post(client, {"bundle": bundle, "context_slot": "latest"})
        assert response.status_code == 200, response.text
    finally:
        root.removeHandler(handler)
        root.setLevel(prior_level)

    for record in captured:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        haystacks = [message, str(record.msg), repr(record.args)]
        for canary in canaries:
            for hay in haystacks:
                assert canary not in hay, (
                    f"PRIVACY violation (PRD line 891): canary {canary!r} "
                    f"leaked into log record from {record.name!r} "
                    f"at level {record.levelname}: {hay!r}"
                )
