import { describe, expect, it } from "vitest";

import { addTerminalTab, closeTerminalTab, createTerminalTabsState, updateTerminalTab } from "./terminalTabs";

describe("terminal tab state", () => {
  it("creates the first terminal tab as the active tab", () => {
    const state = createTerminalTabsState("D:/workspace", () => "tab_1");

    expect(state.activeTabId).toBe("tab_1");
    expect(state.tabs).toEqual([
      expect.objectContaining({
        localId: "tab_1",
        title: "Terminal 1",
        cwd: "D:/workspace",
        status: "starting",
      }),
    ]);
  });

  it("adds a new terminal tab and makes it active", () => {
    const first = createTerminalTabsState("D:/workspace", () => "tab_1");
    const second = addTerminalTab(first, "D:/workspace/subdir", () => "tab_2");

    expect(second.activeTabId).toBe("tab_2");
    expect(second.tabs.map((tab) => tab.localId)).toEqual(["tab_1", "tab_2"]);
    expect(second.tabs[1]).toMatchObject({
      title: "Terminal 2",
      cwd: "D:/workspace/subdir",
    });
  });

  it("updates a terminal tab after the desktop session is created", () => {
    const state = createTerminalTabsState("D:/workspace", () => "tab_1");
    const updated = updateTerminalTab(state, "tab_1", {
      sessionId: "session_1",
      title: "lucode",
      mode: "pty",
      status: "running",
    });

    expect(updated.tabs[0]).toMatchObject({
      localId: "tab_1",
      sessionId: "session_1",
      title: "lucode",
      mode: "pty",
      status: "running",
    });
  });

  it("activates the neighbor when closing the active terminal tab", () => {
    const first = createTerminalTabsState("D:/workspace", () => "tab_1");
    const second = addTerminalTab(first, "D:/workspace", () => "tab_2");
    const third = addTerminalTab(second, "D:/workspace", () => "tab_3");

    const closed = closeTerminalTab(third, "tab_2");

    expect(closed.tabs.map((tab) => tab.localId)).toEqual(["tab_1", "tab_3"]);
    expect(closed.activeTabId).toBe("tab_3");
  });

  it("returns an empty state when the last terminal tab is closed", () => {
    const state = createTerminalTabsState("D:/workspace", () => "tab_1");
    const closed = closeTerminalTab(state, "tab_1");

    expect(closed.tabs).toEqual([]);
    expect(closed.activeTabId).toBe("");
  });
});
