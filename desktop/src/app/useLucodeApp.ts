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
import { createSessionSearchDebouncer, type SessionSearchDebouncer } from "./sessionSearch";
import { mergeSessionPages } from "./sessionPagination";
import {
  MAX_ATTACHMENT_DRAFTS,
  mergeAttachmentDrafts,
  removeAttachmentDraft,
  type AttachmentDraft,
} from "./attachmentDrafts";
import { resolveDroppedFilePaths } from "./localFileDrop";
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
import { selectActiveRunForRenderer } from "./runRecovery";
import { runEventSequenceAction, runStreamReconnectDelay, shouldReconnectRunStream } from "./runStreamRecovery";
import type { SettingsTab } from "./settingsTabs";
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
  visibleSessions: ServerSession[];
  sessionSearchQuery: string;
  visibleSessionHasMore: boolean;
  visibleSessionLoadingMore: boolean;
  input: string;
  attachmentDrafts: AttachmentDraft[];
  runStarting: boolean;
  runtimeError: string;
  runtimeConfig: RuntimeConfig;
  modelSettings: ModelSettingsResponse | null;
  providerCatalog: ProviderCatalogResponse | null;
  pluginState: PluginStateResponse | null;
  activeWorkspace: WorkspaceId;
  settingsTab: SettingsTab;
  rightDockTool: DockToolId;
  rightDockWindows: RightDockWindow[];
  bottomShellOpen: boolean;
  rightDockWidth: number;
  sidebarCollapsed: boolean;
  settingsError: string;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "packages" | "";
  comfyUiState: ComfyUiStateResponse | null;
  comfyUiError: string;
  comfyUiBusy: boolean;
  browserNavigationRequest: BrowserNavigationRequest | null;
  settingsSavingRole: string;
  terminalState: TerminalStateResponse | null;
  terminalError: string;
  terminalCommand: string;
  setInput: (value: string) => void;
  chooseAttachments: () => void;
  addDroppedAttachments: (files: FileList | File[]) => void;
  removeAttachment: (attachmentId: string) => void;
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
  selectSettingsTab: (tab: SettingsTab) => void;
  toggleSidebar: () => void;
  refreshModelSettings: () => void;
  refreshProviderCatalog: () => void;
  refreshPluginState: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateModelReasoningEffort: (modelId: string, effort: string) => void;
  probeModelReasoningEffort: (modelId: string) => void;
  updateQueryRefiner: (enabled: boolean) => void;
  updatePrivacyMode: (mode: string) => void;
  updateWorkerPool: (modelIds: string[]) => void;
  updateLanguage: (language: string) => void;
  saveProvider: (payload: ProviderSettingsPayload, providerId?: string) => Promise<boolean>;
  deleteProvider: (providerId: string) => Promise<boolean>;
  fetchProviderModels: (payload: ProviderModelsFetchPayload) => Promise<ProviderModelsFetchResponse | null>;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  applySkillMetadata: (skillId: string, payload: { negative_queries: string[]; distinguish_from: Record<string, string> }) => Promise<boolean>;
  setSkillEnabled: (skillId: string, enabled: boolean) => Promise<boolean>;
  reindexSkillLibrary: () => Promise<boolean>;
  installMcp: (path: string) => void;
  installPluginPackage: (path: string) => void;
  deletePluginPackage: (pluginId: string) => void;
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
  searchSessions: (query: string) => void;
  loadMoreSessions: () => void;
  selectSession: (sessionId: string) => void;
  requestDeleteSession: (sessionId: string) => void;
};

