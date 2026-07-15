import type { DesktopBrowserBridge, DesktopTerminalBridge, RuntimeConfig } from "../shared/types";

declare global {
  interface Window {
    lucodeRuntime?: RuntimeConfig;
    lucodeDesktop?: {
      choosePluginSource?: (kind: "skill" | "mcp" | "package") => Promise<string>;
      chooseAttachmentFiles?: () => Promise<string[]>;
      droppedFilePath?: (file: File) => string;
    };
    lucodeTerminal?: DesktopTerminalBridge;
    lucodeBrowser?: DesktopBrowserBridge;
  }
}

export function resolveRuntimeConfig(): RuntimeConfig {
  const injected = typeof window === "undefined" ? undefined : window.lucodeRuntime;
  const viteEnv = importMetaEnv();
  const explicitBaseUrl = injected?.baseUrl || stringEnv(viteEnv.VITE_RUNTIME_BASE_URL);
  return {
    baseUrl: explicitBaseUrl || devServerOrigin(viteEnv) || "http://127.0.0.1:43217",
    token: injected?.token || stringEnv(viteEnv.VITE_RUNTIME_TOKEN) || "dev-runtime-token",
    rendererSource: injected?.rendererSource || (viteEnv.DEV === true ? "dev" : "dist"),
    buildTime: injected?.buildTime || "",
  };
}

function importMetaEnv(): Record<string, unknown> {
  return ((import.meta as ImportMeta & { env?: Record<string, unknown> }).env ?? {}) as Record<string, unknown>;
}

function stringEnv(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function devServerOrigin(viteEnv: Record<string, unknown>): string {
  if (viteEnv.DEV !== true || typeof window === "undefined") {
    return "";
  }
  const origin = window.location?.origin;
  return typeof origin === "string" && origin ? origin : "";
}
