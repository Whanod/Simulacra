"""Persist a parsed ``InitialState`` as a re-runnable fork artifact (PRD US-003 line 642).

A "saved fork" is a point artifact: the user explicitly chose a
``(slot, protocols, allowlist)`` triple and wants to reuse that exact parsed
state as a starting point for many runs. The on-disk format is a small,
human-inspectable JSON document — gzip-compressed when the payload exceeds
``gzip_threshold_bytes`` — written under ``<root>/forks/<cache_key>.json``
(or ``.json.gz``).

This is *not* an analytics input — Parquet / pyarrow / column-store paths
are explicitly out of scope (the PRD called out the earlier-draft mistake
of treating saved forks as warehouse rows). It is also not a substitute
for the in-process :class:`InitialStateCache`: that cache exists to skip
re-parsing during a single run; this artifact exists to share a saved
fork across runs and across users.

The schema version (``schema = "fork_initial_state.v1"``) is pinned so a
later format change can be detected and rejected loudly rather than
silently mis-parsed.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from defi_sim.engine.fork import ForkSpec
from defi_sim.engine.fork_cache import cache_key
from defi_sim.engine.fork_loader import ProtocolModelRegistry
from defi_sim.engine.initial_state import (
    FragmentKind,
    InitialState,
    InitialStateFragment,
)

__all__ = [
    "FORK_ARTIFACT_SCHEMA",
    "ForkArtifactError",
    "load_fork_initial_state",
    "save_fork_initial_state",
]


FORK_ARTIFACT_SCHEMA = "fork_initial_state.v1"
_FORKS_SUBDIR = "forks"
_DEFAULT_GZIP_THRESHOLD_BYTES = 8 * 1024


class ForkArtifactError(RuntimeError):
    """Raised when a fork artifact is malformed or schema-incompatible."""


def save_fork_initial_state(
    initial: InitialState,
    fork_spec: ForkSpec,
    registry: ProtocolModelRegistry,
    root: Path,
    *,
    gzip_threshold_bytes: int = _DEFAULT_GZIP_THRESHOLD_BYTES,
) -> Path:
    """Write ``initial`` to ``<root>/forks/<cache_key>.json[.gz]``.

    Returns the path of the written file. Gzip is applied only when the
    encoded JSON exceeds ``gzip_threshold_bytes`` so small forks remain
    diffable and grep-able in source trees.
    """
    key = cache_key(fork_spec, registry)
    payload = {
        "schema": FORK_ARTIFACT_SCHEMA,
        "cache_key": key,
        "slot": initial.slot,
        "fragments": [_fragment_to_dict(f) for f in initial.fragments],
    }
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    forks_dir = Path(root) / _FORKS_SUBDIR
    forks_dir.mkdir(parents=True, exist_ok=True)
    if len(encoded) > gzip_threshold_bytes:
        out_path = forks_dir / f"{key}.json.gz"
        with gzip.open(out_path, "wb") as fh:
            fh.write(encoded)
    else:
        out_path = forks_dir / f"{key}.json"
        out_path.write_bytes(encoded)
    return out_path


def load_fork_initial_state(path: Path) -> InitialState:
    """Read a saved fork artifact and reconstruct its :class:`InitialState`.

    Accepts either the ``.json`` or ``.json.gz`` form (chosen by suffix).
    Raises :class:`ForkArtifactError` when the schema tag is missing or
    does not match :data:`FORK_ARTIFACT_SCHEMA`.
    """
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rb") as fh:
            raw = fh.read()
    else:
        raw = p.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    schema = payload.get("schema")
    if schema != FORK_ARTIFACT_SCHEMA:
        raise ForkArtifactError(
            f"unsupported fork-artifact schema {schema!r} at {p}; "
            f"expected {FORK_ARTIFACT_SCHEMA!r}"
        )
    fragments = [_fragment_from_dict(d) for d in payload["fragments"]]
    return InitialState(slot=int(payload["slot"]), fragments=fragments)


def _fragment_to_dict(f: InitialStateFragment) -> dict:
    return {
        "kind": f.kind,
        "protocol_model": f.protocol_model,
        "pubkey": f.pubkey,
        "owner": f.owner,
        "payload": dict(f.payload),
    }


def _fragment_from_dict(d: dict) -> InitialStateFragment:
    kind: FragmentKind = d["kind"]
    return InitialStateFragment(
        kind=kind,
        protocol_model=d["protocol_model"],
        pubkey=d["pubkey"],
        owner=d.get("owner"),
        payload=d.get("payload", {}),
    )
