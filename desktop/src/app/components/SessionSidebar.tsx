import { KeyboardEvent, MouseEvent, useMemo, useState } from "react";

import { formatSessionTime, type RunStatus } from "../appState";
import type { Translator } from "../i18n";
import type { WorkspaceId } from "../useLucodeApp";
import type { ServerSession } from "../../shared/types";

export type SessionSidebarProps = {
  t: Translator;
  sessions: ServerSession[];
  activeSessionId: string;
  pendingDeleteSessionId: string;
  activeWorkspace: WorkspaceId;
  collapsed: boolean;
  runStatus: RunStatus;
  createNewSession: () => void;
  selectSession: (sessionId: string) => void;
  requestDeleteSession: (sessionId: string) => void;
  switchWorkspace: (workspace: WorkspaceId) => void;
  openSettings: () => void;
  toggleSidebar: () => void;
};

export function SessionSidebar({
  t,
  sessions,
  activeSessionId,
  pendingDeleteSessionId,
  activeWorkspace,
  collapsed,
  runStatus,
  createNewSession,
  selectSession,
  requestDeleteSession,
  switchWorkspace,
  openSettings,
  toggleSidebar,
}: SessionSidebarProps) {
  const [query, setQuery] = useState("");
  const visibleSessions = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) {
      return sessions;
    }
    return sessions.filter((session) => {
      const title = `${session.display_title || ""} ${session.title || ""}`.toLocaleLowerCase();
      return title.includes(term);
    });
  }, [query, sessions]);

  function handleDeleteClick(event: MouseEvent, sessionId: string) {
    event.stopPropagation();
    requestDeleteSession(sessionId);
  }

  function handleDeleteKeyDown(event: KeyboardEvent, sessionId: string) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    requestDeleteSession(sessionId);
  }

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
        <button className="rail-button" type="button" title={t("common.settings")} onClick={openSettings}>
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
        <>
          <button className="new-session-button" type="button" onClick={createNewSession}>
            {t("sidebar.newChat")}
          </button>

          <input
            className="session-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("sidebar.search")}
            aria-label={t("sidebar.search")}
          />

          <div className="sidebar-count">
            <span>{sessions.length ? t("sidebar.sessionCount", { count: sessions.length }) : t("sidebar.noChats")}</span>
            {runStatus === "running" ? <span className="sidebar-running-dot" title={t("sidebar.runningTitle")} /> : null}
          </div>

          <div className="session-list">
            {visibleSessions.length === 0 ? (
              <div className="empty-state">{query.trim() ? t("sidebar.noMatches") : t("sidebar.noChats")}</div>
            ) : (
              visibleSessions.map((session) => {
                const confirmingDelete = pendingDeleteSessionId === session.session_id;
                return (
                  <button
                    key={session.session_id}
                    className={session.session_id === activeSessionId ? "session-row active" : "session-row"}
                    type="button"
                    onClick={() => selectSession(session.session_id)}
                  >
                    <span className="session-row-main">
                      <span className="session-title" title={session.title}>
                        {session.display_title || session.title || t("sidebar.untitled")}
                      </span>
                      <span className="session-meta">{formatSessionTime(session.updated_at, new Date(), t)}</span>
                    </span>
                    <span
                      className={confirmingDelete ? "session-delete confirming" : "session-delete"}
                      role="button"
                      tabIndex={0}
                      title={confirmingDelete ? t("sidebar.confirmDeleteTitle") : t("sidebar.deleteTitle")}
                      onClick={(event) => handleDeleteClick(event, session.session_id)}
                      onKeyDown={(event) => handleDeleteKeyDown(event, session.session_id)}
                    >
                      {confirmingDelete ? t("common.confirm") : t("common.delete")}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </>
      ) : (
        <div className="sidebar-plugin-hint">
          <strong>{t("sidebar.pluginsHintTitle")}</strong>
          <span>{t("sidebar.pluginsHintBody")}</span>
        </div>
      )}

      <div className="sidebar-utility-bar">
        <button className="sidebar-utility-button" type="button" onClick={openSettings}>
          {t("common.settings")}
        </button>
        <button className="sidebar-collapse-button" type="button" onClick={toggleSidebar} title={t("sidebar.collapse")}>
          &lt;
        </button>
      </div>
    </aside>
  );
}
