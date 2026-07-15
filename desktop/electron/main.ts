import { app, BrowserWindow, Menu, dialog, ipcMain, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { DesktopBrowserBridge } from "./browserBridge.js";
import { DesktopBrowserManager } from "./browserManager.js";
import { RuntimeLauncher } from "./runtimeLauncher.js";
import { resolveWorkspaceRoot } from "./runtimeLauncher.js";
import { DesktopTerminalManager } from "./terminalManager.js";
import { chooseAttachmentFiles, choosePluginSource } from "./filePicker.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const runtimeLauncher = new RuntimeLauncher();
const terminalManager = new DesktopTerminalManager({
  workspaceRoot: resolveWorkspaceRoot(process.env, process.cwd()),
});
const browserManager = new DesktopBrowserManager();
const browserBridge = new DesktopBrowserBridge(browserManager);
terminalManager.registerIpc();
browserManager.registerIpc();
ipcMain.handle("lucode-desktop:choose-plugin-source", (_event, kind) =>
  choosePluginSource((options) => dialog.showOpenDialog(options), kind === "mcp" || kind === "package" ? kind : "skill"),
);
ipcMain.handle("lucode-desktop:choose-attachments", () =>
  chooseAttachmentFiles((options) => dialog.showOpenDialog(options)),
);

async function createWindow() {
  const bridge = await browserBridge.start();
  process.env.LUCODE_DESKTOP_BROWSER_BRIDGE_URL = bridge.baseUrl;
  process.env.LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN = bridge.token;
  const runtime = await runtimeLauncher.ensureRuntime();
  const devUrl = process.env.VITE_DEV_SERVER_URL || process.env.LUCODE_RENDERER_DEV_URL;
  const rendererSource = devUrl ? "dev" : "dist";
  const buildTime = new Date().toISOString();
  console.log(
    `Lucode desktop renderer=${rendererSource} runtime=${runtime.baseUrl} ownedRuntime=${runtime.owned}`,
  );
  const preload = path.join(__dirname, "preload.js");
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: "Lucode",
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload,
      additionalArguments: [
        `--lucode-runtime-base-url=${runtime.baseUrl}`,
        `--lucode-runtime-token=${runtime.token}`,
        `--lucode-renderer-source=${rendererSource}`,
        `--lucode-build-time=${buildTime}`,
      ],
    },
  });
  win.setMenuBarVisibility(false);
  const terminalWebContentsId = win.webContents.id;
  win.on("closed", () => {
    terminalManager.disposeForWebContentsId(terminalWebContentsId);
    browserManager.disposeForWebContentsId(terminalWebContentsId);
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  if (devUrl) {
    await win.loadURL(devUrl);
  } else {
    await win.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  await verifySmokeRendererIfNeeded(win);
  scheduleSmokeExitIfNeeded();
}

async function verifySmokeRendererIfNeeded(win: BrowserWindow): Promise<void> {
  if (process.env.LUCODE_ELECTRON_SMOKE !== "1") {
    return;
  }
  const rendered = await waitForRendererRoot(win, 3000);
  if (!rendered) {
    console.error("Electron smoke failed: renderer root stayed empty.");
    app.exit(1);
  }
  const preloadReady = await waitForPreloadBridge(win, 3000);
  if (!preloadReady) {
    console.error("Electron smoke failed: preload bridges were not exposed.");
    app.exit(1);
  }
}

async function waitForRendererRoot(win: BrowserWindow, timeoutMs: number): Promise<boolean> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const rendered = await win.webContents
      .executeJavaScript("Boolean(document.querySelector('#root')?.childElementCount)", true)
      .catch(() => false);
    if (rendered) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

async function waitForPreloadBridge(win: BrowserWindow, timeoutMs: number): Promise<boolean> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const ready = await win.webContents
      .executeJavaScript(
        "Boolean(window.lucodeRuntime?.baseUrl && window.lucodeDesktop?.chooseAttachmentFiles && window.lucodeDesktop?.droppedFilePath && window.lucodeTerminal?.create && window.lucodeBrowser?.createTab && window.lucodeBrowser?.listTabs && window.lucodeBrowser?.getPageSummary)",
        true,
      )
      .catch(() => false);
    if (ready) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

function scheduleSmokeExitIfNeeded(): void {
  if (process.env.LUCODE_ELECTRON_SMOKE !== "1") {
    return;
  }
  const delayMs = Number.parseInt(process.env.LUCODE_ELECTRON_SMOKE_EXIT_MS || "1200", 10);
  setTimeout(() => {
    app.quit();
  }, Number.isFinite(delayMs) ? delayMs : 1200);
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  void createWindow().catch(handleStartupError);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createWindow().catch(handleStartupError);
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  browserManager.dispose();
  terminalManager.dispose();
  runtimeLauncher.stopRuntime();
  void browserBridge.dispose();
});

function handleStartupError(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Lucode desktop failed to start: ${message}`);
  dialog.showErrorBox(
    "Lucode Runtime 启动失败",
    `无法启动或连接 Lucode Runtime Server。\n\n${message}\n\n请使用 run_desktop.bat 重新启动，或确认本机没有拦截 Python/Runtime 进程。`,
  );
  app.exit(1);
}
