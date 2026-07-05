import { spawn, type ChildProcessWithoutNullStreams, type SpawnOptionsWithoutStdio } from "node:child_process";
import crypto from "node:crypto";
import net from "node:net";
import path from "node:path";

export type RuntimeHandle = {
  baseUrl: string;
  token: string;
  process: KillableProcess | null;
  owned: boolean;
};

export type KillableProcess = {
  killed?: boolean;
  kill: () => unknown;
};

export type RuntimeLauncherOptions = {
  env?: NodeJS.ProcessEnv;
  cwd?: string;
  spawnRuntime?: (
    command: string,
    args: string[],
    options: SpawnOptionsWithoutStdio,
  ) => ChildProcessWithoutNullStreams | KillableProcess;
  findFreePort?: () => Promise<number>;
  randomToken?: () => string;
  waitForHealth?: (baseUrl: string, timeoutMs: number) => Promise<void>;
};

export class RuntimeLauncher {
  private readonly env: NodeJS.ProcessEnv;
  private readonly cwd: string;
  private readonly spawnRuntime: NonNullable<RuntimeLauncherOptions["spawnRuntime"]>;
  private readonly findFreePort: NonNullable<RuntimeLauncherOptions["findFreePort"]>;
  private readonly randomToken: NonNullable<RuntimeLauncherOptions["randomToken"]>;
  private readonly waitForHealth: NonNullable<RuntimeLauncherOptions["waitForHealth"]>;
  private runtimeHandle: RuntimeHandle | null = null;

  constructor(options: RuntimeLauncherOptions = {}) {
    this.env = options.env ?? process.env;
    this.cwd = options.cwd ?? process.cwd();
    this.spawnRuntime = options.spawnRuntime ?? spawn;
    this.findFreePort = options.findFreePort ?? findFreePort;
    this.randomToken = options.randomToken ?? (() => crypto.randomBytes(24).toString("hex"));
    this.waitForHealth = options.waitForHealth ?? waitForRuntimeHealth;
  }

  async ensureRuntime(): Promise<RuntimeHandle> {
    if (this.runtimeHandle) {
      return this.runtimeHandle;
    }

    const existingBaseUrl = normalizeBaseUrl(this.env.LUCODE_RUNTIME_BASE_URL || "");
    const existingToken = String(this.env.LUCODE_RUNTIME_TOKEN || "");
    if (existingBaseUrl && existingToken && !this.shouldSpawnRuntimeForDesktopBridge()) {
      await this.waitForHealth(existingBaseUrl, 8000);
      this.runtimeHandle = {
        baseUrl: existingBaseUrl,
        token: existingToken,
        process: null,
        owned: false,
      };
      return this.runtimeHandle;
    }

    const port = await this.findFreePort();
    const token = this.randomToken();
    const workspaceRoot = resolveWorkspaceRoot(this.env, this.cwd);
    const python = this.env.LUCODE_PYTHON || "python";
    const child = this.spawnRuntime(python, buildRuntimeServerArgs(workspaceRoot, port), {
      cwd: workspaceRoot,
      env: {
        ...this.env,
        LUCODE_RUNTIME_TOKEN: token,
      },
      windowsHide: true,
    });
    const baseUrl = `http://127.0.0.1:${port}`;
    this.runtimeHandle = {
      baseUrl,
      token,
      process: child,
      owned: true,
    };
    await this.waitForHealth(baseUrl, 8000);
    return this.runtimeHandle;
  }

  private shouldSpawnRuntimeForDesktopBridge(): boolean {
    const bridgeUrl = String(this.env.LUCODE_DESKTOP_BROWSER_BRIDGE_URL || "").trim();
    const bridgeToken = String(this.env.LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN || "").trim();
    if (!bridgeUrl || !bridgeToken) {
      return false;
    }
    return String(this.env.LUCODE_RUNTIME_REUSE_EXTERNAL || "").trim() !== "1";
  }

  stopRuntime(): void {
    const child = this.runtimeHandle?.owned ? this.runtimeHandle.process : null;
    if (child && !child.killed) {
      child.kill();
    }
  }
}

export function buildRuntimeServerArgs(workspaceRoot: string, port: number): string[] {
  return [
    "-m",
    "runtime.server",
    "--workspace",
    workspaceRoot,
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
  ];
}

export function resolveWorkspaceRoot(env: NodeJS.ProcessEnv, cwd: string): string {
  return env.LUCODE_WORKSPACE_ROOT || path.resolve(cwd, "..");
}

export function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (typeof address === "object" && address?.port) {
          resolve(address.port);
        } else {
          reject(new Error("Unable to allocate local runtime port."));
        }
      });
    });
  });
}

export async function waitForRuntimeHealth(
  baseUrl: string,
  timeoutMs: number,
  deps: {
    fetchImpl?: typeof fetch;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
  } = {},
): Promise<void> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? Date.now;
  const startedAt = now();
  let lastError: unknown;

  while (now() - startedAt < timeoutMs) {
    try {
      const response = await fetchImpl(`${baseUrl}/api/health`);
      if (response.ok) {
        return;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(120);
  }

  throw new Error(`Lucode Runtime Server failed to start: ${String(lastError || "timeout")}`);
}

function normalizeBaseUrl(value: string): string {
  const clean = String(value || "").trim();
  return clean.endsWith("/") ? clean.slice(0, -1) : clean;
}
