import { ipcMain, type IpcMainInvokeEvent, type WebContents } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import os from "node:os";
import path from "node:path";

type TerminalCreateOptions = {
  cols?: number;
  rows?: number;
  cwd?: string;
};

type TerminalCreateResult = {
  sessionId: string;
  shell: string;
  cwd: string;
  mode: "pty" | "pipe";
  pid: number;
};

type TerminalDataPayload = {
  sessionId: string;
  data: string;
};

type TerminalExitPayload = {
  sessionId: string;
  exitCode: number;
  signal?: number | string;
};

type PtyProcess = {
  pid: number;
  write(data: string): void;
  resize(cols: number, rows: number): void;
  kill(): void;
  onData(handler: (data: string) => void): void;
  onExit(handler: (event: { exitCode: number; signal?: number | string }) => void): void;
};

type PtyModule = {
  spawn(
    file: string,
    args: string[],
    options: {
      name?: string;
      cols?: number;
      rows?: number;
      cwd?: string;
      env?: NodeJS.ProcessEnv;
    },
  ): PtyProcess;
};

type TerminalSession =
  | {
      sessionId: string;
      mode: "pty";
      webContents: WebContents;
      webContentsId: number;
      cwd: string;
      shell: string;
      process: PtyProcess;
    }
  | {
      sessionId: string;
      mode: "pipe";
      webContents: WebContents;
      webContentsId: number;
      cwd: string;
      shell: string;
      process: ChildProcessWithoutNullStreams;
    };

export type DesktopTerminalManagerOptions = {
  workspaceRoot: string;
  env?: NodeJS.ProcessEnv;
};

export class DesktopTerminalManager {
  private readonly workspaceRoot: string;
  private readonly env: NodeJS.ProcessEnv;
  private readonly sessions = new Map<string, TerminalSession>();
  private ptyModule: PtyModule | null | undefined;

  constructor(options: DesktopTerminalManagerOptions) {
    this.workspaceRoot = path.resolve(options.workspaceRoot);
    this.env = options.env ?? process.env;
  }

  registerIpc(): void {
    ipcMain.handle("lucode-terminal:create", (event, options: TerminalCreateOptions = {}) =>
      this.createSession(event, options),
    );
    ipcMain.on("lucode-terminal:write", (_event, payload: { sessionId?: string; data?: string }) => {
      this.write(String(payload?.sessionId || ""), String(payload?.data || ""));
    });
    ipcMain.on("lucode-terminal:resize", (_event, payload: { sessionId?: string; cols?: number; rows?: number }) => {
      this.resize(String(payload?.sessionId || ""), Number(payload?.cols || 0), Number(payload?.rows || 0));
    });
    ipcMain.on("lucode-terminal:kill", (_event, payload: { sessionId?: string }) => {
      this.kill(String(payload?.sessionId || ""));
    });
  }

  dispose(): void {
    for (const sessionId of this.sessions.keys()) {
      this.kill(sessionId);
    }
  }

  disposeForWebContents(webContents: WebContents): void {
    this.disposeForWebContentsId(webContents.id);
  }

  disposeForWebContentsId(webContentsId: number): void {
    for (const session of this.sessions.values()) {
      if (session.webContentsId === webContentsId) {
        this.kill(session.sessionId);
      }
    }
  }

  private async createSession(
    event: IpcMainInvokeEvent,
    options: TerminalCreateOptions,
  ): Promise<TerminalCreateResult> {
    const cwd = this.resolveCwd(options.cwd);
    const shell = this.shellPath();
    const args = this.shellArgs(shell);
    const sessionId = randomUUID();
    const cols = clampDimension(options.cols, 120);
    const rows = clampDimension(options.rows, 30);
    const pty = await this.loadPty();

    if (pty) {
      const process = pty.spawn(shell, args, {
        name: "xterm-256color",
        cols,
        rows,
        cwd,
        env: this.terminalEnv(),
      });
      const session: TerminalSession = {
        sessionId,
        mode: "pty",
        webContents: event.sender,
        webContentsId: event.sender.id,
        cwd,
        shell,
        process,
      };
      this.sessions.set(sessionId, session);
      process.onData((data) => this.sendData(session, data));
      process.onExit((exitEvent) => this.finishSession(session, exitEvent.exitCode, exitEvent.signal));
      return { sessionId, shell, cwd, mode: "pty", pid: process.pid };
    }

    const process = spawn(shell, args, {
      cwd,
      env: this.terminalEnv(),
      windowsHide: true,
    });
    const session: TerminalSession = {
      sessionId,
      mode: "pipe",
      webContents: event.sender,
      webContentsId: event.sender.id,
      cwd,
      shell,
      process,
    };
    this.sessions.set(sessionId, session);
    process.stdout.on("data", (chunk: Buffer | string) => this.sendData(session, chunk.toString()));
    process.stderr.on("data", (chunk: Buffer | string) => this.sendData(session, chunk.toString()));
    process.on("exit", (code, signal) => this.finishSession(session, code ?? 0, signal ?? undefined));
    return { sessionId, shell, cwd, mode: "pipe", pid: process.pid ?? 0 };
  }

