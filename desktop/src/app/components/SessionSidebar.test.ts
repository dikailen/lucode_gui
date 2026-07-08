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

function renderSidebar(sessionCount = 12) {
  return renderToStaticMarkup(
    createElement(SessionSidebar, {
      t: createTranslator("en"),
      sessions: Array.from({ length: sessionCount }, (_, index) => makeSession(index + 1)),
      activeSessionId: "session_1",
      pendingDeleteSessionId: "",
      activeWorkspace: "chat",
      collapsed: false,
      runStatus: "idle",
      createNewSession: () => undefined,
      selectSession: () => undefined,
      requestDeleteSession: () => undefined,
      switchWorkspace: () => undefined,
      openSettings: () => undefined,
      toggleSidebar: () => undefined,
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
});
