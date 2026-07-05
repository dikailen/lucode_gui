import { describe, expect, it } from "vitest";
import type { UserConfig } from "vite";

import config from "../../vite.config";

describe("vite development server config", () => {
  it("proxies runtime HTTP and websocket requests through the dev origin", () => {
    const resolved = config as UserConfig;
    const proxy = resolved.server?.proxy;
    const apiProxy = typeof proxy === "object" && !Array.isArray(proxy) ? proxy["/api"] : undefined;

    expect(apiProxy).toMatchObject({
      target: "http://127.0.0.1:43217",
      changeOrigin: true,
      ws: true,
      secure: false,
    });
  });
});
