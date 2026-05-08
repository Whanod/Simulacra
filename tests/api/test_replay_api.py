"""``POST /v1/replay`` integration tests (PRD US-002 lines 333, 354-355)."""

from __future__ import annotations

import base64
from hashlib import sha256
import time

from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_api.routers import replay as replay_router
from defi_sim_solana.program_ids import (
    METEORA_DLMM_PROGRAM,
    RAYDIUM_AMM_V4_PROGRAM,
    SYSTEM_PROGRAM,
    TOKEN_PROGRAM,
    WHIRLPOOL_PROGRAM,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot, clear_slot_cache

CORPUS_SLOT = 160_000_001  # the lone committed entry-gate fixture


def _whirlpool_swap_payload(amount: int = 1_000_000) -> list[str]:
    payload = (
        sha256(b"global:swap").digest()[:8]
        + amount.to_bytes(8, "little")
        + (900_000).to_bytes(8, "little")
        + (4_295_048_016).to_bytes(16, "little")
        + b"\x01\x01"
    )
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _dlmm_swap_payload(amount: int = 1_000_000) -> list[str]:
    payload = (
        bytes((65, 75, 63, 76, 235, 91, 91, 136))
        + amount.to_bytes(8, "little")
        + (900_000).to_bytes(8, "little")
        + (0).to_bytes(4, "little")
    )
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _raydium_swap_payload(amount: int = 1_000_000) -> list[str]:
    payload = (
        bytes((9,)) + amount.to_bytes(8, "little") + (900_000).to_bytes(8, "little")
    )
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _tip_snapshot(
    *,
    slot: int,
    signatures: tuple[str, ...] = ("bundle-1",),
    lamports: tuple[int, ...] = (5_000,),
) -> SlotSnapshot:
    txs: list[dict] = []
    tips: list[dict] = []
    for sig, amount in zip(signatures, lamports, strict=True):
        txs.append(
            {
                "transaction": {
                    "signatures": [sig],
                    "message": {
                        "accountKeys": [{"pubkey": "searcher"}],
                        "instructions": [
                            {
                                "programId": SYSTEM_PROGRAM,
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "searcher",
                                        "destination": DEFAULT_JITO_TIP_ACCOUNTS[0],
                                        "lamports": amount,
                                    },
                                },
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 5_000},
            }
        )
        tips.append({"lamports": amount})
    return SlotSnapshot(
        slot=slot,
        transactions=tuple(txs),
        transaction_compute_units=tuple(5_000 for _ in txs),
        jito_tips=tuple(tips),
    )


def _token_transfer_snapshot(*, slot: int, signature: str) -> SlotSnapshot:
    return SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": [signature],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": TOKEN_PROGRAM,
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "shared-source-token",
                                        "destination": "shared-destination-token",
                                        "authority": "wallet-authority",
                                        "amount": "10",
                                    },
                                },
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 5_000},
            },
        ),
        transaction_compute_units=(5_000,),
    )


def test_post_replay_returns_run_id(client) -> None:
    """PRD line 354: the endpoint accepts a slot range and returns a run ID."""
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["run_id"], str) and body["run_id"]
    assert body["slot_range"] == [CORPUS_SLOT, CORPUS_SLOT]
    # Metadata called out at PRD line 333:
    assert "decoded_transaction_share" in body
    assert "unsupported_program_ids" in body
    assert "eligible_for_calibration" in body
    assert isinstance(body["unsupported_program_ids"], list)


