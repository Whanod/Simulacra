"""API auth for the bundle simulator and other public endpoints (PRD US-005
lines 881-884).

Three auth paths, tried in order:

* ``Authorization: Bearer <JWT>`` — Privy-issued access token. Verified
  against the Privy app's JWKS; the matched DID is stored on
  ``request.state.user_id``. Routers expose this via the
  :func:`current_user` FastAPI dependency.
* ``Authorization: Bearer <API_KEY>`` for programmatic integrators. Keys
  are hashed at rest; the matched key id is returned in the
  ``X-API-Key-Id`` response header for support reference (PRD line 884).
* ``session`` cookie for studio requests (legacy pattern; the studio
  itself now sends Privy JWTs, but the cookie path is left in place for
  any code that still relies on it).

The allowed-key store is sourced from the ``DEFI_SIM_API_KEYS`` env var.
Format: comma-separated ``<key_id>:<sha256_hex>`` entries. When *both*
``DEFI_SIM_API_KEYS`` and ``PRIVY_APP_ID`` are unset the route runs in
*open mode* (auth bypassed) so local development and the existing test
suite keep working without configuration. Production deployments set
``PRIVY_APP_ID`` (and optionally ``DEFI_SIM_API_KEYS`` for service
integrations).

Hashes are SHA-256 of the plaintext key (UTF-8). ``hash_api_key`` is the
canonical helper for both key generation tooling and the runtime check.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

API_KEYS_ENV = "DEFI_SIM_API_KEYS"
PRIVY_APP_ID_ENV = "PRIVY_APP_ID"
_BEARER_PREFIX = "bearer "
_JWKS_TTL_SECONDS = 600  # 10 minutes — matches Privy's documented JWKS rotation cadence.
_JWKS_FETCH_TIMEOUT = 5.0
# Privy issues RS256 today; ES256 is the only other asymmetric alg they
# document. Pinning explicitly defends against `alg=none` and HS256-via-
# RSA-JWK confusion attacks where the verifier honours an attacker-chosen
# alg from the unverified header.
_ALLOWED_JWT_ALGS = frozenset({"RS256", "ES256"})
# JWT pre-check: base64url body for each segment. A bare `count(".") == 2`
# would route any API key containing two dots into the JWT branch and 401
# instead of falling through to the API-key allowlist.
_JWT_SHAPE_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


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


# ── Privy JWT verification ──────────────────────────────────────────────────


@dataclass(frozen=True)
class User:
    """Resolved caller identity. ``id`` is the Privy DID (or ``None`` for
    anonymous / API-key / open-mode requests)."""

    id: str | None
    email: str | None
    is_anonymous: bool

    @property
    def is_authenticated(self) -> bool:
        return self.id is not None


_ANON_USER = User(id=None, email=None, is_anonymous=True)


def _privy_app_id() -> str | None:
    # Accept both the canonical Privy-documented name and the shorter
    # ``PRIVY_ID`` alias some operators put in their .env.
    raw = os.environ.get(PRIVY_APP_ID_ENV) or os.environ.get("PRIVY_ID")
    return raw.strip() if raw else None


def auth_enforced() -> bool:
    """Return True when the API has any auth configured.

    Routers consult this to decide whether to scope list endpoints by
    ``owner_id``. In open mode (no Privy app and no API keys) lists are
    unfiltered — the legacy/dev/test contract.
    """
    return bool(_privy_app_id()) or bool(_parse_api_key_store(os.environ.get(API_KEYS_ENV)))


# JWKS cache: app_id → (fetched_at_monotonic, keys-by-kid). The state
# lock guards the cache and the in-flight map; the per-app-id Event lets
# concurrent callers wait for an in-flight refresh without blocking each
# other across unrelated app ids and without holding the state lock
# across the network call.
_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_INFLIGHT: dict[str, threading.Event] = {}
_JWKS_STATE_LOCK = threading.Lock()


def _fetch_jwks(app_id: str) -> dict[str, Any]:
    """Fetch + cache Privy JWKS for one app id. Process-wide cache, TTL'd."""
    cached = _JWKS_CACHE.get(app_id)
    if cached is not None and (time.monotonic() - cached[0]) < _JWKS_TTL_SECONDS:
        return cached[1]

    # Single-flight: at most one HTTP fetch per app id at a time. Other
    # callers wait on the Event and then re-read the cache rather than
    # holding a global lock across the network call.
    with _JWKS_STATE_LOCK:
        cached = _JWKS_CACHE.get(app_id)
        if cached is not None and (time.monotonic() - cached[0]) < _JWKS_TTL_SECONDS:
            return cached[1]
        existing = _JWKS_INFLIGHT.get(app_id)
        if existing is not None:
            event_to_wait = existing
            should_fetch = False
        else:
            event_to_wait = threading.Event()
            _JWKS_INFLIGHT[app_id] = event_to_wait
            should_fetch = True

    if not should_fetch:
        # Another thread is fetching; wait, then read whatever it wrote.
        event_to_wait.wait(timeout=_JWKS_FETCH_TIMEOUT + 1.0)
        cached = _JWKS_CACHE.get(app_id)
        if cached is not None:
            return cached[1]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="could not fetch Privy JWKS",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        url = f"https://auth.privy.io/api/v1/apps/{app_id}/jwks.json"
        try:
            with urllib.request.urlopen(url, timeout=_JWKS_FETCH_TIMEOUT) as resp:  # noqa: S310 — fixed Privy host
                payload = resp.read()
        except Exception as exc:  # noqa: BLE001
            # Surface as 401 per privy.md §4.1 — every token-path failure
            # returns 401 so we don't leak Privy reachability or split the
            # caller's error handling between two status codes.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"could not fetch Privy JWKS: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        import json as _json

        data = _json.loads(payload)
        keys_by_kid: dict[str, Any] = {}
        for jwk in data.get("keys", []):
            kid = jwk.get("kid")
            if kid:
                keys_by_kid[kid] = jwk
        with _JWKS_STATE_LOCK:
            _JWKS_CACHE[app_id] = (time.monotonic(), keys_by_kid)
        return keys_by_kid
    finally:
        with _JWKS_STATE_LOCK:
            _JWKS_INFLIGHT.pop(app_id, None)
        event_to_wait.set()


