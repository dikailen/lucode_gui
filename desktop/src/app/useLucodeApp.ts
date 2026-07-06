import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  appendUserMessage,
  createInitialAppState,
  markPendingDeleteSession,
  markRunStarted,
  markRunStreamDisconnected,
  reduceRunEvent,
  removeSession,
  selectOrchestratorModelLabel,
  selectPrimaryModelLabel,
  setActiveSession,
  setModelLabel,
  setSessionMessages,
  setSessions,
  type AppState,
} from "./appState";
import {
  closeBottomShell as closeBottomShellState,
  closeRightDock,
  closeRightDockWindow as closeRightDockWindowState,
  clampRightDockWidth,
  createPanelLayoutState,
  DEFAULT_RIGHT_DOCK_WIDTH,
  openRightDockWindow,
  rightDockWindows,
  setRightDockWindowStatus,
  shouldForceCompactSidebar,
  toggleBottomShell as toggleBottomShellState,
  toggleRightDockTool,
  activeRightDockTool,
  type DockToolId,
  type RightDockWindow,
  type RightDockWindowStatus,
  type RightDockWindowTool,
} from "./panelLayout";
import { resolveRuntimeConfig } from "./runtimeEnv";
import { isTerminalRunEvent } from "./runEvents";
import { RuntimeClient } from "../shared/api/runtimeClient";
import type {
  ComfyUiInstallation,
  ComfyUiStateResponse,
  ExternalMcpPayload,
  ModelSettingsResponse,
  PluginStateResponse,
  ProviderCatalogResponse,
  ProviderModelsFetchPayload,
  ProviderModelsFetchResponse,
  ProviderSettingsPayload,
  RunApprovalDecision,
  RunEvent,
  RuntimeConfig,
  ServerSession,
  TerminalStateResponse,
} from "../shared/types";

export type WorkspaceId = "chat" | "plugins" | "settings";
export type { DockToolId, RightDockWindow, RightDockWindowTool } from "./panelLayout";

const RIGHT_DOCK_WIDTH_KEY = "lucode.rightDockWidth";
const DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188";

