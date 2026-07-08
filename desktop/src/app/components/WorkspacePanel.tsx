import { ChatPane } from "./ChatPane";
import { PluginsPanel } from "./PluginsPanel";
import { SettingsPanel } from "./SettingsPanel";
import type { RightDockWindowTool, WorkspaceId } from "../useLucodeApp";
import type { AppState } from "../appState";
import type { Translator } from "../i18n";
import type {
  ComfyUiStateResponse,
  ExternalMcpPayload,
  ModelSettingsResponse,
  PluginStateResponse,
  ProviderCatalogResponse,
  ProviderModelsFetchPayload,
  ProviderModelsFetchResponse,
  ProviderSettingsPayload,
} from "../../shared/types";

export type WorkspacePanelProps = {
  t: Translator;
  activeWorkspace: WorkspaceId;
  state: AppState;
  input: string;
  runtimeError: string;
  pluginState: PluginStateResponse | null;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "packages" | "";
  comfyUiState: ComfyUiStateResponse | null;
  comfyUiError: string;
  comfyUiBusy: boolean;
  modelSettings: ModelSettingsResponse | null;
  providerCatalog: ProviderCatalogResponse | null;
  settingsError: string;
  settingsSavingRole: string;
  bottomShellOpen: boolean;
  rightDockOpen: boolean;
  setInput: (value: string) => void;
  submit: React.FormEventHandler;
  stopRun: () => void;
  showRightDockHome: () => void;
  activateRightDockTool: (tool: RightDockWindowTool) => void;
  collapseRightDock: () => void;
  toggleBottomShell: () => void;
  openSettings: () => void;
  closeSettings: () => void;
  refreshModelSettings: () => void;
  refreshProviderCatalog: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateQueryRefiner: (enabled: boolean) => void;
  updatePrivacyMode: (mode: string) => void;
  updateWorkerPool: (modelIds: string[]) => void;
  updateLanguage: (language: string) => void;
  saveProvider: (payload: ProviderSettingsPayload, providerId?: string) => Promise<boolean>;
  deleteProvider: (providerId: string) => Promise<boolean>;
  fetchProviderModels: (payload: ProviderModelsFetchPayload) => Promise<ProviderModelsFetchResponse | null>;
  refreshPluginState: () => void;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  installMcp: (path: string) => void;
  installPluginPackage: (path: string) => void;
  deletePluginPackage: (pluginId: string) => void;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
  refreshComfyUiState: () => void;
  saveComfyUiUrl: (baseUrl: string, installPath?: string, launchScript?: string) => Promise<boolean>;
  detectComfyUiInstall: (installPath: string, launchScript?: string) => Promise<boolean>;
  checkComfyUi: (baseUrl?: string) => void;
  openComfyUiInBrowser: (baseUrl?: string) => void;
};

export function WorkspacePanel({
  t,
  activeWorkspace,
  state,
  input,
  runtimeError,
  pluginState,
  pluginError,
  pluginInstallingTarget,
  comfyUiState,
  comfyUiError,
  comfyUiBusy,
  modelSettings,
  providerCatalog,
  settingsError,
  settingsSavingRole,
  bottomShellOpen,
  rightDockOpen,
  setInput,
  submit,
  stopRun,
  showRightDockHome,
  activateRightDockTool,
  collapseRightDock,
  toggleBottomShell,
  openSettings,
  closeSettings,
  refreshModelSettings,
  refreshProviderCatalog,
  updateRoleModel,
  updateQueryRefiner,
  updatePrivacyMode,
  updateWorkerPool,
  updateLanguage,
  saveProvider,
  deleteProvider,
  fetchProviderModels,
  refreshPluginState,
  deleteSkill,
  installSkill,
  installMcp,
  installPluginPackage,
  deletePluginPackage,
  registerExternalMcp,
  refreshComfyUiState,
  saveComfyUiUrl,
  detectComfyUiInstall,
  checkComfyUi,
  openComfyUiInBrowser,
}: WorkspacePanelProps) {
  if (activeWorkspace === "settings") {
    return (
      <SettingsPanel
        t={t}
        modelSettings={modelSettings}
        providerCatalog={providerCatalog}
        settingsError={settingsError}
        settingsSavingRole={settingsSavingRole}
        closeSettings={closeSettings}
        refreshModelSettings={refreshModelSettings}
        refreshProviderCatalog={refreshProviderCatalog}
        updateRoleModel={updateRoleModel}
        updateQueryRefiner={updateQueryRefiner}
        updatePrivacyMode={updatePrivacyMode}
        updateWorkerPool={updateWorkerPool}
        updateLanguage={updateLanguage}
        saveProvider={saveProvider}
        deleteProvider={deleteProvider}
        fetchProviderModels={fetchProviderModels}
      />
    );
  }
  if (activeWorkspace === "plugins") {
    return (
      <PluginsPanel
        t={t}
        pluginState={pluginState}
        pluginError={pluginError}
        pluginInstallingTarget={pluginInstallingTarget}
        comfyUiState={comfyUiState}
        comfyUiError={comfyUiError}
        comfyUiBusy={comfyUiBusy}
        refreshPluginState={refreshPluginState}
        deleteSkill={deleteSkill}
        installSkill={installSkill}
        installMcp={installMcp}
        installPluginPackage={installPluginPackage}
        deletePluginPackage={deletePluginPackage}
        registerExternalMcp={registerExternalMcp}
        refreshComfyUiState={refreshComfyUiState}
        saveComfyUiUrl={saveComfyUiUrl}
        detectComfyUiInstall={detectComfyUiInstall}
        checkComfyUi={checkComfyUi}
        openComfyUiInBrowser={openComfyUiInBrowser}
      />
    );
  }
  return (
    <ChatPane
      t={t}
      state={state}
      input={input}
      runtimeError={runtimeError}
      modelSettings={modelSettings}
      setInput={setInput}
      submit={submit}
      stopRun={stopRun}
      bottomShellOpen={bottomShellOpen}
      rightDockOpen={rightDockOpen}
      showRightDockHome={showRightDockHome}
      activateRightDockTool={activateRightDockTool}
      collapseRightDock={collapseRightDock}
      toggleBottomShell={toggleBottomShell}
      openSettings={openSettings}
      updateRoleModel={updateRoleModel}
    />
  );
}
