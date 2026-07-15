export type DockToolId = "" | "home" | "review" | "terminal" | "browser" | "files";
export type RightDockWindowTool = Exclude<DockToolId, "" | "home">;
export type RightDockWindowStatus = "idle" | "running" | "attention";

export type RightDockWindow = {
  id: RightDockWindowTool;
  tool: RightDockWindowTool;
  title: string;
  status: RightDockWindowStatus;
};

export type RightDockState = {
  open: boolean;
  mode: "home" | "window";
  activeWindowId: string;
  windows: RightDockWindow[];
};

export type PanelLayoutState = {
  rightDock: RightDockState;
  bottomShellOpen: boolean;
};

export type OpenRightDockWindowOptions = {
  focus?: boolean;
  status?: RightDockWindowStatus;
};

export const DEFAULT_RIGHT_DOCK_WIDTH = 640;
export const EXPANDED_SIDEBAR_WIDTH = 294;
export const COLLAPSED_SIDEBAR_WIDTH = 64;

const REGULAR_MIN_DOCK_WIDTH = 380;
const COMPACT_MIN_DOCK_WIDTH = 320;
const REGULAR_MIN_WORKSPACE_WIDTH = 420;
const COMPACT_MIN_WORKSPACE_WIDTH = 360;
const FORCE_COMPACT_WIDTH = 960;
const FORCE_COMPACT_WITH_DOCK_WIDTH = 1120;

export function createPanelLayoutState(): PanelLayoutState {
  return {
    rightDock: createClosedRightDockState(),
    bottomShellOpen: false,
  };
}

export function toggleRightDockTool(state: PanelLayoutState, tool: DockToolId): PanelLayoutState {
  if (!tool) {
    return closeRightDock(state);
  }
  if (tool === "home") {
    return openRightDockHome(state);
  }
  return openRightDockWindow(state, tool);
}

export function toggleRightDockHome(state: PanelLayoutState): PanelLayoutState {
  return state.rightDock.open && state.rightDock.mode === "home" ? closeRightDock(state) : openRightDockHome(state);
}

export function openRightDockHome(state: PanelLayoutState): PanelLayoutState {
  return {
    ...state,
    rightDock: {
      ...state.rightDock,
      open: true,
      mode: "home",
      activeWindowId: "",
    },
  };
}

export function openRightDockWindow(
  state: PanelLayoutState,
  tool: RightDockWindowTool,
  options: OpenRightDockWindowOptions = {},
): PanelLayoutState {
  const focus = options.focus ?? true;
  const windows = ensureWindow(state.rightDock.windows, tool, options.status);
  const activeWindowId = focus
    ? tool
    : state.rightDock.activeWindowId || windows.find((window) => window.tool !== tool)?.id || tool;
  return {
    ...state,
    rightDock: {
      open: true,
      mode: "window",
      activeWindowId,
      windows,
    },
  };
}

export function closeRightDockWindow(state: PanelLayoutState, windowId: string): PanelLayoutState {
  const windows = state.rightDock.windows.filter((window) => window.id !== windowId);
  if (!windows.length) {
    return {
      ...state,
      rightDock: {
        ...state.rightDock,
        open: true,
        mode: "home",
        activeWindowId: "",
        windows: [],
      },
    };
  }
  const activeWindowId =
    state.rightDock.activeWindowId === windowId ? windows[Math.max(0, windows.length - 1)].id : state.rightDock.activeWindowId;
  return {
    ...state,
    rightDock: {
      ...state.rightDock,
      open: true,
      mode: "window",
      activeWindowId,
      windows,
    },
  };
}

export function closeRightDock(state: PanelLayoutState): PanelLayoutState {
  return {
    ...state,
    rightDock: {
      ...state.rightDock,
      open: false,
      mode: "home",
      activeWindowId: "",
    },
  };
}

export function setRightDockWindowStatus(
  state: PanelLayoutState,
  tool: RightDockWindowTool,
  status: RightDockWindowStatus,
): PanelLayoutState {
  if (status === "idle" && !state.rightDock.windows.some((window) => window.tool === tool)) {
    return state;
  }
  const windows = ensureWindow(state.rightDock.windows, tool, status).map((window) =>
    window.tool === tool ? { ...window, status } : window,
  );
  return {
    ...state,
    rightDock: {
      ...state.rightDock,
      open: state.rightDock.open || status !== "idle",
      mode: state.rightDock.open ? state.rightDock.mode : "window",
      activeWindowId: state.rightDock.activeWindowId || tool,
      windows,
    },
  };
}

export function activeRightDockTool(state: PanelLayoutState): DockToolId {
  if (!state.rightDock.open) {
    return "";
  }
  if (state.rightDock.mode === "home") {
    return "home";
  }
  return state.rightDock.windows.find((window) => window.id === state.rightDock.activeWindowId)?.tool || "home";
}

export function rightDockWindows(state: PanelLayoutState): RightDockWindow[] {
  return state.rightDock.windows;
}

export function toggleBottomShell(state: PanelLayoutState): PanelLayoutState {
  return {
    ...state,
    bottomShellOpen: !state.bottomShellOpen,
  };
}

export function closeBottomShell(state: PanelLayoutState): PanelLayoutState {
  return {
    ...state,
    bottomShellOpen: false,
  };
}

export function shouldForceCompactSidebar(viewportWidth: number, rightDockOpen: boolean): boolean {
  const width = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 1280;
  return width < FORCE_COMPACT_WIDTH || (rightDockOpen && width < FORCE_COMPACT_WITH_DOCK_WIDTH);
}

export function shouldRenderSessionSidebar(workspace: "chat" | "plugins" | "settings"): boolean {
  void workspace;
  return true;
}

export function clampRightDockWidth(width: number, sidebarCollapsed: boolean, viewportWidth: number): number {
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 1280;
  const sidebarWidth = sidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : EXPANDED_SIDEBAR_WIDTH;
  const compact = sidebarCollapsed || viewport < FORCE_COMPACT_WIDTH;
  const minDockWidth = compact ? COMPACT_MIN_DOCK_WIDTH : REGULAR_MIN_DOCK_WIDTH;
  const minWorkspaceWidth = compact ? COMPACT_MIN_WORKSPACE_WIDTH : REGULAR_MIN_WORKSPACE_WIDTH;
  const maxWidth = Math.max(0, viewport - sidebarWidth - minWorkspaceWidth);
  const minWidth = Math.min(minDockWidth, maxWidth);
  const targetWidth = Math.round(width || DEFAULT_RIGHT_DOCK_WIDTH);

  return Math.max(minWidth, Math.min(targetWidth, maxWidth));
}

function createClosedRightDockState(): RightDockState {
  return {
    open: false,
    mode: "home",
    activeWindowId: "",
    windows: [],
  };
}

function ensureWindow(
  windows: RightDockWindow[],
  tool: RightDockWindowTool,
  status?: RightDockWindowStatus,
): RightDockWindow[] {
  if (windows.some((window) => window.tool === tool)) {
    return windows.map((window) => (window.tool === tool ? { ...window, status: status ?? window.status } : window));
  }
  return [...windows, { id: tool, tool, title: tool, status: status ?? "idle" }];
}
