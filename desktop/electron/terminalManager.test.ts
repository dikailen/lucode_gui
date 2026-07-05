import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  ipcMain: {
    handle: vi.fn(),
    on: vi.fn(),
  },
}));

import { DesktopTerminalManager } from "./terminalManager.js";

type FakeSession = {
  sessionId: string;
  mode: "pipe";
  webContents: unknown;
  webContentsId: number;
  cwd: string;
  shell: string;
  process: { kill: () => void };
};

describe("DesktopTerminalManager", () => {
  it("launches Windows PowerShell with native profile behavior", () => {
    const manager = new DesktopTerminalManager({ workspaceRoot: "D:/workspace", env: {} });
    const shellArgs = (manager as unknown as { shellArgs(shell: string): string[] }).shellArgs;

    expect(shellArgs("powershell.exe")).toEqual(process.platform === "win32" ? ["-NoExit"] : []);
    expect(shellArgs("pwsh.exe")).toEqual(process.platform === "win32" ? ["-NoExit"] : []);
  });

  it("disposes sessions by cached webContents id without touching destroyed webContents", () => {
    const manager = new DesktopTerminalManager({ workspaceRoot: "D:/workspace", env: {} });
    const sessions = (manager as unknown as { sessions: Map<string, FakeSession> }).sessions;
    const kill = vi.fn();
    const destroyedWebContents = {
      get id() {
        throw new Error("object has been destroyed");
      },
    };

    sessions.set("terminal_1", {
      sessionId: "terminal_1",
      mode: "pipe",
      webContents: destroyedWebContents,
      webContentsId: 42,
      cwd: "D:/workspace",
      shell: "powershell.exe",
      process: { kill },
    });

    manager.disposeForWebContentsId(42);

    expect(kill).toHaveBeenCalledOnce();
    expect(sessions.has("terminal_1")).toBe(false);
  });
});
