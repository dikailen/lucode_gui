import {
  BrowserWindow,
  WebContentsView,
  ipcMain,
  shell,
  type IpcMainEvent,
  type IpcMainInvokeEvent,
  type WebContents,
} from "electron";
import { randomUUID } from "node:crypto";

export type BrowserBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type BrowserState = {
  tabId: string;
  sessionId: string;
  url: string;
  title: string;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
  visible: boolean;
  lastError: string;
  automationEnabled: boolean;
  automationOrigin: string;
};

export type BrowserWorkspaceState = {
  activeTabId: string;
  tabs: BrowserState[];
};

export type BrowserDiagnostics = {
  activeTabId: string;
  tabCount: number;
  visibleTabIds: string[];
  hasBounds: boolean;
  bounds: BrowserBounds | null;
  layoutRequestCount: number;
  boundsApplyCount: number;
  visibilityChangeCount: number;
  focusRequestCount: number;
  stateEventCount: number;
};

export type BrowserPageSummaryOptions = {
  maxTextLength?: number;
  maxElements?: number;
};

export type BrowserAutomationTarget = {
  tabId?: string;
};

export type BrowserPageHeading = {
  level: number;
  text: string;
};

export type BrowserPageElement = {
  id: string;
  role: string;
  tagName: string;
  text: string;
  ariaLabel: string;
  name: string;
  placeholder: string;
  value: string;
  href: string;
  inputType: string;
  disabled: boolean;
  selector: string;
};

export type BrowserPageSummary = {
  schemaVersion: "browser_page_summary.v1";
  tabId: string;
  url: string;
  title: string;
  capturedAt: string;
  text: string;
  textTruncated: boolean;
  headings: BrowserPageHeading[];
  elements: BrowserPageElement[];
  elementsTruncated: boolean;
};

export type BrowserPageActionKind = "click" | "set_input_value" | "submit_form";

export type BrowserPageActionTarget = {
  tagName: string;
  role: string;
  selector: string;
  text: string;
  inputType: string;
  disabled: boolean;
};

export type BrowserPageActionResult = {
  schemaVersion: "browser_page_action_result.v1";
  action: BrowserPageActionKind;
  tabId: string;
  selector: string;
  url: string;
  title: string;
  target: BrowserPageActionTarget;
  valueApplied?: boolean;
  valueLength?: number;
  submitted?: boolean;
  pageSummary: BrowserPageSummary | null;
};

export type BrowserAutomationApi = {
  listTabsForAutomation(): BrowserWorkspaceState;
  navigateForAutomation(rawUrl: string, options?: BrowserAutomationTarget): Promise<BrowserWorkspaceState>;
  getPageSummaryForAutomation(
    options?: BrowserAutomationTarget & BrowserPageSummaryOptions,
  ): Promise<BrowserPageSummary>;
  setAutomationPermissionForAutomation(enabled: boolean, options?: BrowserAutomationTarget): BrowserWorkspaceState;
  clickElementForAutomation(selector: string, options?: BrowserAutomationTarget): Promise<BrowserPageActionResult>;
  setInputValueForAutomation(
    selector: string,
    value: string,
    options?: BrowserAutomationTarget,
  ): Promise<BrowserPageActionResult>;
  submitFormForAutomation(selector: string, options?: BrowserAutomationTarget): Promise<BrowserPageActionResult>;
};

type BrowserPageActionRequest = {
  action: BrowserPageActionKind;
  selector: string;
  value?: string;
};

type BrowserSession = {
  sessionId: string;
  owner: WebContents;
  webContentsId: number;
  window: BrowserWindow;
  view: WebContentsView;
  visible: boolean;
  bounds?: BrowserBounds;
  lastError: string;
  close: () => void;
};

type BrowserOwnerDiagnostics = {
  layoutRequestCount: number;
  boundsApplyCount: number;
  visibilityChangeCount: number;
  focusRequestCount: number;
  stateEventCount: number;
};

export class DesktopBrowserManager implements BrowserAutomationApi {
  private readonly sessions = new Map<string, BrowserSession>();
  private readonly activeSessionByOwnerId = new Map<number, string>();
  private readonly boundsByOwnerId = new Map<number, BrowserBounds>();
  private readonly diagnosticsByOwnerId = new Map<number, BrowserOwnerDiagnostics>();
  private readonly automationOriginsByOwnerId = new Map<number, Set<string>>();

