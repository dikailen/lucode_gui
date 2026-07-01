import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  appendUserMessage,
  createInitialAppState,
  markPendingDeleteSession,
  markRunStarted,
  markRunStreamDisconnected,
  reduceRunEvent,
  removeSession,
  selectPrimaryModelLabel,
  setActiveSession,
  setModelLabel,
  setSessionMessages,
  setSessions,
  type AppState,
} from "./appState";
import { displayModelNameForModel } from "./modelDisplay";
import { resolveRuntimeConfig } from "./runtimeEnv";
import { isTerminalRunEvent } from "./runEvents";
import { RuntimeClient } from "../shared/api/runtimeClient";
import type {
  ExternalMcpPayload,
  ModelSettingsResponse,
  PluginStateResponse,
  ProviderCatalogResponse,
  ProviderModelsFetchPayload,
  ProviderModelsFetchResponse,
  ProviderSettingsPayload,
  RunEvent,
  RuntimeConfig,
  ServerSession,
} from "../shared/types";

export type WorkspaceId = "chat" | "plugins" | "settings";
export type DockToolId = "" | "terminal" | "browser" | "files";

export type LucodeAppController = {
  state: AppState;
  input: string;
  runtimeError: string;
  runtimeConfig: RuntimeConfig;
  modelSettings: ModelSettingsResponse | null;
  providerCatalog: ProviderCatalogResponse | null;
  pluginState: PluginStateResponse | null;
  activeWorkspace: WorkspaceId;
  rightDockTool: DockToolId;
  sidebarCollapsed: boolean;
  settingsError: string;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "";
  settingsSavingRole: string;
  setInput: (value: string) => void;
  toggleSettings: () => void;
  openSettings: () => void;
  openDock: (tool: DockToolId) => void;
  closeSettings: () => void;
  switchWorkspace: (workspace: WorkspaceId) => void;
  toggleSidebar: () => void;
  refreshModelSettings: () => void;
  refreshProviderCatalog: () => void;
  refreshPluginState: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateQueryRefiner: (enabled: boolean) => void;
  updatePrivacyMode: (mode: string) => void;
  updateWorkerPool: (modelIds: string[]) => void;
  updateLanguage: (language: string) => void;
  saveProvider: (payload: ProviderSettingsPayload, providerId?: string) => Promise<boolean>;
  deleteProvider: (providerId: string) => Promise<boolean>;
  fetchProviderModels: (payload: ProviderModelsFetchPayload) => Promise<ProviderModelsFetchResponse | null>;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  installMcp: (path: string) => void;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
  submit: (event: FormEvent) => void;
  stopRun: () => void;
  createNewSession: () => void;
  selectSession: (sessionId: string) => void;
  requestDeleteSession: (sessionId: string) => void;
};

