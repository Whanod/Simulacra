"""Client-library conformance coverage for the Solana JSON-RPC shim.

These tests are intentionally opt-in because they start a local API server and
exercise external client libraries. Run them with
``RUN_SOLANA_RPC_CONFORMANCE=1`` in the dedicated conformance lane.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SOLANA_RPC_CONFORMANCE") != "1",
    reason="set RUN_SOLANA_RPC_CONFORMANCE=1 to run client-library conformance tests",
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend"
RPC_ENDPOINT_PATH = "/solana-rpc"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
SIM_BLOCKHASH = "DeFiSim111111111111111111111111111111111111"


@pytest.fixture(scope="module")
def solana_rpc_endpoint() -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "defi_sim_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, process)
        yield f"{base_url}{RPC_ENDPOINT_PATH}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"uvicorn exited before readiness: {stderr}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.25) as response:
                response.read()
            return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for uvicorn health endpoint: {last_error}")


def test_solana_py_basic_flow_works(solana_rpc_endpoint: str) -> None:
    from solana.rpc.api import Client
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    client = Client(solana_rpc_endpoint)

    slot = client.get_slot().value
    assert isinstance(slot, int)
    assert slot >= 420_196_842

    account = client.get_account_info(Pubkey.from_string(SYSTEM_PROGRAM))
    assert account.value is None

    program_accounts = client.get_program_accounts(Pubkey.from_string(SYSTEM_PROGRAM))
    assert program_accounts.value == []

    sender = Keypair()
    receiver = Keypair()
    instruction = transfer(
        TransferParams(
            from_pubkey=sender.pubkey(),
            to_pubkey=receiver.pubkey(),
            lamports=1,
        )
    )
    tx = Transaction(
        [sender],
        Message([instruction], sender.pubkey()),
        Hash.from_string(SIM_BLOCKHASH),
    )

    simulation = client.simulate_transaction(tx)

    assert simulation.value.err is None
    assert simulation.value.units_consumed is not None
    assert simulation.value.units_consumed > 0
    assert simulation.value.units_consumed != 200_000
    assert simulation.value.logs is not None
    assert any("landing_probability" in line for line in simulation.value.logs)


def test_web3_js_basic_flow_works(solana_rpc_endpoint: str) -> None:
    if shutil.which("bun") is None:
        pytest.fail("bun is required for @solana/web3.js conformance tests")

    script = """
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
} from "@solana/web3.js";

const endpoint = process.env.DEFISIM_SOLANA_RPC_ENDPOINT;
const systemProgram = new PublicKey(process.env.DEFISIM_SYSTEM_PROGRAM);
const blockhash = process.env.DEFISIM_SIM_BLOCKHASH;
const connection = new Connection(endpoint, "confirmed");

const slot = await connection.getSlot();
if (!Number.isInteger(slot) || slot < 420196842) {
  throw new Error(`unexpected slot ${slot}`);
}

const account = await connection.getAccountInfo(systemProgram);
if (account !== null) {
  throw new Error("expected missing system-program account in simulator fixture state");
}

const programAccounts = await connection.getProgramAccounts(systemProgram);
if (programAccounts.length !== 0) {
  throw new Error(`expected zero system-program accounts, got ${programAccounts.length}`);
}

const payer = Keypair.generate();
const receiver = Keypair.generate();
const tx = new Transaction({
  feePayer: payer.publicKey,
  recentBlockhash: blockhash,
}).add(
  SystemProgram.transfer({
    fromPubkey: payer.publicKey,
    toPubkey: receiver.publicKey,
    lamports: 1,
  }),
);
tx.sign(payer);

const simulation = await connection.simulateTransaction(tx);
if (simulation.value.err !== null) {
  throw new Error(`simulateTransaction returned err ${JSON.stringify(simulation.value.err)}`);
}
if (!Number.isInteger(simulation.value.unitsConsumed) || simulation.value.unitsConsumed <= 0) {
  throw new Error(`unexpected unitsConsumed ${simulation.value.unitsConsumed}`);
}
if (simulation.value.unitsConsumed === 200000) {
  throw new Error("simulateTransaction still returned the undecoded CU fallback");
}
if (!simulation.value.logs?.some((line) => line.includes("landing_probability"))) {
  throw new Error("simulateTransaction logs did not include landing_probability");
}

try {
  await connection.sendTransaction(tx, [payer]);
  throw new Error("sendTransaction unexpectedly succeeded");
} catch (error) {
  if (!String(error).includes("read-only")) {
    throw error;
  }
}
"""
    env = {
        **os.environ,
        "DEFISIM_SOLANA_RPC_ENDPOINT": solana_rpc_endpoint,
        "DEFISIM_SYSTEM_PROGRAM": SYSTEM_PROGRAM,
        "DEFISIM_SIM_BLOCKHASH": SIM_BLOCKHASH,
    }
    result = subprocess.run(
        ["bun", "--eval", script],
        cwd=FRONTEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
