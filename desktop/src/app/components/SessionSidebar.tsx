import { formatSessionTime, type RunStatus } from "../appState";
import type { Translator } from "../i18n";
import { shouldLoadNextSessionPage } from "../sessionPagination";
import { SETTINGS_TABS, type SettingsTab } from "../settingsTabs";
import type { WorkspaceId } from "../useLucodeApp";
import type { ServerSession } from "../../shared/types";

export type SessionSidebarProps = {
  t: Translator;
  sessions: ServerSession[];
  sessionSearchQuery: string;
  activeSessionId: string;
  pendingDeleteSessionId: string;
  activeWorkspace: WorkspaceId;
  settingsTab: SettingsTab;
  collapsed: boolean;
  runStatus: RunStatus;
  hasMoreSessions: boolean;
  loadingMoreSessions: boolean;
  createNewSession: () => void;
  searchSessions: (query: string) => void;
  loadMoreSessions: () => void;
  selectSession: (sessionId: string) => void;
  requestDeleteSession: (sessionId: string) => void;
  switchWorkspace: (workspace: WorkspaceId) => void;
  selectSettingsTab: (tab: SettingsTab) => void;
  openSettings: () => void;
  toggleSidebar: () => void;
};

export function SessionSidebar({
  t,
  sessions,
  sessionSearchQuery,
  activeSessionId,
  pendingDeleteSessionId,
  activeWorkspace,
  settingsTab,
  collapsed,
  runStatus,
  hasMoreSessions,
  loadingMoreSessions,
  createNewSession,
  searchSessions,
  loadMoreSessions,
  selectSession,
  requestDeleteSession,
  switchWorkspace,
  selectSettingsTab,
  openSettings,
  toggleSidebar,
}: SessionSidebarProps) {
  if (collapsed) {
    return (
      <aside className="session-sidebar collapsed" aria-label={t("sidebar.collapsedLabel")}>
        <div className="rail-logo">L</div>
        <button
          className={activeWorkspace === "chat" ? "rail-button active" : "rail-button"}
          type="button"
          title={t("sidebar.chat")}
          onClick={() => switchWorkspace("chat")}
        >
          C
        </button>
        <button
          className={activeWorkspace === "plugins" ? "rail-button active" : "rail-button"}
          type="button"
          title={t("sidebar.plugins")}
          onClick={() => switchWorkspace("plugins")}
        >
          P
        </button>
        <div className="rail-spacer" />
        <button className={activeWorkspace === "settings" ? "rail-button active" : "rail-button"} type="button" title={t("common.settings")} onClick={openSettings}>
          S
        </button>
        <button className="rail-button" type="button" title={t("sidebar.expand")} onClick={toggleSidebar}>
          &gt;
        </button>
      </aside>
    );
  }

  return (
    <aside className="session-sidebar" aria-label={t("sidebar.fullLabel")}>
      <nav className="sidebar-nav" aria-label={t("sidebar.mainNav")}>
        <button
          className={activeWorkspace === "chat" ? "sidebar-nav-item active" : "sidebar-nav-item"}
          type="button"
          onClick={() => switchWorkspace("chat")}
        >
          {t("sidebar.chat")}
        </button>
        <button
          className={activeWorkspace === "plugins" ? "sidebar-nav-item active" : "sidebar-nav-item"}
          type="button"
          onClick={() => switchWorkspace("plugins")}
        >
          {t("sidebar.plugins")}
        </button>
      </nav>

      {activeWorkspace === "chat" ? (
        <div className="sidebar-chat-panel">
          <button className="new-session-button" type="button" onClick={createNewSession}>
            <span className="new-session-icon" aria-hidden="true">+</span>
            <span className="new-session-label">{t("sidebar.newChatLabel")}</span>
          </button>

          <input
            className="session-search"
            value={sessionSearchQuery}
            onChange={(event) => searchSessions(event.target.value)}
            placeholder={t("sidebar.search")}
            aria-label={t("sidebar.search")}
          />

          <div className="sidebar-count">
            <span>{sessions.length ? t("sidebar.sessionCount", { count: sessions.length }) : t("sidebar.noChats")}</span>
            {runStatus === "running" ? <span className="sidebar-running-dot" title={t("sidebar.runningTitle")} /> : null}
          </div>

          <div
            className="session-list"
            aria-busy={loadingMoreSessions}
            onScroll={(event) => {
              const target = event.currentTarget;
              if (
                shouldLoadNextSessionPage({
                  scrollTop: target.scrollTop,
                  clientHeight: target.clientHeight,
                  scrollHeight: target.scrollHeight,
                  hasMore: hasMoreSessions,
                  loading: loadingMoreSessions,
                })
              ) {
                loadMoreSessions();
              }
            }}
          >
            {sessions.length === 0 ? (
              <div className="empty-state">{sessionSearchQuery.trim() ? t("sidebar.noMatches") : t("sidebar.noChats")}</div>
            ) : (
              sessions.map((session) => {
                const confirmingDelete = pendingDeleteSessionId === session.session_id;
                return (
                  <div key={session.session_id} className={session.session_id === activeSessionId ? "session-row active" : "session-row"}>
                    <button className="session-select" type="button" onClick={() => selectSession(session.session_id)}>
                    <span className="session-row-main">
                      <span className="session-title" title={session.title}>
                        {session.display_title || session.title || t("sidebar.untitled")}
                      </span>
                      <span className="session-meta">{formatSessionTime(session.updated_at, new Date(), t)}</span>
                    </span>
                    </button>
                    <button
                      className={confirmingDelete ? "session-delete confirming" : "session-delete"}
                      type="button"
                      title={confirmingDelete ? t("sidebar.confirmDeleteTitle") : t("sidebar.deleteTitle")}
                      aria-label={confirmingDelete ? t("sidebar.confirmDeleteTitle") : t("sidebar.deleteTitle")}
                      onClick={() => requestDeleteSession(session.session_id)}
                    >
                      {confirmingDelete ? t("common.confirm") : "×"}
                    </button>
                  </div>
                );
              })
            )}
            {loadingMoreSessions ? <span className="session-list-loader" aria-hidden="true" /> : null}
          </div>
        </div>
      ) : activeWorkspace === "settings" ? (
        <div className="settings-sidebar-panel">
          <div className="sidebar-settings-hint">
            <strong>{t("sidebar.settingsHintTitle")}</strong>
            <span>{t("sidebar.settingsHintBody")}</span>
          </div>
          <nav className="settings-sidebar-tabs" aria-label={t("settings.tabsAria")}>
            {SETTINGS_TABS.map((tab) => (
              <button
                key={tab.id}
                className={settingsTab === tab.id ? "settings-sidebar-tab active" : "settings-sidebar-tab"}
                type="button"
                onClick={() => selectSettingsTab(tab.id)}
              >
                {t(tab.labelKey)}
              </button>
            ))}
          </nav>
        </div>
      ) : (
        <div className="sidebar-plugin-hint">
          <strong>{t("sidebar.pluginsHintTitle")}</strong>
          <span>{t("sidebar.pluginsHintBody")}</span>
        </div>
      )}

      <div className="sidebar-utility-bar">
        <button className={activeWorkspace === "settings" ? "sidebar-utility-button settings-active" : "sidebar-utility-button"} type="button" onClick={activeWorkspace === "settings" ? () => switchWorkspace("chat") : openSettings}>
          {activeWorkspace === "settings" ? t("sidebar.returnToChat") : t("common.settings")}
        </button>
        <button className="sidebar-collapse-button" type="button" onClick={toggleSidebar} title={t("sidebar.collapse")}>
          &lt;
        </button>
      </div>
    </aside>
  );
}
