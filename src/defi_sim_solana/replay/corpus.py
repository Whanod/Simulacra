"""Offline corpus fixture loader for Solana mainnet replay.

Reads pre-committed minimized fixtures so CI / dev runs that target known slots
do not have to hit live RPC. When a fixture is absent, returns ``None`` and the
caller falls back to a live client.

Layout (per PRD line 178-188)::

    solana-plans/calibration/corpus/
      <slot>/
        block.json[.gz]
        program_accounts-<program_id>.json[.gz]
        ...

Both ``.json`` and ``.json.gz`` are accepted; ``.json.gz`` wins when both exist
(small fixtures are typically committed gzipped).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

__all__ = ["corpus_root", "load_corpus_fixture"]


def corpus_root() -> Path:
    """Return the on-disk root of the slot-fixture corpus.

    Resolves to ``<repo_root>/solana-plans/calibration/corpus/``.
    """
    return Path(__file__).resolve().parents[3] / "solana-plans" / "calibration" / "corpus"


def load_corpus_fixture(
    slot: int,
    kind: str,
    program_id: str | None = None,
) -> dict | None:
    """Load a committed fixture for ``slot``/``kind`` (optionally scoped to ``program_id``).

    Returns the parsed JSON object, or ``None`` if no matching file is committed.
    """
    slot_dir = corpus_root() / str(slot)
    if not slot_dir.is_dir():
        return None

    stem = kind if program_id is None else f"{kind}-{program_id}"
    gz_path = slot_dir / f"{stem}.json.gz"
    json_path = slot_dir / f"{stem}.json"

    if gz_path.is_file():
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    if json_path.is_file():
        with json_path.open("rt", encoding="utf-8") as fh:
            return json.load(fh)
    return None
