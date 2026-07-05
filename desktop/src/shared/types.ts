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

export type PluginRuntimeCapability = {
  id: string;
  display_name: string;
  summary: string;
  summary_zh: string;
  surface: string;
  status_key: string;
  ability_keys: string[];
  risk_key: string;
};

export type PluginStateResponse = {
  schema_version: "plugin_state.v1";
  skills: PluginSkill[];
  mcp: PluginMcpRow[];
  runtime_capabilities: PluginRuntimeCapability[];
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

export type ComfyUiStatus = "unknown" | "online" | "offline";

export type ComfyUiStateResponse = {
  schema_version: "comfyui.v1";
  base_url: string;
  configured: boolean;
  status: ComfyUiStatus;
  last_error: string;
  checked_at: string;
  endpoints: Record<string, boolean>;
};

export type ComfyUiSettingsPayload = {
  base_url: string;
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

export type RunApprovalDecision = "approve" | "reject";

export type RunApprovalResponse = {
  approval_id: string;
  status: "approved" | "rejected" | "cancelled" | string;
  decision: RunApprovalDecision;
  answer?: string;
  prompt?: string;
  tool_name?: string;
  tool?: string;
  action?: string;
  arguments_summary?: Record<string, unknown>;
  risk?: Record<string, unknown>;
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

export type TerminalCommandStatus = "success" | "failed" | "denied" | "cancelled" | "timeout";

export type TerminalDecisionFinding = {
  severity: string;
  category: string;
  message: string;
  evidence: string;
  blocks_execution: boolean;
};

export type TerminalDecision = {
  decision: string;
  risk_level: string;
  reason: string;
  permission_decision: string;
  permission_reason: string;
  findings: TerminalDecisionFinding[];
};

export type TerminalResultSummary = {
  status: TerminalCommandStatus;
  returncode: number;
  duration_ms: number;
};

export type TerminalCommandResult = TerminalResultSummary & {
  command: string;
  cwd: string;
  stdout: string;
  stderr: string;
  decision: TerminalDecision;
  started_at: string;
  ended_at: string;
  source: string;
  reason: string;
};

export type TerminalHistoryEntry = {
  command: string;
  cwd: string;
  reason: string;
  source: string;
  status: TerminalCommandStatus;
  returncode: number;
  duration_ms: number;
};

export type TerminalTranscriptEntry = {
  kind: "command" | "stdout" | "stderr" | "result" | string;
  command_id: string;
  text: string;
  result: TerminalResultSummary | null;
};

export type TerminalStateResponse = {
  schema_version: "terminal.v1";
  workspace_root: string;
  cwd: string;
  running: boolean;
  running_command_id: string;
  running_command: string;
  last_result: TerminalCommandResult | null;
  history: TerminalHistoryEntry[];
  transcript: TerminalTranscriptEntry[];
  started_command_id?: string;
  stop_requested?: boolean;
};

export type RuntimeConfig = {
  baseUrl: string;
  token: string;
  rendererSource?: "dev" | "dist";
  buildTime?: string;
};

export type DesktopTerminalCreateOptions = {
  cols?: number;
  rows?: number;
  cwd?: string;
};

export type DesktopTerminalCreateResult = {
  sessionId: string;
  shell: string;
  cwd: string;
  mode: "pty" | "pipe";
  pid: number;
};

export type DesktopTerminalDataEvent = {
  sessionId: string;
  data: string;
};

export type DesktopTerminalExitEvent = {
  sessionId: string;
  exitCode: number;
  signal?: number | string;
};

export type DesktopTerminalBridge = {
  create: (options: DesktopTerminalCreateOptions) => Promise<DesktopTerminalCreateResult>;
  write: (sessionId: string, data: string) => void;
  resize: (sessionId: string, cols: number, rows: number) => void;
  kill: (sessionId: string) => void;
  onData: (handler: (payload: DesktopTerminalDataEvent) => void) => () => void;
  onExit: (handler: (payload: DesktopTerminalExitEvent) => void) => () => void;
};

export type DesktopBrowserBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type DesktopBrowserTabState = {
  tabId: string;
  sessionId: string;
  url: string;
  title: string;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
  visible: boolean;
  lastError: string;
  automationEnabled: boolean;
  automationOrigin: string;
};

export type DesktopBrowserWorkspaceState = {
  activeTabId: string;
  tabs: DesktopBrowserTabState[];
};

export type DesktopBrowserPageSummaryOptions = {
  maxTextLength?: number;
  maxElements?: number;
};

export type DesktopBrowserPageHeading = {
  level: number;
  text: string;
};

export type DesktopBrowserPageElement = {
  id: string;
  role: string;
  tagName: string;
  text: string;
  ariaLabel: string;
  name: string;
  placeholder: string;
  value: string;
  href: string;
  inputType: string;
  disabled: boolean;
  selector: string;
};

export type DesktopBrowserPageSummary = {
  schemaVersion: "browser_page_summary.v1";
  tabId: string;
  url: string;
  title: string;
  capturedAt: string;
  text: string;
  textTruncated: boolean;
  headings: DesktopBrowserPageHeading[];
  elements: DesktopBrowserPageElement[];
  elementsTruncated: boolean;
};

export type DesktopBrowserActionKind = "click" | "set_input_value" | "submit_form";

export type DesktopBrowserActionTarget = {
  tagName: string;
  role: string;
  selector: string;
  text: string;
  inputType: string;
  disabled: boolean;
};

export type DesktopBrowserActionResult = {
  schemaVersion: "browser_page_action_result.v1";
  action: DesktopBrowserActionKind;
  tabId: string;
  selector: string;
  url: string;
  title: string;
  target: DesktopBrowserActionTarget;
  valueApplied?: boolean;
  valueLength?: number;
  submitted?: boolean;
  pageSummary: DesktopBrowserPageSummary | null;
};

export type DesktopBrowserDiagnostics = {
  activeTabId: string;
  tabCount: number;
  visibleTabIds: string[];
  hasBounds: boolean;
  bounds: DesktopBrowserBounds | null;
  layoutRequestCount: number;
  boundsApplyCount: number;
  visibilityChangeCount: number;
  focusRequestCount: number;
  stateEventCount: number;
};

export type DesktopBrowserBridge = {
  listTabs: () => Promise<DesktopBrowserWorkspaceState>;
  createTab: () => Promise<DesktopBrowserWorkspaceState>;
  activateTab: (tabId: string) => Promise<DesktopBrowserWorkspaceState>;
  closeTab: (tabId: string) => Promise<DesktopBrowserWorkspaceState>;
  navigate: (tabId: string, url: string) => Promise<DesktopBrowserWorkspaceState>;
  goBack: (tabId: string) => Promise<DesktopBrowserWorkspaceState>;
  goForward: (tabId: string) => Promise<DesktopBrowserWorkspaceState>;
  reload: (tabId: string) => Promise<DesktopBrowserWorkspaceState>;
  getState: (tabId?: string) => Promise<DesktopBrowserWorkspaceState>;
  getPageSummary: (tabId?: string, options?: DesktopBrowserPageSummaryOptions) => Promise<DesktopBrowserPageSummary>;
  clickElement: (tabId: string, selector: string) => Promise<DesktopBrowserActionResult>;
  setInputValue: (tabId: string, selector: string, value: string) => Promise<DesktopBrowserActionResult>;
  submitForm: (tabId: string, selector: string) => Promise<DesktopBrowserActionResult>;
  setAutomationPermission: (tabId: string, enabled: boolean) => Promise<DesktopBrowserWorkspaceState>;
  getDiagnostics: () => Promise<DesktopBrowserDiagnostics>;
  setBounds: (bounds: DesktopBrowserBounds) => void;
  hide: () => void;
  focusActive: () => void;
  onState: (handler: (payload: DesktopBrowserWorkspaceState) => void) => () => void;
};