def _looks_like_jwt(token: str) -> bool:
    """Cheap pre-check: three base64url segments separated by dots."""
    return bool(_JWT_SHAPE_RE.match(token))


def verify_privy_jwt(token: str) -> dict[str, Any]:
    """Verify a Privy access token and return its claims.

    Raises ``HTTPException(401)`` on any failure — bad signature, wrong
    issuer/audience, expired, missing/disallowed alg, or unreachable
    JWKS. Single status code per privy.md §4.1 so callers don't need to
    branch on Privy reachability.
    """
    app_id = _privy_app_id()
    if not app_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Privy auth is not configured on this server",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        import jwt as pyjwt
        from jwt import PyJWKClient  # noqa: F401  — touch to fail fast if crypto extra is missing
    except ImportError as exc:  # pragma: no cover — caught by deploy smoke test
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="server is missing PyJWT[crypto] — install the api extra",
        ) from exc

    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"malformed token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or not alg:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token header missing kid/alg",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if alg not in _ALLOWED_JWT_ALGS:
        # Defense in depth: PyJWT's defaults already block `none` and
        # require `kty=oct` for HMAC-from-JWK, but pinning the alg list
        # makes the contract explicit and forecloses future drift.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unsupported signing alg {alg!r}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    keys_by_kid = _fetch_jwks(app_id)
    jwk = keys_by_kid.get(kid)
    if jwk is None:
        # Possibly a key rotation — bust the cache once and retry.
        with _JWKS_STATE_LOCK:
            _JWKS_CACHE.pop(app_id, None)
        keys_by_kid = _fetch_jwks(app_id)
        jwk = keys_by_kid.get(kid)
        if jwk is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="unknown signing key",
                headers={"WWW-Authenticate": "Bearer"},
            )
    try:
        signing_key = pyjwt.algorithms.get_default_algorithms()[alg].from_jwk(jwk)
    except (KeyError, pyjwt.PyJWTError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unsupported signing alg {alg!r}: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    try:
        claims = pyjwt.decode(
            token,
            key=signing_key,
            algorithms=[alg],
            audience=app_id,
            issuer="privy.io",
            options={"require": ["exp", "sub"]},
        )
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return claims


# ── unified request-auth check ──────────────────────────────────────────────


def verify_request_auth(request: Request) -> str | None:
    """Verify a request's auth and return the matched principal id.

    Returns:
        * The Privy DID (prefixed ``"privy:"``) when a JWT bearer token
          verifies. Also stashes ``request.state.user_id`` (raw DID) and
          ``request.state.user_email`` so :func:`current_user` can read them.
        * The matched API ``key_id`` when ``Authorization: Bearer <key>``
          is valid against the configured allowlist.
        * The literal sentinel ``"session"`` when a ``session`` cookie is
          present (legacy studio path).
        * ``None`` when the route is in open mode (no Privy app and no
          API keys configured) and no auth headers were sent — historical
          dev/test behavior.

    Raises:
        ``HTTPException(401)`` when auth is enforced and the request
        carries a missing or invalid bearer token (and no session cookie).
    """
    # Initialise so downstream readers always find these attributes set.
    request.state.user_id = None
    request.state.user_email = None

    keys = _parse_api_key_store(os.environ.get(API_KEYS_ENV))
    privy_configured = _privy_app_id() is not None

    authorization = request.headers.get("authorization")

    # Bearer token path covers both JWTs and API keys; check it before the
    # cookie so a JWT-bearing studio request lands on the user_id branch
    # (the cookie path is a no-op fallback that pre-dates Privy).
    if authorization and authorization.lower().startswith(_BEARER_PREFIX):
        token = authorization[len(_BEARER_PREFIX):].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if privy_configured and _looks_like_jwt(token):
            claims = verify_privy_jwt(token)
            did = str(claims["sub"])
            email = claims.get("email")
            request.state.user_id = did
            request.state.user_email = email if isinstance(email, str) else None
            return f"privy:{did}"
        if keys:
            key_id = keys.get(hash_api_key(token))
            if key_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return key_id
        # Bearer token sent but neither Privy nor API keys are configured —
        # treat as a misuse rather than silently allowing it through.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="auth is not configured on this server",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Legacy studio cookie path. No identity is extracted; routers that
    # care about per-user scoping see ``current_user().id is None``.
    if request.cookies.get("session"):
        return "session"

    if not keys and not privy_configured:
        # Open mode: no allowlist configured, no enforcement.
        return None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(request: Request) -> User:
    """FastAPI dependency: resolve the current caller into a :class:`User`.

    Designed to be safe to add to *every* router — in open mode it
    returns the anonymous sentinel without raising, so existing tests
    keep their unauthenticated reads. In enforced mode it runs the full
    bearer/cookie check and raises 401 on bad credentials.
    """
    try:
        verify_request_auth(request)
    except HTTPException:
        # Re-raise verbatim — FastAPI surfaces the 401 with the
        # WWW-Authenticate header attached.
        raise
    user_id = getattr(request.state, "user_id", None)
    user_email = getattr(request.state, "user_email", None)
    if user_id is None:
        return _ANON_USER
    return User(id=user_id, email=user_email, is_anonymous=False)
