import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveRuntimeConfig } from "./runtimeEnv";

describe("runtimeEnv", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the Electron preload runtime config when available", () => {
    vi.stubGlobal("window", {
      lucodeRuntime: {
        baseUrl: "http://127.0.0.1:58256",
        token: "electron-token",
        rendererSource: "dev",
        buildTime: "2026-06-30T10:00:00.000Z",
      },
    });

    expect(resolveRuntimeConfig()).toEqual({
      baseUrl: "http://127.0.0.1:58256",
      token: "electron-token",
      rendererSource: "dev",
      buildTime: "2026-06-30T10:00:00.000Z",
    });
  });

  it("does not throw outside a browser-like test environment", () => {
    vi.stubGlobal("window", undefined);

    expect(resolveRuntimeConfig()).toMatchObject({
      baseUrl: expect.any(String),
      token: expect.any(String),
    });
  });
});
