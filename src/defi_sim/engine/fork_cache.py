"""Parsed-``InitialState`` cache (PRD US-003 line 526).

Re-parsing 100k Whirlpool accounts on every load is the cost this cache
removes. The key is ``(slot, fork_spec_hash, hydrator_versions)``: bumping
any participating hydrator's ``schema_version`` invalidates every cached
entry that depends on it, so parser bugfixes propagate to dependent
calibration tests on the next CI run rather than silently serving stale
state.

This is *not* a raw-RPC cache — that role is filled by the LRU cache in
``defi_sim_solana.replay.account_client.get_program_accounts_at_slot``.
This cache stores the *parsed* :class:`InitialState` value object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from defi_sim.engine.fork import ForkSpec
from defi_sim.engine.initial_state import InitialState

if TYPE_CHECKING:
    from defi_sim.engine.fork_loader import ProtocolModelRegistry

__all__ = ["InitialStateCache", "cache_key"]


def cache_key(fork_spec: ForkSpec, registry: "ProtocolModelRegistry") -> str:
    """Stable SHA-256 hex digest for a ``(fork_spec, hydrator_versions)`` pair.

    The hash payload combines ``asdict(fork_spec)`` (so the slot, every
    ``ProtocolForkRequest``, and any wallet allowlist all participate) with
    a ``{protocol_model: schema_version}`` map looked up from ``registry``.
    A bump in any participating hydrator's ``schema_version`` therefore
    rotates the key automatically.

    ``json.dumps(..., sort_keys=True)`` makes the digest stable across dict
    insertion-order differences. Two specs that *order their protocols
    differently* still produce different digests because list order is
    preserved by ``json.dumps``; that is intentional — protocol order
    drives ``InitialState.protocols()`` and therefore materializer
    iteration, so two orderings are semantically distinct fork requests.
    """
    hydrator_versions = {
        req.protocol_model: registry.lookup(
            req.protocol_model
        ).state_hydrator.schema_version
        for req in fork_spec.protocols
    }
    payload = {
        "fork_spec": asdict(fork_spec),
        "hydrator_versions": hydrator_versions,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


class InitialStateCache:
    """In-process ``cache_key -> InitialState`` store.

    Intentionally minimal: ``get`` / ``put`` / ``clear`` over a plain dict.
    No eviction policy and no on-disk persistence — ``materialize_fork``
    owns this lifecycle for the run. The optional
    ``~/.defi_sim/forks/<cache_key>.json`` overlay mentioned in the PRD is
    a separate, opt-in persistence concern and lives outside this class.
    """

    def __init__(self) -> None:
        self._entries: dict[str, InitialState] = {}

    def get(self, key: str) -> InitialState | None:
        return self._entries.get(key)

    def put(self, key: str, value: InitialState) -> None:
        self._entries[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
