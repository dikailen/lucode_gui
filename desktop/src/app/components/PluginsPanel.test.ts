import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import type { ComfyUiStateResponse, PluginRuntimeCapability, PluginStateResponse } from "../../shared/types";
import { ComfyUiMcpRow, PluginsPanel } from "./PluginsPanel";

function pluginState(runtimeCapabilities: PluginRuntimeCapability[] = []): PluginStateResponse {
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
    installed_plugins: [],
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
    installation: {
      install_path: "D:\\develop\\ComfyUI_windows_portable_nvidia",
      resolved_root: "D:\\develop\\ComfyUI_windows_portable_nvidia\\ComfyUI_windows_portable",
      configured: true,
      valid: true,
      status: "launchable",
      launch_mode: "nvidia",
      launch_script: "run_nvidia_gpu.bat",
      launch_command: ".\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build",
      available_launch_scripts: ["run_nvidia_gpu.bat", "run_cpu.bat"],
      validation_errors: [],
    },
  };
}

function renderPanel(
  language: "zh" | "en",
  runtimeCapabilities: PluginRuntimeCapability[] = [],
  comfyUi: ComfyUiStateResponse | null = null,
  state = pluginState(runtimeCapabilities),
) {
  return renderToStaticMarkup(
    createElement(PluginsPanel, {
      t: createTranslator(language),
      pluginState: state,
      pluginError: "",
      pluginInstallingTarget: "",
      comfyUiState: comfyUi,
      comfyUiError: "",
      comfyUiBusy: false,
      refreshPluginState: () => undefined,
      deleteSkill: () => undefined,
      installSkill: () => undefined,
      installMcp: () => undefined,
      installPluginPackage: () => undefined,
      deletePluginPackage: () => undefined,
      registerExternalMcp: async () => true,
      refreshComfyUiState: () => undefined,
      saveComfyUiUrl: async () => true,
      detectComfyUiInstall: async () => true,
      checkComfyUi: () => undefined,
      openComfyUiInBrowser: () => undefined,
    }),
  );
}

function renderManagedComfyUiRow(language: "zh" | "en") {
  return renderToStaticMarkup(
    createElement(ComfyUiMcpRow, {
      t: createTranslator(language),
      url: "http://127.0.0.1:8188",
      state: comfyUiState("online"),
      error: "",
      busy: false,
      managed: true,
      onManage: () => undefined,
      onUrlChange: () => undefined,
      refresh: () => undefined,
      save: async () => true,
      detectInstall: async () => true,
      check: () => undefined,
      openBrowser: () => undefined,
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

  it("renders ComfyUI inside the MCP list instead of a separate optional plugin section", () => {
    const html = renderPanel("en", [], comfyUiState("online"));

    expect(html).toContain("ComfyUI MCP");
    expect(html).toContain("MCP template");
    expect(html).toContain("Online");
    expect(html).toContain("Launchable");
    expect(html).toContain("Manage");
    expect(html).not.toContain("Optional plugins");
    expect(html).not.toContain("Install directory");
    expect(html).not.toContain("Detect install");
    expect(html).not.toContain("Service URL");
    expect(html).not.toContain("D:\\develop\\ComfyUI_windows_portable_nvidia");
  });

  it("renders managed ComfyUI MCP as a compact row detail instead of the full connection card", () => {
    const html = renderManagedComfyUiRow("en");

    expect(html).toContain("ComfyUI MCP");
    expect(html).toContain("comfyui-mcp-compact-form");
    expect(html).toContain("Service URL");
    expect(html).toContain("Install directory");
    expect(html).toContain("Detect install");
    expect(html).toContain("Check connection");
    expect(html).toContain("Open right side");
    expect(html).not.toContain("ComfyUI connection");
    expect(html).not.toContain("comfyui-column");
    expect(html).not.toContain("comfyui-connection-form");
  });

  it("renders plugin package install entry and installed plugin rows", () => {
    const state = {
      ...pluginState([]),
      installed_plugins: [
        {
          id: "demo_plugin",
          title: "Demo Plugin",
          description: "Demo optional plugin.",
          skill_ids: ["demo_operator"],
          mcp_ids: ["demo_graph"],
          launch_profiles: [{ id: "windows_demo", label: "Windows Demo", script: "run_demo.bat" }],
          deletable: true,
        },
      ],
    };

    const html = renderPanel("zh", [], null, state);

    expect(html).toContain("插件包");
    expect(html).toContain("拖入插件包");
    expect(html).toContain("Demo Plugin");
    expect(html).toContain("Demo optional plugin.");
    expect(html).toContain("Skill 1");
    expect(html).toContain("MCP 1");
    expect(html).toContain("启动配置 1");
    expect(html).toContain("卸载");
  });
});
