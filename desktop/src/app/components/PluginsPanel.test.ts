import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import { resolveDroppedFilePaths } from "../localFileDrop";
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
      applySkillMetadata: async () => true,
      setSkillEnabled: async () => true,
      reindexSkillLibrary: async () => true,
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

function incompleteWorkspaceSkillState(): PluginStateResponse {
  return {
    ...pluginState(),
    skill_library: [
      {
        id: "humanizer_zh_main",
        name: "Humanizer zh",
        summary: "Remove repetitive AI writing patterns from Chinese prose.",
        source: "workspace",
        editable_metadata: true,
        category: ["documentation"],
        tags: ["writing"],
        use_when: ["Edit Chinese prose"],
        do_not_use_when: ["Run shell commands"],
        negative_queries: [],
        distinguish_from: {},
        risk_level: "low",
        enabled: true,
        core: false,
        assignable: true,
        metadata_status: "incomplete",
        missing_fields: ["categories"],
        usage: {},
        suggestion: {
          categories: ["documentation"],
          tags: ["writing"],
          use_when: ["Edit Chinese prose"],
          do_not_use_when: ["Run shell commands"],
          negative_queries: [],
          distinguish_from: {},
          source_count: 1,
        },
      },
    ],
  } as PluginStateResponse;
}

describe("PluginsPanel Skill library management", () => {
  it("renders compact workspace enablement and reindex controls without exposing them for core Skills", () => {
    const state = {
      ...pluginState([]),
      skill_library: [
        {
          id: "release_review",
          name: "Release Review",
          summary: "Validate releases.",
          source: "workspace",
          editable_metadata: true,
          category: ["programming"],
          tags: ["release"],
          use_when: ["Review releases"],
          do_not_use_when: ["Write product copy"],
          negative_queries: [],
          distinguish_from: {},
          risk_level: "low",
          enabled: true,
          core: false,
          assignable: true,
          metadata_status: "ready",
          missing_fields: [],
          usage: {},
          suggestion: { categories: [], tags: [], use_when: [], do_not_use_when: [], negative_queries: [], distinguish_from: {}, source_count: 0 },
        },
      ],
    } as PluginStateResponse;

    const html = renderPanel("en", [], null, state);

    expect(html).toContain("Reindex library");
    expect(html).toContain('aria-label="Skill enabled"');
    expect(html).toContain("Enabled");
    expect(html).not.toContain("Core Skill enabled");
  });

  it("renders the Skill library as a compact toolbar with horizontal filters and collapsed advice", () => {
    const html = renderPanel("en", [], null, incompleteWorkspaceSkillState());

    expect(html).toContain('class="skill-library-toolbar"');
    expect(html).toContain('class="skill-library-categories skill-library-filters"');
    expect(html).toContain("skill-library-suggestion-toggle");
    expect(html).toContain("View and edit");
    expect(html).not.toContain('class="skill-library-advice-editor"');
  });
});