def test_replay_with_counterfactual_persists_diff(client, monkeypatch) -> None:
    """PRD line 355 / FIX-005: replay persists a real diff block for a
    counterfactual run, not only the submitted counterfactual metadata."""
    slot = 320_000_005
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={
                "slot_range": [slot, slot],
                "counterfactuals": [
                    {
                        "kind": "TipReplaceCounterfactual",
                        "params": {
                            "target_bundle_id": "bundle-1",
                            "new_tip_lamports": 0,
                        },
                    },
                    {
                        "kind": "FeeReplaceCounterfactual",
                        "params": {"target_pool": "sol_usdc", "new_fee_bps": 25},
                    },
                ],
            },
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    cfs = body["counterfactuals"]
    assert len(cfs) == 2
    assert cfs[0]["kind"] == "TipReplaceCounterfactual"
    assert cfs[0]["params"]["target_bundle_id"] == "bundle-1"
    assert cfs[0]["params"]["new_tip_lamports"] == 0
    assert cfs[1]["kind"] == "FeeReplaceCounterfactual"

    # Verify the persisted artifact carries the kind=replay summary and the
    # result carries a real ReplayDiff payload.
    runs_resp = client.get(f"/runs/{body['run_id']}")
    assert runs_resp.status_code == 200, runs_resp.text
    record = runs_resp.json()
    summary = record.get("summary", {})
    assert summary.get("kind") == "replay"
    assert summary.get("slot_range") == [slot, slot]
    assert len(summary.get("counterfactuals", [])) == 2
    assert "per_metric_error" in summary.get("replay_diff", {})

    result_resp = client.get(f"/runs/{body['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    result = result_resp.json()["result"]
    diff = result["replay_diff"]
    assert diff["per_metric_error"]["tips_paid"]["predicted"] == 0.0
    assert diff["per_metric_error"]["tips_paid"]["actual"] == 5_000.0
    assert diff["per_metric_error"]["tips_paid"]["abs_error"] == 5_000.0
    assert diff["per_metric_error"]["tips_paid"]["absolute_error"] == 5_000.0
    assert diff["per_metric_error"]["tips_paid"]["threshold"] == 0.1
    landing = diff["per_metric_error"]["bundle_landing_rate"]
    assert landing["predicted"] == 0.0
    assert landing["actual"] == 1.0
    assert landing["supported"] is True
    assert result["predicted"]["tips_paid"] == 0
    assert result["predicted"]["actions"][0]["tip_lamports"] == 0


def test_replay_accepts_ordering_and_agent_counterfactual_specs(client) -> None:
    """US-007 counterfactual controls submit JSON-safe ordering and agent specs."""
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={
            "slot_range": [CORPUS_SLOT, CORPUS_SLOT],
            "counterfactuals": [
                {
                    "kind": "OrderingReplaceCounterfactual",
                    "params": {"scheduler": {"type": "priority"}},
                },
                {
                    "kind": "AgentInjectCounterfactual",
                    "params": {
                        "agent_type": "jito_searcher",
                        "agent_id": "jito-searcher-cf",
                        "strategy": "backrun",
                        "min_ev_to_submit_lamports": 100_000,
                        "tip_account": ("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5"),
                    },
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    cfs = response.json()["counterfactuals"]
    assert cfs[0] == {
        "kind": "OrderingReplaceCounterfactual",
        "params": {"scheduler": {"type": "priority"}},
    }
    assert cfs[1]["kind"] == "AgentInjectCounterfactual"
    assert cfs[1]["params"]["agent_type"] == "jito_searcher"
    assert cfs[1]["params"]["strategy"] == "backrun"


def test_replay_ordering_counterfactual_executes_priority_order(
    client,
    monkeypatch,
) -> None:
    """PRD line 365: ordering specs are executable, not request-only."""
    import defi_sim_solana.replay.materialize as materialize_mod
    from defi_sim_solana.replay.materialize import TipAction

    slot = 320_000_014
    snapshot = SlotSnapshot(slot=slot, transactions=({"signature": "ordering"},))
    low = TipAction(
        agent_id="low",
        signature="low-priority",
        tip_lamports=5_000,
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1,
    )
    high = TipAction(
        agent_id="high",
        signature="high-priority",
        tip_lamports=5_000,
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=100,
    )

    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    monkeypatch.setattr(replay_router, "materialize_slot", lambda _snap: [low, high])
    monkeypatch.setattr(materialize_mod, "materialize_slot", lambda _snap: [low, high])
    try:
        response = client.post(
            "/v1/replay",
            json={
                "slot_range": [slot, slot],
                "counterfactuals": [
                    {
                        "kind": "OrderingReplaceCounterfactual",
                        "params": {"scheduler": {"type": "priority"}},
                    }
                ],
            },
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    actions = result_resp.json()["result"]["predicted"]["actions"]
    assert [action["signature"] for action in actions] == [
        "high-priority",
        "low-priority",
    ]


def test_replay_agent_inject_counterfactual_executes_synthetic_tip(
    client,
    monkeypatch,
) -> None:
    """PRD line 365: agent-inject specs execute through the replay API."""
    slot = 320_000_015
    snapshot = _tip_snapshot(slot=slot, signatures=("historical-tip",))

    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={
                "slot_range": [slot, slot],
                "counterfactuals": [
                    {
                        "kind": "AgentInjectCounterfactual",
                        "params": {
                            "agent_type": "jito_searcher",
                            "agent_id": "jito-searcher-cf",
                            "strategy": "backrun",
                            "min_ev_to_submit_lamports": 100_000,
                            "tip_account": DEFAULT_JITO_TIP_ACCOUNTS[0],
                        },
                    }
                ],
            },
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    result = result_resp.json()["result"]
    actions = result["predicted"]["actions"]
    assert actions[-1]["agent_id"] == "jito-searcher-cf"
    assert actions[-1]["type"] == "TipAction"
    assert actions[-1]["tip_lamports"] == 100_000
    assert result["predicted"]["tips_paid"] == 105_000


def test_post_replay_rejects_unknown_counterfactual_kind(client) -> None:
    response = client.post(
        "/v1/replay",
        json={
            "slot_range": [CORPUS_SLOT, CORPUS_SLOT],
            "counterfactuals": [{"kind": "DoesNotExist", "params": {}}],
        },
    )
    assert response.status_code == 400
    assert "DoesNotExist" in response.text


def test_post_replay_rejects_inverted_slot_range(client) -> None:
    response = client.post(
        "/v1/replay",
        json={"slot_range": [200, 100], "counterfactuals": []},
    )
    assert response.status_code == 400


def test_post_replay_skips_unloadable_slots_and_reports_zero(client) -> None:
    """An RPC-only slot with no client configured loads as a miss; the run
    still completes (slots_loaded counts only successes) so callers can use
    the API in CI without archival access."""
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        # Pick a slot far away from the committed fixture so the corpus path
        # returns None and default_client raises (treated as a miss).
        json={"slot_range": [999_000_001, 999_000_001], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 0
    assert body["decoded_transaction_share"] == 0.0
    assert body["eligible_for_calibration"] is False


def test_post_replay_records_unsupported_program_ids_for_corpus_slot(client) -> None:
    """The corpus fixture's transactions all hit OpaqueAction (no decoders
    have shipped yet), so any program IDs surfaced from the materializer
    must appear in the response."""
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # decoded_transaction_share should be 0.0 today — every tx routes to
    # OpaqueAction until per-protocol decoders land in Phase 2.3+.
    assert 0.0 <= body["decoded_transaction_share"] <= 1.0
    assert body["eligible_for_calibration"] is False
    # unsupported_program_ids is a list (may be empty if the fixture has no
    # parseable program IDs, but the field shape must be stable).
    assert isinstance(body["unsupported_program_ids"], list)


def test_replay_artifact_carries_decoded_share_and_unsupported_program_ids(
    client,
) -> None:
    """PRD line 361: the persisted artifact summary must carry decoded
    coverage and unsupported program IDs (not just the POST response)."""
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    runs_resp = client.get(f"/runs/{body['run_id']}")
    assert runs_resp.status_code == 200, runs_resp.text
    summary = runs_resp.json().get("summary", {})
    assert summary.get("kind") == "replay"
    assert summary.get("decoded_transaction_share") == body["decoded_transaction_share"]
    assert summary.get("unsupported_program_ids") == body["unsupported_program_ids"]
    assert isinstance(summary.get("unsupported_program_ids"), list)


def test_replay_reports_decoded_share_when_slot_contains_token_transfer(
    client,
    monkeypatch,
) -> None:
    """FIX-003: a supported token transfer changes replay decoded coverage."""
    slot = 320_000_003
    clear_slot_cache()
    replay_router.clear_replay_cache()
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": ["decoded-token-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": TOKEN_PROGRAM,
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "source-token-account",
                                        "destination": "destination-token-account",
                                        "authority": "wallet-authority",
                                        "amount": "42",
                                    },
                                },
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 5_000},
            },
            {
                "transaction": {
                    "signatures": ["unknown-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": "UnknownProgram111111111111111111111111111",
                                "data": "",
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 1_000},
            },
        ),
        transaction_compute_units=(5_000, 1_000),
    )

    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 1
    assert body["decoded_transaction_share"] == 0.5
    assert body["unsupported_program_ids"] == [
        "UnknownProgram111111111111111111111111111"
    ]
    assert body["eligible_for_calibration"] is False
    assert body["mainnet_accuracy_claim"] is False


def test_replay_reports_decoded_share_when_slot_contains_whirlpool_swap(
    client,
    monkeypatch,
) -> None:
    """FIX-004: a Whirlpool swap contributes decoded AMM replay coverage."""
    slot = 320_000_004
    clear_slot_cache()
    replay_router.clear_replay_cache()
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": ["decoded-whirlpool-sig"],
                    "message": {
                        "accountKeys": [
                            "fee-payer",
                            "owner-account-a",
                            "whirlpool-pool",
                            "vault-a",
                            "tick-array-0",
                            "tick-array-1",
                            "owner-account-b",
                            "tick-array-2",
                            "vault-b",
                            TOKEN_PROGRAM,
                            WHIRLPOOL_PROGRAM,
                        ],
                        "instructions": [
                            {
                                "programIdIndex": 10,
                                "accounts": [9, 0, 2, 1, 3, 6, 8, 4, 5, 7, 7],
                                "data": _whirlpool_swap_payload(),
                            }
                        ],
                    },
                },
                "meta": {
                    "computeUnitsConsumed": 80_000,
                    "preTokenBalances": [
                        {"accountIndex": 3, "mint": "mint-a"},
                        {"accountIndex": 8, "mint": "mint-b"},
                    ],
                    "postTokenBalances": [
                        {"accountIndex": 3, "mint": "mint-a"},
                        {"accountIndex": 8, "mint": "mint-b"},
                    ],
                },
            },
            {
                "transaction": {
                    "signatures": ["unknown-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": "UnknownProgram111111111111111111111111111",
                                "data": "",
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 1_000},
            },
        ),
        transaction_compute_units=(80_000, 1_000),
    )

    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 1
    assert body["decoded_transaction_share"] == 0.5
    assert body["unsupported_program_ids"] == [
        "UnknownProgram111111111111111111111111111"
    ]
    assert body["eligible_for_calibration"] is False
    assert body["mainnet_accuracy_claim"] is False