export function useLucodeApp(): LucodeAppController {
  const runtimeConfig = useMemo(() => resolveRuntimeConfig(), []);
  const client = useMemo(() => new RuntimeClient(runtimeConfig), [runtimeConfig]);
  const [state, setState] = useState<AppState>(() => createInitialAppState());
  const [input, setInput] = useState("");
  const [runtimeError, setRuntimeError] = useState("");
  const [modelSettings, setModelSettings] = useState<ModelSettingsResponse | null>(null);
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogResponse | null>(null);
  const [pluginState, setPluginState] = useState<PluginStateResponse | null>(null);
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceId>("chat");
  const [rightDockTool, setRightDockTool] = useState<DockToolId>("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [pluginError, setPluginError] = useState("");
  const [pluginInstallingTarget, setPluginInstallingTarget] = useState<"skills" | "mcp" | "">("");
  const [settingsSavingRole, setSettingsSavingRole] = useState("");
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadInitialState() {
      try {
        const [models, sessions] = await Promise.all([client.listModels(), client.listSessions()]);
        if (cancelled) {
          return;
        }
        setRuntimeError("");
        setState((current) => setSessions(setModelLabel(current, selectPrimaryModelLabel(models)), sessions));
        void client
          .loadModelSettings()
          .then((settings) => {
            if (!cancelled) {
              setSettingsError("");
              setModelSettings(settings);
            }
          })
          .catch((error) => {
            if (!cancelled) {
              setSettingsError(error instanceof Error ? error.message : String(error));
            }
          });
        void client
          .loadPluginState()
          .then((plugins) => {
            if (!cancelled) {
              setPluginError("");
              setPluginState(plugins);
            }
          })
          .catch((error) => {
            if (!cancelled) {
              setPluginError(error instanceof Error ? error.message : String(error));
            }
          });
        void client
          .loadProviderCatalog()
          .then((catalog) => {
            if (!cancelled) {
              setProviderCatalog(catalog);
            }
          })
          .catch((error) => {
            if (!cancelled) {
              setSettingsError(error instanceof Error ? error.message : String(error));
            }
          });
        const activeSessionId = sessions[0]?.session_id;
        if (activeSessionId) {
          const messages = await client.loadSessionMessages(activeSessionId);
          if (!cancelled) {
            setState((current) => setSessionMessages(current, activeSessionId, messages));
          }
        }
      } catch (error) {
        if (!cancelled) {
          setRuntimeError(error instanceof Error ? error.message : String(error));
        }
      }
    }
    void loadInitialState();
    return () => {
      cancelled = true;
      socketRef.current?.close();
    };
  }, [client]);

  async function refreshModelSettings() {
    setSettingsError("");
    try {
      setModelSettings(await client.loadModelSettings());
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    }
  }

  async function refreshProviderCatalog() {
    setSettingsError("");
    try {
      setProviderCatalog(await client.loadProviderCatalog());
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    }
  }

  async function refreshPluginState() {
    setPluginError("");
    try {
      setPluginState(await client.loadPluginState());
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
    }
  }

  async function deleteSkill(skillId: string) {
    if (!skillId) {
      return;
    }
    setPluginError("");
    try {
      setPluginState(await client.deleteSkill(skillId));
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
    }
  }

  async function installSkill(path: string) {
    const cleanPath = path.trim();
    if (!cleanPath || pluginInstallingTarget) {
      return;
    }
    setPluginError("");
    setPluginInstallingTarget("skills");
    try {
      setPluginState(await client.installSkill(cleanPath));
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
    } finally {
      setPluginInstallingTarget("");
    }
  }

  async function installMcp(path: string) {
    const cleanPath = path.trim();
    if (!cleanPath || pluginInstallingTarget) {
      return;
    }
    setPluginError("");
    setPluginInstallingTarget("mcp");
    try {
      setPluginState(await client.installMcp(cleanPath));
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
    } finally {
      setPluginInstallingTarget("");
    }
  }

  async function registerExternalMcp(payload: ExternalMcpPayload): Promise<boolean> {
    if (pluginInstallingTarget) {
      return false;
    }
    setPluginError("");
    setPluginInstallingTarget("mcp");
    try {
      setPluginState(await client.registerExternalMcp(payload));
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setPluginInstallingTarget("");
    }
    return true;
  }

  async function updateRoleModel(role: string, modelId: string) {
    if (!role || !modelId || settingsSavingRole) {
      return;
    }
    setSettingsError("");
    setSettingsSavingRole(role);
    try {
      const settings = await client.updateModelRole(role, modelId);
      setModelSettings(settings);
      if (role === "orchestrator") {
        setState((current) => setModelLabel(current, displayModelName(modelId, settings)));
      }
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function updateQueryRefiner(enabled: boolean) {
    if (settingsSavingRole) {
      return;
    }
    setSettingsError("");
    setSettingsSavingRole("query_refiner_enabled");
    try {
      setModelSettings(await client.updateQueryRefiner(enabled));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function updatePrivacyMode(mode: string) {
    if (!mode || settingsSavingRole) {
      return;
    }
    setSettingsError("");
    setSettingsSavingRole("privacy_mode");
    try {
      setModelSettings(await client.updatePrivacyMode(mode));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function updateWorkerPool(modelIds: string[]) {
    if (settingsSavingRole) {
      return;
    }
    setSettingsError("");
    setSettingsSavingRole("worker_pool");
    try {
      setModelSettings(await client.updateWorkerPool(modelIds));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function updateLanguage(language: string) {
    if (!language || settingsSavingRole) {
      return;
    }
    setSettingsError("");
    setSettingsSavingRole("language");
    try {
      setModelSettings(await client.updateLanguage(language));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function saveProvider(payload: ProviderSettingsPayload, providerId = ""): Promise<boolean> {
    if (settingsSavingRole) {
      return false;
    }
    const savingKey = providerId ? `provider:${providerId}` : "provider:new";
    setSettingsError("");
    setSettingsSavingRole(savingKey);
    try {
      const nextSettings = providerId
        ? await client.updateProvider(providerId, payload)
        : await client.createProvider(payload);
      setModelSettings(nextSettings);
      const orchestrator = nextSettings.roles.find((role) => role.role === "orchestrator");
      if (orchestrator?.selected_model_id) {
        setState((current) => setModelLabel(current, displayModelName(orchestrator.selected_model_id, nextSettings)));
      }
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setSettingsSavingRole("");
    }
    return true;
  }

  async function deleteProvider(providerId: string): Promise<boolean> {
    if (!providerId || settingsSavingRole) {
      return false;
    }
    setSettingsError("");
    setSettingsSavingRole(`provider_delete:${providerId}`);
    try {
      const nextSettings = await client.deleteProvider(providerId);
      setModelSettings(nextSettings);
      const orchestrator = nextSettings.roles.find((role) => role.role === "orchestrator");
      setState((current) =>
        setModelLabel(
          current,
          orchestrator?.selected_model_id ? displayModelName(orchestrator.selected_model_id, nextSettings) : "未设置",
        ),
      );
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setSettingsSavingRole("");
    }
    return true;
  }

  async function fetchProviderModels(
    payload: ProviderModelsFetchPayload,
  ): Promise<ProviderModelsFetchResponse | null> {
    if (settingsSavingRole) {
      return null;
    }
    setSettingsError("");
    setSettingsSavingRole("provider_fetch_models");
    try {
      return await client.fetchProviderModels(payload);
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function ensureSession(title: string): Promise<ServerSession> {
    const active = state.sessions.find((session) => session.session_id === state.activeSessionId);
    if (active) {
      return active;
    }
    const session = await client.createSession(title);
    setState((current) => setSessions(current, [session, ...current.sessions]));
    return session;
  }

  async function submitRun(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || state.runStatus === "running") {
      return;
    }
    setInput("");
    setRuntimeError("");
    try {
      const session = await ensureSession(text);
      setState((current) => appendUserMessage(current, session.session_id, text));
      const run = await client.startRun(session.session_id, text);
      setState((current) => markRunStarted(current, session.session_id, run.run_id));
      openRunStream(run.run_id);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  async function stopRun() {
    if (!state.activeRunId || state.runStatus !== "running") {
      return;
    }
    try {
      await client.stopRun(state.activeRunId);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  function openRunStream(runId: string) {
    socketRef.current?.close();
    const socket = client.openRunEventSocket(runId);
    let sawTerminalEvent = false;
    socketRef.current = socket;
    socket.addEventListener("message", (event) => {
      let payload: RunEvent;
      try {
        payload = JSON.parse(event.data) as RunEvent;
      } catch {
        setRuntimeError("运行事件解析失败，请查看 Runtime 日志。");
        return;
      }
      setState((current) => reduceRunEvent(current, payload));
      if (isTerminalRunEvent(payload.type)) {
        sawTerminalEvent = true;
        socket.close(1000, "run finished");
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        void refreshSessions();
        void refreshModelSettings();
      }
    });
    socket.addEventListener("error", () => {
      setRuntimeError("运行事件流连接失败，请确认 Runtime Server 仍在运行。");
    });
    socket.addEventListener("close", () => {
      if (sawTerminalEvent) {
        return;
      }
      setState((current) =>
        current.activeRunId === runId && current.runStatus === "running"
          ? markRunStreamDisconnected(current, current.activeSessionId, "连接已关闭")
          : current,
      );
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    });
  }

  async function refreshSessions() {
    try {
      const sessions = await client.listSessions();
      setState((current) => setSessions(current, sessions));
    } catch {
      // Session refresh is auxiliary; the run result remains visible.
    }
  }

  async function createNewSession() {
    try {
      const session = await client.createSession("新会话");
      setRuntimeError("");
      setActiveWorkspace("chat");
      setState((current) =>
        setSessionMessages(
          setActiveSession(setSessions(current, [session, ...current.sessions]), session.session_id),
          session.session_id,
          [],
        ),
      );
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  async function selectSession(sessionId: string) {
    if (sessionId === state.activeSessionId && state.runStatus === "running") {
      return;
    }
    setRuntimeError("");
    setActiveWorkspace("chat");
    try {
      const messages = await client.loadSessionMessages(sessionId);
      setState((current) => setSessionMessages(current, sessionId, messages));
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  async function requestDeleteSession(sessionId: string) {
    if (state.runStatus === "running" && state.activeSessionId === sessionId) {
      return;
    }
    if (state.pendingDeleteSessionId !== sessionId) {
      setState((current) => markPendingDeleteSession(current, sessionId));
      return;
    }
    setRuntimeError("");
    try {
      await client.deleteSession(sessionId);
      setState((current) => removeSession(current, sessionId));
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  return {
    state,
    input,
    runtimeError,
    runtimeConfig,
    modelSettings,
    providerCatalog,
    pluginState,
    activeWorkspace,
    rightDockTool,
    sidebarCollapsed,
    settingsError,
    pluginError,
    pluginInstallingTarget,
    settingsSavingRole,
    setInput,
    toggleSettings: () => {
      setRightDockTool("");
      setActiveWorkspace((current) => (current === "settings" ? "chat" : "settings"));
    },
    openSettings: () => {
      setRightDockTool("");
      setActiveWorkspace("settings");
    },
    openDock: (tool) => setRightDockTool((current) => (current === tool ? "" : tool)),
    closeSettings: () => setActiveWorkspace("chat"),
    switchWorkspace: (workspace) => {
      setActiveWorkspace(workspace);
      if (workspace === "plugins") {
        void refreshPluginState();
      }
    },
    toggleSidebar: () => setSidebarCollapsed((current) => !current),
    refreshModelSettings: () => void refreshModelSettings(),
    refreshProviderCatalog: () => void refreshProviderCatalog(),
    refreshPluginState: () => void refreshPluginState(),
    updateRoleModel: (role, modelId) => void updateRoleModel(role, modelId),
    updateQueryRefiner: (enabled) => void updateQueryRefiner(enabled),
    updatePrivacyMode: (mode) => void updatePrivacyMode(mode),
    updateWorkerPool: (modelIds) => void updateWorkerPool(modelIds),
    updateLanguage: (language) => void updateLanguage(language),
    saveProvider,
    deleteProvider,
    fetchProviderModels,
    deleteSkill: (skillId) => void deleteSkill(skillId),
    installSkill: (path) => void installSkill(path),
    installMcp: (path) => void installMcp(path),
    registerExternalMcp,
    submit: (event) => void submitRun(event),
    stopRun: () => void stopRun(),
    createNewSession: () => void createNewSession(),
    selectSession: (sessionId) => void selectSession(sessionId),
    requestDeleteSession: (sessionId) => void requestDeleteSession(sessionId),
  };
}

function displayModelName(modelId: string, settings: ModelSettingsResponse): string {
  const model = settings.models.find((item) => item.id === modelId);
  return model ? displayModelNameForModel(model) : modelId;
}
