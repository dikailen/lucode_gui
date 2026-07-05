import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  BrowserWindow: {
    fromWebContents: vi.fn(),
    getFocusedWindow: vi.fn(),
    getAllWindows: vi.fn(),
  },
  WebContentsView: vi.fn(),
  ipcMain: {
    handle: vi.fn(),
    on: vi.fn(),
  },
  shell: {
    openExternal: vi.fn(),
  },
}));

import { clampBrowserBounds, DesktopBrowserManager, normalizeBrowserUrl } from "./browserManager.js";
import { BrowserWindow, shell, WebContentsView } from "electron";

type FakeSession = {
  sessionId: string;
  webContentsId: number;
  close: () => void;
};

type FakeWebContents = {
  getURL: ReturnType<typeof vi.fn>;
  getTitle: ReturnType<typeof vi.fn>;
  isLoadingMainFrame: ReturnType<typeof vi.fn>;
  loadURL: ReturnType<typeof vi.fn>;
  reload: ReturnType<typeof vi.fn>;
  focus: ReturnType<typeof vi.fn>;
  executeJavaScript: ReturnType<typeof vi.fn>;
  executeJavaScriptInIsolatedWorld: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  isDestroyed: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  setWindowOpenHandler: ReturnType<typeof vi.fn>;
  navigationHistory: {
    canGoBack: ReturnType<typeof vi.fn>;
    canGoForward: ReturnType<typeof vi.fn>;
    goBack: ReturnType<typeof vi.fn>;
    goForward: ReturnType<typeof vi.fn>;
  };
};

type FakeView = {
  setBounds: ReturnType<typeof vi.fn>;
  setVisible: ReturnType<typeof vi.fn>;
  webContents: FakeWebContents;
};

function makeFakeView(): FakeView {
  return {
    setBounds: vi.fn(),
    setVisible: vi.fn(),
    webContents: {
      getURL: vi.fn(() => "about:blank"),
      getTitle: vi.fn(() => ""),
      isLoadingMainFrame: vi.fn(() => false),
      loadURL: vi.fn(async () => undefined),
      reload: vi.fn(),
      focus: vi.fn(),
      executeJavaScript: vi.fn(async () => ({})),
      executeJavaScriptInIsolatedWorld: vi.fn(async () => ({})),
      close: vi.fn(),
      isDestroyed: vi.fn(() => false),
      on: vi.fn(),
      setWindowOpenHandler: vi.fn(),
      navigationHistory: {
        canGoBack: vi.fn(() => false),
        canGoForward: vi.fn(() => false),
        goBack: vi.fn(),
        goForward: vi.fn(),
      },
    },
  };
}

describe("normalizeBrowserUrl", () => {
  it("keeps explicit http and https URLs", () => {
    expect(normalizeBrowserUrl("http://127.0.0.1:8188")).toBe("http://127.0.0.1:8188/");
    expect(normalizeBrowserUrl("https://example.com/docs")).toBe("https://example.com/docs");
  });

  it("defaults localhost-like addresses to http and public domains to https", () => {
    expect(normalizeBrowserUrl("localhost:5173")).toBe("http://localhost:5173/");
    expect(normalizeBrowserUrl("127.0.0.1:8188")).toBe("http://127.0.0.1:8188/");
    expect(normalizeBrowserUrl("example.com")).toBe("https://example.com/");
  });

  it("rejects unsupported protocols", () => {
    expect(() => normalizeBrowserUrl("javascript:alert(1)")).toThrow("Unsupported browser URL protocol");
  });
});

describe("clampBrowserBounds", () => {
  it("normalizes invalid panel bounds to a safe minimum rectangle", () => {
    expect(clampBrowserBounds({ x: -10, y: 12.8, width: 0, height: 6 })).toEqual({
      x: 0,
      y: 12,
      width: 120,
      height: 80,
    });
  });
});

