"""Wallet-gated artifact persistence tests (PRD US-009/US-010)."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta

from solders.keypair import Keypair

from defi_sim_api.routers import share as share_router

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    run_resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert run_resp.status_code == 200, run_resp.text
    return run_resp.json()["run_id"]


def _signature_base64(keypair: Keypair, message: str) -> str:
    signature = keypair.sign_message(message.encode("utf-8"))
    return base64.b64encode(bytes(signature)).decode("ascii")


def test_signed_artifact_promoted_to_permanent(client) -> None:
    run_id = _create_run(client)
    keypair = Keypair()
    wallet_pubkey = str(keypair.pubkey())

    challenge_resp = client.post(
        f"/wallet/artifacts/{run_id}/challenge",
        json={"wallet_pubkey": wallet_pubkey},
    )
    assert challenge_resp.status_code == 200, challenge_resp.text
    challenge = challenge_resp.json()
    assert challenge["run_id"] == run_id
    assert challenge["wallet_pubkey"] == wallet_pubkey
    assert "transaction" not in challenge["message"].lower()
    assert f"run_id={run_id}" in challenge["message"]

    promote_resp = client.post(
        f"/wallet/artifacts/{run_id}/promote",
        json={
            "wallet_pubkey": wallet_pubkey,
            "nonce": challenge["nonce"],
            "signature": _signature_base64(keypair, challenge["message"]),
            "encoding": "base64",
        },
    )
    assert promote_resp.status_code == 200, promote_resp.text
    promoted = promote_resp.json()
    assert promoted["run_id"] == run_id
    assert promoted["permanent"] is True
    assert promoted["expires_at"] is None

    share_resp = client.get(f"/share/runs/{run_id}")
    assert share_resp.status_code == 200, share_resp.text
    share_body = share_resp.json()
    assert share_body["permanent"] is True
    assert share_body["expires_at"] is None
    assert share_body["run"]["summary"]["persistence"] == "wallet_permanent"
    assert share_body["run"]["summary"]["wallet_owner"] == wallet_pubkey

    artifacts_resp = client.get(
        "/wallet/artifacts",
        params={"wallet_pubkey": wallet_pubkey},
    )
    assert artifacts_resp.status_code == 200, artifacts_resp.text
    artifacts = artifacts_resp.json()
    assert artifacts["count"] == 1
    assert artifacts["artifacts"][0]["run_id"] == run_id


def test_wallet_signature_must_match_challenge(client) -> None:
    run_id = _create_run(client)
    owner = Keypair()
    attacker = Keypair()

    challenge = client.post(
        f"/wallet/artifacts/{run_id}/challenge",
        json={"wallet_pubkey": str(owner.pubkey())},
    ).json()

    promote_resp = client.post(
        f"/wallet/artifacts/{run_id}/promote",
        json={
            "wallet_pubkey": str(owner.pubkey()),
            "nonce": challenge["nonce"],
            "signature": _signature_base64(attacker, challenge["message"]),
            "encoding": "base64",
        },
    )
    assert promote_resp.status_code == 401, promote_resp.text

    share_resp = client.get(f"/share/runs/{run_id}")
    assert share_resp.status_code == 200, share_resp.text
    assert share_resp.json()["permanent"] is False


def test_unsigned_artifact_expires_at_30_days(client, monkeypatch) -> None:
    run_id = _create_run(client)
    created = client.get(f"/runs/{run_id}").json()["created_at"]
    created_at = datetime.fromisoformat(created)
    monkeypatch.setattr(
        share_router,
        "_utc_now",
        lambda: created_at + timedelta(days=31),
    )

    resolve_resp = client.get(f"/share/runs/{run_id}")
    assert resolve_resp.status_code == 410, resolve_resp.text
