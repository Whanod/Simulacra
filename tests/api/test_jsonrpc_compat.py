"""Unit coverage for the Solana JSON-RPC compatibility shim."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from defi_sim_api.routers import jsonrpc_solana, simulate_bundle
from defi_sim_solana.replay.slot_client import SlotSnapshot

WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
KNOWN_POOL = "PoolMemecoinSolBonk33333333333333333333333"


@pytest.fixture(autouse=True)
def _reset_jsonrpc_state() -> None:
    jsonrpc_solana._CURRENT_SLOT = None
    jsonrpc_solana._SLOT_CACHE.clear()
    jsonrpc_solana._ACCOUNT_SNAPSHOT_CACHE.clear()
    jsonrpc_solana._TX_CACHE.clear()


def _rpc(client: Any, method: str, params: list[Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/solana-rpc",
        json={
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
            "params": params or [],
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == method
    return body


def _fake_bundle_response() -> simulate_bundle.SimulateBundleResponse:
    return simulate_bundle.SimulateBundleResponse(
        expected_tip_to_land_lamports=95_000,
        landing_probability=0.87,
        profit_distribution=simulate_bundle.ProfitDistributionModel(
            p50=200_000,
            p90=50_000,
        ),
        alt_compression=simulate_bundle.AltCompressionModel(
            uncompressed_bytes=1500,
            compressed_bytes=800,
        ),
        cu_budget=simulate_bundle.CuBudgetModel(
            tx_cu_used=[200_000],
            slot_cu_headroom=47_800_000,
        ),
        write_lock_contention=simulate_bundle.WriteLockContentionModel(),
        tip_optimizer=None,
        calibration=None,
        metrics=simulate_bundle.MetricsBlock(
            replay=simulate_bundle.ReplayMetricsBlock(
                bundle_landing_rate={
                    "value": 0.87,
                    "unit": "ratio",
                    "sample_size": 100,
                },
                tip_efficiency={
                    "value": 0.5,
                    "unit": "ratio",
                    "sample_size": 1,
                },
                slot_inclusion_latency={
                    "value": 0,
                    "unit": "slots",
                    "sample_size": 1,
                    "mean": 0,
                    "median": 0,
                    "p95": 0,
                    "p99": 0,
                    "samples": [0],
                },
            )
        ),
    )


def _encoded_compute_budget_tx(compute_unit_limit: int) -> str:
    from solders.compute_budget import set_compute_unit_limit
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    payer = Keypair()
    receiver = Keypair()
    message = Message(
        [
            set_compute_unit_limit(compute_unit_limit),
            transfer(
                TransferParams(
                    from_pubkey=payer.pubkey(),
                    to_pubkey=receiver.pubkey(),
                    lamports=1,
                )
            ),
        ],
        payer.pubkey(),
    )
    return base64.b64encode(
        bytes(Transaction([payer], message, Hash.default()))
    ).decode("ascii")


def test_get_slot_returns_int(client) -> None:
    body = _rpc(client, "getSlot")

    assert isinstance(body["result"], int)
    assert body["result"] >= 250_000_000


def test_get_latest_and_recent_blockhash_return_solana_shapes(client) -> None:
    latest = _rpc(client, "getLatestBlockhash")
    recent = _rpc(client, "getRecentBlockhash")

    assert latest["result"]["context"]["slot"] >= 250_000_000
    assert latest["result"]["value"]["blockhash"]
    assert (
        latest["result"]["value"]["lastValidBlockHeight"]
        > latest["result"]["context"]["slot"]
    )
    assert recent["result"]["value"]["feeCalculator"]["lamportsPerSignature"] == 5_000


def test_get_account_info_known_pubkey_returns_account_data(client) -> None:
    body = _rpc(client, "getAccountInfo", [KNOWN_POOL, {"encoding": "base64"}])

    account = body["result"]["value"]
    assert account["owner"] == WHIRLPOOL_PROGRAM
    assert account["lamports"] == 70_407_360
    assert account["data"][1] == "base64"
    assert account["space"] > 0


def test_get_program_accounts_filters_correctly(client) -> None:
    unfiltered = _rpc(client, "getProgramAccounts", [WHIRLPOOL_PROGRAM])
    account = unfiltered["result"][0]["account"]

    matching = _rpc(
        client,
        "getProgramAccounts",
        [WHIRLPOOL_PROGRAM, {"filters": [{"dataSize": account["space"]}]}],
    )
    missing = _rpc(
        client,
        "getProgramAccounts",
        [WHIRLPOOL_PROGRAM, {"filters": [{"dataSize": account["space"] + 1}]}],
    )

    assert [row["pubkey"] for row in matching["result"]] == [KNOWN_POOL]
    assert missing["result"] == []


def test_get_signatures_and_transaction_from_cached_slot(client) -> None:
    tx = {
        "transaction": {
            "signatures": ["sig-jsonrpc-1"],
            "message": {
                "accountKeys": [KNOWN_POOL, "Other111111111111111111111111111111111"]
            },
        },
        "meta": {"err": None, "computeUnitsConsumed": 1234},
    }
    jsonrpc_solana._SLOT_CACHE[123] = SlotSnapshot(
        slot=123,
        blockhash="cached-blockhash",
        block_time=1_700_000_000,
        transactions=(tx,),
    )

    signatures = _rpc(client, "getSignaturesForAddress", [KNOWN_POOL])
    transaction = _rpc(client, "getTransaction", ["sig-jsonrpc-1"])

    assert signatures["result"][0]["signature"] == "sig-jsonrpc-1"
    assert signatures["result"][0]["slot"] == 123
    assert transaction["result"]["slot"] == 123
    assert transaction["result"]["transaction"]["signatures"] == ["sig-jsonrpc-1"]


def test_simulate_transaction_returns_logs_and_account_changes(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_internal(
        request: simulate_bundle.SimulateBundleRequest,
    ) -> simulate_bundle.SimulateBundleResponse:
        assert request.bundle.txs == ["base58encodedtx"]
        return _fake_bundle_response()

    monkeypatch.setattr(simulate_bundle, "simulate_bundle_internal", fake_internal)

    body = _rpc(
        client,
        "simulateTransaction",
        [
            "base58encodedtx",
            {
                "contextSlot": 250_000_000,
                "tipLamports": 123_000,
                "accounts": {"addresses": [KNOWN_POOL]},
            },
        ],
    )

    value = body["result"]["value"]
    assert value["err"] is None
    assert value["accounts"][0]["owner"] == WHIRLPOOL_PROGRAM
    assert value["unitsConsumed"] == 200_000
    assert any("landing_probability=0.870000" in line for line in value["logs"])


def test_simulate_transaction_latest_context_uses_current_slot(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        simulate_bundle,
        "simulate_bundle_internal",
        lambda _request: _fake_bundle_response(),
    )

    body = _rpc(client, "simulateTransaction", ["base58encodedtx", {}])

    assert body["result"]["context"]["slot"] >= 250_000_000


def test_simulate_transaction_inherits_bundle_cu_estimator(client) -> None:
    tx = _encoded_compute_budget_tx(444_000)

    body = _rpc(
        client,
        "simulateTransaction",
        [tx, {"contextSlot": 250_000_000, "tipLamports": 0}],
    )

    value = body["result"]["value"]
    assert value["err"] is None
    assert value["unitsConsumed"] == 444_000
    assert any("landing_probability=" in line for line in value["logs"])


def test_send_transaction_returns_method_not_found(client) -> None:
    body = _rpc(client, "sendTransaction", ["payload"])

    assert body["error"]["code"] == -32601
    assert "read-only" in body["error"]["message"]
    assert "does not sign or send transactions" in body["error"]["message"]


@pytest.mark.parametrize(
    "method",
    [
        "sendRawTransaction",
        "signMessage",
        "signTransaction",
        "signAllTransactions",
        "signTypedData",
    ],
)
def test_signing_methods_return_read_only_method_not_found(
    client,
    method: str,
) -> None:
    body = _rpc(client, method, ["payload"])

    assert body["error"]["code"] == -32601
    assert "read-only" in body["error"]["message"]
    assert "does not sign or send transactions" in body["error"]["message"]


def test_unknown_method_returns_method_not_found(client) -> None:
    body = _rpc(client, "getBlocks", [])

    assert body["error"]["code"] == -32601
    assert body["error"]["message"] == "Method not found: getBlocks"