  registerIpc(): void {
    ipcMain.handle("lucode-browser:list", (event) => this.listTabs(event));
    ipcMain.handle("lucode-browser:create-tab", (event) => this.createTab(event));
    ipcMain.handle("lucode-browser:activate-tab", (event, payload: { tabId?: string }) =>
      this.activateTab(event, String(payload?.tabId || "")),
    );
    ipcMain.handle("lucode-browser:close-tab", (event, payload: { tabId?: string }) =>
      this.closeTab(event, String(payload?.tabId || "")),
    );
    ipcMain.handle("lucode-browser:navigate", (event, payload: { sessionId?: string; url?: string }) =>
      this.navigate(event, String(payload?.sessionId || ""), String(payload?.url || "")),
    );
    ipcMain.handle("lucode-browser:go-back", (event, payload: { sessionId?: string }) =>
      this.goBack(event, String(payload?.sessionId || "")),
    );
    ipcMain.handle("lucode-browser:go-forward", (event, payload: { sessionId?: string }) =>
      this.goForward(event, String(payload?.sessionId || "")),
    );
    ipcMain.handle("lucode-browser:reload", (event, payload: { sessionId?: string }) =>
      this.reload(event, String(payload?.sessionId || "")),
    );
    ipcMain.handle("lucode-browser:get-state", (event, payload: { sessionId?: string }) =>
      this.getState(event, String(payload?.sessionId || "")),
    );
    ipcMain.handle(
      "lucode-browser:page-summary",
      (event, payload: { tabId?: string; maxTextLength?: number; maxElements?: number }) =>
        this.getPageSummary(event, String(payload?.tabId || ""), {
          maxTextLength: payload?.maxTextLength,
          maxElements: payload?.maxElements,
        }),
    );
    ipcMain.handle("lucode-browser:click-element", (event, payload: { tabId?: string; selector?: string }) =>
      this.clickElement(event, String(payload?.tabId || ""), String(payload?.selector || "")),
    );
    ipcMain.handle(
      "lucode-browser:set-input-value",
      (event, payload: { tabId?: string; selector?: string; value?: string }) =>
        this.setInputValue(
          event,
          String(payload?.tabId || ""),
          String(payload?.selector || ""),
          typeof payload?.value === "string" ? payload.value : String(payload?.value ?? ""),
        ),
    );
    ipcMain.handle("lucode-browser:submit-form", (event, payload: { tabId?: string; selector?: string }) =>
      this.submitForm(event, String(payload?.tabId || ""), String(payload?.selector || "")),
    );
    ipcMain.handle(
      "lucode-browser:set-automation-permission",
      (event, payload: { tabId?: string; enabled?: boolean }) =>
        this.setAutomationPermission(event, String(payload?.tabId || ""), payload?.enabled === true),
    );
    ipcMain.handle("lucode-browser:diagnostics", (event) => this.getDiagnostics(event));
    ipcMain.handle("lucode-browser:close", (event, payload: { sessionId?: string }) =>
      this.closeTab(event, String(payload?.sessionId || "")),
    );
    ipcMain.on("lucode-browser:set-bounds", (event, payload: { sessionId?: string; bounds?: Partial<BrowserBounds> }) => {
      this.setBounds(event, payload?.bounds || {});
    });
    ipcMain.on("lucode-browser:hide", (event, payload: { sessionId?: string }) => {
      this.hide(event);
    });
    ipcMain.on("lucode-browser:focus-active", (event) => {
      this.focusActive(event);
    });
  }

  dispose(): void {
    for (const sessionId of Array.from(this.sessions.keys())) {
      this.closeSession(sessionId);
    }
    this.activeSessionByOwnerId.clear();
    this.boundsByOwnerId.clear();
    this.diagnosticsByOwnerId.clear();
    this.automationOriginsByOwnerId.clear();
  }

  disposeForWebContentsId(webContentsId: number): void {
    for (const session of Array.from(this.sessions.values())) {
      if (session.webContentsId === webContentsId) {
        this.closeSession(session.sessionId);
      }
    }
    this.activeSessionByOwnerId.delete(webContentsId);
    this.boundsByOwnerId.delete(webContentsId);
    this.diagnosticsByOwnerId.delete(webContentsId);
    this.automationOriginsByOwnerId.delete(webContentsId);
  }

  listTabsForAutomation(): BrowserWorkspaceState {
    const owner = this.preferredAutomationOwner();
    if (!owner) {
      return { activeTabId: "", tabs: [] };
    }
    return this.snapshotForOwner(owner);
  }

  async navigateForAutomation(
    rawUrl: string,
    options: BrowserAutomationTarget = {},
  ): Promise<BrowserWorkspaceState> {
    const session = this.resolveAutomationSession(options, true);
    const url = normalizeBrowserUrl(rawUrl);
    session.lastError = "";
    this.activateSessionForOwner(session.owner, session.sessionId);
    await session.view.webContents.loadURL(url);
    this.applyActiveBounds(session.owner);
    return this.snapshotForOwner(session.owner);
  }

  async getPageSummaryForAutomation(
    options: BrowserAutomationTarget & BrowserPageSummaryOptions = {},
  ): Promise<BrowserPageSummary> {
    const session = this.resolveAutomationSession(options, false);
    return this.getPageSummaryForSession(session, options);
  }

  setAutomationPermissionForAutomation(
    enabled: boolean,
    options: BrowserAutomationTarget = {},
  ): BrowserWorkspaceState {
    const session = this.resolveAutomationSession(options, false);
    return this.setAutomationPermissionForSession(session, enabled);
  }

  async clickElementForAutomation(
    selector: string,
    options: BrowserAutomationTarget = {},
  ): Promise<BrowserPageActionResult> {
    const session = this.resolveAutomationSession(options, false);
    return this.performPageActionForSession(session, { action: "click", selector });
  }

  async setInputValueForAutomation(
    selector: string,
    value: string,
    options: BrowserAutomationTarget = {},
  ): Promise<BrowserPageActionResult> {
    const session = this.resolveAutomationSession(options, false);
    return this.performPageActionForSession(session, { action: "set_input_value", selector, value });
  }

  async submitFormForAutomation(
    selector: string,
    options: BrowserAutomationTarget = {},
  ): Promise<BrowserPageActionResult> {
    const session = this.resolveAutomationSession(options, false);
    return this.performPageActionForSession(session, { action: "submit_form", selector });
  }

  private listTabs(event: IpcMainInvokeEvent): BrowserWorkspaceState {
    return this.snapshotForOwner(event.sender);
  }

  private createTab(event: IpcMainInvokeEvent): BrowserWorkspaceState {
    const parentWindow = BrowserWindow.fromWebContents(event.sender);
    if (!parentWindow) {
      throw new Error("Cannot create browser session without a parent window.");
    }

    const session = this.createBrowserSession(event.sender, parentWindow);
    this.activateSessionForOwner(event.sender, session.sessionId);
    return this.snapshotForOwner(event.sender);
  }

