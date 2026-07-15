import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import type { ServerSession } from "../../shared/types";
import { SessionSidebar } from "./SessionSidebar";

function makeSession(index: number): ServerSession {
  return {
    schema_version: "session.v1",
    session_id: `session_${index}`,
    title: `Session ${index}`,
    display_title: `Session ${index}`,
    created_at: "2026-07-07T08:00:00Z",
    updated_at: "2026-07-07T08:00:00Z",
  };
}

function renderSidebar(sessionCount = 12, overrides: Partial<Parameters<typeof SessionSidebar>[0]> = {}) {
  return renderToStaticMarkup(
    createElement(SessionSidebar, {
      t: createTranslator("en"),
      sessions: Array.from({ length: sessionCount }, (_, index) => makeSession(index + 1)),
      sessionSearchQuery: "",
      activeSessionId: "session_1",
      pendingDeleteSessionId: "",
      activeWorkspace: "chat",
      settingsTab: "models",
      collapsed: false,
      runStatus: "idle",
      hasMoreSessions: false,
      loadingMoreSessions: false,
      createNewSession: () => undefined,
      searchSessions: () => undefined,
      loadMoreSessions: () => undefined,
      selectSession: () => undefined,
      requestDeleteSession: () => undefined,
      switchWorkspace: () => undefined,
      selectSettingsTab: () => undefined,
      openSettings: () => undefined,
      toggleSidebar: () => undefined,
      ...overrides,
    }),
  );
}

describe("SessionSidebar", () => {
  it("keeps the session history in a bounded chat panel above sidebar utilities", () => {
    const html = renderSidebar();

    const panelIndex = html.indexOf('class="sidebar-chat-panel"');
    const listIndex = html.indexOf('class="session-list"');
    const utilityIndex = html.indexOf('class="sidebar-utility-bar"');

    expect(panelIndex).toBeGreaterThanOrEqual(0);
    expect(listIndex).toBeGreaterThan(panelIndex);
    expect(utilityIndex).toBeGreaterThan(listIndex);
  });

  it("does not hide backend search results with local title filtering", () => {
    const html = renderSidebar(0, {
      sessionSearchQuery: "refund ledger",
      sessions: [
        {
          schema_version: "session.v1",
          session_id: "session_ledger",
          title: "Accounting",
          display_title: "Accounting",
          created_at: "2026-07-07T08:00:00Z",
          updated_at: "2026-07-07T08:00:00Z",
        },
      ],
    });

    expect(html).toContain("Accounting");
    expect(html).toContain('value="refund ledger"');
    expect(html).not.toContain("No matching chats");
  });

  it("renders a settings context instead of the plugin workspace for settings", () => {
    const html = renderSidebar(0, { activeWorkspace: "settings" });

    expect(html).toContain("Settings center");
    expect(html).not.toContain("Plugin workspace");
    expect(html).toContain('class="settings-sidebar-tabs"');
    expect(html).toContain("Models");
    expect(html).toContain("Privacy");
  });

  it("keeps the compact workbench new-chat command as an icon plus a readable label", () => {
    const html = renderSidebar();

    expect(html).toContain('class="new-session-button"');
    expect(html).toContain('class="new-session-icon" aria-hidden="true">+</span>');
    expect(html).toContain('class="new-session-label">New chat</span>');
  });
});