  private write(sessionId: string, data: string): void {
    const session = this.sessions.get(sessionId);
    if (!session || !data) {
      return;
    }
    if (session.mode === "pty") {
      session.process.write(data);
      return;
    }
    session.process.stdin.write(data);
  }

  private resize(sessionId: string, cols: number, rows: number): void {
    const session = this.sessions.get(sessionId);
    if (!session || session.mode !== "pty") {
      return;
    }
    session.process.resize(clampDimension(cols, 120), clampDimension(rows, 30));
  }

  private kill(sessionId: string): void {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return;
    }
    this.sessions.delete(sessionId);
    try {
      session.process.kill();
    } catch {
      // Process is already gone.
    }
  }

  private sendData(session: TerminalSession, data: string): void {
    this.send(session.webContents, "lucode-terminal:data", {
      sessionId: session.sessionId,
      data,
    } satisfies TerminalDataPayload);
  }

  private finishSession(session: TerminalSession, exitCode: number, signal?: number | string): void {
    this.sessions.delete(session.sessionId);
    this.send(session.webContents, "lucode-terminal:exit", {
      sessionId: session.sessionId,
      exitCode,
      signal,
    } satisfies TerminalExitPayload);
  }

  private send(webContents: WebContents, channel: string, payload: TerminalDataPayload | TerminalExitPayload): void {
    try {
      if (webContents.isDestroyed()) {
        return;
      }
      webContents.send(channel, payload);
    } catch {
      // The renderer may already be destroyed while the terminal process is exiting.
    }
  }

  private async loadPty(): Promise<PtyModule | null> {
    if (this.ptyModule !== undefined) {
      return this.ptyModule;
    }
    try {
      const mod = (await import("node-pty")) as PtyModule | { default?: PtyModule };
      this.ptyModule = "spawn" in mod ? mod : mod.default || null;
    } catch (error) {
      console.warn(`Lucode terminal PTY unavailable, falling back to shell pipes: ${errorMessage(error)}`);
      this.ptyModule = null;
    }
    return this.ptyModule;
  }

  private resolveCwd(value: string | undefined): string {
    const candidate = path.resolve(value || this.workspaceRoot);
    if (isInside(candidate, this.workspaceRoot)) {
      return candidate;
    }
    return this.workspaceRoot;
  }

  private shellPath(): string {
    if (this.env.LUCODE_TERMINAL_SHELL) {
      return this.env.LUCODE_TERMINAL_SHELL;
    }
    if (process.platform === "win32") {
      return "powershell.exe";
    }
    return this.env.SHELL || (process.platform === "darwin" ? "/bin/zsh" : "/bin/bash");
  }

  private shellArgs(shell: string): string[] {
    const name = path.basename(shell).toLowerCase();
    if (
      process.platform === "win32" &&
      (name === "powershell.exe" || name === "powershell" || name === "pwsh.exe" || name === "pwsh")
    ) {
      return ["-NoExit"];
    }
    return [];
  }

  private terminalEnv(): NodeJS.ProcessEnv {
    return {
      ...this.env,
      TERM: this.env.TERM || "xterm-256color",
      COLORTERM: this.env.COLORTERM || "truecolor",
    };
  }
}

function clampDimension(value: unknown, fallback: number): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return fallback;
  }
  return Math.max(2, Math.min(Math.floor(numeric), 500));
}

function isInside(candidate: string, root: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
