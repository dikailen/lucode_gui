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

  it("uses the current Vite origin in browser dev mode so API calls go through the proxy", () => {
    vi.stubGlobal("window", {
      location: {
        origin: "http://127.0.0.1:5173",
      },
    });

    expect(resolveRuntimeConfig()).toMatchObject({
      baseUrl: "http://127.0.0.1:5173",
      token: "dev-runtime-token",
      rendererSource: "dev",
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
