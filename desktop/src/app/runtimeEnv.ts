import type { RuntimeConfig } from "../shared/types";

declare global {
  interface Window {
    lucodeRuntime?: RuntimeConfig;
    lucodeDesktop?: {
      droppedFilePaths?: (files: File[] | FileList) => string[];
    };
  }
}

export function resolveRuntimeConfig(): RuntimeConfig {
  const injected = typeof window === "undefined" ? undefined : window.lucodeRuntime;
  const viteEnv = importMetaEnv();
  return {
    baseUrl: injected?.baseUrl || stringEnv(viteEnv.VITE_RUNTIME_BASE_URL) || "http://127.0.0.1:43217",
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
