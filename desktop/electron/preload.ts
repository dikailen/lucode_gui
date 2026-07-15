import { contextBridge, ipcRenderer, webUtils } from "electron";

import { resolveDroppedFilePath } from "./filePicker.js";

function argValue(name: string): string {
  const prefix = `--${name}=`;
  const match = process.argv.find((item) => item.startsWith(prefix));
  return match ? match.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld("lucodeRuntime", {
  baseUrl: argValue("lucode-runtime-base-url"),
  token: argValue("lucode-runtime-token"),
  rendererSource: argValue("lucode-renderer-source") || "dist",
  buildTime: argValue("lucode-build-time"),
});

contextBridge.exposeInMainWorld("lucodeDesktop", {
  choosePluginSource(kind: "skill" | "mcp" | "package") {
    return ipcRenderer.invoke("lucode-desktop:choose-plugin-source", kind);
  },
  chooseAttachmentFiles(): Promise<string[]> {
    return ipcRenderer.invoke("lucode-desktop:choose-attachments");
  },
  droppedFilePath(file: File): string {
    return resolveDroppedFilePath(file, (item) => webUtils.getPathForFile(item));
  },
});

contextBridge.exposeInMainWorld("lucodeTerminal", {
  create(options: { cols?: number; rows?: number; cwd?: string }) {
    return ipcRenderer.invoke("lucode-terminal:create", options || {});
  },
  write(sessionId: string, data: string) {
    ipcRenderer.send("lucode-terminal:write", { sessionId, data });
  },
  resize(sessionId: string, cols: number, rows: number) {
    ipcRenderer.send("lucode-terminal:resize", { sessionId, cols, rows });
  },
  kill(sessionId: string) {
    ipcRenderer.send("lucode-terminal:kill", { sessionId });
  },
  onData(handler: (payload: { sessionId: string; data: string }) => void) {
    const listener = (_event: Electron.IpcRendererEvent, payload: { sessionId: string; data: string }) => handler(payload);
    ipcRenderer.on("lucode-terminal:data", listener);
    return () => ipcRenderer.off("lucode-terminal:data", listener);
  },
  onExit(handler: (payload: { sessionId: string; exitCode: number; signal?: number | string }) => void) {
    const listener = (
      _event: Electron.IpcRendererEvent,
      payload: { sessionId: string; exitCode: number; signal?: number | string },
    ) => handler(payload);
    ipcRenderer.on("lucode-terminal:exit", listener);
    return () => ipcRenderer.off("lucode-terminal:exit", listener);
  },
});

contextBridge.exposeInMainWorld("lucodeBrowser", {
  listTabs() {
    return ipcRenderer.invoke("lucode-browser:list");
  },
  createTab() {
    return ipcRenderer.invoke("lucode-browser:create-tab");
  },
  activateTab(tabId: string) {
    return ipcRenderer.invoke("lucode-browser:activate-tab", { tabId });
  },
  closeTab(tabId: string) {
    return ipcRenderer.invoke("lucode-browser:close-tab", { tabId });
  },
  navigate(tabId: string, url: string) {
    return ipcRenderer.invoke("lucode-browser:navigate", { sessionId: tabId, url });
  },
  goBack(tabId: string) {
    return ipcRenderer.invoke("lucode-browser:go-back", { sessionId: tabId });
  },
  goForward(tabId: string) {
    return ipcRenderer.invoke("lucode-browser:go-forward", { sessionId: tabId });
  },
  reload(tabId: string) {
    return ipcRenderer.invoke("lucode-browser:reload", { sessionId: tabId });
  },
  getState(tabId?: string) {
    return ipcRenderer.invoke("lucode-browser:get-state", { sessionId: tabId || "" });
  },
  getPageSummary(tabId?: string, options?: { maxTextLength?: number; maxElements?: number }) {
    return ipcRenderer.invoke("lucode-browser:page-summary", {
      tabId: tabId || "",
      maxTextLength: options?.maxTextLength,
      maxElements: options?.maxElements,
    });
  },
  clickElement(tabId: string, selector: string) {
    return ipcRenderer.invoke("lucode-browser:click-element", { tabId, selector });
  },
  setInputValue(tabId: string, selector: string, value: string) {
    return ipcRenderer.invoke("lucode-browser:set-input-value", { tabId, selector, value });
  },
  submitForm(tabId: string, selector: string) {
    return ipcRenderer.invoke("lucode-browser:submit-form", { tabId, selector });
  },
  setAutomationPermission(tabId: string, enabled: boolean) {
    return ipcRenderer.invoke("lucode-browser:set-automation-permission", { tabId, enabled });
  },
  getDiagnostics() {
    return ipcRenderer.invoke("lucode-browser:diagnostics");
  },
  setBounds(bounds: { x: number; y: number; width: number; height: number }) {
    ipcRenderer.send("lucode-browser:set-bounds", { bounds });
  },
  hide() {
    ipcRenderer.send("lucode-browser:hide", {});
  },
  focusActive() {
    ipcRenderer.send("lucode-browser:focus-active", {});
  },
  onState(handler: (payload: {
    activeTabId: string;
    tabs: Array<{
      tabId: string;
      sessionId: string;
      url: string;
      title: string;
      canGoBack: boolean;
      canGoForward: boolean;
      loading: boolean;
      visible: boolean;
      lastError: string;
    }>;
  }) => void) {
    const listener = (
      _event: Electron.IpcRendererEvent,
      payload: {
        activeTabId: string;
        tabs: Array<{
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
        }>;
      },
    ) => handler(payload);
    ipcRenderer.on("lucode-browser:state", listener);
    return () => ipcRenderer.off("lucode-browser:state", listener);
  },
});
