import { FormEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";

import type { Translator } from "../i18n";
import type {
  DesktopBrowserBridge,
  DesktopBrowserBounds,
  DesktopBrowserTabState,
  DesktopBrowserWorkspaceState,
} from "../../shared/types";

export type BrowserPanelProps = {
  t: Translator;
  onClose?: () => void;
};

const EMPTY_WORKSPACE: DesktopBrowserWorkspaceState = {
  activeTabId: "",
  tabs: [],
};

export function BrowserPanel({ t, onClose }: BrowserPanelProps) {
  const bridge = typeof window === "undefined" ? undefined : window.lucodeBrowser;

  if (!bridge) {
    return <BrowserUnavailable t={t} />;
  }

  return <BrowserWorkspace t={t} bridge={bridge} onClose={onClose} />;
}

function BrowserWorkspace({
  t,
  bridge,
  onClose,
}: {
  t: Translator;
  bridge: DesktopBrowserBridge;
  onClose?: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const activeTabIdRef = useRef("");
  const workspaceRef = useRef<DesktopBrowserWorkspaceState>(EMPTY_WORKSPACE);
  const pendingBoundsFrameRef = useRef<number | null>(null);
  const lastSentBoundsRef = useRef<DesktopBrowserBounds | null>(null);
  const [workspace, setWorkspace] = useState<DesktopBrowserWorkspaceState>(EMPTY_WORKSPACE);
  const [addressByTab, setAddressByTab] = useState<Record<string, string>>({});
  const [automationError, setAutomationError] = useState("");
  const [automationPending, setAutomationPending] = useState(false);

  const activeTab = workspace.tabs.find((tab) => tab.tabId === workspace.activeTabId) ?? workspace.tabs[0] ?? null;

  const applyWorkspace = useCallback((nextWorkspace: DesktopBrowserWorkspaceState) => {
    workspaceRef.current = nextWorkspace;
    activeTabIdRef.current = nextWorkspace.activeTabId;
    setWorkspace(nextWorkspace);
    setAutomationError("");
    setAddressByTab((current) => {
      const next = { ...current };
      for (const tab of nextWorkspace.tabs) {
        if (tab.url && tab.url !== "about:blank" && !next[tab.tabId]) {
          next[tab.tabId] = tab.url;
        }
      }
      return next;
    });
  }, []);

  const syncBounds = useCallback(() => {
    const element = viewportRef.current;
    const current = workspaceRef.current;
    const tab = current.tabs.find((item) => item.tabId === current.activeTabId) ?? null;
    if (!element || !tab || !tabHasPage(tab)) {
      bridge.hide();
      lastSentBoundsRef.current = null;
      return;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 80) {
      bridge.hide();
      lastSentBoundsRef.current = null;
      return;
    }
    const nextBounds = desktopBoundsFromRect(rect);
    if (sameDesktopBounds(lastSentBoundsRef.current, nextBounds)) {
      return;
    }
    lastSentBoundsRef.current = nextBounds;
    bridge.setBounds(nextBounds);
    void debugBrowserDiagnostics(bridge, "bounds");
  }, [bridge]);

  const requestBoundsSync = useCallback(() => {
    if (pendingBoundsFrameRef.current !== null) {
      window.cancelAnimationFrame(pendingBoundsFrameRef.current);
    }
    pendingBoundsFrameRef.current = window.requestAnimationFrame(() => {
      pendingBoundsFrameRef.current = null;
      syncBounds();
    });
  }, [syncBounds]);

  useEffect(() => {
    let disposed = false;
    const offState = bridge.onState((nextWorkspace) => {
      if (disposed) {
        return;
      }
      applyWorkspace(nextWorkspace);
      requestBoundsSync();
    });

    const ensureWorkspace = async () => {
      try {
        const listed = await bridge.listTabs();
        if (disposed) {
          return;
        }
        if (listed.tabs.length > 0) {
          applyWorkspace(listed);
          requestBoundsSync();
          return;
        }
        const created = await bridge.createTab();
        if (!disposed) {
          applyWorkspace(created);
          requestBoundsSync();
        }
      } catch {
        if (!disposed) {
          applyWorkspace(EMPTY_WORKSPACE);
        }
      }
    };

    void ensureWorkspace();
    return () => {
      disposed = true;
      offState();
      bridge.hide();
      lastSentBoundsRef.current = null;
      if (pendingBoundsFrameRef.current !== null) {
        window.cancelAnimationFrame(pendingBoundsFrameRef.current);
        pendingBoundsFrameRef.current = null;
      }
    };
  }, [applyWorkspace, bridge, requestBoundsSync]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(requestBoundsSync);
    observer.observe(element);
    window.addEventListener("resize", requestBoundsSync);
    requestBoundsSync();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", requestBoundsSync);
    };
  }, [requestBoundsSync, workspace.activeTabId]);

  function focusViewport() {
    if (!tabHasPage(activeTab)) {
      return;
    }
    bridge.focusActive();
    void debugBrowserDiagnostics(bridge, "focus");
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!activeTab) {
      return;
    }
    const targetUrl = addressForTab(activeTab).trim();
    if (!targetUrl) {
      return;
    }
    try {
      const nextWorkspace = await bridge.navigate(activeTab.tabId, targetUrl);
      applyWorkspace(nextWorkspace);
      const nextTab = nextWorkspace.tabs.find((tab) => tab.tabId === nextWorkspace.activeTabId) ?? null;
      if (nextTab?.url) {
        setAddressByTab((current) => ({ ...current, [nextTab.tabId]: nextTab.url }));
      }
      requestBoundsSync();
    } catch (navigateError) {
      applyWorkspace({
        ...workspaceRef.current,
        tabs: workspaceRef.current.tabs.map((tab) =>
          tab.tabId === activeTab.tabId ? { ...tab, lastError: errorMessage(navigateError) } : tab,
        ),
      });
    }
  }

  async function applyAction(action: "back" | "forward" | "reload") {
    if (!activeTab) {
      return;
    }
    try {
      const nextWorkspace =
        action === "back"
          ? await bridge.goBack(activeTab.tabId)
          : action === "forward"
            ? await bridge.goForward(activeTab.tabId)
            : await bridge.reload(activeTab.tabId);
      applyWorkspace(nextWorkspace);
      requestBoundsSync();
    } catch (actionError) {
      applyWorkspace({
        ...workspaceRef.current,
        tabs: workspaceRef.current.tabs.map((tab) =>
          tab.tabId === activeTab.tabId ? { ...tab, lastError: errorMessage(actionError) } : tab,
        ),
      });
    }
  }

  async function openBlankTab() {
    const nextWorkspace = await bridge.createTab();
    applyWorkspace(nextWorkspace);
    requestBoundsSync();
  }

  async function activateTab(tabId: string) {
    const nextWorkspace = await bridge.activateTab(tabId);
    applyWorkspace(nextWorkspace);
    requestBoundsSync();
  }

  async function closeTab(event: MouseEvent, tabId: string) {
    event.stopPropagation();
    const nextWorkspace = await bridge.closeTab(tabId);
    if (shouldCloseBrowserPanelAfterTabClose(nextWorkspace)) {
      bridge.hide();
      lastSentBoundsRef.current = null;
      applyWorkspace(EMPTY_WORKSPACE);
      onClose?.();
      return;
    }
    applyWorkspace(nextWorkspace);
    requestBoundsSync();
  }

  async function toggleAutomationPermission() {
    if (!activeTab || !activeTab.automationOrigin) {
      return;
    }
    setAutomationPending(true);
    try {
      const nextWorkspace = await bridge.setAutomationPermission(activeTab.tabId, !activeTab.automationEnabled);
      applyWorkspace(nextWorkspace);
    } catch (error) {
      setAutomationError(errorMessage(error));
    } finally {
      setAutomationPending(false);
    }
  }

  function updateActiveAddress(value: string) {
    if (!activeTab) {
      return;
    }
    setAddressByTab((current) => ({ ...current, [activeTab.tabId]: value }));
  }

  function addressForTab(tab: DesktopBrowserTabState): string {
    return addressByTab[tab.tabId] ?? (tab.url && tab.url !== "about:blank" ? tab.url : "");
  }

  const hasPage = tabHasPage(activeTab);
  const address = activeTab ? addressForTab(activeTab) : "";
  const error = automationError || activeTab?.lastError || "";
  const canGoBack = Boolean(activeTab?.canGoBack);
  const canGoForward = Boolean(activeTab?.canGoForward);
  const canReload = hasPage && Boolean(activeTab);
  const canOpen = Boolean(address.trim() && activeTab);
  const automationOrigin = activeTab?.automationOrigin || "";
  const automationEnabled = Boolean(activeTab?.automationEnabled);
  const showAutomationBar = hasPage && Boolean(automationOrigin);

  return (
    <div className="browser-panel">
      <header className="browser-chrome">
        <div className="browser-tab-strip">
          {workspace.tabs.map((tab) => {
            const label = tabLabel(tab, t);
            return (
              <button
                className={tab.tabId === workspace.activeTabId ? "browser-tab active" : "browser-tab"}
                type="button"
                title={label}
                onClick={() => void activateTab(tab.tabId)}
                key={tab.tabId}
              >
                <span className="browser-tab-globe" aria-hidden="true" />
                <span>{label}</span>
                <span
                  className="browser-tab-close"
                  title={t("common.close")}
                  onClick={(event) => void closeTab(event, tab.tabId)}
                />
              </button>
            );
          })}
          <button className="browser-tab-add" type="button" title={t("browser.newTab")} onClick={() => void openBlankTab()}>
            +
          </button>
        </div>
        <div className="browser-window-actions">
          <button className="browser-window-button" type="button" title={t("browser.syncView")} onClick={requestBoundsSync}>
            <span className="browser-window-icon maximize" aria-hidden="true" />
          </button>
          <button className="browser-window-button" type="button" title={t("common.close")} onClick={onClose}>
            <span className="browser-window-icon close" aria-hidden="true" />
          </button>
        </div>
      </header>
      <form className="browser-toolbar" onSubmit={handleSubmit}>
        <button
          className="browser-icon-button"
          type="button"
          title={t("browser.back")}
          disabled={!canGoBack}
          onClick={() => void applyAction("back")}
        >
          <span className="browser-icon back" aria-hidden="true" />
        </button>
        <button
          className="browser-icon-button"
          type="button"
          title={t("browser.forward")}
          disabled={!canGoForward}
          onClick={() => void applyAction("forward")}
        >
          <span className="browser-icon forward" aria-hidden="true" />
        </button>
        <button
          className="browser-icon-button"
          type="button"
          title={t("browser.reload")}
          disabled={!canReload}
          onClick={() => void applyAction("reload")}
        >
          <span className="browser-icon reload" aria-hidden="true" />
        </button>
        <label className="browser-address-field" aria-label={t("browser.urlLabel")}>
          <input
            value={address}
            placeholder={t("browser.addressPlaceholder")}
            onChange={(event) => updateActiveAddress(event.target.value)}
          />
          <button className="browser-address-submit" type="submit" title={t("browser.open")} disabled={!canOpen}>
            <span className="browser-icon external" aria-hidden="true" />
          </button>
        </label>
      </form>
      {showAutomationBar ? (
        <div className="browser-automation-bar">
          <div className="browser-automation-copy">
            <span className={automationEnabled ? "browser-automation-badge allowed" : "browser-automation-badge blocked"}>
              {automationEnabled ? t("browser.automationAllowed") : t("browser.automationBlocked")}
            </span>
            <span className="browser-automation-detail">
              {automationEnabled
                ? t("browser.automationAllowedDetail", { origin: automationOrigin })
                : t("browser.automationBlockedDetail", { origin: automationOrigin })}
            </span>
          </div>
          <button
            className={automationEnabled ? "browser-automation-button danger" : "browser-automation-button"}
            type="button"
            disabled={automationPending}
            onClick={() => void toggleAutomationPermission()}
          >
            {automationEnabled ? t("browser.blockAutomation") : t("browser.allowAutomation")}
          </button>
        </div>
      ) : null}
      {error ? (
        <div className="browser-error">
          {t("browser.errorPrefix")}: {error}
        </div>
      ) : null}
      <div
        className={hasPage ? "browser-viewport has-page" : "browser-viewport"}
        ref={viewportRef}
        onPointerDown={focusViewport}
      >
        {!hasPage ? (
          <div className="browser-empty-state">
            <span className="browser-empty-globe" aria-hidden="true" />
            <strong>{t("browser.emptyTitle")}</strong>
            <p>{t("browser.emptyBody")}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function BrowserUnavailable({ t }: { t: Translator }) {
  return (
    <div className="dock-placeholder">
      <div className="dock-placeholder-icon browser" aria-hidden="true" />
      <div className="dock-placeholder-title">{t("browser.noBridgeTitle")}</div>
      <p>{t("browser.noBridgeBody")}</p>
    </div>
  );
}

function tabHasPage(tab: DesktopBrowserTabState | null): boolean {
  return Boolean(tab?.url && tab.url !== "about:blank");
}

export function shouldCloseBrowserPanelAfterTabClose(workspace: DesktopBrowserWorkspaceState): boolean {
  return workspace.tabs.length === 0;
}

function tabLabel(tab: DesktopBrowserTabState, t: Translator): string {
  if (tab.title) {
    return tab.title;
  }
  if (tab.url && tab.url !== "about:blank") {
    return tab.url.replace(/^https?:\/\//, "");
  }
  return t("browser.newTab");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function sameDesktopBounds(left: DesktopBrowserBounds | null, right: DesktopBrowserBounds): boolean {
  return Boolean(
    left &&
      left.x === right.x &&
      left.y === right.y &&
      left.width === right.width &&
      left.height === right.height,
  );
}

function desktopBoundsFromRect(rect: DOMRect): DesktopBrowserBounds {
  return {
    x: Math.max(0, Math.floor(rect.x)),
    y: Math.max(0, Math.floor(rect.y)),
    width: Math.max(120, Math.floor(rect.width)),
    height: Math.max(80, Math.floor(rect.height)),
  };
}

async function debugBrowserDiagnostics(bridge: DesktopBrowserBridge, reason: "bounds" | "focus"): Promise<void> {
  if (!browserDiagnosticsEnabled()) {
    return;
  }
  try {
    const diagnostics = await bridge.getDiagnostics();
    console.debug("[lucode-browser]", reason, diagnostics);
  } catch {
    // Diagnostics are development-only and must never break the browser UI.
  }
}

function browserDiagnosticsEnabled(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem("lucode.browserDiagnostics") === "1";
  } catch {
    return false;
  }
}
