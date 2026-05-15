"""Tests for verify_request_auth and the current_user dependency.

Covers the three auth paths (Privy JWT, API key, session cookie) and
open-mode behavior. JWKS fetches are mocked in-process — no real Privy
network calls, no fixtures pinned to a real key.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from defi_sim_api import auth as auth_module
from defi_sim_api.auth import (
    API_KEYS_ENV,
    PRIVY_APP_ID_ENV,
    User,
    auth_enforced,
    current_user,
    hash_api_key,
    verify_request_auth,
)

APP_ID = "test-app-123"
ALICE_DID = "did:privy:alice"


@pytest.fixture()
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(
        private_key.public_key(), as_dict=True
    )
    public_jwk["kid"] = "test-kid-1"
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return private_key, public_jwk


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    auth_module._JWKS_CACHE.clear()
    yield
    auth_module._JWKS_CACHE.clear()


def _make_token(private_key, *, sub=ALICE_DID, exp_offset=300, aud=APP_ID,
                iss="privy.io", email="alice@example.com"):
    payload = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    if email is not None:
        payload["email"] = email
    return pyjwt.encode(payload, private_key, algorithm="RS256",
                        headers={"kid": "test-kid-1"})


@pytest.fixture()
def stub_jwks(monkeypatch, rsa_keypair):
    _, public_jwk = rsa_keypair
    auth_module._JWKS_CACHE[APP_ID] = (time.monotonic(), {public_jwk["kid"]: public_jwk})
    yield
    auth_module._JWKS_CACHE.clear()


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: User = Depends(current_user)):
        return {"id": user.id, "email": user.email, "anon": user.is_anonymous}

    @app.get("/raw-auth")
    def raw_auth(request: Request):
        principal = verify_request_auth(request)
        return {"principal": principal, "user_id": getattr(request.state, "user_id", None)}

    return app


def test_open_mode_returns_anonymous(monkeypatch):
    monkeypatch.delenv(API_KEYS_ENV, raising=False)
    monkeypatch.delenv(PRIVY_APP_ID_ENV, raising=False)
    assert not auth_enforced()
    client = TestClient(_build_test_app())
    r = client.get("/whoami")
    assert r.status_code == 200
    assert r.json() == {"id": None, "email": None, "anon": True}


def test_api_key_path_unaffected_by_privy_config(monkeypatch):
    key_id = "svc-1"
    plaintext = "supersecret"
    monkeypatch.setenv(API_KEYS_ENV, f"{key_id}:{hash_api_key(plaintext)}")
    monkeypatch.delenv(PRIVY_APP_ID_ENV, raising=False)
    client = TestClient(_build_test_app())
    r = client.get("/raw-auth", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200
    body = r.json()
    assert body["principal"] == key_id
    # API-key callers leave user_id None — they're service-level, not a user.
    assert body["user_id"] is None


def test_jwt_path_resolves_user(monkeypatch, rsa_keypair, stub_jwks):
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    monkeypatch.delenv(API_KEYS_ENV, raising=False)
    token = _make_token(private_key)
    client = TestClient(_build_test_app())
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": ALICE_DID, "email": "alice@example.com", "anon": False}


def test_jwt_with_wrong_audience_rejected(monkeypatch, rsa_keypair, stub_jwks):
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    token = _make_token(private_key, aud="some-other-app")
    client = TestClient(_build_test_app())
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_jwt_with_wrong_issuer_rejected(monkeypatch, rsa_keypair, stub_jwks):
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    token = _make_token(private_key, iss="evil.example.com")
    client = TestClient(_build_test_app())
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_expired_jwt_rejected(monkeypatch, rsa_keypair, stub_jwks):
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    token = _make_token(private_key, exp_offset=-60)
    client = TestClient(_build_test_app())
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_jwt_and_api_key_coexist(monkeypatch, rsa_keypair, stub_jwks):
    """Both auth paths active. JWT bearers route to user_id; non-JWT
    bearers fall through to the API-key allowlist."""
    private_key, _ = rsa_keypair
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    monkeypatch.setenv(API_KEYS_ENV, f"svc-1:{hash_api_key('apikey-secret')}")
    client = TestClient(_build_test_app())

    # JWT branch
    token = _make_token(private_key)
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["id"] == ALICE_DID

    # API-key branch (non-JWT bearer)
    r = client.get("/raw-auth", headers={"Authorization": "Bearer apikey-secret"})
    assert r.status_code == 200
    assert r.json()["principal"] == "svc-1"


def test_session_cookie_returns_anonymous_user(monkeypatch):
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    monkeypatch.delenv(API_KEYS_ENV, raising=False)
    client = TestClient(_build_test_app())
    client.cookies.set("session", "ignored")
    r = client.get("/whoami")
    # Cookie path: principal is "session" but no user_id is resolved.
    assert r.status_code == 200
    assert r.json()["id"] is None


def test_missing_bearer_when_enforced_401(monkeypatch):
    monkeypatch.setenv(PRIVY_APP_ID_ENV, APP_ID)
    monkeypatch.delenv(API_KEYS_ENV, raising=False)
    client = TestClient(_build_test_app())
    r = client.get("/whoami")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