  private createBrowserSession(owner: WebContents, parentWindow: BrowserWindow): BrowserSession {
    const view = new WebContentsView({
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    const sessionId = randomUUID();
    const session: BrowserSession = {
      sessionId,
      owner,
      webContentsId: owner.id,
      window: parentWindow,
      view,
      visible: false,
      lastError: "",
      close: () => {
        try {
          parentWindow.contentView.removeChildView(view);
        } catch {
          // The parent window may already be closing.
        }
        try {
          if (!view.webContents.isDestroyed()) {
            view.webContents.close();
          }
        } catch {
          // The page may already be gone.
        }
      },
    };

    this.sessions.set(sessionId, session);
    parentWindow.contentView.addChildView(view);
    view.setVisible(false);
    this.attachPageEvents(session);
    return session;
  }

  private activateTab(event: IpcMainInvokeEvent, tabId: string): BrowserWorkspaceState {
    this.requireOwnedSession(event.sender, tabId);
    this.activateSessionForOwner(event.sender, tabId);
    return this.snapshotForOwner(event.sender);
  }

  private async navigate(event: IpcMainInvokeEvent, sessionId: string, rawUrl: string): Promise<BrowserWorkspaceState> {
    const session = this.requireOwnedSession(event.sender, sessionId);
    const url = normalizeBrowserUrl(rawUrl);
    session.lastError = "";
    this.activateSessionForOwner(event.sender, sessionId);
    await session.view.webContents.loadURL(url);
    this.applyActiveBounds(event.sender);
    return this.snapshotForOwner(event.sender);
  }

  private goBack(event: IpcMainInvokeEvent, sessionId: string): BrowserWorkspaceState {
    const session = this.requireOwnedSession(event.sender, sessionId);
    if (session.view.webContents.navigationHistory.canGoBack()) {
      session.view.webContents.navigationHistory.goBack();
    }
    return this.snapshotForOwner(event.sender);
  }

  private goForward(event: IpcMainInvokeEvent, sessionId: string): BrowserWorkspaceState {
    const session = this.requireOwnedSession(event.sender, sessionId);
    if (session.view.webContents.navigationHistory.canGoForward()) {
      session.view.webContents.navigationHistory.goForward();
    }
    return this.snapshotForOwner(event.sender);
  }

  private reload(event: IpcMainInvokeEvent, sessionId: string): BrowserWorkspaceState {
    const session = this.requireOwnedSession(event.sender, sessionId);
    session.view.webContents.reload();
    return this.snapshotForOwner(event.sender);
  }

  private getState(event: IpcMainInvokeEvent, sessionId: string): BrowserWorkspaceState {
    if (sessionId) {
      this.requireOwnedSession(event.sender, sessionId);
    }
    return this.snapshotForOwner(event.sender);
  }

  private closeTab(event: IpcMainInvokeEvent, sessionId: string): BrowserWorkspaceState {
    const session = this.requireOwnedSession(event.sender, sessionId);
    const ownerId = session.webContentsId;
    this.closeSession(sessionId);
    if (this.activeSessionByOwnerId.get(ownerId) === sessionId) {
      const fallback = Array.from(this.sessions.values())
        .filter((item) => item.webContentsId === ownerId)
        .at(-1);
      if (fallback) {
        this.activeSessionByOwnerId.set(ownerId, fallback.sessionId);
        this.activateSessionForOwner(event.sender, fallback.sessionId);
      } else {
        this.activeSessionByOwnerId.delete(ownerId);
      }
    }
    return this.snapshotForOwner(event.sender);
  }

  private setAutomationPermission(
    event: IpcMainInvokeEvent,
    tabId: string,
    enabled: boolean,
  ): BrowserWorkspaceState {
    const session = tabId ? this.requireOwnedSession(event.sender, tabId) : this.activeSessionForOwner(event.sender);
    if (!session) {
      throw new Error("Browser session is not available.");
    }
    return this.setAutomationPermissionForSession(session, enabled);
  }

  private setBounds(event: IpcMainEvent, bounds: Partial<BrowserBounds>): void {
    const nextBounds = clampBrowserBounds(bounds);
    this.diagnosticsForOwner(event.sender.id).layoutRequestCount += 1;
    this.boundsByOwnerId.set(event.sender.id, nextBounds);
    this.applyActiveBounds(event.sender);
  }

  private hide(event: IpcMainEvent): void {
    for (const session of this.sessionsForOwner(event.sender.id)) {
      this.setSessionVisible(session, false);
    }
  }

  private focusActive(event: IpcMainEvent | IpcMainInvokeEvent): void {
    const session = this.activeSessionForOwner(event.sender);
    if (!session || !session.visible || !this.hasVisiblePage(session)) {
      return;
    }
    session.view.webContents.focus();
    this.diagnosticsForOwner(event.sender.id).focusRequestCount += 1;
  }

  private getDiagnostics(event: IpcMainInvokeEvent): BrowserDiagnostics {
    const ownerId = event.sender.id;
    const diagnostics = this.diagnosticsForOwner(ownerId);
    const bounds = this.boundsByOwnerId.get(ownerId) ?? null;
    return {
      activeTabId: this.activeSessionByOwnerId.get(ownerId) || "",
      tabCount: this.sessionsForOwner(ownerId).length,
      visibleTabIds: this.sessionsForOwner(ownerId)
        .filter((session) => session.visible)
        .map((session) => session.sessionId),
      hasBounds: Boolean(bounds),
      bounds,
      layoutRequestCount: diagnostics.layoutRequestCount,
      boundsApplyCount: diagnostics.boundsApplyCount,
      visibilityChangeCount: diagnostics.visibilityChangeCount,
      focusRequestCount: diagnostics.focusRequestCount,
      stateEventCount: diagnostics.stateEventCount,
    };
  }

  private async getPageSummary(
    event: IpcMainInvokeEvent,
    tabId: string,
    options: BrowserPageSummaryOptions = {},
  ): Promise<BrowserPageSummary> {
    const session = tabId ? this.requireOwnedSession(event.sender, tabId) : this.activeSessionForOwner(event.sender);
    if (!session || !this.hasVisiblePage(session)) {
      throw new Error("Browser page is not available.");
    }
    return this.getPageSummaryForSession(session, options);
  }

  private clickElement(event: IpcMainInvokeEvent, tabId: string, selector: string): Promise<BrowserPageActionResult> {
    return this.performPageAction(event, tabId, { action: "click", selector });
  }

  private setInputValue(
    event: IpcMainInvokeEvent,
    tabId: string,
    selector: string,
    value: string,
  ): Promise<BrowserPageActionResult> {
    return this.performPageAction(event, tabId, { action: "set_input_value", selector, value });
  }

  private submitForm(event: IpcMainInvokeEvent, tabId: string, selector: string): Promise<BrowserPageActionResult> {
    return this.performPageAction(event, tabId, { action: "submit_form", selector });
  }

  private async performPageAction(
    event: IpcMainInvokeEvent,
    tabId: string,
    request: BrowserPageActionRequest,
  ): Promise<BrowserPageActionResult> {
    const session = tabId ? this.requireOwnedSession(event.sender, tabId) : this.activeSessionForOwner(event.sender);
    if (!session || !this.hasVisiblePage(session)) {
      throw new Error("Browser page is not available.");
    }
    return this.performPageActionForSession(session, request);
  }

  private async getPageSummaryForSession(
    session: BrowserSession,
    options: BrowserPageSummaryOptions = {},
  ): Promise<BrowserPageSummary> {
    const rawSummary = await readBrowserDomSummary(session.view.webContents);
    return normalizeBrowserPageSummary(session, rawSummary, options);
  }

  private setAutomationPermissionForSession(session: BrowserSession, enabled: boolean): BrowserWorkspaceState {
    const origin = browserAutomationOriginFromUrl(session.view.webContents.getURL());
    if (!origin) {
      throw new Error("Browser automation can only be enabled for loaded http/https pages.");
    }
    const allowedOrigins = this.allowedAutomationOriginsForOwner(session.webContentsId);
    if (enabled) {
      allowedOrigins.add(origin);
    } else {
      allowedOrigins.delete(origin);
    }
    this.sendState(session);
    return this.snapshotForOwner(session.owner);
  }

  private async performPageActionForSession(
    session: BrowserSession,
    request: BrowserPageActionRequest,
  ): Promise<BrowserPageActionResult> {
    const selector = request.selector.trim();
    if (!selector) {
      throw new Error("Browser selector is required.");
    }
    const automationOrigin = browserAutomationOriginFromUrl(session.view.webContents.getURL());
    if (!automationOrigin) {
      throw new Error("Browser automation only supports loaded http/https pages.");
    }
    if (!this.isAutomationAllowed(session)) {
      throw new Error(`Browser automation is blocked for this site. Allow automation for ${automationOrigin} first.`);
    }
    const actionRequest: BrowserPageActionRequest = {
      action: request.action,
      selector,
      value: request.value ?? "",
    };
    const rawResult = await runBrowserDomAction(session.view.webContents, actionRequest);
    const normalized = normalizeBrowserPageActionResult(session, rawResult, actionRequest);
    let pageSummary: BrowserPageSummary | null = null;
    if (this.hasVisiblePage(session)) {
      try {
        const rawSummary = await readBrowserDomSummary(session.view.webContents);
        pageSummary = normalizeBrowserPageSummary(session, rawSummary, {});
      } catch {
        pageSummary = null;
      }
    }
    return {
      ...normalized,
      pageSummary,
    };
  }

  private attachPageEvents(session: BrowserSession): void {
    const contents = session.view.webContents;
    contents.setWindowOpenHandler(({ url }) => {
      this.handleWindowOpen(session, url);
      return { action: "deny" };
    });
    contents.on("will-navigate", (event, url) => {
      try {
        normalizeBrowserUrl(url);
      } catch {
        event.preventDefault();
      }
    });
    contents.on("did-start-loading", () => {
      session.lastError = "";
      this.sendState(session);
    });
    contents.on("did-stop-loading", () => this.sendState(session));
    contents.on("page-title-updated", () => this.sendState(session));
    contents.on("did-navigate", () => this.sendState(session));
    contents.on("did-navigate-in-page", () => this.sendState(session));
    contents.on("did-fail-load", (_event, errorCode, errorDescription, _validatedURL, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) {
        return;
      }
      session.lastError = errorDescription || `Load failed (${errorCode})`;
      this.sendState(session);
    });
  }

  private handleWindowOpen(opener: BrowserSession, rawUrl: string): void {
    const externalUrl = externalPopupUrl(rawUrl);
    if (externalUrl) {
      void shell.openExternal(externalUrl);
      return;
    }

    let normalizedUrl: string;
    try {
      normalizedUrl = normalizeBrowserUrl(rawUrl);
    } catch {
      return;
    }

    const session = this.createBrowserSession(opener.owner, opener.window);
    session.lastError = "";
    this.activateSessionForOwner(opener.owner, session.sessionId);
    void session.view.webContents
      .loadURL(normalizedUrl)
      .then(() => {
        this.applyActiveBounds(opener.owner);
      })
      .catch((error: unknown) => {
        session.lastError = error instanceof Error ? error.message : String(error);
        this.sendState(session);
      });
    this.sendState(session);
  }

  private requireOwnedSession(webContents: WebContents, sessionId: string): BrowserSession {
    const session = this.sessions.get(sessionId);
    if (!session || session.webContentsId !== webContents.id) {
      throw new Error("Browser session is not available.");
    }
    return session;
  }

  private requireSessionById(sessionId: string): BrowserSession {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error("Browser session is not available.");
    }
    return session;
  }

