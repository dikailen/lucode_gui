import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { FormEvent, KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import "@xterm/xterm/css/xterm.css";

import type { Translator } from "../i18n";
import type {
  DesktopTerminalBridge,
  DesktopTerminalCreateResult,
  TerminalStateResponse,
  TerminalTranscriptEntry,
} from "../../shared/types";
import {
  activateTerminalTab,
  addTerminalTab,
  closeTerminalTab,
  createTerminalTabsState,
  updateTerminalTab,
  type TerminalTab,
  type TerminalTabsState,
} from "./terminalTabs";

export type TerminalPanelProps = {
  t: Translator;
  terminalState: TerminalStateResponse | null;
  terminalError: string;
  terminalCommand: string;
  setTerminalCommand: (value: string) => void;
  runTerminalCommand: () => void;
  clearTerminal: () => void;
  rerunTerminalCommand: () => void;
  setTerminalCwd: (cwd: string) => void;
  closeTerminal: () => void;
};

type TerminalController = {
  refresh: () => void;
};

export function TerminalPanel(props: TerminalPanelProps) {
  const bridge = typeof window === "undefined" ? undefined : window.lucodeTerminal;

  if (!bridge) {
    return <ControlledShellPanel {...props} bridgeNotice={props.t("terminal.fallbackNotice")} />;
  }

  return <TerminalWorkspace {...props} bridge={bridge} />;
}

function TerminalWorkspace(props: TerminalPanelProps & { bridge: DesktopTerminalBridge }) {
  const initialCwd = props.terminalState?.cwd || props.terminalState?.workspace_root || "";
  const [state, setState] = useState<TerminalTabsState>(() => createTerminalTabsState(initialCwd));
  const controllersRef = useRef(new Map<string, TerminalController>());
  const activeTab = state.tabs.find((tab) => tab.localId === state.activeTabId) ?? state.tabs[0] ?? null;

  useEffect(() => {
    const controller = controllersRef.current.get(state.activeTabId);
    if (!controller) {
      return;
    }
    const timer = window.setTimeout(() => controller.refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [state.activeTabId]);

  function registerController(localId: string, controller: TerminalController | null) {
    if (controller) {
      controllersRef.current.set(localId, controller);
      return;
    }
    controllersRef.current.delete(localId);
  }

  function handleAddTab() {
    setState((current) => addTerminalTab(current, activeTab?.cwd || initialCwd));
  }

  function handleCloseTab(localId: string) {
    if (state.tabs.length <= 1) {
      props.closeTerminal();
      return;
    }
    setState((current) => closeTerminalTab(current, localId));
  }

  function handleUpdateTab(localId: string, patch: Partial<Omit<TerminalTab, "localId">>) {
    setState((current) => updateTerminalTab(current, localId, patch));
  }

  return (
    <TerminalChrome
      t={props.t}
      tabs={state.tabs}
      activeTabId={state.activeTabId}
      activeTab={activeTab}
      onAddTab={handleAddTab}
      onActivateTab={(localId) => setState((current) => activateTerminalTab(current, localId))}
      onCloseTab={handleCloseTab}
    >
      <div className="terminal-session-stack">
        {state.tabs.map((tab) => (
          <TerminalSessionView
            key={tab.localId}
            t={props.t}
            bridge={props.bridge}
            tab={tab}
            active={tab.localId === state.activeTabId}
            onUpdateTab={handleUpdateTab}
            onRegisterController={registerController}
          />
        ))}
      </div>
    </TerminalChrome>
  );
}

function TerminalSessionView({
  t,
  bridge,
  tab,
  active,
  onUpdateTab,
  onRegisterController,
}: {
  t: Translator;
  bridge: DesktopTerminalBridge;
  tab: TerminalTab;
  active: boolean;
  onUpdateTab: (localId: string, patch: Partial<Omit<TerminalTab, "localId">>) => void;
  onRegisterController: (localId: string, controller: TerminalController | null) => void;
}) {
  const xtermHostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const sessionIdRef = useRef("");

  useEffect(() => {
    if (!xtermHostRef.current) {
      return;
    }

    let disposed = false;
    const terminal = new Terminal({
      allowProposedApi: false,
      convertEol: true,
      cursorBlink: true,
      fontFamily: 'Consolas, "SFMono-Regular", Menlo, monospace',
      fontSize: 13,
      lineHeight: 1.18,
      scrollback: 5000,
      theme: {
        background: "#ffffff",
        foreground: "#0b0f19",
        cursor: "#0b0f19",
        selectionBackground: "#dbeafe",
        black: "#0b0f19",
        blue: "#175cd3",
        cyan: "#0e7490",
        green: "#15803d",
        magenta: "#7e22ce",
        red: "#b42318",
        white: "#f8fafc",
        yellow: "#a16207",
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(xtermHostRef.current);
    terminalRef.current = terminal;
    fitRef.current = fit;

    const refresh = () => {
      fit.fit();
      terminal.focus();
      if (sessionIdRef.current) {
        bridge.resize(sessionIdRef.current, terminal.cols, terminal.rows);
      }
    };
    onRegisterController(tab.localId, {
      refresh,
    });

    const dataDisposable = terminal.onData((data) => {
      if (sessionIdRef.current) {
        bridge.write(sessionIdRef.current, data);
      }
    });
    const offData = bridge.onData((payload) => {
      if (payload.sessionId === sessionIdRef.current) {
        terminal.write(payload.data);
      }
    });
    const offExit = bridge.onExit((payload) => {
      if (payload.sessionId !== sessionIdRef.current) {
        return;
      }
      terminal.writeln("");
      terminal.writeln(`[process exited with code ${payload.exitCode}]`);
      onUpdateTab(tab.localId, { status: "exited" });
    });
    const resizeObserver = new ResizeObserver(() => {
      if (active) {
        refresh();
      }
    });
    resizeObserver.observe(xtermHostRef.current);

    bridge
      .create({
        cols: terminal.cols,
        rows: terminal.rows,
        cwd: tab.cwd,
      })
      .then((created: DesktopTerminalCreateResult) => {
        if (disposed) {
          bridge.kill(created.sessionId);
          return;
        }
        sessionIdRef.current = created.sessionId;
        onUpdateTab(tab.localId, {
          sessionId: created.sessionId,
          cwd: created.cwd,
          title: compactPath(created.cwd),
          mode: created.mode,
          status: "running",
          error: "",
        });
        refresh();
      })
      .catch((error) => {
        onUpdateTab(tab.localId, {
          status: "failed",
          error: `${t("terminal.fallbackNotice")} ${errorMessage(error)}`,
        });
      });

    return () => {
      disposed = true;
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = "";
      resizeObserver.disconnect();
      offData();
      offExit();
      dataDisposable.dispose();
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
      onRegisterController(tab.localId, null);
      if (sessionId) {
        bridge.kill(sessionId);
      }
    };
  }, []);

  useEffect(() => {
    if (!active || !terminalRef.current || !fitRef.current) {
      return;
    }
    const timer = window.setTimeout(() => {
      fitRef.current?.fit();
      terminalRef.current?.focus();
      if (sessionIdRef.current && terminalRef.current) {
        bridge.resize(sessionIdRef.current, terminalRef.current.cols, terminalRef.current.rows);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [active]);

  return (
    <div className={active ? "terminal-session active" : "terminal-session"} aria-hidden={!active}>
      {tab.error ? <div className="terminal-session-error">{tab.error}</div> : null}
      <div className="terminal-xterm-host" ref={xtermHostRef} />
    </div>
  );
}

function TerminalChrome({
  t,
  tabs,
  activeTabId,
  activeTab,
  onAddTab,
  onActivateTab,
  onCloseTab,
  children,
}: {
  t: Translator;
  tabs: TerminalTab[];
  activeTabId: string;
  activeTab: TerminalTab | null;
  onAddTab: () => void;
  onActivateTab: (localId: string) => void;
  onCloseTab: (localId: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="terminal-shell-panel">
      <header className="terminal-shell-chrome">
        <div className="terminal-tab-strip" role="tablist" aria-label={t("terminal.tabsAria")}>
          {tabs.map((tab) => (
            <div className={tab.localId === activeTabId ? "terminal-tab active" : "terminal-tab"} key={tab.localId}>
              <button
                className="terminal-tab-main"
                type="button"
                role="tab"
                aria-selected={tab.localId === activeTabId}
                title={tab.cwd || tab.title}
                onClick={() => onActivateTab(tab.localId)}
              >
                <span className="terminal-tab-icon">&gt;</span>
                <span className="terminal-tab-label">{tab.title}</span>
              </button>
              <button
                className="terminal-tab-close"
                type="button"
                title={t("terminal.closeTab")}
                aria-label={t("terminal.closeTab")}
                onClick={() => onCloseTab(tab.localId)}
              >
                x
              </button>
            </div>
          ))}
          <button className="terminal-tab-add" type="button" title={t("terminal.newTab")} onClick={onAddTab}>
            +
          </button>
          {activeTab?.mode ? <span className="terminal-mode-pill">{activeTab.mode}</span> : null}
        </div>
      </header>
      {children}
    </div>
  );
}

function ControlledShellPanel(props: TerminalPanelProps & { bridgeNotice: string }) {
  const {
    t,
    terminalState,
    terminalError,
    terminalCommand,
    setTerminalCommand,
    runTerminalCommand,
    clearTerminal,
    rerunTerminalCommand,
    setTerminalCwd,
    bridgeNotice,
  } = props;
  const transcript = useMemo(() => terminalState?.transcript ?? [], [terminalState?.transcript]);
  const running = Boolean(terminalState?.running);
  const cwd = terminalState?.cwd || terminalState?.workspace_root || "";
  const lastCommand = terminalState?.history.at(-1)?.command || "";

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const command = terminalCommand.trim();
    if (!command || running) {
      return;
    }
    const lowered = command.toLowerCase();
    if (lowered === "cls" || lowered === "clear") {
      clearTerminal();
      setTerminalCommand("");
      return;
    }
    if (lowered === "rerun") {
      rerunTerminalCommand();
      setTerminalCommand("");
      return;
    }
    if (lowered === "cd" || lowered.startsWith("cd ")) {
      const target = command.slice(2).trim() || terminalState?.workspace_root || cwd;
      setTerminalCwd(target);
      setTerminalCommand("");
      return;
    }
    runTerminalCommand();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowUp" && lastCommand && !terminalCommand) {
      event.preventDefault();
      setTerminalCommand(lastCommand);
    }
  }

  return (
    <div className="terminal-shell-panel">
      <header className="terminal-shell-chrome">
        <div className="terminal-tab-strip">
          <div className="terminal-tab active" title={cwd}>
            <span className="terminal-tab-icon">&gt;</span>
            <span className="terminal-tab-label">{compactPath(cwd)}</span>
          </div>
          <span className="terminal-mode-pill">safe</span>
        </div>
      </header>
      <main className="terminal-shell-screen" role="log" aria-live="polite">
        {transcript.length ? null : <TerminalBanner t={t} />}
        {bridgeNotice ? (
          <div className="terminal-shell-status">
            <span>{bridgeNotice}</span>
          </div>
        ) : null}
        {transcript.map((entry, index) => (
          <ShellLine key={`${entry.command_id}-${entry.kind}-${index}`} cwd={cwd} entry={entry} />
        ))}
        {terminalError ? (
          <div className="terminal-shell-error">
            <span>!</span>
            <pre>{terminalError}</pre>
          </div>
        ) : null}
        {running ? (
          <div className="terminal-shell-status">
            <span>{t("terminal.running")}</span>
            <span>{terminalState?.running_command}</span>
          </div>
        ) : null}
        <form className="terminal-shell-prompt" onSubmit={handleSubmit}>
          <span className="terminal-prompt-prefix">PS {cwd}&gt;</span>
          <input
            value={terminalCommand}
            disabled={running}
            onChange={(event) => setTerminalCommand(event.target.value)}
            onKeyDown={handleKeyDown}
            autoComplete="off"
            spellCheck={false}
            aria-label={t("terminal.commandPlaceholder")}
          />
        </form>
      </main>
    </div>
  );
}

function TerminalBanner({ t }: { t: Translator }) {
  return (
    <div className="terminal-banner">
      <div>{t("terminal.bannerTitle")}</div>
      <div>{t("terminal.bannerCopyright")}</div>
      <br />
      <div>{t("terminal.bannerHint")}</div>
    </div>
  );
}

function ShellLine({ cwd, entry }: { cwd: string; entry: TerminalTranscriptEntry }) {
  if (entry.kind === "command") {
    return (
      <div className="terminal-shell-line command">
        <span className="terminal-prompt-prefix">PS {cwd}&gt;</span>
        <pre>{entry.text}</pre>
      </div>
    );
  }
  if (entry.kind === "result" && entry.result) {
    return entry.result.status === "success" ? null : (
      <div className="terminal-shell-line result">
        <pre>{`${entry.result.status} (${entry.result.returncode})`}</pre>
      </div>
    );
  }
  return (
    <div className={`terminal-shell-line ${entry.kind}`}>
      <pre>{entry.text}</pre>
    </div>
  );
}

function compactPath(value: string): string {
  const cleanPath = String(value || "").trim();
  if (!cleanPath) {
    return "Terminal";
  }
  if (cleanPath.length <= 28) {
    return cleanPath;
  }
  const parts = cleanPath.split(/[\\/]/).filter(Boolean);
  if (parts.length >= 2) {
    return `...\\${parts.at(-2)}\\${parts.at(-1)}`;
  }
  return `...${cleanPath.slice(-25)}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
