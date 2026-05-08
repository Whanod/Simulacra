import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import net from "node:net";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

function resolveUvicorn(): string {
  const fromEnv = process.env.DEFI_SIM_UVICORN;
  if (fromEnv && existsSync(fromEnv)) return fromEnv;
  const venvBin = path.join(REPO_ROOT, ".venv", "bin", "uvicorn");
  if (existsSync(venvBin)) return venvBin;
  return "uvicorn";
}

let apiProcess: ChildProcess | undefined;
let artifactRoot: string | undefined;

async function findFreePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (addr && typeof addr === "object") {
        const port = addr.port;
        server.close(() => resolve(port));
      } else {
        server.close();
        reject(new Error("Could not determine free port"));
      }
    });
  });
}

async function waitForHealth(baseUrl: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/health`);
      if (res.ok) return;
      lastErr = new Error(`health returned ${res.status}`);
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `Backend at ${baseUrl} did not become healthy in ${timeoutMs}ms: ${String(lastErr)}`,
  );
}

export async function setup(): Promise<void> {
  if (process.env.DEFI_SIM_INT_SKIP_SPAWN === "1") {
    if (!process.env.NEXT_PUBLIC_API_URL) {
      throw new Error(
        "DEFI_SIM_INT_SKIP_SPAWN=1 but NEXT_PUBLIC_API_URL is not set — set it to an already-running backend.",
      );
    }
    return;
  }

  const port = await findFreePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  artifactRoot = mkdtempSync(path.join(tmpdir(), "defi-sim-int-"));

  process.env.NEXT_PUBLIC_API_URL = baseUrl;
  process.env.DEFI_SIM_INT_API_URL = baseUrl;

  const uvicornBin = resolveUvicorn();
  apiProcess = spawn(
    uvicornBin,
    ["defi_sim_api.main:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        DEFI_SIM_ARTIFACT_ROOT: artifactRoot,
        CORS_ALLOWED_ORIGINS: "http://localhost:3000,http://127.0.0.1:3000",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  apiProcess.stdout?.on("data", (chunk) => {
    if (process.env.DEFI_SIM_INT_VERBOSE) process.stdout.write(`[api] ${chunk}`);
  });
  apiProcess.stderr?.on("data", (chunk) => {
    if (process.env.DEFI_SIM_INT_VERBOSE) process.stderr.write(`[api] ${chunk}`);
  });

  const exitPromise = new Promise<never>((_, reject) => {
    apiProcess?.on("exit", (code, signal) => {
      reject(
        new Error(`uvicorn exited early (code=${code}, signal=${signal}) before tests started`),
      );
    });
  });

  await Promise.race([waitForHealth(baseUrl), exitPromise]);
}

export async function teardown(): Promise<void> {
  if (apiProcess) {
    apiProcess.removeAllListeners("exit");
    apiProcess.kill("SIGTERM");
    await new Promise<void>((resolve) => {
      if (!apiProcess) return resolve();
      const timer = setTimeout(() => {
        apiProcess?.kill("SIGKILL");
        resolve();
      }, 3_000);
      apiProcess.on("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
    apiProcess = undefined;
  }
  if (artifactRoot) {
    rmSync(artifactRoot, { recursive: true, force: true });
    artifactRoot = undefined;
  }
}