  private closeSession(sessionId: string): void {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return;
    }
    this.sessions.delete(sessionId);
    session.close();
  }

  private sendState(session: BrowserSession): void {
    try {
      if (session.owner.isDestroyed()) {
        return;
      }
      this.diagnosticsForOwner(session.webContentsId).stateEventCount += 1;
      session.owner.send("lucode-browser:state", this.snapshotForOwner(session.owner));
    } catch {
      // Renderer may already be closing.
    }
  }

  private activateSessionForOwner(webContents: WebContents, sessionId: string): void {
    const session = this.requireOwnedSession(webContents, sessionId);
    this.activeSessionByOwnerId.set(webContents.id, session.sessionId);
    for (const item of this.sessionsForOwner(webContents.id)) {
      if (item.sessionId !== session.sessionId) {
        this.setSessionVisible(item, false);
      }
    }
    this.applyActiveBounds(webContents);
  }

  private applyActiveBounds(webContents: WebContents): void {
    const session = this.activeSessionForOwner(webContents);
    if (!session) {
      return;
    }
    const bounds = this.boundsByOwnerId.get(webContents.id);
    if (!bounds) {
      this.setSessionVisible(session, false);
      return;
    }
    if (!sameBrowserBounds(session.bounds, bounds)) {
      session.view.setBounds(bounds);
      session.bounds = bounds;
      this.diagnosticsForOwner(webContents.id).boundsApplyCount += 1;
    }
    const shouldShow = this.hasVisiblePage(session);
    this.setSessionVisible(session, shouldShow);
  }

  private activeSessionForOwner(webContents: WebContents): BrowserSession | null {
    const sessionId = this.activeSessionByOwnerId.get(webContents.id);
    if (!sessionId) {
      return null;
    }
    const session = this.sessions.get(sessionId);
    if (!session || session.webContentsId !== webContents.id) {
      return null;
    }
    return session;
  }

  private preferredAutomationOwner(): WebContents | null {
    const focusedWindow = BrowserWindow.getFocusedWindow();
    if (focusedWindow?.webContents && !focusedWindow.webContents.isDestroyed()) {
      return focusedWindow.webContents;
    }
    for (const session of this.sessions.values()) {
      if (!session.owner.isDestroyed()) {
        return session.owner;
      }
    }
    for (const window of BrowserWindow.getAllWindows()) {
      if (window?.webContents && !window.webContents.isDestroyed()) {
        return window.webContents;
      }
    }
    return null;
  }

  private requireAutomationWindowContext(): { owner: WebContents; window: BrowserWindow } {
    const focusedWindow = BrowserWindow.getFocusedWindow();
    if (focusedWindow?.webContents && !focusedWindow.webContents.isDestroyed()) {
      return { owner: focusedWindow.webContents, window: focusedWindow };
    }
    for (const session of this.sessions.values()) {
      if (!session.owner.isDestroyed()) {
        return { owner: session.owner, window: session.window };
      }
    }
    const firstWindow = BrowserWindow.getAllWindows().find(
      (window) => Boolean(window?.webContents) && !window.webContents.isDestroyed(),
    );
    if (firstWindow?.webContents) {
      return { owner: firstWindow.webContents, window: firstWindow };
    }
    throw new Error("Browser window is not available.");
  }

  private resolveAutomationSession(
    options: BrowserAutomationTarget = {},
    createIfMissing: boolean,
  ): BrowserSession {
    const tabId = String(options.tabId || "").trim();
    if (tabId) {
      return this.requireSessionById(tabId);
    }
    const owner = this.preferredAutomationOwner();
    if (owner) {
      const session = this.activeSessionForOwner(owner);
      if (session) {
        return session;
      }
    }
    if (!createIfMissing) {
      throw new Error("Browser session is not available.");
    }
    const context = this.requireAutomationWindowContext();
    const session = this.createBrowserSession(context.owner, context.window);
    this.activateSessionForOwner(context.owner, session.sessionId);
    return session;
  }

  private sessionsForOwner(webContentsId: number): BrowserSession[] {
    return Array.from(this.sessions.values()).filter((session) => session.webContentsId === webContentsId);
  }

  private hasVisiblePage(session: BrowserSession): boolean {
    const url = session.view.webContents.getURL();
    return Boolean(url && url !== "about:blank");
  }

  private setSessionVisible(session: BrowserSession, visible: boolean): void {
    if (session.visible === visible) {
      return;
    }
    session.visible = visible;
    session.view.setVisible(visible);
    this.diagnosticsForOwner(session.webContentsId).visibilityChangeCount += 1;
  }

  private diagnosticsForOwner(webContentsId: number): BrowserOwnerDiagnostics {
    const existing = this.diagnosticsByOwnerId.get(webContentsId);
    if (existing) {
      return existing;
    }
    const created: BrowserOwnerDiagnostics = {
      layoutRequestCount: 0,
      boundsApplyCount: 0,
      visibilityChangeCount: 0,
      focusRequestCount: 0,
      stateEventCount: 0,
    };
    this.diagnosticsByOwnerId.set(webContentsId, created);
    return created;
  }

  private snapshotForOwner(webContents: WebContents): BrowserWorkspaceState {
    const sessions = this.sessionsForOwner(webContents.id);
    return {
      activeTabId: this.activeSessionByOwnerId.get(webContents.id) || "",
      tabs: sessions.map((session) => this.snapshot(session)),
    };
  }

  private snapshot(session: BrowserSession): BrowserState {
    const contents = session.view.webContents;
    return {
      tabId: session.sessionId,
      sessionId: session.sessionId,
      url: contents.getURL(),
      title: contents.getTitle(),
      canGoBack: contents.navigationHistory.canGoBack(),
      canGoForward: contents.navigationHistory.canGoForward(),
      loading: contents.isLoadingMainFrame(),
      visible: session.visible,
      lastError: session.lastError,
      automationEnabled: this.isAutomationAllowed(session),
      automationOrigin: browserAutomationOriginFromUrl(contents.getURL()),
    };
  }

  private allowedAutomationOriginsForOwner(webContentsId: number): Set<string> {
    const existing = this.automationOriginsByOwnerId.get(webContentsId);
    if (existing) {
      return existing;
    }
    const created = new Set<string>();
    this.automationOriginsByOwnerId.set(webContentsId, created);
    return created;
  }

  private isAutomationAllowed(session: BrowserSession): boolean {
    const origin = browserAutomationOriginFromUrl(session.view.webContents.getURL());
    if (!origin) {
      return false;
    }
    return this.allowedAutomationOriginsForOwner(session.webContentsId).has(origin);
  }
}

