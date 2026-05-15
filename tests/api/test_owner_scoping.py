"""End-to-end ownership tests covering the router layer.

Exercises the contract from privy.md §4.3:

* Open mode (no PRIVY_APP_ID, no DEFI_SIM_API_KEYS) → everything
  visible, lists unfiltered, no 404s for cross-owner reads.
* Enforced mode (PRIVY_APP_ID set) → list scoped to caller, cross-owner
  get-by-id returns 404, anon-owned rows stay readable.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from defi_sim_api import auth as auth_module
from defi_sim_api.auth import PRIVY_APP_ID_ENV
from defi_sim_api.backend.store import get_artifact_store

APP_ID = "test-app-owners"
ALICE_DID = "did:privy:alice"
BOB_DID = "did:privy:bob"


@pytest.fixture()
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(
        private_key.public_key(), as_dict=True
    )
    public_jwk["kid"] = "test-owner-kid"
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return private_key, public_jwk


@pytest.fixture()
def stub_jwks(monkeypatch, rsa_keypair):
    _, public_jwk = rsa_keypair
    auth_module._JWKS_CACHE.clear()
    auth_module._JWKS_CACHE[APP_ID] = (time.monotonic(), {public_jwk["kid"]: public_jwk})
    yield
    auth_module._JWKS_CACHE.clear()


def _token(private_key, sub: str, *, email: str | None = None) -> str:
    payload = {
        "sub": sub,
        "iss": "privy.io",
        "aud": APP_ID,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
    }
    if email is not None:
        payload["email"] = email
    return pyjwt.encode(payload, private_key, algorithm="RS256",
                        headers={"kid": "test-owner-kid"})


def _spec():
    return {
        "market": {
            "type": "cfamm",
            "tokens": [
                {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
            ],
            "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
        },
        "agents": [
            {"type": "noise", "agent_id": "noise-1",
             "params": {"collateral": "USDC", "frequency": 0.0},
             "initial_balances": {"USDC": 1_000_000_000}},
        ],
        "num_rounds": 3,
        "snapshot_interval": 1,
        "seed": 1,
    }


def test_open_mode_lists_everything(client):
    """Without PRIVY_APP_ID / DEFI_SIM_API_KEYS, list_runs returns every
    row regardless of who wrote it. This is the historical contract the
    golden harness and local dev depend on."""
    # Two runs persisted through different "users" at the store layer —
    # both should be visible because the server isn't enforcing.
    store = get_artifact_store()
    store.create_run(
        "run-alice-open", spec={"k": 1}, status="completed", seed=1,
        market_type="cfamm", source="sync", owner_id="did:privy:alice",
    )
    store.create_run(
        "run-bob-open", spec={"k": 2}, status="completed", seed=2,
        market_type="cfamm", source="sync", owner_id="did:privy:bob",
    )

    r = client.get("/runs")
    assert r.status_code == 200
    run_ids = {row["run_id"] for row in r.json()["runs"]}
    assert {"run-alice-open", "run-bob-open"}.issubset(run_ids)

    # Cross-owner get-by-id is allowed in open mode (auth not enforced).
    assert client.get("/runs/run-alice-open").status_code == 200
    assert client.get("/runs/run-bob-open").status_code == 200


def test_enforced_mode_scopes_list_and_404s_cross_owner(
    client, monkeypatch, rsa_keypair, stub_jwks
):
    """With PRIVY_APP_ID set, /runs returns only the caller's runs and
    cross-owner /runs/{id} 404s — leaking existence is the failure mode
    we're preventing."""
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)

    # Two users each post a run via /simulations/run; one anon row gets
    # planted directly into the store (legacy / API-key path).
    alice_token = _token(private_key, ALICE_DID, email="alice@example.com")
    bob_token = _token(private_key, BOB_DID, email="bob@example.com")

    r = client.post("/simulations/run", json=_spec(),
                    headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 200, r.text
    alice_run = r.json()["run_id"]

    r = client.post("/simulations/run", json=_spec(),
                    headers={"Authorization": f"Bearer {bob_token}"})
    assert r.status_code == 200, r.text
    bob_run = r.json()["run_id"]

    store = get_artifact_store()
    store.create_run(
        "run-anon-enforced", spec={"k": 0}, status="completed", seed=0,
        market_type="cfamm", source="sync",
    )

    # Alice sees only her run + anon-owned legacy rows.
    r = client.get("/runs", headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 200
    alice_visible = {row["run_id"] for row in r.json()["runs"]}
    assert alice_run in alice_visible
    assert bob_run not in alice_visible

    # Alice gets a 404 (not 403) when reading Bob's run by id.
    r = client.get(f"/runs/{bob_run}", headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 404

    # Alice can read her own run.
    r = client.get(f"/runs/{alice_run}", headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 200

    # Anon-owned legacy rows stay readable to any signed-in caller — that's
    # the contract that keeps share-link / API-key-write data accessible.
    r = client.get("/runs/run-anon-enforced",
                   headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 200


def test_enforced_mode_rejects_unauthenticated_request(
    client, monkeypatch, rsa_keypair, stub_jwks
):
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    r = client.get("/runs")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