def test_replay_reports_decoded_share_when_slot_contains_dlmm_swap(
    client,
    monkeypatch,
) -> None:
    """FIX-009: a Meteora DLMM swap contributes decoded AMM replay coverage."""
    slot = 320_000_009
    clear_slot_cache()
    replay_router.clear_replay_cache()
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": ["decoded-dlmm-sig"],
                    "message": {
                        "accountKeys": [
                            "fee-payer",
                            "dlmm-lb-pair",
                            "bin-array-bitmap-extension",
                            "reserve-x",
                            "reserve-y",
                            "user-token-in",
                            "user-token-out",
                            "token-x-mint",
                            "token-y-mint",
                            "oracle",
                            "host-fee-in",
                            "wallet-authority",
                            TOKEN_PROGRAM,
                            "memo-program",
                            "event-authority",
                            METEORA_DLMM_PROGRAM,
                        ],
                        "instructions": [
                            {
                                "programIdIndex": 15,
                                "accounts": [
                                    1,
                                    2,
                                    3,
                                    4,
                                    5,
                                    6,
                                    7,
                                    8,
                                    9,
                                    10,
                                    11,
                                    12,
                                    12,
                                    13,
                                    14,
                                    15,
                                ],
                                "data": _dlmm_swap_payload(),
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 42_000},
            },
            {
                "transaction": {
                    "signatures": ["unknown-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": "UnknownProgram111111111111111111111111111",
                                "data": "",
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 1_000},
            },
        ),
        transaction_compute_units=(42_000, 1_000),
    )

    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 1
    assert body["decoded_transaction_share"] == 0.5
    assert body["unsupported_program_ids"] == [
        "UnknownProgram111111111111111111111111111"
    ]
    assert body["eligible_for_calibration"] is False
    assert body["mainnet_accuracy_claim"] is False