export function normalizeBrowserUrl(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    throw new Error("Browser URL is required.");
  }
  const withProtocol = hasExplicitProtocol(trimmed) ? trimmed : `${defaultProtocol(trimmed)}://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (!["http:", "https:", "about:"].includes(parsed.protocol)) {
    throw new Error(`Unsupported browser URL protocol: ${parsed.protocol}`);
  }
  if (parsed.protocol === "about:" && parsed.href !== "about:blank") {
    throw new Error("Only about:blank is supported.");
  }
  return parsed.href;
}

export function clampBrowserBounds(bounds: Partial<BrowserBounds>): BrowserBounds {
  return {
    x: Math.max(0, Math.floor(numberOr(bounds.x, 0))),
    y: Math.max(0, Math.floor(numberOr(bounds.y, 0))),
    width: Math.max(120, Math.floor(numberOr(bounds.width, 120))),
    height: Math.max(80, Math.floor(numberOr(bounds.height, 80))),
  };
}

async function readBrowserDomSummary(webContents: WebContents): Promise<unknown> {
  const contents = webContents as WebContents & {
    executeJavaScriptInIsolatedWorld?: (
      worldId: number,
      scripts: Array<{ code: string }>,
      userGesture?: boolean,
    ) => Promise<unknown>;
  };
  if (typeof contents.executeJavaScriptInIsolatedWorld === "function") {
    return contents.executeJavaScriptInIsolatedWorld(1001, [{ code: BROWSER_DOM_SUMMARY_SCRIPT }], false);
  }
  return webContents.executeJavaScript(BROWSER_DOM_SUMMARY_SCRIPT, false);
}

async function runBrowserDomAction(webContents: WebContents, request: BrowserPageActionRequest): Promise<unknown> {
  const code = buildBrowserDomActionScript(request);
  const contents = webContents as WebContents & {
    executeJavaScriptInIsolatedWorld?: (
      worldId: number,
      scripts: Array<{ code: string }>,
      userGesture?: boolean,
    ) => Promise<unknown>;
  };
  if (typeof contents.executeJavaScriptInIsolatedWorld === "function") {
    return contents.executeJavaScriptInIsolatedWorld(1001, [{ code }], true);
  }
  return webContents.executeJavaScript(code, true);
}

function normalizeBrowserPageSummary(
  session: BrowserSession,
  rawSummary: unknown,
  options: BrowserPageSummaryOptions,
): BrowserPageSummary {
  const limits = normalizeSummaryOptions(options);
  const raw = isRecord(rawSummary) ? rawSummary : {};
  const rawText = stringField(raw.text);
  const rawElements = Array.isArray(raw.elements) ? raw.elements : [];
  const elements = rawElements.slice(0, limits.maxElements).map((element, index) => normalizeBrowserPageElement(element, index));
  return {
    schemaVersion: "browser_page_summary.v1",
    tabId: session.sessionId,
    url: session.view.webContents.getURL(),
    title: session.view.webContents.getTitle(),
    capturedAt: new Date().toISOString(),
    text: rawText.slice(0, limits.maxTextLength),
    textTruncated: rawText.length > limits.maxTextLength,
    headings: normalizeBrowserPageHeadings(raw.headings),
    elements,
    elementsTruncated: rawElements.length > limits.maxElements,
  };
}

function normalizeBrowserPageActionResult(
  session: BrowserSession,
  rawResult: unknown,
  request: BrowserPageActionRequest,
): Omit<BrowserPageActionResult, "pageSummary"> {
  const raw = isRecord(rawResult) ? rawResult : {};
  if (raw.ok !== true) {
    throw new Error(clampText(raw.error, 320) || `Browser action failed: ${request.action}`);
  }
  const result: Omit<BrowserPageActionResult, "pageSummary"> = {
    schemaVersion: "browser_page_action_result.v1",
    action: request.action,
    tabId: session.sessionId,
    selector: request.selector,
    url: session.view.webContents.getURL(),
    title: session.view.webContents.getTitle(),
    target: normalizeBrowserPageActionTarget(raw.target, request.selector),
  };
  if (request.action === "set_input_value") {
    result.valueApplied = raw.valueApplied === true;
    result.valueLength = boundedInteger(raw.valueLength, 0, 0, 1_000_000);
  }
  if (request.action === "submit_form") {
    result.submitted = raw.submitted === true;
  }
  return result;
}

function normalizeSummaryOptions(options: BrowserPageSummaryOptions): Required<BrowserPageSummaryOptions> {
  return {
    maxTextLength: boundedInteger(options.maxTextLength, 4000, 1, 12000),
    maxElements: boundedInteger(options.maxElements, 80, 0, 200),
  };
}

function normalizeBrowserPageHeadings(value: unknown): BrowserPageHeading[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.slice(0, 30).flatMap((item): BrowserPageHeading[] => {
    if (!isRecord(item)) {
      return [];
    }
    const level = boundedInteger(item.level, 1, 1, 6);
    const text = clampText(item.text, 180);
    return text ? [{ level, text }] : [];
  });
}

function normalizeBrowserPageElement(value: unknown, index: number): BrowserPageElement {
  const raw = isRecord(value) ? value : {};
  const tagName = clampText(raw.tagName, 24).toLowerCase();
  const role = clampText(raw.role, 32) || inferredBrowserRole(tagName);
  return {
    id: `el_${index + 1}`,
    role,
    tagName,
    text: clampText(raw.text, 220),
    ariaLabel: clampText(raw.ariaLabel, 180),
    name: clampText(raw.name, 120),
    placeholder: clampText(raw.placeholder, 160),
    value: clampText(raw.value, 120),
    href: clampText(raw.href, 500),
    inputType: clampText(raw.inputType, 40),
    disabled: raw.disabled === true,
    selector: clampText(raw.selector, 300),
  };
}

function normalizeBrowserPageActionTarget(value: unknown, fallbackSelector: string): BrowserPageActionTarget {
  const raw = isRecord(value) ? value : {};
  const tagName = clampText(raw.tagName, 24).toLowerCase();
  return {
    tagName,
    role: clampText(raw.role, 32) || inferredBrowserRole(tagName),
    selector: clampText(raw.selector, 300) || fallbackSelector,
    text: clampText(raw.text, 220),
    inputType: clampText(raw.inputType, 40),
    disabled: raw.disabled === true,
  };
}

function inferredBrowserRole(tagName: string): string {
  if (tagName === "a") {
    return "link";
  }
  if (["input", "textarea", "select"].includes(tagName)) {
    return "input";
  }
  if (tagName === "form") {
    return "form";
  }
  if (tagName === "button") {
    return "button";
  }
  return "element";
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, Math.floor(numeric)));
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function clampText(value: unknown, maxLength: number): string {
  return stringField(value).replace(/\s+/g, " ").trim().slice(0, maxLength);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function hasExplicitProtocol(value: string): boolean {
  const match = value.match(/^([a-zA-Z][a-zA-Z\d+\-.]*):(.*)$/);
  if (!match) {
    return false;
  }
  const protocol = match[1].toLowerCase();
  const rest = match[2] || "";
  if (["http", "https", "about", "javascript", "file", "data"].includes(protocol)) {
    return true;
  }
  return rest.startsWith("//");
}

function externalPopupUrl(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return "";
  }
  const match = trimmed.match(/^([a-zA-Z][a-zA-Z\d+\-.]*):/);
  if (!match) {
    return "";
  }
  const protocol = match[1].toLowerCase();
  return ["mailto", "tel"].includes(protocol) ? trimmed : "";
}

function browserAutomationOriginFromUrl(url: string): string {
  const value = String(url || "").trim();
  if (!value) {
    return "";
  }
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return "";
    }
    return parsed.origin;
  } catch {
    return "";
  }
}

function defaultProtocol(value: string): "http" | "https" {
  const host = value.split("/")[0] || value;
  if (host.startsWith("localhost") || host.startsWith("127.") || host.startsWith("[::1]")) {
    return "http";
  }
  return "https";
}

function sameBrowserBounds(left: BrowserBounds | undefined, right: BrowserBounds): boolean {
  return Boolean(
    left &&
      left.x === right.x &&
      left.y === right.y &&
      left.width === right.width &&
      left.height === right.height,
  );
}

function numberOr(value: unknown, fallback: number): number {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

const BROWSER_DOM_SUMMARY_SCRIPT = `
(() => {
  const MAX_TEXT_CHARS = 16000;
  const MAX_ELEMENTS = 240;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "CANVAS"]);

  function cleanText(value, max = 400) {
    return String(value || "").replace(/\\s+/g, " ").trim().slice(0, max);
  }

  function isVisible(element) {
    if (!element || SKIP_TAGS.has(element.tagName)) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function roleFor(element) {
    const explicitRole = cleanText(element.getAttribute("role"), 48);
    if (explicitRole) {
      return explicitRole;
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "a") {
      return "link";
    }
    if (tag === "button") {
      return "button";
    }
    if (tag === "select") {
      return "select";
    }
    if (tag === "textarea") {
      return "textarea";
    }
    if (tag === "input") {
      return "input";
    }
    return "element";
  }

  function selectorFor(element) {
    if (element.id) {
      return "#" + CSS.escape(element.id);
    }
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
      const tag = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const siblings = Array.from(parent.children).filter((item) => item.tagName === current.tagName);
      const index = siblings.indexOf(current) + 1;
      parts.unshift(siblings.length > 1 ? tag + ":nth-of-type(" + index + ")" : tag);
      current = parent;
    }
    return parts.join(" > ");
  }

  function elementText(element) {
    const tag = element.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      return cleanText(element.value || element.textContent, 220);
    }
    return cleanText(element.innerText || element.textContent, 220);
  }

  const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6"))
    .filter(isVisible)
    .slice(0, 30)
    .map((element) => ({
      level: Number(element.tagName.slice(1)),
      text: cleanText(element.innerText || element.textContent, 180),
    }))
    .filter((item) => item.text);

  const candidates = Array.from(
    document.querySelectorAll("a[href],button,input,textarea,select,[role='button'],[role='link'],[contenteditable='true']")
  );
  const elements = candidates
    .filter(isVisible)
    .slice(0, MAX_ELEMENTS)
    .map((element) => ({
      role: roleFor(element),
      tagName: element.tagName.toLowerCase(),
      text: elementText(element),
      ariaLabel: cleanText(element.getAttribute("aria-label"), 180),
      name: cleanText(element.getAttribute("name"), 120),
      placeholder: cleanText(element.getAttribute("placeholder"), 160),
      value: cleanText(element.value, 120),
      href: cleanText(element.href, 500),
      inputType: cleanText(element.getAttribute("type"), 40),
      disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
      selector: selectorFor(element),
    }));

  return {
    text: cleanText(document.body ? document.body.innerText : "", MAX_TEXT_CHARS),
    headings,
    elements,
  };
})()
`;

function buildBrowserDomActionScript(request: BrowserPageActionRequest): string {
  return `