describe("PluginsPanel local file drops", () => {
  it("asks the desktop bridge for each dropped File instead of passing a FileList across the bridge", () => {
    const files = [{ name: "skill-folder" }, { name: "mcp.json" }] as File[];
    const resolvePath = (file: File) => `D:/drop/${file.name}`;

    expect(resolveDroppedFilePaths(files, resolvePath)).toEqual([
      "D:/drop/skill-folder",
      "D:/drop/mcp.json",
    ]);
  });
});

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

  it("renders desktop runtime capabilities as compact summary strips", () => {
    const html = renderPanel("en", [
      {
        id: "desktop_browser",
        display_name: "Desktop browser",
        summary: "Operate the embedded browser.",
        summary_zh: "操作内置浏览器。",
        surface: "desktop",
        status_key: "desktop_runtime",
        ability_keys: ["navigate", "page_summary"],
        risk_key: "approval_required",
      },
    ]);

    expect(html).toContain('class="runtime-capability-strip"');
    expect(html).not.toContain('class="runtime-capability-card"');
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
  it("renders metadata with pending tuning advice collapsed by default", () => {
    const state = {
      ...pluginState([]),
      skill_library: [
        {
          id: "release_review",
          name: "Release Review",
          summary: "Validate a release before publishing it.",
          source: "workspace",
          editable_metadata: true,
          category: ["programming", "testing"],
          tags: ["release", "regression"],
          use_when: ["Review release changes"],
          do_not_use_when: ["Writing product copy"],
          negative_queries: ["Write a marketing announcement"],
          distinguish_from: { documentation: "Use documentation planning for prose-only tasks." },
          risk_level: "unknown",
          enabled: true,
          core: false,
          assignable: true,
          metadata_status: "ready",
          missing_fields: [],
          usage: { selected: 4 },
          suggestion: {
            categories: ["tools/browser"],
            negative_queries: ["Draft a product launch"],
            distinguish_from: { documentation: "Use documentation planning for prose-only tasks." },
            source_count: 2,
          },
        },
      ],
    } as PluginStateResponse;

    const html = renderPanel("en", [], null, state);

    expect(html).toContain("Skill library");
    expect(html).toContain("Release Review");
    expect(html).toContain("Review release changes");
    expect(html).toContain("Writing product copy");
    expect(html).toContain("Pending metadata advice");
    expect(html).toContain("View and edit");
    expect(html).not.toContain("Browser");
    expect(html).not.toContain("Draft a product launch");
    expect(html).not.toContain("skill-library-advice-editor");
    expect(html).not.toContain("body_path");
  });

  it("keeps Skill installation in the library instead of duplicating legacy Skill cards", () => {
    const html = renderPanel("en");

    expect(html).toContain('aria-label="Skill library"');
    expect(html).toContain('aria-label="Import"');
    expect(html).not.toContain('aria-label="Skill list"');
    expect(html).not.toContain("Skill One");
  });

  it("keeps an imported incomplete Skill in a collapsed metadata completion state", () => {
    const state = {
      ...pluginState([]),
      skill_library: [
        {
          id: "humanizer_zh_main",
          name: "Humanizer",
          summary: "Rewrite text to remove generic AI writing patterns.",
          source: "workspace",
          editable_metadata: true,
          category: [],
          tags: [],
          use_when: ["Edit or review text to remove AI writing traces."],
          do_not_use_when: [],
          negative_queries: [],
          distinguish_from: {},
          risk_level: "unknown",
          enabled: true,
          core: false,
          assignable: false,
          metadata_status: "incomplete",
          missing_fields: ["category", "tags", "do_not_use_when"],
          usage: {},
          metadata_proposal: {
            proposal_id: "skill-proposal:humanizer",
            source: "rules",
            status: "pending",
            confidence: 1,
            payload: { categories: ["documentation"], tags: ["humanizer", "writing"] },
            reason: "rules: indexed metadata and taxonomy",
            updated_at: "2026-07-11T07:00:00Z",
          },
          suggestion: {
            categories: ["documentation"],
            tags: ["humanizer", "writing"],
            use_when: ["Edit or review text to remove AI writing traces."],
            do_not_use_when: [],
            negative_queries: [],
            distinguish_from: {},
            source_count: 0,
          },
        },
      ],
    } as PluginStateResponse;

    const html = renderPanel("en", [], null, state);

    expect(html).toContain("View and edit");
    expect(html).not.toContain("Complete metadata");
    expect(html).not.toContain('aria-label="Tags"');
    expect(html).not.toContain('aria-label="Use when"');
    expect(html).not.toContain('aria-label="Do not use when"');
    expect(html).not.toContain("Complete category, tags, use-when, and do-not-use conditions before enabling this Skill.");
    expect(html).toContain("Rule suggestion");
    expect(html).toContain("Pending confirmation");
    expect(html).toContain("rules: indexed metadata and taxonomy");
  });

  it("localizes Skill Library taxonomy labels with the active UI language", () => {
    const state = {
      ...pluginState([]),
      skill_library: [
        {
          id: "code_review",
          name: "Code Review",
          summary: "Review code.",
          source: "workspace",
          editable_metadata: true,
          category: ["programming", "documentation"],
          tags: [],
          use_when: [],
          do_not_use_when: [],
          negative_queries: [],
          distinguish_from: {},
          risk_level: "low",
          enabled: true,
          core: false,
          assignable: true,
          metadata_status: "ready",
          missing_fields: [],
          usage: {},
          suggestion: { categories: [], negative_queries: [], distinguish_from: {}, source_count: 0 },
        },
      ],
      skill_library_categories: [
        { id: "programming", name: "Programming", description: "Engineering work." },
        { id: "documentation", name: "Documentation", description: "Documentation work." },
      ],
    } as PluginStateResponse;

    const zhHtml = renderPanel("zh", [], null, state);
    const enHtml = renderPanel("en", [], null, state);

    expect(zhHtml).toContain("\u7f16\u7a0b");
    expect(zhHtml).toContain("\u6587\u6863");
    expect(zhHtml).not.toContain(">Programming<");
    expect(enHtml).toContain(">Programming<");
    expect(enHtml).toContain(">Documentation<");
  });
});
