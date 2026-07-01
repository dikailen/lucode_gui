import { describe, expect, it, vi } from "vitest";

import { RuntimeLauncher, buildRuntimeServerArgs, waitForRuntimeHealth } from "./runtimeLauncher.js";

describe("runtimeLauncher", () => {
  it("reuses an existing runtime from environment without spawning Python", async () => {
    const spawnRuntime = vi.fn();
    const waitForHealth = vi.fn(async () => undefined);
    const launcher = new RuntimeLauncher({
      env: {
        LUCODE_RUNTIME_BASE_URL: "http://127.0.0.1:43217",
        LUCODE_RUNTIME_TOKEN: "token_1",
      },
      cwd: "D:/pycharm/code/lucode/desktop",
      spawnRuntime,
      findFreePort: async () => 45678,
      randomToken: () => "generated-token",
      waitForHealth,
    });

    await expect(launcher.ensureRuntime()).resolves.toMatchObject({
      baseUrl: "http://127.0.0.1:43217",
      token: "token_1",
      process: null,
      owned: false,
    });
    expect(spawnRuntime).not.toHaveBeenCalled();
    expect(waitForHealth).toHaveBeenCalledWith("http://127.0.0.1:43217", 8000);
  });

  it("spawns the Python runtime once with a generated token and workspace root", async () => {
    const child = { killed: false, kill: vi.fn() };
    const spawnRuntime = vi.fn(() => child);
    const waitForHealth = vi.fn(async () => undefined);
    const launcher = new RuntimeLauncher({
      env: {
        Path: "existing-path",
        LUCODE_WORKSPACE_ROOT: "D:/pycharm/code/lucode",
        LUCODE_PYTHON: "D:/Python/python.exe",
      },
      cwd: "D:/pycharm/code/lucode/desktop",
      spawnRuntime,
      findFreePort: async () => 45678,
      randomToken: () => "generated-token",
      waitForHealth,
    });

    const first = await launcher.ensureRuntime();
    const second = await launcher.ensureRuntime();

    expect(first).toBe(second);
    expect(first).toMatchObject({
      baseUrl: "http://127.0.0.1:45678",
      token: "generated-token",
      process: child,
      owned: true,
    });
    expect(spawnRuntime).toHaveBeenCalledOnce();
    expect(spawnRuntime).toHaveBeenCalledWith(
      "D:/Python/python.exe",
      ["-m", "runtime.server", "--workspace", "D:/pycharm/code/lucode", "--host", "127.0.0.1", "--port", "45678"],
      expect.objectContaining({
        cwd: "D:/pycharm/code/lucode",
        windowsHide: true,
        env: expect.objectContaining({
          LUCODE_RUNTIME_TOKEN: "generated-token",
        }),
      }),
    );
    expect(waitForHealth).toHaveBeenCalledWith("http://127.0.0.1:45678", 8000);
  });

  it("builds stable runtime server args", () => {
    expect(buildRuntimeServerArgs("D:/workspace", 45678)).toEqual([
      "-m",
      "runtime.server",
      "--workspace",
      "D:/workspace",
      "--host",
      "127.0.0.1",
      "--port",
      "45678",
    ]);
  });

  it("kills only owned child processes on stop", async () => {
    const child = { killed: false, kill: vi.fn() };
    const launcher = new RuntimeLauncher({
      env: {},
      cwd: "D:/pycharm/code/lucode/desktop",
      spawnRuntime: () => child,
      findFreePort: async () => 45678,
      randomToken: () => "generated-token",
      waitForHealth: async () => undefined,
    });

    await launcher.ensureRuntime();
    launcher.stopRuntime();

    expect(child.kill).toHaveBeenCalledOnce();
  });

  it("waits for runtime health until the server responds", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new Error("not ready"))
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true });
    const sleep = vi.fn(async () => undefined);

    await waitForRuntimeHealth("http://127.0.0.1:45678", 1000, {
      fetchImpl,
      sleep,
      now: makeClock([0, 100, 200]),
    });

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
  });
});

function makeClock(values: number[]) {
  let index = 0;
  return () => values[Math.min(index++, values.length - 1)];
}