(() => {
  const request = ${JSON.stringify({
    action: request.action,
    selector: request.selector,
    value: request.value ?? "",
  })};

  function cleanText(value, max = 220) {
    return String(value || "").replace(/\\s+/g, " ").trim().slice(0, max);
  }

  function roleFor(element) {
    const explicitRole = cleanText(element.getAttribute?.("role"), 48);
    if (explicitRole) {
      return explicitRole;
    }
    const tag = String(element.tagName || "").toLowerCase();
    if (tag === "a") {
      return "link";
    }
    if (tag === "button") {
      return "button";
    }
    if (tag === "select") {
      return "select";
    }
    if (tag === "textarea") {
      return "textarea";
    }
    if (tag === "form") {
      return "form";
    }
    if (tag === "input") {
      return "input";
    }
    return "element";
  }

  function targetFor(element, selector) {
    const tagName = String(element.tagName || "").toLowerCase();
    return {
      tagName,
      role: roleFor(element),
      selector: cleanText(selector, 300),
      text: cleanText(
        tagName === "input" || tagName === "textarea" || tagName === "select"
          ? element.value || element.textContent
          : element.innerText || element.textContent,
        220,
      ),
      inputType: tagName === "input" ? cleanText(element.type || "", 40).toLowerCase() : "",
      disabled: Boolean(element.disabled),
    };
  }

  function dispatchValueEvents(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setElementValue(element, nextValue) {
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : element instanceof HTMLSelectElement
          ? HTMLSelectElement.prototype
          : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && typeof descriptor.set === "function") {
      descriptor.set.call(element, nextValue);
      return;
    }
    element.value = nextValue;
  }

  const selector = String(request.selector || "").trim();
  if (!selector) {
    return { ok: false, error: "Browser selector is required." };
  }

  const element = document.querySelector(selector);
  if (!(element instanceof Element)) {
    return { ok: false, error: "Selector not found: " + selector };
  }

  if (request.action === "click") {
    if (Boolean(element.disabled)) {
      return { ok: false, error: "Target is disabled: " + selector };
    }
    if (typeof element.click !== "function") {
      return { ok: false, error: "Selected element is not clickable: " + selector };
    }
    if (element instanceof HTMLElement) {
      element.focus?.();
      element.scrollIntoView?.({ block: "center", inline: "center" });
    }
    element.click();
    return {
      ok: true,
      action: "click",
      selector,
      target: targetFor(element, selector),
    };
  }

  if (request.action === "set_input_value") {
    const nextValue = typeof request.value === "string" ? request.value : String(request.value ?? "");
    if (Boolean(element.disabled)) {
      return { ok: false, error: "Target is disabled: " + selector };
    }
    if (element instanceof HTMLInputElement) {
      const inputType = cleanText(element.type || "", 40).toLowerCase();
      if (["file", "checkbox", "radio", "submit", "reset", "button", "image", "hidden"].includes(inputType)) {
        return { ok: false, error: "Unsupported input element for set_input_value: input[type=" + inputType + "]" };
      }
      if (element.readOnly) {
        return { ok: false, error: "Target is read-only: " + selector };
      }
      setElementValue(element, nextValue);
      dispatchValueEvents(element);
      return {
        ok: true,
        action: "set_input_value",
        selector,
        valueApplied: true,
        valueLength: nextValue.length,
        target: targetFor(element, selector),
      };
    }
    if (element instanceof HTMLTextAreaElement) {
      if (element.readOnly) {
        return { ok: false, error: "Target is read-only: " + selector };
      }
      setElementValue(element, nextValue);
      dispatchValueEvents(element);
      return {
        ok: true,
        action: "set_input_value",
        selector,
        valueApplied: true,
        valueLength: nextValue.length,
        target: targetFor(element, selector),
      };
    }
    if (element instanceof HTMLSelectElement) {
      const nextOption = Array.from(element.options).find((option) => option.value === nextValue || option.text === nextValue);
      if (!nextOption && nextValue) {
        return { ok: false, error: "Option not found for selector: " + selector };
      }
      setElementValue(element, nextOption ? nextOption.value : nextValue);
      dispatchValueEvents(element);
      return {
        ok: true,
        action: "set_input_value",
        selector,
        valueApplied: true,
        valueLength: nextValue.length,
        target: targetFor(element, selector),
      };
    }
    return {
      ok: false,
      error: "Unsupported element for set_input_value: " + String(element.tagName || "").toLowerCase(),
    };
  }

  if (request.action === "submit_form") {
    const submitter =
      element instanceof HTMLButtonElement ||
      (element instanceof HTMLInputElement && ["submit", "image"].includes(cleanText(element.type || "", 40).toLowerCase()))
        ? element
        : null;
    const form =
      element instanceof HTMLFormElement
        ? element
        : submitter?.form || (element instanceof HTMLElement ? element.closest("form") : null);
    if (!(form instanceof HTMLFormElement)) {
      return { ok: false, error: "No form found for selector: " + selector };
    }
    if (typeof form.requestSubmit === "function") {
      submitter ? form.requestSubmit(submitter) : form.requestSubmit();
    } else if (typeof form.submit === "function") {
      form.submit();
    } else {
      return { ok: false, error: "Selected form cannot be submitted: " + selector };
    }
    return {
      ok: true,
      action: "submit_form",
      selector,
      submitted: true,
      target: targetFor(form, selector),
    };
  }

  return { ok: false, error: "Unsupported browser action: " + String(request.action || "") };
})()
`;
}
