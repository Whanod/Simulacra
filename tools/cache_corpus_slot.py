"""Manual corpus-add tool for committing a Solana slot to the offline fixture corpus.

Per PRD US-001 line 199. Pulls ``getBlock(slot)`` and
``get_program_accounts_at_slot(program_id, slot)`` for each requested program,
writes minimized JSON.gz proof fixtures into
``solana-plans/calibration/corpus/<slot>/`` along with a placeholder
``manifest.yaml`` and a ``checksums.txt`` of the committed payloads. When
``ARTIFACT_STORAGE_URI`` or ``--artifact-storage-uri`` is configured, it also
stores raw payloads under checksum-addressed file URIs and records those URIs
in ``checksums.txt``.

Usage::

    python tools/cache_corpus_slot.py \\
        --slot 160_000_001 \\
        --programs whirLb...,LBUZKh... \\
        --historical-backend triton \\
        --out solana-plans/calibration/corpus/

The author then hand-fills the manifest's expected metric values and reviews
the git diff before committing. **No runtime CLI for ingesting arbitrary slot
ranges** — see PRD line 211. This is a one-off authoring tool.

The library function :func:`cache_slot_corpus` is the unit-testable surface;
the CLI wraps it with the wired-up default RPC clients (which currently raise
until the Phase-2 entry-gate provider is chosen).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from defi_sim_solana.replay.account_client import HistoricalAccountBackend
from defi_sim_solana.replay.slot_client import SolanaClient

__all__ = ["cache_slot_corpus", "main"]

ARTIFACT_STORAGE_ENV = "ARTIFACT_STORAGE_URI"


def _serialize_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _gzip_payload(payload: dict[str, Any]) -> bytes:
    return gzip.compress(_serialize_json(payload), mtime=0)


def _write_json_gz(path: Path, payload: dict[str, Any]) -> None:
    serialized = _gzip_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialized)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _upload_raw_payload(
    *,
    storage_uri: str | None,
    slot: int,
    logical_name: str,
    payload: dict[str, Any],
) -> tuple[str, str] | None:
    """Store a raw payload under a checksum-addressed URI when configured.

    Phase 2 intentionally keeps only small proof fixtures in git. This helper
    supports local/file URI artifact storage for corpus authoring and tests;
    unsupported schemes fail closed instead of fabricating external URIs.
    """
    if not storage_uri:
        return None
    raw_bytes = _gzip_payload(payload)
    checksum = _sha256_bytes(raw_bytes)
    parsed = urlparse(storage_uri)
    if parsed.scheme in ("", "file"):
        root = (
            Path(parsed.path)
            if parsed.scheme == "file"
            else Path(storage_uri)
        ).expanduser()
        target = (
            root.resolve()
            / "solana-corpus-raw"
            / str(slot)
            / f"{logical_name}-{checksum}.json.gz"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_bytes)
        return checksum, target.as_uri()
    raise ValueError(
        f"unsupported artifact storage URI scheme {parsed.scheme!r}; "
        "use a local path or file:// URI for this tool"
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit_manifest_yaml(
    slot: int,
    programs: list[str],
    block_payload: dict[str, Any],
) -> str:
    """Emit a placeholder manifest.yaml for the author to fill in.

    Hand-rolled YAML — no PyYAML dep. Only scalars + lists are emitted, all
    keys are quoted to avoid YAML reserved-word surprises.
    """
    tx_count = len(block_payload.get("transactions") or ())
    lines: list[str] = [
        "# Corpus manifest for slot " + str(slot) + ".",
        "# Hand-fill expected_* values after reviewing the committed fixtures.",
        "# `category` MUST be one of the StressCategory values (high_volume_dex,",
        "# liquidation_cascade, token_launch, bundle_auction_contended,",
        "# perp_stress, steady_state) for real calibration fixtures, or",
        "# `synthetic` for hand-crafted parser fixtures. The snapshotter",
        "# (FIX-019) overwrites this with the qualifying real category when it",
        "# captures a slot.",
        "",
        f'slot: {slot}',
        "category: # CALIBRATE-2.4: set to <stress_category> | synthetic",
        f'block_height: {int(block_payload.get("blockHeight") or 0)}',
        f'parent_slot: {int(block_payload.get("parentSlot") or 0)}',
        f'blockhash: "{block_payload.get("blockhash") or ""}"',
        "programs:",
    ]
    for pid in programs:
        lines.append(f'  - "{pid}"')
    lines.extend(
        [
            "expected:",
            f"  tx_count: {tx_count}     # observed at fixture-write time; CALIBRATE-2.4",
            "  decoded_coverage: null  # fill after decoders ship (PRD line 194)",
            "  pool_reserves: {}       # CALIBRATE-2.3 -- per-pool address -> [reserve_a, reserve_b]",
            "thresholds: {}",
            "",
        ]
    )
    return "\n".join(lines)


def cache_slot_corpus(
    slot: int,
    programs: list[str],
    *,
    out_dir: Path,
    slot_client: SolanaClient,
    account_backend: HistoricalAccountBackend | None = None,
    artifact_storage_uri: str | None = None,
) -> dict[str, Path]:
    """Pull ``slot`` + each program's accounts and write the corpus fixture set.

    ``slot_client`` provides ``getBlock(slot)``. ``account_backend`` provides
    historical ``get_program_accounts_at_slot(program_id, slot)``; required when
    ``programs`` is non-empty. ``out_dir`` is the corpus root (typically
    ``solana-plans/calibration/corpus/``); a ``<slot>/`` subdirectory is created
    underneath it.

    ``artifact_storage_uri`` may be a local path or ``file://`` URI. When set,
    raw slot/account payloads are also written there under checksum-addressed
    filenames and the external URIs are recorded in ``checksums.txt``.

    Returns a mapping of logical names to written paths so callers can inspect
    / log the committed proof fixture set.
    """
    if programs and account_backend is None:
        raise ValueError(
            "account_backend is required when programs are requested; "
            "pass --historical-backend on the CLI or wire account_backend= in tests."
        )

    slot_dir = out_dir / str(slot)
    slot_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    storage_uri = artifact_storage_uri or os.environ.get(ARTIFACT_STORAGE_ENV)
    artifact_records: list[tuple[str, str, str]] = []

    block_payload = slot_client.get_block(slot)
    block_path = slot_dir / "block.json.gz"
    _write_json_gz(block_path, block_payload)
    written["block"] = block_path
    raw_block = _upload_raw_payload(
        storage_uri=storage_uri,
        slot=slot,
        logical_name="block",
        payload=block_payload,
    )
    if raw_block is not None:
        artifact_records.append(("block.raw.json.gz", raw_block[0], raw_block[1]))

    for program_id in programs:
        assert account_backend is not None  # narrowed above
        payload = account_backend.get_program_accounts_at_slot(program_id, slot)
        path = slot_dir / f"program_accounts-{program_id}.json.gz"
        _write_json_gz(path, payload)
        written[f"program_accounts-{program_id}"] = path
        raw_program = _upload_raw_payload(
            storage_uri=storage_uri,
            slot=slot,
            logical_name=f"program_accounts-{program_id}",
            payload=payload,
        )
        if raw_program is not None:
            artifact_records.append(
                (
                    f"program_accounts-{program_id}.raw.json.gz",
                    raw_program[0],
                    raw_program[1],
                )
            )

    manifest_path = slot_dir / "manifest.yaml"
    manifest_path.write_text(_emit_manifest_yaml(slot, programs, block_payload), encoding="utf-8")
    written["manifest"] = manifest_path

    checksums_path = slot_dir / "checksums.txt"
    checksum_lines = [
        f"{_sha256(p)}  {p.name}"
        for p in sorted(written.values())
        if p.name != "checksums.txt"
    ]
    checksum_lines.extend(
        f"{checksum}  {name}  {uri}"
        for name, checksum, uri in artifact_records
    )
    checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    written["checksums"] = checksums_path

    return written


def _parse_programs(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Commit a Solana slot to the offline corpus fixture set.",
    )
    parser.add_argument("--slot", type=int, required=True, help="Mainnet slot number.")
    parser.add_argument(
        "--programs",
        type=str,
        default="",
        help="Comma-separated program IDs to snapshot (e.g. whirLb...,LBUZKh...).",
    )
    parser.add_argument(
        "--historical-backend",
        type=str,
        default=None,
        help="Identifier of the historical-account backend (informational; backend wiring "
        "happens via default_historical_backend()).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("solana-plans/calibration/corpus/"),
        help="Corpus root directory.",
    )
    parser.add_argument(
        "--artifact-storage-uri",
        type=str,
        default=os.environ.get(ARTIFACT_STORAGE_ENV),
        help=(
            "Optional raw-payload artifact storage path/URI. Defaults to "
            f"{ARTIFACT_STORAGE_ENV} when set."
        ),
    )
    args = parser.parse_args(argv)

    from defi_sim_solana.replay.account_client import default_historical_backend
    from defi_sim_solana.replay.slot_client import default_client

    programs = _parse_programs(args.programs)
    slot_client = default_client()
    account_backend = default_historical_backend() if programs else None

    written = cache_slot_corpus(
        slot=args.slot,
        programs=programs,
        out_dir=args.out,
        slot_client=slot_client,
        account_backend=account_backend,
        artifact_storage_uri=args.artifact_storage_uri,
    )
    for name, path in written.items():
        print(f"wrote {name}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