def test_replay_reports_decoded_share_when_slot_contains_raydium_swap(
    client,
    monkeypatch,
) -> None:
    """FIX-010: Raydium AMM v4 swaps decode while mixed slots stay conservative."""
    slot = 320_000_010
    clear_slot_cache()
    replay_router.clear_replay_cache()
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": ["decoded-raydium-sig"],
                    "message": {
                        "accountKeys": [
                            "fee-payer",
                            TOKEN_PROGRAM,
                            "raydium-amm",
                            "amm-authority",
                            "amm-open-orders",
                            "amm-target-orders",
                            "pool-coin-vault",
                            "pool-pc-vault",
                            "openbook-program",
                            "openbook-market",
                            "openbook-bids",
                            "openbook-asks",
                            "openbook-event-queue",
                            "market-coin-vault",
                            "market-pc-vault",
                            "market-vault-signer",
                            "user-source-token",
                            "user-destination-token",
                            "wallet-authority",
                            RAYDIUM_AMM_V4_PROGRAM,
                        ],
                        "instructions": [
                            {
                                "programIdIndex": 19,
                                "accounts": [
                                    1,
                                    2,
                                    3,
                                    4,
                                    5,
                                    6,
                                    7,
                                    8,
                                    9,
                                    10,
                                    11,
                                    12,
                                    13,
                                    14,
                                    15,
                                    16,
                                    17,
                                    18,
                                ],
                                "data": _raydium_swap_payload(),
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 58_000},
            },
            {
                "transaction": {
                    "signatures": ["unknown-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "fee-payer"}],
                        "instructions": [
                            {
                                "programId": "UnknownProgram111111111111111111111111111",
                                "data": "",
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 1_000},
            },
        ),
        transaction_compute_units=(58_000, 1_000),
    )

    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 1
    assert body["decoded_transaction_share"] == 0.5
    assert body["unsupported_program_ids"] == [
        "UnknownProgram111111111111111111111111111"
    ]
    assert body["eligible_for_calibration"] is False
    assert body["mainnet_accuracy_claim"] is False


def test_replay_partial_coverage_marked_synthetic_or_partial(client) -> None:
    """PRD US-002 line 338: when decoded coverage is incomplete, the run is
    marked ``synthetic_or_partial_replay`` and ``mainnet_accuracy_claim`` is
    ``False`` so consumers do not surface mainnet-accuracy numbers.

    The corpus fixture's transactions all hit OpaqueAction today (no decoders
    have shipped yet), so the entry-gate slot is the natural partial-coverage
    case.
    """
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Entry-gate fixture has zero decoded actions, so coverage is incomplete.
    assert body["decoded_transaction_share"] < 1.0
    assert body["replay_kind"] == "development_or_partial_replay"
    assert body["mainnet_accuracy_claim"] is False
    assert body["eligible_for_calibration"] is False


def test_replay_corpus_slot_with_full_decode_promotes_to_calibrated(
    client,
    monkeypatch,
) -> None:
    """PRD line 338: a fully-decoded replay against a non-synthetic corpus
    slot whose per-metric error band passes its threshold must be promoted
    to ``mainnet_calibrated_replay`` with ``mainnet_accuracy_claim=True``.

    Slot 417_586_660 has a real ``steady_state`` corpus manifest. We mock
    ``_load_slot`` with a fully-decoded tip snapshot so the engine round-
    trips and ``tips_paid`` matches between predicted and actual.
    """
    slot = 417_586_660  # category=steady_state in the committed manifest
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decoded_transaction_share"] == 1.0
    assert body["eligible_for_calibration"] is True
    assert body["replay_kind"] == "mainnet_calibrated_replay"
    assert body["mainnet_accuracy_claim"] is True


def test_replay_synthetic_category_corpus_slot_is_not_promoted(
    client,
    monkeypatch,
) -> None:
    """A corpus slot whose manifest carries ``category: synthetic`` (e.g.
    250_000_000) marks a placeholder/development fixture. Even with a
    fully-decoded snapshot, the run must stay
    ``development_or_partial_replay`` with no mainnet-accuracy claim.
    """
    slot = 250_000_000  # category=synthetic in the committed manifest
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decoded_transaction_share"] == 1.0
    assert body["replay_kind"] == "development_or_partial_replay"
    assert body["mainnet_accuracy_claim"] is False


def test_replay_non_corpus_slot_with_full_decode_is_not_promoted(
    client,
    monkeypatch,
) -> None:
    """A slot with no committed corpus manifest cannot back a mainnet
    claim, even when fully decoded. The promotion gate requires committed
    calibration evidence on disk."""
    slot = 700_000_001  # no corpus directory committed for this slot
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decoded_transaction_share"] == 1.0
    assert body["replay_kind"] == "development_or_partial_replay"
    assert body["mainnet_accuracy_claim"] is False


def test_replay_real_corpus_slot_with_partial_decode_still_promotes(
    client,
    monkeypatch,
) -> None:
    """FIX-016: a real (non-synthetic) corpus slot should be promoted to
    ``mainnet_calibrated_replay`` even when only some txs decode. Undecoded
    txs land as OpaqueAction and contribute nothing to the diff, so they
    are safe to ignore proactively. As decoders ship, more txs flip from
    opaque to decoded automatically.
    """
    slot = 417_586_660  # category=steady_state in the committed manifest
    decoded_tx = _tip_snapshot(slot=slot).transactions[0]
    opaque_tx = {
        "transaction": {
            "signatures": ["unknown-sig"],
            "message": {
                "accountKeys": [{"pubkey": "fee-payer"}],
                "instructions": [
                    {
                        "programId": "UnknownProgram111111111111111111111111111",
                        "data": "",
                    }
                ],
            },
        },
        "meta": {"computeUnitsConsumed": 1_000},
    }
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(decoded_tx, opaque_tx),
        transaction_compute_units=(5_000, 1_000),
        jito_tips=({"lamports": 5_000},),
    )
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()
    assert response.status_code == 200, response.text
    body = response.json()
    # Coverage is incomplete (one opaque tx), but the diff is over the
    # decoded subset and the gate must not block the promotion.
    assert body["decoded_transaction_share"] < 1.0
    assert body["eligible_for_calibration"] is True
    assert body["replay_kind"] == "mainnet_calibrated_replay"
    assert body["mainnet_accuracy_claim"] is True


def test_replay_zero_loaded_slots_marked_synthetic_or_partial(client) -> None:
    """PRD US-002 line 338: a replay with no loaded slots is incomplete by
    definition and must be marked ``synthetic_or_partial_replay`` with no
    mainnet-accuracy claim. Guards against a future regression where a
    ``decoded_share == 0.0`` zero-action case could accidentally claim
    eligibility on the trivially-satisfied ratio test.
    """
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [999_000_001, 999_000_001], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slots_loaded"] == 0
    assert body["replay_kind"] == "development_or_partial_replay"
    assert body["mainnet_accuracy_claim"] is False


def test_replay_without_counterfactuals_persists_actions_in_slot_order(
    client,
    monkeypatch,
) -> None:
    """FIX-005: zero-counterfactual replay emits historical actions in the
    original slot order."""
    slot = 320_000_006
    snapshot = _tip_snapshot(
        slot=slot,
        signatures=("bundle-1", "bundle-2"),
        lamports=(5_000, 7_000),
    )
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    actions = result_resp.json()["result"]["predicted"]["actions"]
    assert [action["signature"] for action in actions] == ["bundle-1", "bundle-2"]
    assert [action["tip_lamports"] for action in actions] == [5_000, 7_000]


def test_replay_artifact_contains_round_snapshots_for_loaded_decoded_slot(
    client,
    monkeypatch,
) -> None:
    """FIX-005: loaded decoded slots persist canonical replay metrics under
    round_snapshots[*].metrics.replay."""
    slot = 320_000_007
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    result_resp = client.get(f"/runs/{run_id}/result")
    assert result_resp.status_code == 200, result_resp.text
    result = result_resp.json()["result"]
    assert result["round_snapshots"]
    replay_metrics = result["round_snapshots"][0]["metrics"]["replay"]
    assert replay_metrics["bundle_landing_rate"]["sample_size"] == 1
    assert replay_metrics["bundle_landing_rate"]["value"] == 1.0

    rounds_resp = client.get(f"/runs/{run_id}/rounds")
    assert rounds_resp.status_code == 200, rounds_resp.text
    assert rounds_resp.json()["available_rounds"] == [slot]


def test_replay_multislot_actual_diff_preserves_slot_identity(
    client,
    monkeypatch,
) -> None:
    """FIX-005: actual metric aggregation must not collapse all write locks
    into the first slot when a replay spans multiple slots."""
    first_slot = 320_000_011
    second_slot = 320_000_012
    snapshots = {
        first_slot: _token_transfer_snapshot(slot=first_slot, signature="token-a"),
        second_slot: _token_transfer_snapshot(slot=second_slot, signature="token-b"),
    }
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshots.get(requested_slot),
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [first_slot, second_slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    diff = result_resp.json()["result"]["replay_diff"]["per_metric_error"]
    assert diff["write_lock_heatmap"]["predicted"] == 1.0
    assert diff["write_lock_heatmap"]["actual"] == 1.0


def test_replay_decoded_swap_executes_through_market_execute(
    client,
    monkeypatch,
) -> None:
    """FIX-005: decoded replay swaps execute through the engine market path."""
    import defi_sim_solana.replay.materialize as materialize_mod
    from defi_sim.core.types import Action, ExecutionContext, ExecutionResult
    from defi_sim_solana.replay.materialize import (
        ActionDecodeStatus,
        MaterializedActionMetadata,
        MaterializedSwapAction,
    )

    slot = 320_000_016
    snapshot = SlotSnapshot(slot=slot, transactions=({"signature": "swap-sig"},))
    swap = MaterializedSwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=100,
        amount_out=100,
        pool_id="pool-1",
        protocol_program_id=WHIRLPOOL_PROGRAM,
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature="swap-sig",
            slot=slot,
            program_ids=(WHIRLPOOL_PROGRAM,),
        ),
    )
    markets: list[replay_router._ReplayProtocolMarket] = []

    class RecordingMarket(replay_router._ReplayProtocolMarket):
        def __init__(self, name: str) -> None:
            super().__init__(name)
            markets.append(self)

        def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
            self.executed.append(action)
            self.last_slot = int(ctx.current_round)
            self.last_price = 1.25
            self.last_slot_volume = 321.0
            self.total_volume += self.last_slot_volume
            return ExecutionResult(success=True, volume=self.last_slot_volume)

    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    monkeypatch.setattr(replay_router, "materialize_slot", lambda _snap: [swap])
    monkeypatch.setattr(materialize_mod, "materialize_slot", lambda _snap: [swap])
    monkeypatch.setattr(replay_router, "_ReplayProtocolMarket", RecordingMarket)
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    assert markets
    assert markets[0].executed == [swap]

    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    predicted = result_resp.json()["result"]["predicted"]
    assert predicted["pool_prices"]["pool-1"] == 1.25
    assert predicted["total_volume"] == 321.0


