import type { CSSProperties } from "react";

import { BrowserPanel } from "./components/BrowserPanel";
import { ReviewPanel } from "./components/ReviewPanel";
import { RightDock } from "./components/RightDock";
import { SessionSidebar } from "./components/SessionSidebar";
import { TerminalPanel } from "./components/TerminalPanel";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { createTranslator } from "./i18n";
import { shouldRenderSessionSidebar } from "./panelLayout";
import { useLucodeApp } from "./useLucodeApp";

export function App() {
  const controller = useLucodeApp();
  const { state } = controller;
  const language = controller.modelSettings?.ui_preferences?.language || "zh";
  const t = createTranslator(language);
  const showSessionSidebar = shouldRenderSessionSidebar(controller.activeWorkspace);
  const shellStyle = {
    "--right-dock-width": `${controller.rightDockWidth}px`,
  } as CSSProperties;

  return (
    <main
      className={[
        "app-shell",
        controller.rightDockTool ? "dock-open" : "",
        controller.bottomShellOpen ? "bottom-shell-open" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={shellStyle}
    >
      {showSessionSidebar ? (
        <SessionSidebar
          t={t}
          sessions={controller.visibleSessions}
          sessionSearchQuery={controller.sessionSearchQuery}
          activeSessionId={state.activeSessionId}
          pendingDeleteSessionId={state.pendingDeleteSessionId}
          activeWorkspace={controller.activeWorkspace}
          settingsTab={controller.settingsTab}
          collapsed={controller.sidebarCollapsed}
          runStatus={state.runStatus}
          hasMoreSessions={controller.visibleSessionHasMore}
          loadingMoreSessions={controller.visibleSessionLoadingMore}
          createNewSession={controller.createNewSession}
          searchSessions={controller.searchSessions}
          loadMoreSessions={controller.loadMoreSessions}
          selectSession={controller.selectSession}
          requestDeleteSession={controller.requestDeleteSession}
          switchWorkspace={controller.switchWorkspace}
          selectSettingsTab={controller.selectSettingsTab}
          openSettings={controller.openSettings}
          toggleSidebar={controller.toggleSidebar}
        />
      ) : null}
      <div className="workspace-column">
        <div className="workspace-main">
          <WorkspacePanel
            t={t}
            activeWorkspace={controller.activeWorkspace}
            settingsTab={controller.settingsTab}
            state={state}
            input={controller.input}
            attachmentDrafts={controller.attachmentDrafts}
            runStarting={controller.runStarting}
            runtimeError={controller.runtimeError}
            pluginState={controller.pluginState}
            pluginError={controller.pluginError}
            pluginInstallingTarget={controller.pluginInstallingTarget}
            comfyUiState={controller.comfyUiState}
            comfyUiError={controller.comfyUiError}
            comfyUiBusy={controller.comfyUiBusy}
            modelSettings={controller.modelSettings}
            providerCatalog={controller.providerCatalog}
            settingsError={controller.settingsError}
            settingsSavingRole={controller.settingsSavingRole}
            bottomShellOpen={controller.bottomShellOpen}
            rightDockOpen={Boolean(controller.rightDockTool)}
            setInput={controller.setInput}
            chooseAttachments={controller.chooseAttachments}
            addDroppedAttachments={controller.addDroppedAttachments}
            removeAttachment={controller.removeAttachment}
            submit={controller.submit}
            stopRun={controller.stopRun}
            showRightDockHome={controller.showRightDockHome}
            activateRightDockTool={controller.activateRightDockTool}
            collapseRightDock={controller.collapseRightDock}
            toggleBottomShell={controller.toggleBottomShell}
            openSettings={controller.openSettings}
            closeSettings={controller.closeSettings}
            selectSettingsTab={controller.selectSettingsTab}
            refreshModelSettings={controller.refreshModelSettings}
            refreshProviderCatalog={controller.refreshProviderCatalog}
            updateRoleModel={controller.updateRoleModel}
            updateModelReasoningEffort={controller.updateModelReasoningEffort}
            probeModelReasoningEffort={controller.probeModelReasoningEffort}
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
              applySkillMetadata={controller.applySkillMetadata}
              setSkillEnabled={controller.setSkillEnabled}
              reindexSkillLibrary={controller.reindexSkillLibrary}
              installMcp={controller.installMcp}
            installPluginPackage={controller.installPluginPackage}
            deletePluginPackage={controller.deletePluginPackage}
            registerExternalMcp={controller.registerExternalMcp}
            refreshComfyUiState={controller.refreshComfyUiState}
            saveComfyUiUrl={controller.saveComfyUiUrl}
            detectComfyUiInstall={controller.detectComfyUiInstall}
            checkComfyUi={controller.checkComfyUi}
            openComfyUiInBrowser={controller.openComfyUiInBrowser}
          />
        </div>
        {controller.bottomShellOpen ? (
          <section className="bottom-shell-dock" aria-label={t("terminal.tabsAria")}>
            <TerminalPanel
              t={t}
              terminalState={controller.terminalState}
              terminalError={controller.terminalError}
              terminalCommand={controller.terminalCommand}
              setTerminalCommand={controller.setTerminalCommand}
              runTerminalCommand={controller.runTerminalCommand}
              clearTerminal={controller.clearTerminal}
              rerunTerminalCommand={controller.rerunTerminalCommand}
              setTerminalCwd={controller.setTerminalCwd}
              closeTerminal={controller.closeBottomShell}
            />
          </section>
        ) : null}
      </div>
      <RightDock
        t={t}
        activeTool={controller.rightDockTool}
        windows={controller.rightDockWindows}
        activateTool={controller.activateRightDockTool}
        collapseDock={controller.collapseRightDock}
        closeWindow={controller.closeRightDockWindow}
        startResize={controller.startRightDockResize}
        resetWidth={controller.resetRightDockWidth}
      >
        {controller.rightDockTool === "review" ? (
          <ReviewPanel
            state={state}
            openBrowser={() => controller.activateRightDockTool("browser")}
            resolveApproval={controller.resolveRunApproval}
          />
        ) : controller.rightDockTool === "terminal" ? (
          <TerminalPanel
            t={t}
            terminalState={controller.terminalState}
            terminalError={controller.terminalError}
            terminalCommand={controller.terminalCommand}
            setTerminalCommand={controller.setTerminalCommand}
            runTerminalCommand={controller.runTerminalCommand}
            clearTerminal={controller.clearTerminal}
            rerunTerminalCommand={controller.rerunTerminalCommand}
            setTerminalCwd={controller.setTerminalCwd}
            closeTerminal={() => controller.closeRightDockWindow("terminal")}
          />
        ) : controller.rightDockTool === "browser" ? (
          <BrowserPanel
            t={t}
            onClose={() => controller.closeRightDockWindow("browser")}
            requestedNavigation={controller.browserNavigationRequest}
          />
        ) : null}
      </RightDock>
      <div className="build-badge" title={controller.runtimeConfig.buildTime || "renderer build time unknown"}>
        {controller.runtimeConfig.rendererSource === "dev" ? "DEV" : "DIST"}
      </div>
    </main>
  );
}
