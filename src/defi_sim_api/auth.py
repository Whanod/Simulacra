"""API auth for the bundle simulator and other public endpoints (PRD US-005
lines 881-884).

Two auth paths:

* ``Authorization: Bearer <API_KEY>`` for programmatic integrators. Keys are
  hashed at rest; the matched key id is returned in the ``X-API-Key-Id``
  response header for support reference (PRD line 884).
* ``session`` cookie for studio requests (existing pattern). The cookie is
  trusted as-is here — the studio session lifecycle lives outside this
  module.

The allowed-key store is sourced from the ``DEFI_SIM_API_KEYS`` env var.
Format: comma-separated ``<key_id>:<sha256_hex>`` entries. When empty or
unset the route runs in *open mode* (auth bypassed) so local development
and the existing test suite keep working without configuration. Production
deployments set the env var to enforce auth.

Hashes are SHA-256 of the plaintext key (UTF-8). ``hash_api_key`` is the
canonical helper for both key generation tooling and the runtime check.
"""

from __future__ import annotations

import hashlib
import os

from fastapi import HTTPException, Request, status

API_KEYS_ENV = "DEFI_SIM_API_KEYS"
_BEARER_PREFIX = "bearer "


def hash_api_key(plaintext: str) -> str:
    """Return the canonical hex SHA-256 of an API key plaintext."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _parse_api_key_store(raw: str | None) -> dict[str, str]:
    """Parse the ``DEFI_SIM_API_KEYS`` env var into ``{hash_hex: key_id}``.

    Empty or unset env var returns an empty dict (open mode). Malformed
    entries (missing ``:``) are silently dropped — a deploy-time mistake
    should not silently weaken auth, but it should not 500 the service
    either; the missing entry simply rejects the would-be key at request
    time.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key_id, hash_hex = entry.split(":", 1)
        key_id = key_id.strip()
        hash_hex = hash_hex.strip().lower()
        if not key_id or not hash_hex:
            continue
        out[hash_hex] = key_id
    return out


def verify_request_auth(request: Request) -> str | None:
    """Verify a request's auth and return the matched key id (or sentinel).

    Returns:
        * ``None`` when the route is in open mode (no API_KEYS configured)
          and no auth headers were sent — historical/dev behavior.
        * The matched ``key_id`` when ``Authorization: Bearer <key>`` is
          valid against the configured allowlist (caller surfaces this in
          ``X-API-Key-Id`` per PRD line 884).
        * The literal sentinel ``"session"`` when a ``session`` cookie is
          present — the studio path, no further check here.

    Raises:
        ``HTTPException(401)`` when API keys are configured and the request
        carries a missing or invalid bearer token (and no session cookie).
    """
    keys = _parse_api_key_store(os.environ.get(API_KEYS_ENV))

    # Studio path: session cookie short-circuits the bearer check (PRD line
    # 883). The session cookie's lifecycle is owned elsewhere.
    if request.cookies.get("session"):
        return "session"

    authorization = request.headers.get("authorization")

    if not keys:
        # Open mode: no allowlist configured, no enforcement.
        return None

    if authorization is None or not authorization.lower().startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[len(_BEARER_PREFIX):].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key_id = keys.get(hash_api_key(token))
    if key_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return key_id
