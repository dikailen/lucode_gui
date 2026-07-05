import { describe, expect, it } from "vitest";

import {
  activeRightDockTool,
  clampRightDockWidth,
  closeBottomShell,
  closeRightDockWindow,
  createPanelLayoutState,
  openRightDockWindow,
  rightDockWindows,
  setRightDockWindowStatus,
  shouldForceCompactSidebar,
  toggleBottomShell,
  toggleRightDockHome,
  toggleRightDockTool,
} from "./panelLayout";

describe("panel layout state", () => {
  it("toggles the bottom shell independently from the right dock", () => {
    const dockOpen = toggleRightDockTool(createPanelLayoutState(), "terminal");
    const shellOpen = toggleBottomShell(dockOpen);

    expect(activeRightDockTool(shellOpen)).toBe("terminal");
    expect(shellOpen.bottomShellOpen).toBe(true);

    expect(activeRightDockTool(closeBottomShell(shellOpen))).toBe("terminal");
    expect(closeBottomShell(shellOpen).bottomShellOpen).toBe(false);
  });

  it("keeps the active right dock tab open when it is clicked again", () => {
    const dockOpen = toggleRightDockTool(createPanelLayoutState(), "browser");

    expect(activeRightDockTool(toggleRightDockTool(dockOpen, "browser"))).toBe("browser");
    expect(rightDockWindows(toggleRightDockTool(dockOpen, "browser")).map((window) => window.tool)).toEqual(["browser"]);
  });

  it("only closes a right dock tool through the close-window action", () => {
    const dockOpen = toggleRightDockTool(createPanelLayoutState(), "browser");
    const closedWindow = closeRightDockWindow(dockOpen, "browser");

    expect(activeRightDockTool(closedWindow)).toBe("home");
    expect(rightDockWindows(closedWindow)).toEqual([]);
  });

  it("returns to the previous right dock window when the active tab is closed", () => {
    const terminalOpen = toggleRightDockTool(createPanelLayoutState(), "terminal");
    const browserOpen = toggleRightDockTool(terminalOpen, "browser");
    const closedBrowser = closeRightDockWindow(browserOpen, "browser");

    expect(activeRightDockTool(closedBrowser)).toBe("terminal");
    expect(rightDockWindows(closedBrowser).map((window) => window.tool)).toEqual(["terminal"]);
  });

  it("keeps the current window active when a different right dock tab is closed", () => {
    const terminalOpen = toggleRightDockTool(createPanelLayoutState(), "terminal");
    const browserOpen = toggleRightDockTool(terminalOpen, "browser");
    const reviewOpen = toggleRightDockTool(browserOpen, "review");
    const closedBrowser = closeRightDockWindow(reviewOpen, "browser");

    expect(activeRightDockTool(closedBrowser)).toBe("review");
    expect(rightDockWindows(closedBrowser).map((window) => window.tool)).toEqual(["terminal", "review"]);
  });

  it("collapses the right dock without deleting open windows", () => {
    const dockOpen = toggleRightDockTool(toggleRightDockTool(createPanelLayoutState(), "terminal"), "browser");
    const collapsed = toggleRightDockTool(dockOpen, "");
    const reopenedBrowser = toggleRightDockTool(collapsed, "browser");

    expect(activeRightDockTool(collapsed)).toBe("");
    expect(rightDockWindows(collapsed).map((window) => window.tool)).toEqual(["terminal", "browser"]);
    expect(activeRightDockTool(reopenedBrowser)).toBe("browser");
    expect(rightDockWindows(reopenedBrowser).map((window) => window.tool)).toEqual(["terminal", "browser"]);
  });

  it("keeps the command home separate from tool windows", () => {
    const homeOpen = toggleRightDockTool(createPanelLayoutState(), "home");

    expect(activeRightDockTool(homeOpen)).toBe("home");
    expect(rightDockWindows(homeOpen)).toEqual([]);

    const browserOpen = toggleRightDockTool(homeOpen, "browser");

    expect(activeRightDockTool(browserOpen)).toBe("browser");
    expect(rightDockWindows(browserOpen).map((window) => window.tool)).toEqual(["browser"]);
  });

  it("opens and switches same-level right dock windows", () => {
    const terminalOpen = toggleRightDockTool(createPanelLayoutState(), "terminal");
    const browserOpen = toggleRightDockTool(terminalOpen, "browser");
    const reviewOpen = toggleRightDockTool(browserOpen, "review");

    expect(activeRightDockTool(reviewOpen)).toBe("review");
    expect(rightDockWindows(reviewOpen).map((window) => window.tool)).toEqual(["terminal", "browser", "review"]);

    const backToBrowser = toggleRightDockTool(reviewOpen, "browser");

    expect(activeRightDockTool(backToBrowser)).toBe("browser");
    expect(rightDockWindows(backToBrowser).map((window) => window.tool)).toEqual(["terminal", "browser", "review"]);
  });

  it("lets agent browser activity mark the browser tab without stealing focus", () => {
    const reviewing = toggleRightDockTool(toggleRightDockTool(createPanelLayoutState(), "browser"), "review");
    const browserRunning = setRightDockWindowStatus(openRightDockWindow(reviewing, "browser", { focus: false }), "browser", "running");

    expect(activeRightDockTool(browserRunning)).toBe("review");
    expect(rightDockWindows(browserRunning).find((window) => window.tool === "browser")?.status).toBe("running");
  });

  it("preserves a running window status when the user switches to that tab", () => {
    const reviewing = toggleRightDockTool(toggleRightDockTool(createPanelLayoutState(), "browser"), "review");
    const browserRunning = setRightDockWindowStatus(reviewing, "browser", "running");
    const browserFocused = toggleRightDockTool(browserRunning, "browser");

    expect(activeRightDockTool(browserFocused)).toBe("browser");
    expect(rightDockWindows(browserFocused).find((window) => window.tool === "browser")?.status).toBe("running");
  });

  it("does not create a hidden idle window when clearing a status that never existed", () => {
    const cleared = setRightDockWindowStatus(createPanelLayoutState(), "browser", "idle");

    expect(activeRightDockTool(cleared)).toBe("");
    expect(rightDockWindows(cleared)).toEqual([]);
  });

  it("toggles only the right dock home entry point", () => {
    const homeOpen = toggleRightDockHome(createPanelLayoutState());
    const homeClosed = toggleRightDockHome(homeOpen);

    expect(activeRightDockTool(homeOpen)).toBe("home");
    expect(activeRightDockTool(homeClosed)).toBe("");
  });

  it("forces compact sidebar only when the viewport needs it", () => {
    expect(shouldForceCompactSidebar(1280, true)).toBe(false);
    expect(shouldForceCompactSidebar(1060, false)).toBe(false);
    expect(shouldForceCompactSidebar(1060, true)).toBe(true);
    expect(shouldForceCompactSidebar(900, false)).toBe(true);
  });

  it("clamps the right dock width to preserve the workspace at tested viewports", () => {
    expect(clampRightDockWidth(640, false, 1280)).toBe(566);
    expect(clampRightDockWidth(640, true, 1060)).toBe(636);
    expect(clampRightDockWidth(640, true, 900)).toBe(476);
    expect(clampRightDockWidth(640, false, 1060)).toBe(346);
  });
});
