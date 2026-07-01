import { RightDock } from "./components/RightDock";
import { SessionSidebar } from "./components/SessionSidebar";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { createTranslator } from "./i18n";
import { useLucodeApp } from "./useLucodeApp";

export function App() {
  const controller = useLucodeApp();
  const { state } = controller;
  const language = controller.modelSettings?.ui_preferences?.language || "zh";
  const t = createTranslator(language);

  return (
    <main className={controller.rightDockTool ? "app-shell dock-open" : "app-shell"}>
      <SessionSidebar
        t={t}
        sessions={state.sessions}
        activeSessionId={state.activeSessionId}
        pendingDeleteSessionId={state.pendingDeleteSessionId}
        activeWorkspace={controller.activeWorkspace}
        collapsed={controller.sidebarCollapsed}
        runStatus={state.runStatus}
        createNewSession={controller.createNewSession}
        selectSession={controller.selectSession}
        requestDeleteSession={controller.requestDeleteSession}
        switchWorkspace={controller.switchWorkspace}
        openSettings={controller.openSettings}
        toggleSidebar={controller.toggleSidebar}
      />
      <WorkspacePanel
        t={t}
        activeWorkspace={controller.activeWorkspace}
        state={state}
        input={controller.input}
        runtimeError={controller.runtimeError}
        pluginState={controller.pluginState}
        pluginError={controller.pluginError}
        pluginInstallingTarget={controller.pluginInstallingTarget}
        modelSettings={controller.modelSettings}
        providerCatalog={controller.providerCatalog}
        settingsError={controller.settingsError}
        settingsSavingRole={controller.settingsSavingRole}
        setInput={controller.setInput}
        submit={controller.submit}
        stopRun={controller.stopRun}
        openDock={controller.openDock}
        openSettings={controller.openSettings}
        closeSettings={controller.closeSettings}
        refreshModelSettings={controller.refreshModelSettings}
        refreshProviderCatalog={controller.refreshProviderCatalog}
        updateRoleModel={controller.updateRoleModel}
        updateQueryRefiner={controller.updateQueryRefiner}
        updatePrivacyMode={controller.updatePrivacyMode}
        updateWorkerPool={controller.updateWorkerPool}
        updateLanguage={controller.updateLanguage}
        saveProvider={controller.saveProvider}
        deleteProvider={controller.deleteProvider}
        fetchProviderModels={controller.fetchProviderModels}
        refreshPluginState={controller.refreshPluginState}
        deleteSkill={controller.deleteSkill}
        installSkill={controller.installSkill}
        installMcp={controller.installMcp}
        registerExternalMcp={controller.registerExternalMcp}
      />
      <RightDock t={t} activeTool={controller.rightDockTool} openDock={controller.openDock}>
        {null}
      </RightDock>
      <div className="build-badge" title={controller.runtimeConfig.buildTime || "renderer build time unknown"}>
        {controller.runtimeConfig.rendererSource === "dev" ? "DEV" : "DIST"}
      </div>
    </main>
  );
}
