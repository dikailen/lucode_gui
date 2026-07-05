import { describe, expect, it, vi } from "vitest";

import { RuntimeClient } from "./runtimeClient";

describe("RuntimeClient", () => {
  it("sends bearer token and parses runtime responses", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token_1");
      if (url.endsWith("/api/models")) {
        return response({ schema_version: "models.v1", models: [] });
      }
      if (url.endsWith("/api/sessions") && init?.method === "POST") {
        return response({
          schema_version: "session.v1",
          session_id: "session_1",
          title: "New chat",
          display_title: "New chat",
          created_at: "now",
          updated_at: "now",
        });
      }
      if (url.endsWith("/api/runs") && init?.method === "POST") {
        return response({
          schema_version: "run.v1",
          run_id: "run_1",
          session_id: "session_1",
          status: "running",
          created_at: "now",
          updated_at: "now",
        });
      }
      if (url.endsWith("/api/runs/run_1/approvals/approval_1") && init?.method === "POST") {
        expect(init.body).toBe(JSON.stringify({ decision: "approve" }));
        return response({
          approval_id: "approval_1",
          status: "approved",
          decision: "approve",
          answer: "yes",
        });
      }
      if (url.endsWith("/api/sessions/session_1/messages")) {
        return response({
          schema_version: "messages.v1",
          session_id: "session_1",
          messages: [{ role: "user", content: "hello" }],
        });
      }
      if (url.endsWith("/api/sessions/session_1") && init?.method === "DELETE") {
        return response({ deleted: true, session_id: "session_1" });
      }
      if (url.endsWith("/api/settings/models")) {
        return response({
          schema_version: "model_settings.v1",
          summary: {
            model_count: 1,
            configured_model_count: 1,
            provider_count: 1,
            configured_provider_count: 1,
          },
          models: [
            {
              id: "deepseek_deepseek_chat_model",
              ref: "deepseek/deepseek-chat",
              display_name: "DeepSeek Chat",
              provider: "deepseek",
              configured: true,
              available: true,
              backend_type: "openai_compatible",
              model_name: "deepseek-chat",
              privacy_level: "cloud",
              supports_tools: true,
              reasoning_level: "high",
              cost_level: "medium",
              model_tier: "large",
            },
          ],
          providers: [
            {
              provider: "deepseek",
              display_name: "deepseek",
              model_count: 1,
              configured_model_count: 1,
              configured: true,
            },
          ],
          roles: [
            {
              role: "orchestrator",
              label: "主脑规划脑",
              model_priority: ["deepseek_deepseek_chat_model"],
              selected_model_id: "deepseek_deepseek_chat_model",
            },
          ],
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "local_first",
            query_refiner_enabled: false,
            allowed_worker_models: [],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "zh",
          },
        });
      }
      if (url.endsWith("/api/settings/models/roles/orchestrator") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ model_id: "deepseek_deepseek_chat_model" }));
        return response({
          schema_version: "model_settings.v1",
          summary: {
            model_count: 1,
            configured_model_count: 1,
            provider_count: 1,
            configured_provider_count: 1,
          },
          models: [
            {
              id: "deepseek_deepseek_chat_model",
              ref: "deepseek/deepseek-chat",
              display_name: "DeepSeek Chat",
              provider: "deepseek",
              configured: true,
              available: true,
              backend_type: "openai_compatible",
              model_name: "deepseek-chat",
              privacy_level: "cloud",
              supports_tools: true,
              reasoning_level: "high",
              cost_level: "medium",
              model_tier: "large",
            },
          ],
          providers: [
            {
              provider: "deepseek",
              display_name: "deepseek",
              model_count: 1,
              configured_model_count: 1,
              configured: true,
            },
          ],
          roles: [
            {
              role: "orchestrator",
              label: "主脑规划脑",
              model_priority: ["deepseek_deepseek_chat_model"],
              selected_model_id: "deepseek_deepseek_chat_model",
            },
          ],
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "local_first",
            query_refiner_enabled: false,
            allowed_worker_models: [],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "zh",
          },
        });
      }
      if (url.endsWith("/api/settings/query-refiner") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ enabled: true }));
        return response({
          ...modelSettingsResponse(),
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "local_first",
            query_refiner_enabled: true,
            allowed_worker_models: [],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "zh",
          },
        });
      }
      if (url.endsWith("/api/settings/privacy") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ mode: "offline" }));
        return response({
          ...modelSettingsResponse(),
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "offline",
            query_refiner_enabled: false,
            allowed_worker_models: [],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "zh",
          },
        });
      }
      if (url.endsWith("/api/settings/worker-pool") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ model_ids: ["deepseek_deepseek_chat_model"] }));
        return response({
          ...modelSettingsResponse(),
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "local_first",
            query_refiner_enabled: false,
            allowed_worker_models: ["deepseek_deepseek_chat_model"],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "zh",
          },
        });
      }
      if (url.endsWith("/api/settings/language") && init?.method === "PUT") {
        expect(init.body).toBe(JSON.stringify({ language: "en" }));
        return response({
          ...modelSettingsResponse(),
          runtime_preferences: {
            execution_mode: "auto",
            privacy_mode: "local_first",
            query_refiner_enabled: false,
            allowed_worker_models: [],
            worker_pool_available: true,
          },
          ui_preferences: {
            language: "en",
          },
        });
      }
      if (url.endsWith("/api/settings/providers/catalog")) {
        return response({
          schema_version: "provider_catalog.v1",
          providers: [
            {
              provider: "deepseek",
              display_name: "DeepSeek",
              homepage: "https://platform.deepseek.com",
              base_url: "https://api.deepseek.com",
              compatible_type: "openai_compatible",
              models: ["deepseek-chat"],
              local: false,
              supports_tools: true,
              custom: false,
            },
            {
              provider: "custom_openai_compatible",
              display_name: "自定义中转",
              homepage: "",
              base_url: "",
              compatible_type: "openai_compatible",
              models: [],
              local: false,
              supports_tools: "probe",
              custom: true,
            },
          ],
        });
      }
      if (url.endsWith("/api/plugins") && !init?.method) {
        return response({
          schema_version: "plugin_state.v1",
          skills: [],
          mcp: [],
          runtime_capabilities: [
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
          ],
        });
      }
      if (url.endsWith("/api/plugins/skills/install") && init?.method === "POST") {
        expect(init.body).toBe(JSON.stringify({ path: "D:\\skills\\demo" }));
        return response({
          schema_version: "plugin_state.v1",
          installed_skill_id: "demo",
          skills: [
            {
              id: "demo",
              title: "Demo",
              description: "Dropped skill",
              chips: ["自定义", "本地"],
              core: false,
              deletable: true,
            },
          ],
          mcp: [],
          runtime_capabilities: [],
        });
      }
      if (url.endsWith("/api/plugins/mcp/install") && init?.method === "POST") {
        expect(init.body).toBe(JSON.stringify({ path: "D:\\mcp\\demo.json" }));
        return response({
          schema_version: "plugin_state.v1",
          installed_mcp_id: "demo",
          skills: [],
          runtime_capabilities: [],
          mcp: [
            {
              id: "demo",
              title: "demo",
              status: "已添加",
              detail: "未连接",
            },
          ],
        });
      }
      if (url.endsWith("/api/plugins/mcp/external") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          id: "local_docs",
          transport: "stdio",
          command: "python",
          args: ["server.py", "--stdio"],
        });
        return response({
          schema_version: "plugin_state.v1",
          registered_mcp_id: "local_docs",
          skills: [],
          runtime_capabilities: [],
          mcp: [
            {
              id: "local_docs",
              title: "local_docs",
              status: "已添加",
              detail: "stdio - python",
            },
          ],
        });
      }
      if (url.endsWith("/api/comfyui") && !init?.method) {
        return response(comfyUiStateResponse());
      }
      if (url.endsWith("/api/comfyui") && init?.method === "PUT") {
        expect(JSON.parse(String(init.body))).toEqual({ base_url: "http://127.0.0.1:8188" });
        return response({ ...comfyUiStateResponse(), configured: true });
      }
      if (url.endsWith("/api/comfyui/check") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({ base_url: "http://127.0.0.1:8188" });
        return response({
          ...comfyUiStateResponse(),
          configured: true,
          status: "online",
          checked_at: "now",
          endpoints: { system_stats: true, queue: true },
        });
      }
      throw new Error(`unexpected url ${url}`);
    });
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217/",
      token: "token_1",
      fetchImpl: fetchMock as typeof fetch,
    });

    await expect(client.listModels()).resolves.toEqual([]);
    await expect(client.createSession("New chat")).resolves.toMatchObject({ session_id: "session_1" });
    await expect(client.startRun("session_1", "hello")).resolves.toMatchObject({ run_id: "run_1" });
    await expect(client.resolveRunApproval("run_1", "approval_1", "approve")).resolves.toMatchObject({
      approval_id: "approval_1",
      decision: "approve",
    });
    await expect(client.loadSessionMessages("session_1")).resolves.toEqual([{ role: "user", content: "hello" }]);
    await expect(client.deleteSession("session_1")).resolves.toMatchObject({ deleted: true });
    await expect(client.loadModelSettings()).resolves.toMatchObject({
      schema_version: "model_settings.v1",
      summary: { configured_model_count: 1 },
    });
    await expect(client.updateModelRole("orchestrator", "deepseek_deepseek_chat_model")).resolves.toMatchObject({
      roles: [{ role: "orchestrator", selected_model_id: "deepseek_deepseek_chat_model" }],
    });
    await expect(client.updateQueryRefiner(true)).resolves.toMatchObject({
      runtime_preferences: { query_refiner_enabled: true },
    });
    await expect(client.updatePrivacyMode("offline")).resolves.toMatchObject({
      runtime_preferences: { privacy_mode: "offline" },
    });
    await expect(client.updateWorkerPool(["deepseek_deepseek_chat_model"])).resolves.toMatchObject({
      runtime_preferences: { allowed_worker_models: ["deepseek_deepseek_chat_model"] },
    });
    await expect(client.updateLanguage("en")).resolves.toMatchObject({
      ui_preferences: { language: "en" },
    });
    await expect(client.loadProviderCatalog()).resolves.toMatchObject({
      schema_version: "provider_catalog.v1",
      providers: [
        { provider: "deepseek", display_name: "DeepSeek" },
        { provider: "custom_openai_compatible", custom: true },
      ],
    });
    await expect(client.loadPluginState()).resolves.toMatchObject({
      schema_version: "plugin_state.v1",
      runtime_capabilities: [{ id: "desktop_browser", status_key: "desktop_runtime" }],
    });
    await expect(client.installSkill("D:\\skills\\demo")).resolves.toMatchObject({
      installed_skill_id: "demo",
      skills: [{ id: "demo", title: "Demo" }],
    });
    await expect(client.installMcp("D:\\mcp\\demo.json")).resolves.toMatchObject({
      installed_mcp_id: "demo",
      mcp: [{ id: "demo", title: "demo" }],
    });
    await expect(
      client.registerExternalMcp({
        id: "local_docs",
        transport: "stdio",
        command: "python",
        args: ["server.py", "--stdio"],
      }),
    ).resolves.toMatchObject({
      registered_mcp_id: "local_docs",
      mcp: [{ id: "local_docs", title: "local_docs" }],
    });
    await expect(client.loadComfyUiState()).resolves.toMatchObject({
      schema_version: "comfyui.v1",
      base_url: "http://127.0.0.1:8188",
      status: "unknown",
    });
    await expect(client.saveComfyUiSettings({ base_url: "http://127.0.0.1:8188" })).resolves.toMatchObject({
      configured: true,
    });
    await expect(client.checkComfyUiConnection({ base_url: "http://127.0.0.1:8188" })).resolves.toMatchObject({
      status: "online",
      endpoints: { system_stats: true, queue: true },
    });

    expect(fetchMock).toHaveBeenCalledTimes(20);
  });

  it("builds authenticated websocket urls from http base urls", () => {
    const client = new RuntimeClient({ baseUrl: "http://127.0.0.1:43217", token: "token with space" });

    expect(client.runEventsUrl("run_1")).toBe(
      "ws://127.0.0.1:43217/api/runs/run_1/events?token=token%20with%20space",
    );
  });

  it("controls the terminal API through authenticated requests", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token_1");
      if (url.endsWith("/api/terminal") && !init?.method) {
        return response(terminalStateResponse());
      }
      if (url.endsWith("/api/terminal/run") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          command: "python --version",
          cwd: "D:\\repo",
          timeout_seconds: 20,
        });
        return response({ ...terminalStateResponse(), running: true, started_command_id: "cmd_1" });
      }
      if (url.endsWith("/api/terminal/stop") && init?.method === "POST") {
        return response({ ...terminalStateResponse(), stop_requested: true });
      }
      if (url.endsWith("/api/terminal/clear") && init?.method === "POST") {
        return response({ ...terminalStateResponse(), transcript: [] });
      }
      if (url.endsWith("/api/terminal/rerun") && init?.method === "POST") {
        return response({ ...terminalStateResponse(), running: true, started_command_id: "cmd_2" });
      }
      if (url.endsWith("/api/terminal/cwd") && init?.method === "PUT") {
        expect(JSON.parse(String(init.body))).toEqual({ cwd: "D:\\repo\\desktop" });
        return response({ ...terminalStateResponse(), cwd: "D:\\repo\\desktop" });
      }
      throw new Error(`unexpected url ${url}`);
    });
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217/",
      token: "token_1",
      fetchImpl: fetchMock as typeof fetch,
    });

    await expect(client.loadTerminalState()).resolves.toMatchObject({ schema_version: "terminal.v1" });
    await expect(client.runTerminalCommand("python --version", { cwd: "D:\\repo", timeout_seconds: 20 })).resolves.toMatchObject({
      started_command_id: "cmd_1",
    });
    await expect(client.stopTerminalCommand()).resolves.toMatchObject({ stop_requested: true });
    await expect(client.clearTerminal()).resolves.toMatchObject({ transcript: [] });
    await expect(client.rerunTerminalCommand()).resolves.toMatchObject({ started_command_id: "cmd_2" });
    await expect(client.setTerminalCwd("D:\\repo\\desktop")).resolves.toMatchObject({ cwd: "D:\\repo\\desktop" });
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it("creates, updates, and deletes providers without putting API keys in responses", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token_1");
      if (url.endsWith("/api/settings/providers") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          provider_id: "my_proxy",
          display_name: "My Proxy",
          homepage: "https://proxy.example",
          base_url: "https://proxy.example/v1",
          api_key: "test-api-key",
          models: ["qwen-max", "deepseek-chat"],
          compatible_type: "openai_compatible",
          local: false,
          supports_tools: true,
          custom: true,
        });
        return response({
          ...modelSettingsResponse(),
          saved_provider_id: "my_proxy",
          providers: [
            {
              provider: "my_proxy",
              display_name: "My Proxy",
              model_count: 2,
              configured_model_count: 2,
              configured: true,
              base_url: "https://proxy.example/v1",
              compatible_type: "openai_compatible",
              local: false,
              supports_tools: true,
              custom: true,
              key_configured: true,
            },
          ],
        });
      }
      if (url.endsWith("/api/settings/providers/my_proxy") && init?.method === "PUT") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          display_name: "My Proxy Updated",
          api_key: "",
          models: ["qwen-max"],
        });
        return response({
          ...modelSettingsResponse(),
          saved_provider_id: "my_proxy",
          providers: [
            {
              provider: "my_proxy",
              display_name: "My Proxy Updated",
              model_count: 1,
              configured_model_count: 1,
              configured: true,
              key_configured: true,
            },
          ],
        });
      }
      if (url.endsWith("/api/settings/providers/my_proxy") && init?.method === "DELETE") {
        return response({
          ...modelSettingsResponse(),
          deleted_provider_id: "my_proxy",
          providers: [],
        });
      }
      if (url.endsWith("/api/settings/providers/fetch-models") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          base_url: "https://proxy.example/v1",
          api_key: "test-api-key",
          compatible_type: "openai_compatible",
          local: false,
        });
        return response({
          schema_version: "provider_models.v1",
          ok: true,
          models: ["qwen-max", "deepseek-chat"],
          source: "upstream",
          error: "",
        });
      }
      throw new Error(`unexpected url ${url}`);
    });
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217/",
      token: "token_1",
      fetchImpl: fetchMock as typeof fetch,
    });

    const created = await client.createProvider({
      provider_id: "my_proxy",
      display_name: "My Proxy",
      homepage: "https://proxy.example",
      base_url: "https://proxy.example/v1",
      api_key: "test-api-key",
      models: ["qwen-max", "deepseek-chat"],
      compatible_type: "openai_compatible",
      local: false,
      supports_tools: true,
      custom: true,
    });
    const updated = await client.updateProvider("my_proxy", {
      display_name: "My Proxy Updated",
      api_key: "",
      models: ["qwen-max"],
    });
    const fetched = await client.fetchProviderModels({
      base_url: "https://proxy.example/v1",
      api_key: "test-api-key",
      compatible_type: "openai_compatible",
      local: false,
    });
    const deleted = await client.deleteProvider("my_proxy");

    expect(JSON.stringify(created)).not.toContain("test-api-key");
    expect(JSON.stringify(fetched)).not.toContain("test-api-key");
    expect(created.providers[0]).toMatchObject({ provider: "my_proxy", key_configured: true });
    expect(updated.providers[0]).toMatchObject({ display_name: "My Proxy Updated", key_configured: true });
    expect(fetched.models).toEqual(["qwen-max", "deepseek-chat"]);
    expect(deleted.providers).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("throws readable errors for failed runtime requests", async () => {
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217",
      token: "token_1",
      fetchImpl: async () => response({ error: { message: "bad token" } }, 401) as Response,
    });

    await expect(client.listSessions()).rejects.toThrow("bad token");
  });

  it("turns bare 404 responses into runtime setup guidance", async () => {
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217",
      token: "token_1",
      fetchImpl: async () => response({}, 404) as Response,
    });

    await expect(client.listSessions()).rejects.toThrow("没有连上 Lucode Runtime Server");
  });

  it("turns missing session messages into a stale-session error instead of runtime setup guidance", async () => {
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217",
      token: "token_1",
      fetchImpl: async () => response({}, 404) as Response,
    });

    await expect(client.loadSessionMessages("session_stale")).rejects.toThrow("历史会话已不存在");
    await expect(client.loadSessionMessages("session_stale")).rejects.not.toThrow("没有连上 Lucode Runtime Server");
  });

  it("turns network failures into readable runtime connection errors", async () => {
    const client = new RuntimeClient({
      baseUrl: "http://127.0.0.1:43217",
      token: "token_1",
      fetchImpl: async () => {
        throw new TypeError("fetch failed");
      },
    });

    await expect(client.listSessions()).rejects.toThrow("无法连接 Lucode Runtime Server");
  });
});

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function modelSettingsResponse() {
  return {
    schema_version: "model_settings.v1",
    summary: {
      model_count: 1,
      configured_model_count: 1,
      provider_count: 1,
      configured_provider_count: 1,
    },
    models: [
      {
        id: "deepseek_deepseek_chat_model",
        ref: "deepseek/deepseek-chat",
        display_name: "DeepSeek Chat",
        provider: "deepseek",
        configured: true,
        available: true,
        backend_type: "openai_compatible",
        model_name: "deepseek-chat",
        privacy_level: "cloud",
        supports_tools: true,
        reasoning_level: "high",
        cost_level: "medium",
        model_tier: "large",
      },
    ],
    providers: [
      {
        provider: "deepseek",
        display_name: "deepseek",
        model_count: 1,
        configured_model_count: 1,
        configured: true,
      },
    ],
    roles: [
      {
        role: "orchestrator",
        label: "主脑规划脑",
        model_priority: ["deepseek_deepseek_chat_model"],
        selected_model_id: "deepseek_deepseek_chat_model",
      },
    ],
    ui_preferences: {
      language: "zh",
    },
  };
}

function terminalStateResponse() {
  return {
    schema_version: "terminal.v1",
    workspace_root: "D:\\repo",
    cwd: "D:\\repo",
    running: false,
    running_command_id: "",
    running_command: "",
    last_result: null,
    history: [],
    transcript: [],
  };
}

function comfyUiStateResponse() {
  return {
    schema_version: "comfyui.v1",
    base_url: "http://127.0.0.1:8188",
    configured: false,
    status: "unknown",
    last_error: "",
    checked_at: "",
    endpoints: {},
  };
}
