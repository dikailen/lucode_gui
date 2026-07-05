import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopBrowserBridge } from "./browserBridge.js";

describe("DesktopBrowserBridge", () => {
  let bridge: DesktopBrowserBridge | null = null;

  afterEach(async () => {
    if (bridge) {
      await bridge.dispose();
      bridge = null;
    }
  });

  it("requires bearer auth and proxies browser commands to the main-process manager", async () => {
    const manager = {
      listTabsForAutomation: vi.fn(() => ({
        activeTabId: "tab_1",
        tabs: [
          {
            tabId: "tab_1",
            sessionId: "tab_1",
            url: "https://example.com/",
            title: "Example",
            canGoBack: false,
            canGoForward: false,
            loading: false,
            visible: true,
            lastError: "",
            automationEnabled: false,
            automationOrigin: "https://example.com",
          },
        ],
      })),
      navigateForAutomation: vi.fn(async (url: string, options?: { tabId?: string }) => ({
        activeTabId: options?.tabId || "tab_1",
        tabs: [
          {
            tabId: options?.tabId || "tab_1",
            sessionId: options?.tabId || "tab_1",
            url,
            title: "Loaded",
            canGoBack: false,
            canGoForward: false,
            loading: false,
            visible: true,
            lastError: "",
            automationEnabled: false,
            automationOrigin: "https://example.com",
          },
        ],
      })),
      getPageSummaryForAutomation: vi.fn(async (options?: { tabId?: string; maxTextLength?: number; maxElements?: number }) => ({
        schemaVersion: "browser_page_summary.v1" as const,
        tabId: options?.tabId || "tab_1",
        url: "https://example.com/",
        title: "Example",
        capturedAt: "2026-07-02T00:00:00Z",
        text: "Example page",
        textTruncated: false,
        headings: [],
        elements: [],
        elementsTruncated: false,
      })),
      setAutomationPermissionForAutomation: vi.fn((enabled: boolean, options?: { tabId?: string }) => ({
        activeTabId: options?.tabId || "tab_1",
        tabs: [
          {
            tabId: options?.tabId || "tab_1",
            sessionId: options?.tabId || "tab_1",
            url: "https://example.com/",
            title: "Example",
            canGoBack: false,
            canGoForward: false,
            loading: false,
            visible: true,
            lastError: "",
            automationEnabled: enabled,
            automationOrigin: "https://example.com",
          },
        ],
      })),
      clickElementForAutomation: vi.fn(async (selector: string, options?: { tabId?: string }) => ({
        schemaVersion: "browser_page_action_result.v1" as const,
        action: "click" as const,
        tabId: options?.tabId || "tab_1",
        selector,
        url: "https://example.com/",
        title: "Example",
        target: {
          tagName: "button",
          role: "button",
          selector,
          text: "Continue",
          inputType: "",
          disabled: false,
        },
        pageSummary: null,
      })),
      setInputValueForAutomation: vi.fn(async (selector: string, value: string, options?: { tabId?: string }) => ({
        schemaVersion: "browser_page_action_result.v1" as const,
        action: "set_input_value" as const,
        tabId: options?.tabId || "tab_1",
        selector,
        url: "https://example.com/",
        title: "Example",
        target: {
          tagName: "input",
          role: "input",
          selector,
          text: "",
          inputType: "text",
          disabled: false,
        },
        valueApplied: true,
        valueLength: value.length,
        pageSummary: null,
      })),
      submitFormForAutomation: vi.fn(async (selector: string, options?: { tabId?: string }) => ({
        schemaVersion: "browser_page_action_result.v1" as const,
        action: "submit_form" as const,
        tabId: options?.tabId || "tab_1",
        selector,
        url: "https://example.com/",
        title: "Example",
        target: {
          tagName: "form",
          role: "form",
          selector,
          text: "",
          inputType: "",
          disabled: false,
        },
        submitted: true,
        pageSummary: null,
      })),
    };

    bridge = new DesktopBrowserBridge(manager, {
      token: "secret_token",
      host: "127.0.0.1",
      port: 0,
    });
    const handle = await bridge.start();

    const unauthorized = await fetch(`${handle.baseUrl}/tabs`);
    expect(unauthorized.status).toBe(401);

    const tabsResponse = await fetch(`${handle.baseUrl}/tabs`, {
      headers: { Authorization: "Bearer secret_token" },
    });
    expect(await tabsResponse.json()).toEqual({
      activeTabId: "tab_1",
      tabs: [
        {
          tabId: "tab_1",
          sessionId: "tab_1",
          url: "https://example.com/",
          title: "Example",
          canGoBack: false,
          canGoForward: false,
          loading: false,
          visible: true,
          lastError: "",
          automationEnabled: false,
          automationOrigin: "https://example.com",
        },
      ],
    });
    expect(manager.listTabsForAutomation).toHaveBeenCalledOnce();

    const navigateResponse = await fetch(`${handle.baseUrl}/navigate`, {
      method: "POST",
      headers: {
        Authorization: "Bearer secret_token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tab_id: "tab_1", url: "https://example.com/docs" }),
    });
    expect(await navigateResponse.json()).toEqual({
      activeTabId: "tab_1",
      tabs: [
        {
          tabId: "tab_1",
          sessionId: "tab_1",
          url: "https://example.com/docs",
          title: "Loaded",
          canGoBack: false,
          canGoForward: false,
          loading: false,
          visible: true,
          lastError: "",
          automationEnabled: false,
          automationOrigin: "https://example.com",
        },
      ],
    });
    expect(manager.navigateForAutomation).toHaveBeenCalledWith("https://example.com/docs", { tabId: "tab_1" });

    const clickResponse = await fetch(`${handle.baseUrl}/click`, {
      method: "POST",
      headers: {
        Authorization: "Bearer secret_token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tab_id: "tab_1", selector: "button.primary" }),
    });
    expect(await clickResponse.json()).toMatchObject({
      action: "click",
      selector: "button.primary",
      tabId: "tab_1",
    });
    expect(manager.clickElementForAutomation).toHaveBeenCalledWith("button.primary", { tabId: "tab_1" });
  });
});
