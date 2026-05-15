"""Shared owner-scoping helpers for Privy-authenticated routers.

The plan is to keep each router's diff small: a single ``Depends(current_user)``
on the route function plus one of these helpers wherever an artifact is
listed or fetched. Storage stays policy-free; the policy lives here.

Open-mode contract (``PRIVY_APP_ID`` and ``DEFI_SIM_API_KEYS`` both unset):
every helper short-circuits to "no filter / no check", so the existing
test suite and local-dev experience are unchanged.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, status

from defi_sim_api.auth import User, auth_enforced


class _OwnerLookup(Protocol):
    def __call__(self, ident: str) -> str | None:  # pragma: no cover — typing only
        ...


def list_owner_filter(user: User) -> str | None:
    """Return the ``owner_id`` to forward into ``list_*`` / ``count_*``.

    * Open mode → ``None`` (unfiltered, the historical behaviour).
    * Enforced + signed-in → the caller's DID (strict per-user scoping).
    * Enforced + anonymous (cookie / API-key) → the caller's DID is
      ``None``, which would unfilter the list. We return a sentinel
      (``"\x00no-owner\x00"``) so the SQL filter matches zero rows
      instead of leaking everyone's data. API-key callers should not be
      hitting per-user list endpoints; if they are, they get an empty
      list rather than another user's runs.
    """
    if not auth_enforced():
        return None
    if user.id is None:
        return "\x00no-owner\x00"
    return user.id


def assert_visible(
    owner_id: str | None,
    user: User,
    *,
    not_found_detail: str,
) -> None:
    """Raise 404 when ``owner_id`` is set and doesn't match the caller.

    ``owner_id`` is the value persisted on the row. The contract:

    * Open mode (no auth configured) → no check at all. Even if a row
      carries an ``owner_id`` (e.g. it was written when the server *was*
      enforced and got pulled back into a dev-mode pool), the caller can
      still read it. This keeps the local-dev contract and the existing
      golden suite intact.
    * ``owner_id is None`` → row was created without an owner (anon /
      API-key / open-mode write). Visible to everyone, including
      signed-in users on enforced servers — this is what makes share-link
      reads and legacy data accessible.
    * Equal to ``user.id`` → row belongs to the caller. Visible.
    * Anything else → 404 (not 403 — don't leak existence).
    """
    if not auth_enforced():
        return
    if owner_id is None:
        return
    if user.id == owner_id:
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)


def owner_for_create(user: User) -> str | None:
    """Return the ``owner_id`` to persist on a new artifact.

    Open mode and anonymous (cookie / API-key) writes always land with
    NULL ownership so existing flows keep working unchanged. Signed-in
    Privy callers tag rows with their DID.
    """
    return user.id