export type BrowserNavigationRequest = {
  id: number;
  url: string;
};

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
  rightDockWindows: RightDockWindow[];
  bottomShellOpen: boolean;
  rightDockWidth: number;
  sidebarCollapsed: boolean;
  settingsError: string;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "";
  comfyUiState: ComfyUiStateResponse | null;
  comfyUiError: string;
  comfyUiBusy: boolean;
  browserNavigationRequest: BrowserNavigationRequest | null;
  settingsSavingRole: string;
  terminalState: TerminalStateResponse | null;
  terminalError: string;
  terminalCommand: string;
  setInput: (value: string) => void;
  setTerminalCommand: (value: string) => void;
  toggleSettings: () => void;
  openSettings: () => void;
  showRightDockHome: () => void;
  activateRightDockTool: (tool: RightDockWindowTool) => void;
  collapseRightDock: () => void;
  closeRightDockWindow: (windowId: string) => void;
  toggleBottomShell: () => void;
  closeBottomShell: () => void;
  startRightDockResize: (clientX: number) => void;
  resetRightDockWidth: () => void;
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
  refreshComfyUiState: () => void;
  saveComfyUiUrl: (baseUrl: string, installPath?: string, launchScript?: string) => Promise<boolean>;
  detectComfyUiInstall: (installPath: string, launchScript?: string) => Promise<boolean>;
  checkComfyUi: (baseUrl?: string) => void;
  openComfyUiInBrowser: (baseUrl?: string) => void;
  refreshTerminalState: () => void;
  runTerminalCommand: () => void;
  stopTerminalCommand: () => void;
  clearTerminal: () => void;
  rerunTerminalCommand: () => void;
  setTerminalCwd: (cwd: string) => void;
  submit: (event: FormEvent) => void;
  stopRun: () => void;
  resolveRunApproval: (runId: string, approvalId: string, decision: RunApprovalDecision) => void;
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
  const [panelLayout, setPanelLayout] = useState(() => createPanelLayoutState());
  const rightDockTool = activeRightDockTool(panelLayout);
  const dockWindows = rightDockWindows(panelLayout);
  const bottomShellOpen = panelLayout.bottomShellOpen;
  const [viewportWidth, setViewportWidth] = useState(() => readViewportWidth());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const effectiveSidebarCollapsed = sidebarCollapsed || shouldForceCompactSidebar(viewportWidth, Boolean(rightDockTool));
  const [rightDockWidth, setRightDockWidth] = useState(() => loadRightDockWidth(readViewportWidth(), false));
  const [settingsError, setSettingsError] = useState("");
  const [pluginError, setPluginError] = useState("");
  const [pluginInstallingTarget, setPluginInstallingTarget] = useState<"skills" | "mcp" | "">("");
  const [comfyUiState, setComfyUiState] = useState<ComfyUiStateResponse | null>(null);
  const [comfyUiError, setComfyUiError] = useState("");
  const [comfyUiBusy, setComfyUiBusy] = useState(false);
  const [browserNavigationRequest, setBrowserNavigationRequest] = useState<BrowserNavigationRequest | null>(null);
  const [settingsSavingRole, setSettingsSavingRole] = useState("");
  const [terminalState, setTerminalState] = useState<TerminalStateResponse | null>(null);
  const [terminalError, setTerminalError] = useState("");
  const [terminalCommand, setTerminalCommand] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const browserNavigationSeqRef = useRef(0);

  function saveRightDockWidth(width: number) {
    const clamped = clampRightDockWidth(width, effectiveSidebarCollapsed, viewportWidth);
    setRightDockWidth(clamped);
    try {
      window.localStorage.setItem(RIGHT_DOCK_WIDTH_KEY, String(clamped));
    } catch {
      // Width persistence is optional.
    }
  }

  function startRightDockResize(clientX: number) {
    const startX = Number(clientX);
    const startWidth = rightDockWidth;
    const collapsed = effectiveSidebarCollapsed;
    const viewport = viewportWidth;
    const handleMove = (event: PointerEvent) => {
      saveRightDockWidth(startWidth + startX - event.clientX);
    };
    const handleEnd = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
      document.body.classList.remove("resizing-right-dock");
    };
    document.body.classList.add("resizing-right-dock");
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
    setRightDockWidth(clampRightDockWidth(startWidth, collapsed, viewport));
  }

  useEffect(() => {
    function handleResize() {
      setViewportWidth(readViewportWidth());
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    setRightDockWidth((current) => {
      const clamped = clampRightDockWidth(current, effectiveSidebarCollapsed, viewportWidth);
      if (clamped === current) {
        return current;
      }
      try {
        window.localStorage.setItem(RIGHT_DOCK_WIDTH_KEY, String(clamped));
      } catch {
        // Width persistence is optional.
      }
      return clamped;
    });
  }, [effectiveSidebarCollapsed, viewportWidth]);

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
              setState((current) => setModelLabel(current, selectOrchestratorModelLabel(settings, current.modelLabel)));
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
        void client
          .loadComfyUiState()
          .then((comfyUi) => {
            if (!cancelled) {
              setComfyUiError("");
              setComfyUiState(comfyUi);
            }
          })
          .catch((error) => {
            if (!cancelled) {
              setComfyUiError(error instanceof Error ? error.message : String(error));
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

  useEffect(() => {
    if (bottomShellOpen || rightDockTool === "terminal") {
      void refreshTerminalState();
    }
  }, [bottomShellOpen, rightDockTool]);

  useEffect(() => {
    if ((!bottomShellOpen && rightDockTool !== "terminal") || !terminalState?.running) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshTerminalState();
    }, 700);
    return () => window.clearInterval(timer);
  }, [bottomShellOpen, rightDockTool, terminalState?.running]);

  async function refreshModelSettings() {
    setSettingsError("");
    try {
      const settings = await client.loadModelSettings();
      setModelSettings(settings);
      setState((current) => setModelLabel(current, selectOrchestratorModelLabel(settings, current.modelLabel)));
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

  async function refreshComfyUiState() {
    setComfyUiError("");
    try {
      setComfyUiState(await client.loadComfyUiState());
    } catch (error) {
      setComfyUiError(error instanceof Error ? error.message : String(error));
    }
  }

  async function saveComfyUiUrl(baseUrl: string, installPath?: string, launchScript?: string): Promise<boolean> {
    const cleanBaseUrl = baseUrl.trim();
    if (!cleanBaseUrl || comfyUiBusy) {
      return false;
    }
    setComfyUiError("");
    setComfyUiBusy(true);
    try {
      const payload: { base_url: string; install_path?: string; launch_script?: string } = { base_url: cleanBaseUrl };
      if (installPath !== undefined) {
        payload.install_path = installPath.trim();
      }
      if (launchScript !== undefined) {
        payload.launch_script = launchScript.trim();
      }
      setComfyUiState(await client.saveComfyUiSettings(payload));
    } catch (error) {
      setComfyUiError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setComfyUiBusy(false);
    }
    return true;
  }

  async function detectComfyUiInstall(installPath: string, launchScript = ""): Promise<boolean> {
    const cleanPath = installPath.trim();
    if (!cleanPath || comfyUiBusy) {
      return false;
    }
    setComfyUiError("");
    setComfyUiBusy(true);
    try {
      const installation = await client.detectComfyUiInstallation({
        install_path: cleanPath,
        launch_script: launchScript.trim() || undefined,
      });
      setComfyUiState((current) => mergeComfyUiInstallation(current, installation));
      return installation.valid;
    } catch (error) {
      setComfyUiError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setComfyUiBusy(false);
    }
  }

  async function checkComfyUi(baseUrl = "") {
    if (comfyUiBusy) {
      return;
    }
    setComfyUiError("");
    setComfyUiBusy(true);
    try {
      const cleanBaseUrl = baseUrl.trim();
      setComfyUiState(await client.checkComfyUiConnection(cleanBaseUrl ? { base_url: cleanBaseUrl } : {}));
    } catch (error) {
      setComfyUiError(error instanceof Error ? error.message : String(error));
    } finally {
      setComfyUiBusy(false);
    }
  }

  function openComfyUiInBrowser(baseUrl = "") {
    const url = (baseUrl.trim() || comfyUiState?.base_url || DEFAULT_COMFYUI_URL).trim();
    if (!url) {
      return;
    }
    browserNavigationSeqRef.current += 1;
    setBrowserNavigationRequest({ id: browserNavigationSeqRef.current, url });
    setPanelLayout((current) => openRightDockWindow(current, "browser"));
  }

  async function refreshTerminalState() {
    setTerminalError("");
    try {
      setTerminalState(await client.loadTerminalState());
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function runTerminalCommand() {
    const command = terminalCommand.trim();
    if (!command || terminalState?.running) {
      return;
    }
    setTerminalError("");
    try {
      setTerminalState(await client.runTerminalCommand(command, { cwd: terminalState?.cwd || "" }));
      setTerminalCommand("");
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function stopTerminalCommand() {
    setTerminalError("");
    try {
      setTerminalState(await client.stopTerminalCommand());
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function clearTerminal() {
    setTerminalError("");
    try {
      setTerminalState(await client.clearTerminal());
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function rerunTerminalCommand() {
    if (terminalState?.running) {
      return;
    }
    setTerminalError("");
    try {
      setTerminalState(await client.rerunTerminalCommand());
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
  }

  async function setTerminalCwd(cwd: string) {
    const cleanCwd = cwd.trim();
    if (!cleanCwd || terminalState?.running) {
      return;
    }
    setTerminalError("");
    try {
      setTerminalState(await client.setTerminalCwd(cleanCwd));
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
    }
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
        setState((current) => setModelLabel(current, selectOrchestratorModelLabel(settings, current.modelLabel)));
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
      setState((current) => setModelLabel(current, selectOrchestratorModelLabel(nextSettings, current.modelLabel)));
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
      setState((current) => setModelLabel(current, selectOrchestratorModelLabel(nextSettings, "未设置")));
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

  async function resolveRunApproval(runId: string, approvalId: string, decision: RunApprovalDecision) {
    const cleanRunId = runId.trim();
    const cleanApprovalId = approvalId.trim();
    if (!cleanRunId || !cleanApprovalId) {
      return;
    }
    setRuntimeError("");
    try {
      await client.resolveRunApproval(cleanRunId, cleanApprovalId, decision);
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
      updateRightDockForRunEvent(payload);
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

  function updateRightDockForRunEvent(event: RunEvent) {
    if (isBrowserCapabilityEvent(event)) {
      const status = rightDockStatusFromBrowserEvent(event);
      setPanelLayout((current) => openRightDockWindow(current, "browser", { focus: false, status }));
      return;
    }
    if (event.type === "run.completed" || event.type === "run.cancelled") {
      setPanelLayout((current) => setRightDockWindowStatus(current, "browser", "idle"));
    }
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
    rightDockWindows: dockWindows,
    bottomShellOpen,
    rightDockWidth,
    sidebarCollapsed: effectiveSidebarCollapsed,
    settingsError,
    pluginError,
    pluginInstallingTarget,
    comfyUiState,
    comfyUiError,
    comfyUiBusy,
    browserNavigationRequest,
    settingsSavingRole,
    terminalState,
    terminalError,
    terminalCommand,
    setInput,
    setTerminalCommand,
    toggleSettings: () => {
      setPanelLayout((current) => closeRightDock(current));
      setActiveWorkspace((current) => (current === "settings" ? "chat" : "settings"));
    },
    openSettings: () => {
      setPanelLayout((current) => closeRightDock(current));
      setActiveWorkspace("settings");
    },
    showRightDockHome: () => setPanelLayout((current) => toggleRightDockTool(current, "home")),
    activateRightDockTool: (tool) => setPanelLayout((current) => toggleRightDockTool(current, tool)),
    collapseRightDock: () => setPanelLayout((current) => closeRightDock(current)),
    closeRightDockWindow: (windowId) => setPanelLayout((current) => closeRightDockWindowState(current, windowId)),
    toggleBottomShell: () => setPanelLayout((current) => toggleBottomShellState(current)),
    closeBottomShell: () => setPanelLayout((current) => closeBottomShellState(current)),
    startRightDockResize,
    resetRightDockWidth: () => saveRightDockWidth(DEFAULT_RIGHT_DOCK_WIDTH),
    closeSettings: () => setActiveWorkspace("chat"),
    switchWorkspace: (workspace) => {
      setActiveWorkspace(workspace);
      if (workspace === "plugins") {
        void refreshPluginState();
        void refreshComfyUiState();
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
    refreshComfyUiState: () => void refreshComfyUiState(),
    saveComfyUiUrl,
    detectComfyUiInstall,
    checkComfyUi: (baseUrl) => void checkComfyUi(baseUrl),
    openComfyUiInBrowser,
    refreshTerminalState: () => void refreshTerminalState(),
    runTerminalCommand: () => void runTerminalCommand(),
    stopTerminalCommand: () => void stopTerminalCommand(),
    clearTerminal: () => void clearTerminal(),
    rerunTerminalCommand: () => void rerunTerminalCommand(),
    setTerminalCwd: (cwd) => void setTerminalCwd(cwd),
    submit: (event) => void submitRun(event),
    stopRun: () => void stopRun(),
    resolveRunApproval: (runId, approvalId, decision) => void resolveRunApproval(runId, approvalId, decision),
    createNewSession: () => void createNewSession(),
    selectSession: (sessionId) => void selectSession(sessionId),
    requestDeleteSession: (sessionId) => void requestDeleteSession(sessionId),
  };
}

function isBrowserCapabilityEvent(event: RunEvent): boolean {
  const payload = event.payload || {};
  const toolName = stringPayloadField(payload, "tool_name") || stringPayloadField(payload, "tool");
  const action = stringPayloadField(payload, "action") || actionFromToolName(toolName);
  const haystack = `${toolName} ${action}`.toLowerCase();
  return haystack.includes("desktop_browser") || haystack.includes("browser_");
}

function rightDockStatusFromBrowserEvent(event: RunEvent): RightDockWindowStatus {
  const marker = `${event.type} ${stringPayloadField(event.payload || {}, "status")} ${stringPayloadField(
    event.payload || {},
    "outcome",
  )} ${stringPayloadField(event.payload || {}, "decision")}`.toLowerCase();
  if (/(approval|waiting|required)/.test(marker)) {
    return "attention";
  }
  if (/(failed|error|reject|denied)/.test(marker)) {
    return "attention";
  }
  return "running";
}

function actionFromToolName(toolName: string): string {
  const clean = toolName.trim();
  if (!clean) {
    return "";
  }
  return clean.includes(".") ? clean.split(".").at(-1) || "" : clean;
}

function stringPayloadField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value.trim() : "";
}

function mergeComfyUiInstallation(
  current: ComfyUiStateResponse | null,
  installation: ComfyUiInstallation,
): ComfyUiStateResponse {
  const cleanInstallation = {
    install_path: installation.install_path,
    resolved_root: installation.resolved_root,
    configured: installation.configured,
    valid: installation.valid,
    status: installation.status,
    launch_mode: installation.launch_mode,
    launch_script: installation.launch_script,
    launch_command: installation.launch_command,
    available_launch_scripts: installation.available_launch_scripts,
    validation_errors: installation.validation_errors,
  };
  return {
    schema_version: "comfyui.v1",
    base_url: current?.base_url || DEFAULT_COMFYUI_URL,
    configured: Boolean(current?.configured || installation.configured),
    status: current?.status || "unknown",
    last_error: current?.last_error || "",
    checked_at: current?.checked_at || "",
    endpoints: current?.endpoints || {},
    installation: cleanInstallation,
  };
}

function readViewportWidth(): number {
  if (typeof window === "undefined") {
    return 1280;
  }
  return window.innerWidth || 1280;
}

function loadRightDockWidth(viewportWidth: number, sidebarCollapsed: boolean): number {
  if (typeof window === "undefined") {
    return DEFAULT_RIGHT_DOCK_WIDTH;
  }
  try {
    const stored = Number.parseInt(window.localStorage.getItem(RIGHT_DOCK_WIDTH_KEY) || "", 10);
    return clampRightDockWidth(Number.isFinite(stored) ? stored : DEFAULT_RIGHT_DOCK_WIDTH, sidebarCollapsed, viewportWidth);
  } catch {
    return clampRightDockWidth(DEFAULT_RIGHT_DOCK_WIDTH, sidebarCollapsed, viewportWidth);
  }
}