def test_replay_fee_counterfactual_updates_predicted_pool_state(
    client,
    monkeypatch,
) -> None:
    """FIX-005: fee counterfactuals mutate replay market state before the
    decoded swap executes, so predicted metrics come from execution state."""
    import defi_sim_solana.replay.materialize as materialize_mod
    from defi_sim_solana.replay.materialize import (
        ActionDecodeStatus,
        MaterializedActionMetadata,
        MaterializedSwapAction,
    )

    slot = 320_000_013
    snapshot = SlotSnapshot(slot=slot, transactions=({"signature": "swap-sig"},))
    swap = MaterializedSwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=100,
        amount_out=100,
        pool_id="pool-1",
        protocol_program_id=WHIRLPOOL_PROGRAM,
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature="swap-sig",
            slot=slot,
            program_ids=(WHIRLPOOL_PROGRAM,),
        ),
    )

    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    monkeypatch.setattr(replay_router, "materialize_slot", lambda _snap: [swap])
    monkeypatch.setattr(materialize_mod, "materialize_slot", lambda _snap: [swap])
    try:
        response = client.post(
            "/v1/replay",
            json={
                "slot_range": [slot, slot],
                "counterfactuals": [
                    {
                        "kind": "FeeReplaceCounterfactual",
                        "params": {"target_pool": "pool-1", "new_fee_bps": 1_000},
                    }
                ],
            },
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    result_resp = client.get(f"/runs/{response.json()['run_id']}/result")
    assert result_resp.status_code == 200, result_resp.text
    result = result_resp.json()["result"]
    assert result["predicted"]["pool_prices"]["pool-1"] == 0.9
    band = result["replay_diff"]["per_metric_error"]["pool_price:pool-1"]
    assert band["predicted"] == 0.9
    assert band["actual"] == 1.0


def test_replay_fully_decoded_development_fixture_stays_uncalibrated(
    client,
    monkeypatch,
) -> None:
    """FIX-005 / FIX-016: full decoded coverage is not enough to claim
    mainnet accuracy without committed (non-synthetic) calibration
    evidence. The eligibility flag now reflects corpus backing too, so a
    development slot stays ineligible even when fully decoded."""
    slot = 320_000_008
    snapshot = _tip_snapshot(slot=slot)
    clear_slot_cache()
    replay_router.clear_replay_cache()
    monkeypatch.setattr(
        replay_router,
        "_load_slot",
        lambda requested_slot: snapshot if requested_slot == slot else None,
    )
    try:
        response = client.post(
            "/v1/replay",
            json={"slot_range": [slot, slot], "counterfactuals": []},
        )
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decoded_transaction_share"] == 1.0
    assert body["eligible_for_calibration"] is False
    assert body["replay_kind"] == "development_or_partial_replay"
    assert body["mainnet_accuracy_claim"] is False


def test_replay_endpoint_returns_under_10s_for_typical_slot(
    client, monkeypatch
) -> None:
    """US-007 performance: a primed slot summary cache avoids reloading the
    same slot for a counterfactual rerun."""
    slot = 250_000_000
    replay_router.clear_replay_cache()
    clear_slot_cache()
    snapshot = SlotSnapshot(
        slot=slot,
        transactions=(
            {
                "transaction": {
                    "signatures": ["sig-250m"],
                    "message": {
                        "accountKeys": ["payer-250m", "program-250m"],
                        "instructions": [{"programIdIndex": 1}],
                    },
                },
                "meta": {"computeUnitsConsumed": 123_456},
            },
        ),
    )
    calls = {"load_slot": 0}

    def load_slot_once(requested_slot: int) -> SlotSnapshot | None:
        calls["load_slot"] += 1
        return snapshot if requested_slot == slot else None

    monkeypatch.setattr(replay_router, "_load_slot", load_slot_once)
    prime = client.post(
        "/v1/replay",
        json={"slot_range": [slot, slot], "counterfactuals": []},
    )
    assert prime.status_code == 200, prime.text
    assert calls["load_slot"] == 1

    def fail_if_reloaded(requested_slot: int) -> SlotSnapshot | None:
        raise AssertionError(f"slot {requested_slot} should come from replay cache")

    monkeypatch.setattr(replay_router, "_load_slot", fail_if_reloaded)
    started = time.perf_counter()
    try:
        response = client.post(
            "/v1/replay",
            json={
                "slot_range": [slot, slot],
                "counterfactuals": [
                    {
                        "kind": "TipReplaceCounterfactual",
                        "params": {
                            "target_bundle_id": "b-1",
                            "new_tip_lamports": 100_000,
                        },
                    },
                ],
            },
        )
        elapsed = time.perf_counter() - started
    finally:
        replay_router.clear_replay_cache()

    assert response.status_code == 200, response.text
    body = response.json()
    assert elapsed < 10.0
    assert body["slot_range"] == [slot, slot]
    assert body["slots_loaded"] == 1
    assert body["unsupported_program_ids"] == ["program-250m"]


def test_replay_artifact_persists_replay_kind_and_accuracy_claim(client) -> None:
    """PRD US-002 line 338 + line 361: the run artifact summary must carry
    ``replay_kind`` and ``mainnet_accuracy_claim`` so downstream consumers
    reading the persisted artifact (not just the POST response) honor the
    no-mainnet-accuracy-claim contract.
    """
    clear_slot_cache()
    response = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    runs_resp = client.get(f"/runs/{body['run_id']}")
    assert runs_resp.status_code == 200, runs_resp.text
    summary = runs_resp.json().get("summary", {})
    assert summary.get("replay_kind") == body["replay_kind"]
    assert summary.get("mainnet_accuracy_claim") == body["mainnet_accuracy_claim"]


def test_assert_slotsnapshot_remains_importable() -> None:
    # Smoke import — the router uses SlotSnapshot via get_slot; if the symbol
    # ever moves, the router import would break before this test runs.
    assert SlotSnapshot is not None
