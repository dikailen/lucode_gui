export type TerminalTabStatus = "starting" | "running" | "exited" | "failed";

export type TerminalTab = {
  localId: string;
  sessionId: string;
  title: string;
  cwd: string;
  mode: "pty" | "pipe" | "";
  status: TerminalTabStatus;
  error: string;
};

export type TerminalTabsState = {
  tabs: TerminalTab[];
  activeTabId: string;
  nextOrdinal: number;
};

export type TerminalTabIdFactory = () => string;

export function createTerminalTabsState(cwd: string, idFactory: TerminalTabIdFactory = defaultTerminalTabId): TerminalTabsState {
  const tab = createTerminalTab(1, cwd, idFactory);
  return {
    tabs: [tab],
    activeTabId: tab.localId,
    nextOrdinal: 2,
  };
}

export function addTerminalTab(
  state: TerminalTabsState,
  cwd: string,
  idFactory: TerminalTabIdFactory = defaultTerminalTabId,
): TerminalTabsState {
  const tab = createTerminalTab(state.nextOrdinal, cwd, idFactory);
  return {
    tabs: [...state.tabs, tab],
    activeTabId: tab.localId,
    nextOrdinal: state.nextOrdinal + 1,
  };
}

export function activateTerminalTab(state: TerminalTabsState, localId: string): TerminalTabsState {
  if (!state.tabs.some((tab) => tab.localId === localId)) {
    return state;
  }
  return {
    ...state,
    activeTabId: localId,
  };
}

export function updateTerminalTab(
  state: TerminalTabsState,
  localId: string,
  patch: Partial<Omit<TerminalTab, "localId">>,
): TerminalTabsState {
  return {
    ...state,
    tabs: state.tabs.map((tab) => (tab.localId === localId ? { ...tab, ...patch } : tab)),
  };
}

export function closeTerminalTab(state: TerminalTabsState, localId: string): TerminalTabsState {
  const closingIndex = state.tabs.findIndex((tab) => tab.localId === localId);
  if (closingIndex < 0) {
    return state;
  }
  const tabs = state.tabs.filter((tab) => tab.localId !== localId);
  if (tabs.length === 0) {
    return {
      tabs: [],
      activeTabId: "",
      nextOrdinal: state.nextOrdinal,
    };
  }
  if (state.activeTabId !== localId) {
    return {
      ...state,
      tabs,
    };
  }
  const nextActiveIndex = Math.min(closingIndex, tabs.length - 1);
  return {
    tabs,
    activeTabId: tabs[nextActiveIndex].localId,
    nextOrdinal: state.nextOrdinal,
  };
}

function createTerminalTab(ordinal: number, cwd: string, idFactory: TerminalTabIdFactory): TerminalTab {
  return {
    localId: idFactory(),
    sessionId: "",
    title: `Terminal ${ordinal}`,
    cwd,
    mode: "",
    status: "starting",
    error: "",
  };
}

function defaultTerminalTabId(): string {
  return `terminal_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
