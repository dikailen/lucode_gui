import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import type { PluginRuntimeCapability } from "../../shared/types";
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

function renderPanel(language: "zh" | "en", runtimeCapabilities: PluginRuntimeCapability[] = []) {
  return renderToStaticMarkup(
    createElement(PluginsPanel, {
      t: createTranslator(language),
      pluginState: pluginState(runtimeCapabilities),
      pluginError: "",
      pluginInstallingTarget: "",
      refreshPluginState: () => undefined,
      deleteSkill: () => undefined,
      installSkill: () => undefined,
      installMcp: () => undefined,
      registerExternalMcp: async () => true,
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
});
