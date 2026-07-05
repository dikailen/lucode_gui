import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import type { ComfyUiStateResponse, PluginRuntimeCapability } from "../../shared/types";
import { PluginsPanel } from "./PluginsPanel";

function pluginState(runtimeCapabilities: PluginRuntimeCapability[] = []) {
  return {
    schema_version: "plugin_state.v1" as const,
    skills: [
      {
        id: "skill.one",
        title: "Skill One",
        description: "Test skill",
        chips: ["核心"],
        core: true,
        deletable: false,
      },
    ],
    mcp: [
      {
        id: "mcp.one",
        title: "MCP One",
        status: "已连接",
        detail: "Test MCP",
      },
    ],
    runtime_capabilities: runtimeCapabilities,
  };
}

function comfyUiState(status: ComfyUiStateResponse["status"] = "unknown"): ComfyUiStateResponse {
  return {
    schema_version: "comfyui.v1",
    base_url: "http://127.0.0.1:8188",
    configured: true,
    status,
    last_error: "",
    checked_at: status === "unknown" ? "" : "2026-07-05T10:00:00Z",
    endpoints: status === "online" ? { system_stats: true, queue: true } : {},
  };
}

function renderPanel(
  language: "zh" | "en",
  runtimeCapabilities: PluginRuntimeCapability[] = [],
  comfyUi: ComfyUiStateResponse | null = null,
) {
  return renderToStaticMarkup(
    createElement(PluginsPanel, {
      t: createTranslator(language),
      pluginState: pluginState(runtimeCapabilities),
      pluginError: "",
      pluginInstallingTarget: "",
      comfyUiState: comfyUi,
      comfyUiError: "",
      comfyUiBusy: false,
      refreshPluginState: () => undefined,
      deleteSkill: () => undefined,
      installSkill: () => undefined,
      installMcp: () => undefined,
      registerExternalMcp: async () => true,
      refreshComfyUiState: () => undefined,
      saveComfyUiUrl: async () => true,
      checkComfyUi: () => undefined,
      openComfyUiInBrowser: () => undefined,
    }),
  );
}

describe("PluginsPanel runtime capability section", () => {
  it("hides desktop runtime capabilities when the payload does not expose them", () => {
    const html = renderPanel("zh");

    expect(html).not.toContain("运行时能力");
    expect(html).not.toContain("desktop_browser");
  });

  it("shows desktop browser capability details from the runtime payload", () => {
    const html = renderPanel("zh", [
      {
        id: "desktop_browser",
        display_name: "桌面内置浏览器",
        summary: "Operate the embedded desktop browser through a local authenticated bridge. DOM actions require approval.",
        summary_zh: "通过本地认证桥操作 Electron 内置浏览器，可读页面摘要并执行受控点击、填表、提交。",
        surface: "desktop",
        status_key: "desktop_runtime",
        ability_keys: ["navigate", "page_summary", "controlled_click", "form_input", "form_submit"],
        risk_key: "approval_required",
      },
    ]);

    expect(html).toContain("运行时能力");
    expect(html).toContain("desktop_browser");
    expect(html).toContain("桌面运行时能力");
    expect(html).toContain("导航");
    expect(html).toContain("页面摘要");
    expect(html).toContain("受控点击");
    expect(html).toContain("填表");
    expect(html).toContain("提交");
    expect(html).toContain("页面操作需审批");
  });

  it("renders the same capability section in English when the UI language is English", () => {
    const html = renderPanel("en", [
      {
        id: "desktop_browser",
        display_name: "桌面内置浏览器",
        summary: "Operate the embedded desktop browser through a local authenticated bridge. DOM actions require approval.",
        summary_zh: "通过本地认证桥操作 Electron 内置浏览器，可读页面摘要并执行受控点击、填表、提交。",
        surface: "desktop",
        status_key: "desktop_runtime",
        ability_keys: ["navigate", "page_summary", "controlled_click", "form_input", "form_submit"],
        risk_key: "approval_required",
      },
    ]);

    expect(html).toContain("Runtime capabilities");
    expect(html).toContain("Desktop runtime capability");
    expect(html).toContain("Navigation");
    expect(html).toContain("Page summary");
    expect(html).toContain("Controlled click");
    expect(html).toContain("Form input");
    expect(html).toContain("Form submit");
    expect(html).toContain("Page actions require approval");
  });

  it("renders a compact ComfyUI connection card with actionable controls", () => {
    const html = renderPanel("en", [], comfyUiState("online"));

    expect(html).toContain("ComfyUI connection");
    expect(html).toContain("http://127.0.0.1:8188");
    expect(html).toContain("Online");
    expect(html).toContain("Check connection");
    expect(html).toContain("Open right side");
  });
});