describe("DesktopBrowserManager", () => {
  const createdViews: FakeView[] = [];
  const owner = {
    id: 77,
    isDestroyed: vi.fn(() => false),
    send: vi.fn(),
  };
  const fakeWindow = {
    webContents: owner,
    contentView: {
      addChildView: vi.fn(),
      removeChildView: vi.fn(),
    },
  };
  const event = { sender: owner };

  beforeEach(() => {
    createdViews.length = 0;
    vi.clearAllMocks();
    vi.mocked(BrowserWindow.fromWebContents).mockReturnValue(fakeWindow as never);
    vi.mocked(BrowserWindow.getFocusedWindow).mockReturnValue(fakeWindow as never);
    vi.mocked(BrowserWindow.getAllWindows).mockReturnValue([fakeWindow] as never);
    vi.mocked(WebContentsView).mockImplementation(() => {
      const view = makeFakeView();
      createdViews.push(view);
      return view as never;
    });
  });

  it("keeps browser tabs and the active tab in main-process state", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      listTabs(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
    };

    const first = api.createTab(event);
    const second = api.createTab(event);
    const listed = api.listTabs(event);

    expect(first.tabs).toHaveLength(1);
    expect(second.tabs).toHaveLength(2);
    expect(listed.tabs).toHaveLength(2);
    expect(listed.activeTabId).toBe(second.tabs[1].tabId);
  });

  it("shows only the active tab view when switching tabs", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      activateTab(ipcEvent: typeof event, tabId: string): { activeTabId: string };
      setBounds(ipcEvent: typeof event, bounds: { x: number; y: number; width: number; height: number }): void;
    };

    const first = api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://first.example/");
    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });
    const second = api.createTab(event);
    createdViews[1].webContents.getURL.mockReturnValue("https://second.example/");
    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });

    const next = api.activateTab(event, first.tabs[0].tabId);

    expect(next.activeTabId).toBe(first.tabs[0].tabId);
    expect(createdViews[1].setVisible).toHaveBeenCalledWith(false);
    expect(createdViews[0].setVisible).toHaveBeenLastCalledWith(true);
  });

  it("closes the active tab and activates a remaining tab", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      closeTab(ipcEvent: typeof event, tabId: string): { activeTabId: string; tabs: Array<{ tabId: string }> };
    };

    const first = api.createTab(event);
    const second = api.createTab(event);
    const closed = api.closeTab(event, second.activeTabId);

    expect(closed.tabs).toHaveLength(1);
    expect(closed.activeTabId).toBe(first.activeTabId);
    expect(createdViews[1].webContents.close).toHaveBeenCalledOnce();
    expect(fakeWindow.contentView.removeChildView).toHaveBeenCalledWith(createdViews[1]);
  });

  it("does not emit browser state for bounds-only layout updates", () => {
    const manager = new DesktopBrowserManager();
    const sessions = (manager as unknown as { sessions: Map<string, Record<string, unknown>> }).sessions;
    const owner = {
      id: 77,
      isDestroyed: vi.fn(() => false),
      send: vi.fn(),
    };
    const view = {
      setBounds: vi.fn(),
      setVisible: vi.fn(),
      webContents: {
        getURL: vi.fn(() => "https://example.com/"),
        getTitle: vi.fn(() => "Example"),
        isLoadingMainFrame: vi.fn(() => false),
        navigationHistory: {
          canGoBack: vi.fn(() => false),
          canGoForward: vi.fn(() => false),
        },
      },
    };

    sessions.set("browser_1", {
      sessionId: "browser_1",
      owner,
      webContentsId: 77,
      view,
      visible: false,
      lastError: "",
      close: vi.fn(),
    });

    (manager as unknown as {
      setBounds(event: { sender: { id: number } }, bounds: { x: number; y: number; width: number; height: number }): void;
      activeSessionByOwnerId: Map<number, string>;
    }).activeSessionByOwnerId.set(77, "browser_1");

    (manager as unknown as {
      setBounds(event: { sender: { id: number } }, bounds: { x: number; y: number; width: number; height: number }): void;
    }).setBounds({ sender: { id: 77 } }, { x: 10, y: 20, width: 300, height: 200 });

    expect(view.setBounds).toHaveBeenCalledWith({ x: 10, y: 20, width: 300, height: 200 });
    expect(view.setVisible).toHaveBeenCalledWith(true);
    expect(owner.send).not.toHaveBeenCalled();
  });

  it("deduplicates repeated equal bounds and visibility updates", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setBounds(ipcEvent: typeof event, bounds: { x: number; y: number; width: number; height: number }): void;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    createdViews[0].setBounds.mockClear();
    createdViews[0].setVisible.mockClear();

    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });
    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });

    expect(createdViews[0].setBounds).toHaveBeenCalledTimes(1);
    expect(createdViews[0].setVisible).toHaveBeenCalledTimes(1);
    expect(createdViews[0].setVisible).toHaveBeenCalledWith(true);
  });

  it("focuses the active page only through an explicit focus request", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setBounds(ipcEvent: typeof event, bounds: { x: number; y: number; width: number; height: number }): void;
      focusActive(ipcEvent: typeof event): void;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });

    expect(createdViews[0].webContents.focus).not.toHaveBeenCalled();

    api.focusActive(event);

    expect(createdViews[0].webContents.focus).toHaveBeenCalledOnce();
  });

  it("exposes browser diagnostics without sending a state event", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setBounds(ipcEvent: typeof event, bounds: { x: number; y: number; width: number; height: number }): void;
      focusActive(ipcEvent: typeof event): void;
      getDiagnostics(ipcEvent: typeof event): {
        activeTabId: string;
        tabCount: number;
        visibleTabIds: string[];
        hasBounds: boolean;
        layoutRequestCount: number;
        boundsApplyCount: number;
        visibilityChangeCount: number;
        focusRequestCount: number;
        stateEventCount: number;
      };
    };

    const created = api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    owner.send.mockClear();
    api.setBounds(event, { x: 10, y: 20, width: 300, height: 200 });
    api.focusActive(event);

    const diagnostics = api.getDiagnostics(event);

    expect(diagnostics).toMatchObject({
      activeTabId: created.activeTabId,
      tabCount: 1,
      visibleTabIds: [created.activeTabId],
      hasBounds: true,
      layoutRequestCount: 1,
      boundsApplyCount: 1,
      focusRequestCount: 1,
      stateEventCount: 0,
    });
    expect(diagnostics.visibilityChangeCount).toBeGreaterThanOrEqual(1);
    expect(owner.send).not.toHaveBeenCalled();
  });

  it("summarizes the active browser page through an isolated DOM read", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      getPageSummary(
        ipcEvent: typeof event,
        tabId: string,
        options?: { maxTextLength?: number; maxElements?: number },
      ): Promise<{
        schemaVersion: string;
        tabId: string;
        url: string;
        title: string;
        text: string;
        headings: Array<{ level: number; text: string }>;
        elements: Array<{ role: string; text: string; selector: string; href?: string }>;
      }>;
    };

    const created = api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    createdViews[0].webContents.getTitle.mockReturnValue("Example Domain");
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld.mockResolvedValue({
      text: "Example Domain\nThis domain is for examples.",
      headings: [{ level: 1, text: "Example Domain" }],
      elements: [{ role: "link", tagName: "a", text: "More information", selector: "a:nth-of-type(1)", href: "https://iana.org/" }],
    });

    const summary = await api.getPageSummary(event, "", { maxTextLength: 200, maxElements: 20 });

    expect(summary).toMatchObject({
      schemaVersion: "browser_page_summary.v1",
      tabId: created.activeTabId,
      url: "https://example.com/",
      title: "Example Domain",
      text: "Example Domain\nThis domain is for examples.",
      headings: [{ level: 1, text: "Example Domain" }],
      elements: [{ role: "link", text: "More information", selector: "a:nth-of-type(1)", href: "https://iana.org/" }],
    });
    expect(createdViews[0].webContents.executeJavaScriptInIsolatedWorld).toHaveBeenCalledOnce();
    expect(createdViews[0].webContents.executeJavaScript).not.toHaveBeenCalled();
  });

  it("limits browser page summary text and element output", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      getPageSummary(
        ipcEvent: typeof event,
        tabId: string,
        options?: { maxTextLength?: number; maxElements?: number },
      ): Promise<{
        text: string;
        textTruncated: boolean;
        elementsTruncated: boolean;
        elements: Array<{ text: string }>;
      }>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld.mockResolvedValue({
      text: "0123456789abcdef",
      headings: [],
      elements: [
        { role: "button", tagName: "button", text: "One", selector: "button:nth-of-type(1)" },
        { role: "button", tagName: "button", text: "Two", selector: "button:nth-of-type(2)" },
        { role: "button", tagName: "button", text: "Three", selector: "button:nth-of-type(3)" },
      ],
    });

    const summary = await api.getPageSummary(event, "", { maxTextLength: 10, maxElements: 2 });

    expect(summary.text).toBe("0123456789");
    expect(summary.textTruncated).toBe(true);
    expect(summary.elements.map((element) => element.text)).toEqual(["One", "Two"]);
    expect(summary.elementsTruncated).toBe(true);
  });

  it("rejects page summary requests when there is no loaded browser page", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      getPageSummary(ipcEvent: typeof event, tabId: string): Promise<unknown>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("about:blank");

    await expect(api.getPageSummary(event, "")).rejects.toThrow("Browser page is not available.");
    expect(createdViews[0].webContents.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled();
  });

  it("marks browser automation blocked by default for a loaded site origin", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      getState(ipcEvent: typeof event, tabId: string): {
        activeTabId: string;
        tabs: Array<{ tabId: string; automationEnabled: boolean; automationOrigin: string }>;
      };
    };

    const created = api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/docs");

    const state = api.getState(event, "");
    const active = state.tabs.find((tab) => tab.tabId === created.activeTabId);

    expect(active).toMatchObject({
      tabId: created.activeTabId,
      automationEnabled: false,
      automationOrigin: "https://example.com",
    });
  });

  it("blocks browser actions until automation is explicitly allowed for the current site", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      clickElement(ipcEvent: typeof event, tabId: string, selector: string): Promise<unknown>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");

    await expect(api.clickElement(event, "", "button.primary")).rejects.toThrow(
      "Browser automation is blocked for this site. Allow automation for https://example.com first.",
    );
    expect(createdViews[0].webContents.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled();
  });

  it("clicks a selected browser element through an isolated-world action and returns the refreshed page summary", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setAutomationPermission(
        ipcEvent: typeof event,
        tabId: string,
        enabled: boolean,
      ): { activeTabId: string; tabs: Array<{ tabId: string; automationEnabled: boolean; automationOrigin: string }> };
      clickElement(
        ipcEvent: typeof event,
        tabId: string,
        selector: string,
      ): Promise<{
        schemaVersion: string;
        action: string;
        selector: string;
        tabId: string;
        url: string;
        title: string;
        target: { tagName: string; role: string; selector: string };
        pageSummary: { title: string; elements: Array<{ selector: string; text: string }> } | null;
      }>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    createdViews[0].webContents.getTitle.mockReturnValue("Example Domain");
    expect(api.setAutomationPermission(event, "", true).tabs[0]).toMatchObject({
      automationEnabled: true,
      automationOrigin: "https://example.com",
    });
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld
      .mockResolvedValueOnce({
        ok: true,
        action: "click",
        selector: "button.primary",
        target: {
          tagName: "button",
          role: "button",
          selector: "button.primary",
          text: "Continue",
          inputType: "",
          disabled: false,
        },
      })
      .mockResolvedValueOnce({
        text: "Next step",
        headings: [{ level: 1, text: "Next step" }],
        elements: [{ role: "button", tagName: "button", text: "Done", selector: "button.done" }],
      });

    const result = await api.clickElement(event, "", "button.primary");

    expect(result).toMatchObject({
      schemaVersion: "browser_page_action_result.v1",
      action: "click",
      selector: "button.primary",
      tabId: expect.any(String),
      url: "https://example.com/",
      title: "Example Domain",
      target: {
        tagName: "button",
        role: "button",
        selector: "button.primary",
      },
      pageSummary: {
        title: "Example Domain",
        elements: [{ selector: "button.done", text: "Done" }],
      },
    });
    expect(createdViews[0].webContents.executeJavaScriptInIsolatedWorld).toHaveBeenCalledTimes(2);
    expect(createdViews[0].webContents.executeJavaScript).not.toHaveBeenCalled();
  });

  it("sets a browser input value through an isolated-world action without echoing the raw value", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setAutomationPermission(
        ipcEvent: typeof event,
        tabId: string,
        enabled: boolean,
      ): { activeTabId: string; tabs: Array<{ tabId: string; automationEnabled: boolean }> };
      setInputValue(
        ipcEvent: typeof event,
        tabId: string,
        selector: string,
        value: string,
      ): Promise<{
        schemaVersion: string;
        action: string;
        selector: string;
        target: { tagName: string; inputType: string; selector: string };
        pageSummary: { elements: Array<{ selector: string; value: string }> } | null;
        valueApplied: boolean;
        valueLength: number;
      }>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/login");
    createdViews[0].webContents.getTitle.mockReturnValue("Sign in");
    api.setAutomationPermission(event, "", true);
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld
      .mockResolvedValueOnce({
        ok: true,
        action: "set_input_value",
        selector: "input[name='email']",
        valueApplied: true,
        valueLength: 16,
        target: {
          tagName: "input",
          role: "input",
          selector: "input[name='email']",
          text: "",
          inputType: "email",
          disabled: false,
        },
      })
      .mockResolvedValueOnce({
        text: "Sign in",
        headings: [{ level: 1, text: "Sign in" }],
        elements: [
          {
            role: "input",
            tagName: "input",
            text: "",
            selector: "input[name='email']",
            value: "user@example.com",
            inputType: "email",
          },
        ],
      });

    const result = await api.setInputValue(event, "", "input[name='email']", "user@example.com");

    expect(result).toMatchObject({
      schemaVersion: "browser_page_action_result.v1",
      action: "set_input_value",
      selector: "input[name='email']",
      target: {
        tagName: "input",
        inputType: "email",
        selector: "input[name='email']",
      },
      pageSummary: {
        elements: [{ selector: "input[name='email']", value: "user@example.com" }],
      },
      valueApplied: true,
      valueLength: 16,
    });
    expect(result).not.toHaveProperty("value");
  });

  it("submits the targeted browser form through an isolated-world action", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setAutomationPermission(
        ipcEvent: typeof event,
        tabId: string,
        enabled: boolean,
      ): { activeTabId: string; tabs: Array<{ tabId: string; automationEnabled: boolean }> };
      submitForm(
        ipcEvent: typeof event,
        tabId: string,
        selector: string,
      ): Promise<{
        schemaVersion: string;
        action: string;
        selector: string;
        submitted: boolean;
        target: { tagName: string; role: string; selector: string };
      }>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/login");
    createdViews[0].webContents.getTitle.mockReturnValue("Sign in");
    api.setAutomationPermission(event, "", true);
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld
      .mockResolvedValueOnce({
        ok: true,
        action: "submit_form",
        selector: "form.login",
        submitted: true,
        target: {
          tagName: "form",
          role: "form",
          selector: "form.login",
          text: "",
          inputType: "",
          disabled: false,
        },
      })
      .mockResolvedValueOnce({
        text: "Loading",
        headings: [],
        elements: [],
      });

    const result = await api.submitForm(event, "", "form.login");

    expect(result).toMatchObject({
      schemaVersion: "browser_page_action_result.v1",
      action: "submit_form",
      selector: "form.login",
      submitted: true,
      target: {
        tagName: "form",
        role: "form",
        selector: "form.login",
      },
    });
  });

  it("rejects browser actions when the selected element cannot be operated safely", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      setAutomationPermission(
        ipcEvent: typeof event,
        tabId: string,
        enabled: boolean,
      ): { activeTabId: string; tabs: Array<{ tabId: string; automationEnabled: boolean }> };
      setInputValue(ipcEvent: typeof event, tabId: string, selector: string, value: string): Promise<unknown>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    api.setAutomationPermission(event, "", true);
    createdViews[0].webContents.executeJavaScriptInIsolatedWorld.mockResolvedValueOnce({
      ok: false,
      error: "Unsupported input element for set_input_value: input[type=file]",
    });

    await expect(api.setInputValue(event, "", "input[type='file']", "ignored")).rejects.toThrow(
      "Unsupported input element for set_input_value: input[type=file]",
    );
  });

  it("reuses an allowed origin across browser tabs from the same site", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      setAutomationPermission(
        ipcEvent: typeof event,
        tabId: string,
        enabled: boolean,
      ): { activeTabId: string; tabs: Array<{ tabId: string; automationEnabled: boolean; automationOrigin: string }> };
      clickElement(ipcEvent: typeof event, tabId: string, selector: string): Promise<{
        action: string;
        selector: string;
      }>;
    };

    const first = api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("https://example.com/");
    api.setAutomationPermission(event, first.activeTabId, true);

    const second = api.createTab(event);
    createdViews[1].webContents.getURL.mockReturnValue("https://example.com/app");
    createdViews[1].webContents.getTitle.mockReturnValue("Example App");
    createdViews[1].webContents.executeJavaScriptInIsolatedWorld
      .mockResolvedValueOnce({
        ok: true,
        action: "click",
        selector: "button.primary",
        target: {
          tagName: "button",
          role: "button",
          selector: "button.primary",
          text: "Continue",
          inputType: "",
          disabled: false,
        },
      })
      .mockResolvedValueOnce({
        text: "Updated",
        headings: [],
        elements: [],
      });

    const result = await api.clickElement(event, second.activeTabId, "button.primary");

    expect(result).toMatchObject({
      action: "click",
      selector: "button.primary",
    });
  });

  it("rejects browser actions when there is no loaded page", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string };
      clickElement(ipcEvent: typeof event, tabId: string, selector: string): Promise<unknown>;
    };

    api.createTab(event);
    createdViews[0].webContents.getURL.mockReturnValue("about:blank");

    await expect(api.clickElement(event, "", "button.primary")).rejects.toThrow("Browser page is not available.");
    expect(createdViews[0].webContents.executeJavaScriptInIsolatedWorld).not.toHaveBeenCalled();
  });

  it("opens http popup requests in a new internal browser tab", async () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      listTabs(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
    };

    api.createTab(event);
    const openHandler = createdViews[0].webContents.setWindowOpenHandler.mock.calls[0][0] as (payload: {
      url: string;
    }) => { action: "deny" | "allow" };

    const result = openHandler({ url: "https://child.example/path" });
    await Promise.resolve();

    const listed = api.listTabs(event);
    expect(result).toEqual({ action: "deny" });
    expect(createdViews).toHaveLength(2);
    expect(listed.tabs).toHaveLength(2);
    expect(listed.activeTabId).toBe(listed.tabs[1].tabId);
    expect(createdViews[1].webContents.loadURL).toHaveBeenCalledWith("https://child.example/path");
    expect(shell.openExternal).not.toHaveBeenCalled();
    expect(owner.send).toHaveBeenCalledWith("lucode-browser:state", expect.objectContaining({ activeTabId: listed.tabs[1].tabId }));
  });

  it("keeps external popup protocols outside the embedded browser", () => {
    const manager = new DesktopBrowserManager();
    const api = manager as unknown as {
      createTab(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
      listTabs(ipcEvent: typeof event): { activeTabId: string; tabs: Array<{ tabId: string }> };
    };

    api.createTab(event);
    const openHandler = createdViews[0].webContents.setWindowOpenHandler.mock.calls[0][0] as (payload: {
      url: string;
    }) => { action: "deny" | "allow" };

    const result = openHandler({ url: "mailto:test@example.com" });
    const listed = api.listTabs(event);

    expect(result).toEqual({ action: "deny" });
    expect(createdViews).toHaveLength(1);
    expect(listed.tabs).toHaveLength(1);
    expect(shell.openExternal).toHaveBeenCalledWith("mailto:test@example.com");
  });

  it("disposes sessions for a renderer webContents id without reading destroyed webContents", () => {
    const manager = new DesktopBrowserManager();
    const sessions = (manager as unknown as { sessions: Map<string, FakeSession> }).sessions;
    const close = vi.fn();

    sessions.set("browser_1", {
      sessionId: "browser_1",
      webContentsId: 77,
      close,
    });

    manager.disposeForWebContentsId(77);

    expect(close).toHaveBeenCalledOnce();
    expect(sessions.has("browser_1")).toBe(false);
  });

  it("navigates through the automation API without a renderer event", async () => {
    const manager = new DesktopBrowserManager();

    const state = await manager.navigateForAutomation("https://example.com/docs");

    expect(createdViews).toHaveLength(1);
    expect(createdViews[0].webContents.loadURL).toHaveBeenCalledWith("https://example.com/docs");
    expect(state.tabs).toHaveLength(1);
    expect(state.activeTabId).toBe(state.tabs[0].tabId);
  });
});
