export type RuntimeModel = {
  id: string;
  display_name: string;
  provider: string;
  configured: boolean;
};

export type ModelSettingsModel = {
  id: string;
  ref: string;
  display_name: string;
  provider: string;
  configured: boolean;
  available: boolean;
  backend_type: string;
  model_name: string;
  privacy_level: string;
  supports_tools: boolean;
  reasoning_level: string;
  cost_level: string;
  model_tier: string;
};

export type ModelSettingsProvider = {
  provider: string;
  display_name: string;
  model_count: number;
  configured_model_count: number;
  configured: boolean;
  models?: string[];
  homepage?: string;
  base_url?: string;
  compatible_type?: string;
  local?: boolean;
  supports_tools?: boolean | string;
  custom?: boolean;
  key_configured?: boolean;
  key_hint?: string;
};

export type ProviderCatalogItem = {
  provider: string;
  display_name: string;
  homepage: string;
  base_url: string;
  compatible_type: string;
  models: string[];
  local: boolean;
  supports_tools?: boolean | string;
  custom: boolean;
  reasoning_level?: string;
  cost_level?: string;
};

export type ProviderCatalogResponse = {
  schema_version: "provider_catalog.v1";
  providers: ProviderCatalogItem[];
};

export type ProviderSettingsPayload = {
  provider_id?: string;
  display_name?: string;
  homepage?: string;
  base_url?: string;
  api_key?: string;
  models: string[];
  compatible_type?: string;
  local?: boolean;
  supports_tools?: boolean;
  custom?: boolean;
};

export type ProviderModelsFetchPayload = {
  provider_id?: string;
  base_url: string;
  api_key?: string;
  compatible_type?: string;
  local?: boolean;
};

export type ProviderModelsFetchResponse = {
  schema_version: "provider_models.v1";
  ok: boolean;
  models: string[];
  source: string;
  error: string;
};

export type ModelSettingsRole = {
  role: string;
  label: string;
  model_priority: string[];
  selected_model_id: string;
};

export type ModelSettingsResponse = {
  schema_version: "model_settings.v1";
  summary: {
    model_count: number;
    configured_model_count: number;
    provider_count: number;
    configured_provider_count: number;
  };
  models: ModelSettingsModel[];
  providers: ModelSettingsProvider[];
  roles: ModelSettingsRole[];
  runtime_preferences?: {
    execution_mode: string;
    privacy_mode: string;
    query_refiner_enabled: boolean;
    allowed_worker_models: string[];
    worker_pool_available: boolean;
  };
  ui_preferences?: {
    language: string;
  };
};

export type PluginSkill = {
  id: string;
  title: string;
  description: string;
  chips: string[];
  core: boolean;
  deletable: boolean;
};

export type PluginMcpRow = {
  id: string;
  title: string;
  status: string;
  detail: string;
};

export type PluginStateResponse = {
  schema_version: "plugin_state.v1";
  skills: PluginSkill[];
  mcp: PluginMcpRow[];
  deleted_skill_id?: string;
  installed_skill_id?: string;
  installed_mcp_id?: string;
  registered_mcp_id?: string;
};

export type ExternalMcpPayload = {
  id: string;
  transport: "stdio" | "http" | "sse";
  command?: string;
  args?: string[];
  url?: string;
};

export type ServerSession = {
  schema_version: "session.v1";
  session_id: string;
  title: string;
  display_title: string;
  created_at: string;
  updated_at: string;
};

export type ServerRun = {
  schema_version: "run.v1";
  run_id: string;
  session_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  created_at: string;
  updated_at: string;
};

export type RunEvent = {
  schema_version: "run_event.v1";
  run_id: string;
  session_id: string;
  seq: number;
  type: string;
  created_at: string;
  payload: Record<string, unknown>;
};

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessage = {
  id: string;
  sessionId: string;
  role: ChatRole;
  content: string;
  status?: "streaming" | "completed" | "failed" | "cancelled";
};

export type RenderedMessagePart =
  | {
      type: "text";
      content: string;
    }
  | {
      type: "code";
      content: string;
      language: string;
    };

export type PersistedChatMessage = {
  role: ChatRole;
  content: string;
};

export type SessionMessagesResponse = {
  schema_version: "messages.v1";
  session_id: string;
  messages: PersistedChatMessage[];
};

export type DeleteSessionResponse = {
  schema_version?: "session.v1";
  session_id: string;
  deleted: boolean;
  title?: string;
};

export type RuntimeConfig = {
  baseUrl: string;
  token: string;
  rendererSource?: "dev" | "dist";
  buildTime?: string;
};
