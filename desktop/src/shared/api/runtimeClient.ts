import type {
  ComfyUiDetectionPayload,
  ComfyUiDetectionResponse,
  ComfyUiSettingsPayload,
  ComfyUiStateResponse,
  DeleteSessionResponse,
  ExternalMcpPayload,
  ModelSettingsResponse,
  PersistedChatMessage,
  PluginStateResponse,
  ProviderCatalogResponse,
  ProviderModelsFetchPayload,
  ProviderModelsFetchResponse,
  ProviderSettingsPayload,
  RunApprovalDecision,
  RunApprovalResponse,
  RuntimeModel,
  ServerRun,
  ServerSession,
  SessionPageResponse,
  SessionMessagesResponse,
  TerminalStateResponse,
} from "../types";

type FetchLike = typeof fetch;

export type RuntimeClientOptions = {
  baseUrl: string;
  token: string;
  fetchImpl?: FetchLike;
};

export class RuntimeClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly fetchImpl: FetchLike;

  constructor(options: RuntimeClientOptions) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl);
    this.token = options.token;
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  async listModels(): Promise<RuntimeModel[]> {
    const payload = await this.request<{ schema_version: "models.v1"; models: RuntimeModel[] }>("/api/models");
    return payload.models;
  }

  async loadModelSettings(): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/models");
  }

  async updateModelRole(role: string, modelId: string): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>(`/api/settings/models/roles/${encodeURIComponent(role)}`, {
      method: "PUT",
      body: JSON.stringify({ model_id: modelId }),
    });
  }

  async updateQueryRefiner(enabled: boolean): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/query-refiner", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    });
  }

  async updatePrivacyMode(mode: string): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/privacy", {
      method: "PUT",
      body: JSON.stringify({ mode }),
    });
  }

  async updateWorkerPool(modelIds: string[]): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/worker-pool", {
      method: "PUT",
      body: JSON.stringify({ model_ids: modelIds }),
    });
  }

  async updateLanguage(language: string): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/language", {
      method: "PUT",
      body: JSON.stringify({ language }),
    });
  }

  async loadProviderCatalog(): Promise<ProviderCatalogResponse> {
    return this.request<ProviderCatalogResponse>("/api/settings/providers/catalog");
  }

  async createProvider(payload: ProviderSettingsPayload): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>("/api/settings/providers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async updateProvider(providerId: string, payload: ProviderSettingsPayload): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>(`/api/settings/providers/${encodeURIComponent(providerId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }

  async deleteProvider(providerId: string): Promise<ModelSettingsResponse> {
    return this.request<ModelSettingsResponse>(`/api/settings/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
    });
  }

  async fetchProviderModels(payload: ProviderModelsFetchPayload): Promise<ProviderModelsFetchResponse> {
    return this.request<ProviderModelsFetchResponse>("/api/settings/providers/fetch-models", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async loadPluginState(): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>("/api/plugins");
  }

  async deleteSkill(skillId: string): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>(`/api/plugins/skills/${encodeURIComponent(skillId)}`, {
      method: "DELETE",
    });
  }

  async installSkill(path: string): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>("/api/plugins/skills/install", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  }

  async installMcp(path: string): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>("/api/plugins/mcp/install", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  }

  async installPluginPackage(path: string): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>("/api/plugins/packages/install", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  }

  async deletePluginPackage(pluginId: string): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>(`/api/plugins/packages/${encodeURIComponent(pluginId)}`, {
      method: "DELETE",
    });
  }

  async registerExternalMcp(payload: ExternalMcpPayload): Promise<PluginStateResponse> {
    return this.request<PluginStateResponse>("/api/plugins/mcp/external", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async loadComfyUiState(): Promise<ComfyUiStateResponse> {
    return this.request<ComfyUiStateResponse>("/api/comfyui");
  }

  async saveComfyUiSettings(payload: ComfyUiSettingsPayload): Promise<ComfyUiStateResponse> {
    return this.request<ComfyUiStateResponse>("/api/comfyui", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }

  async detectComfyUiInstallation(payload: ComfyUiDetectionPayload): Promise<ComfyUiDetectionResponse> {
    return this.request<ComfyUiDetectionResponse>("/api/comfyui/detect", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async checkComfyUiConnection(payload: Partial<ComfyUiSettingsPayload> = {}): Promise<ComfyUiStateResponse> {
    return this.request<ComfyUiStateResponse>("/api/comfyui/check", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async loadTerminalState(): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal");
  }

  async runTerminalCommand(
    command: string,
    options: { cwd?: string; timeout_seconds?: number } = {},
  ): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal/run", {
      method: "POST",
      body: JSON.stringify({
        command,
        cwd: options.cwd || "",
        timeout_seconds: options.timeout_seconds ?? 60,
      }),
    });
  }

  async stopTerminalCommand(): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async clearTerminal(): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal/clear", {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async rerunTerminalCommand(): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal/rerun", {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async setTerminalCwd(cwd: string): Promise<TerminalStateResponse> {
    return this.request<TerminalStateResponse>("/api/terminal/cwd", {
      method: "PUT",
      body: JSON.stringify({ cwd }),
    });
  }

  async listSessions(query = ""): Promise<ServerSession[]> {
    const cleanQuery = query.trim();
    const path = cleanQuery ? `/api/sessions?${new URLSearchParams({ q: cleanQuery }).toString()}` : "/api/sessions";
    const payload = await this.request<{ schema_version: "session.v1"; sessions: ServerSession[] }>(
      path,
    );
    return payload.sessions;
  }

  async listSessionPage(
    options: { query?: string; limit?: number; cursor?: string } = {},
  ): Promise<SessionPageResponse> {
    const params = new URLSearchParams();
    const cleanQuery = String(options.query || "").trim();
    if (cleanQuery) {
      params.set("q", cleanQuery);
    }
    params.set("limit", String(Math.max(1, Math.min(100, Math.trunc(options.limit ?? 50)))));
    const cursor = String(options.cursor || "").trim();
    if (cursor) {
      params.set("cursor", cursor);
    }
    const payload = await this.request<Partial<SessionPageResponse> & { sessions: ServerSession[] }>(
      `/api/sessions?${params.toString()}`,
    );
    return {
      schema_version: "session.v1",
      sessions: payload.sessions,
      next_cursor: String(payload.next_cursor || ""),
      has_more: Boolean(payload.has_more),
    };
  }

  async createSession(title: string): Promise<ServerSession> {
    return this.request<ServerSession>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  }

  async startRun(sessionId: string, input: string): Promise<ServerRun> {
    return this.request<ServerRun>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, input }),
    });
  }

  async loadSessionMessages(sessionId: string): Promise<PersistedChatMessage[]> {
    const payload = await this.request<SessionMessagesResponse>(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
    return payload.messages;
  }

  async deleteSession(sessionId: string): Promise<DeleteSessionResponse> {
    return this.request<DeleteSessionResponse>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  }

  async stopRun(runId: string): Promise<ServerRun> {
    return this.request<ServerRun>(`/api/runs/${encodeURIComponent(runId)}/stop`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async resolveRunApproval(
    runId: string,
    approvalId: string,
    decision: RunApprovalDecision,
  ): Promise<RunApprovalResponse> {
    return this.request<RunApprovalResponse>(
      `/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
      },
    );
  }

  runEventsUrl(runId: string): string {
    const url = new URL(`/api/runs/${encodeURIComponent(runId)}/events`, this.baseUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.search = `?token=${encodeURIComponent(this.token)}`;
    return url.toString();
  }

  openRunEventSocket(runId: string): WebSocket {
    return new WebSocket(this.runEventsUrl(runId));
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await this.fetchImpl(new URL(path, this.baseUrl), {
        ...init,
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
          ...(init.headers as Record<string, string> | undefined),
        },
      });
    } catch (error) {
      throw new Error(
        `无法连接 Lucode Runtime Server（${this.baseUrl}）。请确认 Electron 已启动内置 Runtime，或配置正确的 VITE_RUNTIME_BASE_URL。${errorMessage(error)}`,
      );
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(runtimeErrorMessage(payload, response.status, path));
    }
    return payload as T;
  }
}

function normalizeBaseUrl(value: string): string {
  const clean = String(value || "").trim();
  if (!clean) {
    return "http://127.0.0.1:43217/";
  }
  return clean.endsWith("/") ? clean : `${clean}/`;
}

function runtimeErrorMessage(payload: unknown, status: number, path: string): string {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: { message?: string } }).error;
    if (error?.message) {
      return error.message;
    }
  }
  if (status === 404) {
    if (path.includes("/api/sessions/") && path.endsWith("/messages")) {
      return "历史会话已不存在或索引已过期，请新建会话或选择其他会话。";
    }
    if (path.startsWith("/api/sessions/")) {
      return "历史会话已不存在或已被删除。";
    }
    if (path.startsWith("/api/runs/")) {
      return "当前运行已不存在或 Runtime 已重启，请重新发送任务。";
    }
    return "没有连上 Lucode Runtime Server：当前地址没有找到 Runtime API。请从 Electron App 启动，或让 VITE_RUNTIME_BASE_URL 指向 runtime/server。";
  }
  return `Runtime request failed with status ${status}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return ` 原始错误：${error.message}`;
  }
  return "";
}
