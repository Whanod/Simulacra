"""Wallet-owned artifact persistence endpoints.

The studio uses a one-time `signMessage` challenge to prove ownership of a
wallet before marking an otherwise ephemeral run artifact permanent. This
module never asks for transaction-signing capabilities.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from defi_sim_api.backend.store import get_artifact_store

CHALLENGE_TTL_SECONDS = 300

router = APIRouter(prefix="/wallet", tags=["wallet"])


class WalletChallengeRequest(BaseModel):
    wallet_pubkey: str = Field(min_length=32, max_length=64)


class WalletPromoteRequest(BaseModel):
    wallet_pubkey: str = Field(min_length=32, max_length=64)
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(min_length=32)
    encoding: Literal["base64", "base58"] = "base64"


@dataclass(frozen=True)
class _Challenge:
    run_id: str
    wallet_pubkey: str
    nonce: str
    message: str
    expires_at: datetime


_CHALLENGES: dict[str, _Challenge] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_pubkey(value: str) -> str:
    try:
        from solders.pubkey import Pubkey

        return str(Pubkey.from_string(value))
    except Exception as exc:  # pragma: no cover - exact exception type varies by solders version
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="wallet_pubkey must be a valid Solana public key",
        ) from exc


def _signature_from_request(signature: str, encoding: str):
    try:
        from solders.signature import Signature

        if encoding == "base58":
            return Signature.from_string(signature)
        raw = base64.b64decode(signature, validate=True)
        return Signature.from_bytes(raw)
    except Exception as exc:  # pragma: no cover - exact exception type varies by solders version
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="signature must be a valid Solana Ed25519 signature",
        ) from exc


def _verify_signature(wallet_pubkey: str, message: str, signature: str, encoding: str) -> bool:
    from solders.pubkey import Pubkey

    pubkey = Pubkey.from_string(wallet_pubkey)
    parsed_signature = _signature_from_request(signature, encoding)
    return bool(parsed_signature.verify(pubkey, message.encode("utf-8")))


def _challenge_message(
    *,
    run_id: str,
    wallet_pubkey: str,
    nonce: str,
    expires_at: datetime,
) -> str:
    return "\n".join(
        [
            "defi-sim artifact persistence",
            f"run_id={run_id}",
            f"wallet={wallet_pubkey}",
            f"nonce={nonce}",
            f"expires_at={expires_at.isoformat()}",
        ]
    )


def _prune_expired_challenges(now: datetime) -> None:
    expired = [
        nonce
        for nonce, challenge in _CHALLENGES.items()
        if challenge.expires_at <= now
    ]
    for nonce in expired:
        _CHALLENGES.pop(nonce, None)


def _run_or_404(run_id: str) -> dict[str, Any]:
    run = get_artifact_store().get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id!r} not found",
        )
    return run


def _wallet_owner(summary: dict[str, Any]) -> str | None:
    owner = summary.get("wallet_owner")
    if isinstance(owner, str) and owner:
        return owner
    persistence = summary.get("wallet_persistence")
    if isinstance(persistence, dict):
        owner = persistence.get("owner")
        if isinstance(owner, str) and owner:
            return owner
    return None


@router.post(
    "/artifacts/{run_id}/challenge",
    response_model=dict[str, Any],
    summary="Create a wallet message-signing challenge for artifact persistence",
)
def create_artifact_persistence_challenge(
    run_id: str,
    body: WalletChallengeRequest,
) -> dict[str, Any]:
    _run_or_404(run_id)
    wallet_pubkey = _validate_pubkey(body.wallet_pubkey)
    now = _utc_now()
    _prune_expired_challenges(now)
    expires_at = now + timedelta(seconds=CHALLENGE_TTL_SECONDS)
    nonce = secrets.token_urlsafe(24)
    message = _challenge_message(
        run_id=run_id,
        wallet_pubkey=wallet_pubkey,
        nonce=nonce,
        expires_at=expires_at,
    )
    _CHALLENGES[nonce] = _Challenge(
        run_id=run_id,
        wallet_pubkey=wallet_pubkey,
        nonce=nonce,
        message=message,
        expires_at=expires_at,
    )
    return {
        "run_id": run_id,
        "wallet_pubkey": wallet_pubkey,
        "nonce": nonce,
        "message": message,
        "expires_at": expires_at.isoformat(),
    }


@router.post(
    "/artifacts/{run_id}/promote",
    response_model=dict[str, Any],
    summary="Promote a run artifact to permanent wallet-owned storage",
)
def promote_artifact_with_wallet(
    run_id: str,
    body: WalletPromoteRequest,
) -> dict[str, Any]:
    run = _run_or_404(run_id)
    wallet_pubkey = _validate_pubkey(body.wallet_pubkey)
    now = _utc_now()
    _prune_expired_challenges(now)
    challenge = _CHALLENGES.get(body.nonce)
    if (
        challenge is None
        or challenge.run_id != run_id
        or challenge.wallet_pubkey != wallet_pubkey
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="challenge not found for run and wallet",
        )
    if challenge.expires_at <= now:
        _CHALLENGES.pop(body.nonce, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="challenge expired",
        )
    if not _verify_signature(
        wallet_pubkey,
        challenge.message,
        body.signature,
        body.encoding,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="wallet signature does not match challenge",
        )

    summary = dict(run.get("summary") or {})
    promoted_at = now.isoformat()
    summary.update(
        {
            "permanent": True,
            "saved": True,
            "persistence": "wallet_permanent",
            "wallet_owner": wallet_pubkey,
            "wallet_persistence": {
                "owner": wallet_pubkey,
                "promoted_at": promoted_at,
                "challenge": "signMessage",
            },
        }
    )
    updated = get_artifact_store().update_run(run_id, summary=summary)
    _CHALLENGES.pop(body.nonce, None)
    return {
        "run_id": run_id,
        "wallet_pubkey": wallet_pubkey,
        "permanent": True,
        "expires_at": None,
        "run": updated,
    }


@router.get(
    "/artifacts",
    response_model=dict[str, Any],
    summary="List permanent artifacts owned by a wallet",
)
def list_wallet_artifacts(
    wallet_pubkey: str = Query(..., min_length=32, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    wallet_pubkey = _validate_pubkey(wallet_pubkey)
    store = get_artifact_store()
    artifacts: list[dict[str, Any]] = []
    for run in store.list_runs(limit=500, offset=0):
        summary = run.get("summary") or {}
        if _wallet_owner(summary) == wallet_pubkey:
            artifacts.append(run)
    return {
        "wallet_pubkey": wallet_pubkey,
        "artifacts": artifacts[offset : offset + limit],
        "count": len(artifacts),
        "limit": limit,
        "offset": offset,
    }
