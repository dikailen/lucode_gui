import { describe, expect, it } from "vitest";

import { shouldCloseBrowserPanelAfterTabClose } from "./BrowserPanel";
import type { DesktopBrowserWorkspaceState } from "../../shared/types";

function workspace(tabIds: string[]): DesktopBrowserWorkspaceState {
  return {
    activeTabId: tabIds[0] || "",
    tabs: tabIds.map((tabId) => ({
      tabId,
      sessionId: tabId,
      url: "about:blank",
      title: "",
      canGoBack: false,
      canGoForward: false,
      loading: false,
      visible: false,
      lastError: "",
      automationEnabled: false,
      automationOrigin: "",
    })),
  };
}

describe("BrowserPanel tab close behavior", () => {
  it("closes the browser panel when the last embedded browser tab is closed", () => {
    expect(shouldCloseBrowserPanelAfterTabClose(workspace([]))).toBe(true);
  });

  it("keeps the browser panel open when other embedded browser tabs remain", () => {
    expect(shouldCloseBrowserPanelAfterTabClose(workspace(["browser_1"]))).toBe(false);
  });
});
