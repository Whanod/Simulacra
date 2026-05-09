"""Minimal Python sample for `POST /v1/simulate-bundle`.

Standard library only; no defi-sim package import. Mirrors the `minimal`
example block in `simulate-bundle.openapi.yaml`.

Usage:
    DEFI_SIM_API_KEY=... python simulate_bundle.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = os.environ.get("DEFI_SIM_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("DEFI_SIM_API_KEY")


def simulate_bundle(
    txs: list[str],
    tip_lamports: int,
    tip_recipient: str,
    context_slot: int | str = "latest",
) -> dict:
    if not API_KEY:
        raise SystemExit("DEFI_SIM_API_KEY not set")

    payload = {
        "bundle": {
            "txs": txs,
            "tip_lamports": tip_lamports,
            "tip_recipient": tip_recipient,
        },
        "context_slot": context_slot,
    }
    req = urllib.request.Request(
        url=f"{API_URL}/v1/simulate-bundle",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} body={body}") from e


if __name__ == "__main__":
    result = simulate_bundle(
        txs=["base58encodedtx1", "base58encodedtx2"],
        tip_lamports=100_000,
        tip_recipient="T1pestRecipientPubkey11111111111111111111111",
        context_slot="latest",
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