export function useLucodeApp(): LucodeAppController {
  const runtimeConfig = useMemo(() => resolveRuntimeConfig(), []);
  const client = useMemo(() => new RuntimeClient(runtimeConfig), [runtimeConfig]);
  const [state, setState] = useState<AppState>(() => createInitialAppState());
  const [sessionSearchQuery, setSessionSearchQuery] = useState("");
  const [sessionSearchResults, setSessionSearchResults] = useState<ServerSession[] | null>(null);
  const [sessionNextCursor, setSessionNextCursor] = useState("");
  const [sessionHasMore, setSessionHasMore] = useState(false);
  const [sessionPageLoading, setSessionPageLoading] = useState(false);
  const [sessionSearchNextCursor, setSessionSearchNextCursor] = useState("");
  const [sessionSearchHasMore, setSessionSearchHasMore] = useState(false);
  const [sessionSearchPageLoading, setSessionSearchPageLoading] = useState(false);
  const [input, setInput] = useState("");
  const [attachmentDrafts, setAttachmentDrafts] = useState<AttachmentDraft[]>([]);
  const [runStarting, setRunStarting] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");
  const [modelSettings, setModelSettings] = useState<ModelSettingsResponse | null>(null);
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogResponse | null>(null);
  const [pluginState, setPluginState] = useState<PluginStateResponse | null>(null);
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceId>("chat");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("models");
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
  const [pluginInstallingTarget, setPluginInstallingTarget] = useState<"skills" | "mcp" | "packages" | "">("");
  const [comfyUiState, setComfyUiState] = useState<ComfyUiStateResponse | null>(null);
  const [comfyUiError, setComfyUiError] = useState("");
  const [comfyUiBusy, setComfyUiBusy] = useState(false);
  const [browserNavigationRequest, setBrowserNavigationRequest] = useState<BrowserNavigationRequest | null>(null);
  const [settingsSavingRole, setSettingsSavingRole] = useState("");
  const [terminalState, setTerminalState] = useState<TerminalStateResponse | null>(null);
  const [terminalError, setTerminalError] = useState("");
  const [terminalCommand, setTerminalCommand] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const activeRunRef = useRef({ activeRunId: state.activeRunId, runStatus: state.runStatus });
  const runStreamCursorRef = useRef(new Map<string, number>());
  const runStreamReconnectAttemptsRef = useRef(new Map<string, number>());
  const runStreamReconnectTimerRef = useRef<number | null>(null);
  const runStreamGenerationRef = useRef(0);
  const runStartingRef = useRef(false);
  const pendingRunRequestRef = useRef<{ fingerprint: string; requestId: string; runId: string } | null>(null);
  const browserNavigationSeqRef = useRef(0);
  const sessionSearchSeqRef = useRef(0);
  const sessionPageLoadingRef = useRef(false);
  const sessionSearchPageLoadingRef = useRef(false);
  const sessionSearchDebouncerRef = useRef<SessionSearchDebouncer | null>(null);
  if (sessionSearchDebouncerRef.current === null) {
    sessionSearchDebouncerRef.current = createSessionSearchDebouncer();
  }
  const searchActive = Boolean(sessionSearchQuery.trim());
  const visibleSessions = searchActive && sessionSearchResults ? sessionSearchResults : state.sessions;
  const visibleSessionHasMore = searchActive ? sessionSearchHasMore : sessionHasMore;
  const visibleSessionLoadingMore = searchActive ? sessionSearchPageLoading : sessionPageLoading;
  activeRunRef.current = { activeRunId: state.activeRunId, runStatus: state.runStatus };

  useEffect(() => () => sessionSearchDebouncerRef.current?.cancel(), []);

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
        const [models, sessionPage] = await Promise.all([client.listModels(), client.listSessionPage()]);
        if (cancelled) {
          return;
        }
        const sessions = sessionPage.sessions;
        setRuntimeError("");
        setSessionNextCursor(sessionPage.next_cursor);
        setSessionHasMore(sessionPage.has_more);
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
        let recoveredRun = null;
        try {
          recoveredRun = selectActiveRunForRenderer(await client.listActiveRuns(), sessions[0]?.session_id || "");
        } catch {
          // Active-run discovery is optional while the Runtime is being upgraded.
        }
        const activeSessionId = recoveredRun?.session_id || sessions[0]?.session_id;
        if (activeSessionId) {
          const messages = await client.loadSessionMessages(activeSessionId);
          if (!cancelled) {
            setState((current) => {
              const loaded = setSessionMessages(current, activeSessionId, messages);
              return recoveredRun ? markRunStarted(loaded, recoveredRun.session_id, recoveredRun.run_id) : loaded;
            });
            if (recoveredRun) {
              activeRunRef.current = { activeRunId: recoveredRun.run_id, runStatus: "running" };
              openRunStream(recoveredRun.run_id);
            }
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
      clearRunStreamReconnectTimer();
      runStreamGenerationRef.current += 1;
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

  async function applySkillMetadata(
    skillId: string,
    payload: {
      categories?: string[];
      tags?: string[];
      use_when?: string[];
      do_not_use_when?: string[];
      negative_queries: string[];
      distinguish_from: Record<string, string>;
    },
  ): Promise<boolean> {
    if (!skillId) return false;
    setPluginError("");
    try {
      setPluginState(await client.applySkillMetadata(skillId, payload));
      return true;
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }

  async function setSkillEnabled(skillId: string, enabled: boolean): Promise<boolean> {
    if (!skillId) return false;
    setPluginError("");
    try {
      setPluginState(await client.setSkillEnabled(skillId, enabled));
      return true;
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }

  async function reindexSkillLibrary(): Promise<boolean> {
    setPluginError("");
    try {
      setPluginState(await client.reindexSkillLibrary());
      return true;
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
      return false;
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

  async function installPluginPackage(path: string) {
    const cleanPath = path.trim();
    if (!cleanPath || pluginInstallingTarget) {
      return;
    }
    setPluginError("");
    setPluginInstallingTarget("packages");
    try {
      setPluginState(await client.installPluginPackage(cleanPath));
    } catch (error) {
      setPluginError(error instanceof Error ? error.message : String(error));
    } finally {
      setPluginInstallingTarget("");
    }
  }

  async function deletePluginPackage(pluginId: string) {
    const cleanPluginId = pluginId.trim();
    if (!cleanPluginId || pluginInstallingTarget) {
      return;
    }
    setPluginError("");
    setPluginInstallingTarget("packages");
    try {
      setPluginState(await client.deletePluginPackage(cleanPluginId));
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

  async function updateModelReasoningEffort(modelId: string, effort: string) {
    if (!modelId || !effort || settingsSavingRole) return;
    setSettingsError("");
    setSettingsSavingRole(`reasoning_effort:${modelId}`);
    try {
      const settings = await client.updateModelReasoningEffort(modelId, effort);
      setModelSettings(settings);
      setState((current) => setModelLabel(current, selectOrchestratorModelLabel(settings, current.modelLabel)));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSavingRole("");
    }
  }

  async function probeModelReasoningEffort(modelId: string) {
    if (!modelId || settingsSavingRole) return;
    setSettingsError("");
    setSettingsSavingRole(`reasoning_probe:${modelId}`);
    try {
      const settings = await client.probeModelReasoningEffort(modelId);
      setModelSettings(settings);
      setState((current) => setModelLabel(current, selectOrchestratorModelLabel(settings, current.modelLabel)));
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
    if (!text || state.runStatus === "running" || runStartingRef.current) {
      return;
    }
    runStartingRef.current = true;
    setRunStarting(true);
      setRuntimeError("");
      try {
        const session = await ensureSession(text);
        const fingerprint = runRequestFingerprint(session.session_id, text, attachmentDrafts);
        const pendingRequest = pendingRunRequestRef.current;
        const request = pendingRequest && pendingRequest.fingerprint === fingerprint
          ? pendingRequest
          : { fingerprint, requestId: createClientRequestId(), runId: "" };
        pendingRunRequestRef.current = request;
        const run = await client.startRun(
          session.session_id,
          text,
          attachmentDrafts.map((item) => ({ path: item.path })),
          request.requestId,
        );
        request.runId = run.run_id;
      setState((current) => appendUserMessage(
        current,
        session.session_id,
        text,
        run.attachments?.length ? { attachments: run.attachments } : undefined,
      ));
      setInput("");
      setAttachmentDrafts([]);
      setState((current) => markRunStarted(current, session.session_id, run.run_id));
      activeRunRef.current = { activeRunId: run.run_id, runStatus: "running" };
      openRunStream(run.run_id);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    } finally {
      runStartingRef.current = false;
      setRunStarting(false);
    }
  }

  function addAttachmentPaths(paths: string[]) {
    setAttachmentDrafts((current) => {
      const result = mergeAttachmentDrafts(current, paths);
      if (result.rejectedCount) {
        setRuntimeError(`每条消息最多可添加 ${MAX_ATTACHMENT_DRAFTS} 个文件。`);
      }
      return result.drafts;
    });
  }

  async function chooseAttachments() {
    const picker = typeof window === "undefined" ? undefined : window.lucodeDesktop?.chooseAttachmentFiles;
    if (!picker) {
      setRuntimeError("当前运行环境不支持读取本地附件，请使用桌面版。");
      return;
    }
    setRuntimeError("");
    try {
      addAttachmentPaths(await picker());
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  function addDroppedAttachments(files: FileList | File[]) {
    const resolver = typeof window === "undefined" ? undefined : window.lucodeDesktop?.droppedFilePath;
    if (!resolver) {
      setRuntimeError("当前运行环境无法读取拖入文件的本地路径，请使用桌面版的添加文件按钮。");
      return;
    }
    const paths = resolveDroppedFilePaths(files, resolver);
    if (!paths.length && Array.from(files || []).length) {
      setRuntimeError("没有读取到可用的本地文件路径，请改用添加文件按钮。");
      return;
    }
    setRuntimeError("");
    addAttachmentPaths(paths);
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

  function clearRunStreamReconnectTimer() {
    if (runStreamReconnectTimerRef.current !== null) {
      window.clearTimeout(runStreamReconnectTimerRef.current);
      runStreamReconnectTimerRef.current = null;
    }
  }

  function openRunStream(runId: string) {
    clearRunStreamReconnectTimer();
    const generation = ++runStreamGenerationRef.current;
    const previousSocket = socketRef.current;
    socketRef.current = null;
    previousSocket?.close();
    const socket = client.openRunEventSocket(runId, runStreamCursorRef.current.get(runId) || 0);
    let sawTerminalEvent = false;
    socketRef.current = socket;
    socket.addEventListener("message", (event) => {
      if (runStreamGenerationRef.current !== generation || socketRef.current !== socket) {
        return;
      }
      let payload: RunEvent;
      try {
        payload = JSON.parse(event.data) as RunEvent;
      } catch {
        setRuntimeError("运行事件解析失败，请查看 Runtime 日志。");
        return;
      }
      if (payload.run_id !== runId) {
        return;
      }
      const lastReceivedSeq = runStreamCursorRef.current.get(runId) || 0;
      const sequenceAction = runEventSequenceAction(lastReceivedSeq, payload.seq);
      if (sequenceAction === "duplicate") {
        return;
      }
      if (sequenceAction === "replay_from_cursor") {
        socket.close(4001, "event sequence gap");
        return;
      }
      runStreamCursorRef.current.set(runId, payload.seq);
      runStreamReconnectAttemptsRef.current.delete(runId);
      setState((current) => reduceRunEvent(current, payload));
      updateRightDockForRunEvent(payload);
      if (isTerminalRunEvent(payload.type)) {
        if (pendingRunRequestRef.current?.runId === payload.run_id) {
          pendingRunRequestRef.current = null;
        }
        sawTerminalEvent = true;
        clearRunStreamReconnectTimer();
        runStreamCursorRef.current.delete(runId);
        runStreamReconnectAttemptsRef.current.delete(runId);
        socket.close(1000, "run finished");
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        void refreshSessions();
        void refreshModelSettings();
      }
    });
    socket.addEventListener("close", () => {
      if (runStreamGenerationRef.current !== generation || socketRef.current !== socket) {
        return;
      }
      socketRef.current = null;
      if (!shouldReconnectRunStream({
        activeRunId: activeRunRef.current.activeRunId,
        runStatus: activeRunRef.current.runStatus,
        runId,
        sawTerminalEvent,
      })) {
        return;
      }
      const attempt = (runStreamReconnectAttemptsRef.current.get(runId) || 0) + 1;
      const delay = runStreamReconnectDelay(attempt);
      if (delay === null) {
        setState((current) =>
          current.activeRunId === runId && current.runStatus === "running"
            ? markRunStreamDisconnected(current, current.activeSessionId, "重连次数已达上限")
            : current,
        );
        setRuntimeError("运行事件流重连失败，请确认 Runtime Server 仍在运行。");
        return;
      }
      runStreamReconnectAttemptsRef.current.set(runId, attempt);
      runStreamReconnectTimerRef.current = window.setTimeout(() => {
        runStreamReconnectTimerRef.current = null;
        if (runStreamGenerationRef.current !== generation) {
          return;
        }
        if (!shouldReconnectRunStream({
          activeRunId: activeRunRef.current.activeRunId,
          runStatus: activeRunRef.current.runStatus,
          runId,
          sawTerminalEvent: false,
        })) {
          return;
        }
        openRunStream(runId);
      }, delay);
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
    const activeSearchQuery = sessionSearchQuery.trim();
    const searchSeq = ++sessionSearchSeqRef.current;
    try {
      const sessionPage = await client.listSessionPage();
      setState((current) => setSessions(current, sessionPage.sessions));
      setSessionNextCursor(sessionPage.next_cursor);
      setSessionHasMore(sessionPage.has_more);
      if (activeSearchQuery) {
        const searchPage = await client.listSessionPage({ query: activeSearchQuery });
        if (sessionSearchSeqRef.current === searchSeq) {
          setSessionSearchResults(searchPage.sessions);
          setSessionSearchNextCursor(searchPage.next_cursor);
          setSessionSearchHasMore(searchPage.has_more);
        }
      }
    } catch {
      // Session refresh is auxiliary; the run result remains visible.
    }
  }

  function searchSessions(query: string) {
    setSessionSearchQuery(query);
    const cleanQuery = query.trim();
    const searchSeq = ++sessionSearchSeqRef.current;
    sessionSearchDebouncerRef.current?.cancel();
    setSessionSearchNextCursor("");
    setSessionSearchHasMore(false);
    setSessionSearchPageLoading(false);
    if (!cleanQuery) {
      setSessionSearchResults(null);
      void client
        .listSessionPage()
        .then((page) => {
          if (sessionSearchSeqRef.current === searchSeq) {
            setState((current) => setSessions(current, page.sessions));
            setSessionNextCursor(page.next_cursor);
            setSessionHasMore(page.has_more);
          }
        })
        .catch(() => {
          // Session search is auxiliary; keep the current list if refresh fails.
        });
      return;
    }
    sessionSearchDebouncerRef.current?.schedule(() => {
      void client
        .listSessionPage({ query: cleanQuery })
        .then((page) => {
          if (sessionSearchSeqRef.current === searchSeq) {
            setSessionSearchResults(page.sessions);
            setSessionSearchNextCursor(page.next_cursor);
            setSessionSearchHasMore(page.has_more);
          }
        })
        .catch(() => {
          if (sessionSearchSeqRef.current === searchSeq) {
            setSessionSearchResults([]);
          }
        });
    });
  }

  async function loadMoreSessions() {
    const activeSearchQuery = sessionSearchQuery.trim();
    if (activeSearchQuery) {
      if (!sessionSearchHasMore || !sessionSearchNextCursor || sessionSearchPageLoadingRef.current) {
        return;
      }
      const searchSeq = sessionSearchSeqRef.current;
      sessionSearchPageLoadingRef.current = true;
      setSessionSearchPageLoading(true);
      try {
        const page = await client.listSessionPage({
          query: activeSearchQuery,
          cursor: sessionSearchNextCursor,
        });
        if (sessionSearchSeqRef.current === searchSeq) {
          setSessionSearchResults((current) => mergeSessionPages(current ?? [], page.sessions));
          setSessionSearchNextCursor(page.next_cursor);
          setSessionSearchHasMore(page.has_more);
        }
      } catch {
        // Pagination is auxiliary; keep the pages already visible.
      } finally {
        sessionSearchPageLoadingRef.current = false;
        setSessionSearchPageLoading(false);
      }
      return;
    }
    if (!sessionHasMore || !sessionNextCursor || sessionPageLoadingRef.current) {
      return;
    }
    sessionPageLoadingRef.current = true;
    setSessionPageLoading(true);
    try {
      const page = await client.listSessionPage({ cursor: sessionNextCursor });
      setState((current) => setSessions(current, mergeSessionPages(current.sessions, page.sessions)));
      setSessionNextCursor(page.next_cursor);
      setSessionHasMore(page.has_more);
    } catch {
      // Pagination is auxiliary; keep the pages already visible.
    } finally {
      sessionPageLoadingRef.current = false;
      setSessionPageLoading(false);
    }
  }

  async function createNewSession() {
    try {
      const session = await client.createSession("新会话");
      sessionSearchSeqRef.current += 1;
      sessionSearchDebouncerRef.current?.cancel();
      setSessionSearchQuery("");
      setSessionSearchResults(null);
      let sessionPage: Awaited<ReturnType<RuntimeClient["listSessionPage"]>> | null = null;
      try {
        sessionPage = await client.listSessionPage();
      } catch {
        sessionPage = null;
      }
      if (sessionPage) {
        setSessionNextCursor(sessionPage.next_cursor);
        setSessionHasMore(sessionPage.has_more);
      }
      setRuntimeError("");
      setAttachmentDrafts([]);
      setActiveWorkspace("chat");
      setState((current) =>
        setSessionMessages(
          setActiveSession(
            setSessions(
              current,
              sessionPage?.sessions ?? [session, ...current.sessions.filter((item) => item.session_id !== session.session_id)],
            ),
            session.session_id,
          ),
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
      setAttachmentDrafts([]);
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
      setSessionSearchResults((current) => current?.filter((session) => session.session_id !== sessionId) ?? null);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }

  return {
    state,
    visibleSessions,
    sessionSearchQuery,
    visibleSessionHasMore,
    visibleSessionLoadingMore,
    input,
    attachmentDrafts,
    runStarting,
    runtimeError,
    runtimeConfig,
    modelSettings,
    providerCatalog,
    pluginState,
    activeWorkspace,
    settingsTab,
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
    chooseAttachments: () => void chooseAttachments(),
    addDroppedAttachments,
    removeAttachment: (attachmentId) => setAttachmentDrafts((current) => removeAttachmentDraft(current, attachmentId)),
    setTerminalCommand,
    toggleSettings: () => {
      setPanelLayout((current) => closeRightDock(current));
      setActiveWorkspace((current) => (current === "settings" ? "chat" : "settings"));
    },
    openSettings: () => {
      setPanelLayout((current) => closeRightDock(current));
      setSidebarCollapsed(false);
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
        setPanelLayout((current) => closeRightDock(current));
        void refreshPluginState();
        void refreshComfyUiState();
      } else if (workspace === "settings") {
        setSidebarCollapsed(false);
      }
    },
    selectSettingsTab: setSettingsTab,
    toggleSidebar: () => setSidebarCollapsed((current) => !current),
    refreshModelSettings: () => void refreshModelSettings(),
    refreshProviderCatalog: () => void refreshProviderCatalog(),
    refreshPluginState: () => void refreshPluginState(),
    updateRoleModel: (role, modelId) => void updateRoleModel(role, modelId),
    updateModelReasoningEffort: (modelId, effort) => void updateModelReasoningEffort(modelId, effort),
    probeModelReasoningEffort: (modelId) => void probeModelReasoningEffort(modelId),
    updateQueryRefiner: (enabled) => void updateQueryRefiner(enabled),
    updatePrivacyMode: (mode) => void updatePrivacyMode(mode),
    updateWorkerPool: (modelIds) => void updateWorkerPool(modelIds),
    updateLanguage: (language) => void updateLanguage(language),
    saveProvider,
    deleteProvider,
    fetchProviderModels,
      deleteSkill: (skillId) => void deleteSkill(skillId),
      installSkill: (path) => void installSkill(path),
      applySkillMetadata,
    setSkillEnabled,
    reindexSkillLibrary,
    installMcp: (path) => void installMcp(path),
    installPluginPackage: (path) => void installPluginPackage(path),
    deletePluginPackage: (pluginId) => void deletePluginPackage(pluginId),
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
    searchSessions: (query) => void searchSessions(query),
    loadMoreSessions: () => void loadMoreSessions(),
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

function runRequestFingerprint(sessionId: string, input: string, attachments: AttachmentDraft[]): string {
  return [
    sessionId,
    input,
    ...attachments.map((item) => item.path).sort(),
  ].join("\u0000");
}

function createClientRequestId(): string {
  const randomId = globalThis.crypto?.randomUUID?.();
  if (randomId) {
    return `desktop_${randomId}`;
  }
  return `desktop_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
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
