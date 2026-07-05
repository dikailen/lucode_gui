import { ChatPane } from "./ChatPane";
import { PluginsPanel } from "./PluginsPanel";
import { SettingsPanel } from "./SettingsPanel";
import type { RightDockWindowTool, WorkspaceId } from "../useLucodeApp";
import type { AppState } from "../appState";
import type { Translator } from "../i18n";
import type {
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
  pluginInstallingTarget: "skills" | "mcp" | "";
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
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
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
  registerExternalMcp,
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
        refreshPluginState={refreshPluginState}
        deleteSkill={deleteSkill}
        installSkill={installSkill}
        installMcp={installMcp}
        registerExternalMcp={registerExternalMcp}
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
