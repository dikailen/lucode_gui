import { useEffect, useRef, useState, type ReactNode } from "react";

import type { TranslationKey, Translator } from "../i18n";
import type { DockToolId, RightDockWindow, RightDockWindowTool } from "../useLucodeApp";

export type RightDockProps = {
  t: Translator;
  activeTool: DockToolId;
  windows: RightDockWindow[];
  activateTool: (tool: RightDockWindowTool) => void;
  collapseDock: () => void;
  closeWindow: (windowId: string) => void;
  startResize: (clientX: number) => void;
  resetWidth: () => void;
  children?: ReactNode;
};

type DockTool = Exclude<DockToolId, "" | "home">;

const DOCK_TOOLS: Array<{
  id: DockTool;
  labelKey: TranslationKey;
  placeholderKey: TranslationKey;
  shortcutKey: TranslationKey;
  icon: string;
}> = [
  {
    id: "review",
    labelKey: "rightDock.review",
    placeholderKey: "rightDock.reviewPlaceholder",
    shortcutKey: "rightDock.shortcutReview",
    icon: "review",
  },
  {
    id: "terminal",
    labelKey: "rightDock.terminal",
    placeholderKey: "rightDock.terminalPlaceholder",
    shortcutKey: "rightDock.shortcutTerminal",
    icon: "terminal",
  },
  {
    id: "browser",
    labelKey: "rightDock.browser",
    placeholderKey: "rightDock.browserPlaceholder",
    shortcutKey: "rightDock.shortcutBrowser",
    icon: "browser",
  },
  {
    id: "files",
    labelKey: "rightDock.files",
    placeholderKey: "rightDock.filesPlaceholder",
    shortcutKey: "rightDock.shortcutFiles",
    icon: "files",
  },
];

export function RightDock({
  t,
  activeTool,
  windows,
  activateTool,
  collapseDock,
  closeWindow,
  startResize,
  resetWidth,
  children,
}: RightDockProps) {
  if (!activeTool) {
    return null;
  }

  return (
    <aside className="right-dock" aria-label={t("rightDock.aria")}>
      <div
        className="right-dock-resize-handle"
        role="separator"
        aria-orientation="vertical"
        title={t("rightDock.resize")}
        onPointerDown={(event) => {
          event.preventDefault();
          startResize(event.clientX);
        }}
        onDoubleClick={resetWidth}
      />
      {activeTool === "home" ? (
        <RightDockHeader t={t} collapseDock={collapseDock} />
      ) : (
        <RightDockWindowChrome
          t={t}
          activeTool={activeTool}
          windows={windows}
          activateTool={activateTool}
          collapseDock={collapseDock}
          closeWindow={closeWindow}
        />
      )}
      <section className="right-dock-body">
        {activeTool === "home" ? (
          <RightDockHome t={t} activateTool={activateTool} />
        ) : (
          children ?? <PlaceholderPanel t={t} tool={activeTool} />
        )}
      </section>
    </aside>
  );
}

function RightDockWindowChrome({
  t,
  activeTool,
  windows,
  activateTool,
  collapseDock,
  closeWindow,
}: {
  t: Translator;
  activeTool: Exclude<DockToolId, "" | "home">;
  windows: RightDockWindow[];
  activateTool: (tool: RightDockWindowTool) => void;
  collapseDock: () => void;
  closeWindow: (windowId: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const openWindowTools = new Set(windows.map((window) => window.tool));

  useEffect(() => {
    if (!menuOpen) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && menuRef.current?.contains(target)) {
        return;
      }
      setMenuOpen(false);
    }
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [menuOpen]);

  function chooseTool(tool: DockTool) {
    activateTool(tool);
    setMenuOpen(false);
  }

  return (
    <header className="right-dock-window-chrome">
      <div className="right-dock-tabs" role="tablist" aria-label={t("rightDock.aria")}>
        {windows.map((window) => (
          <div
            className={[
              "right-dock-tab",
              window.tool === activeTool ? "active" : "",
              window.status !== "idle" ? window.status : "",
            ]
              .filter(Boolean)
              .join(" ")}
            role="presentation"
            key={window.id}
          >
            <button
              className="right-dock-tab-main"
              type="button"
              role="tab"
              aria-selected={window.tool === activeTool}
              title={toolLabel(t, window.tool)}
              onClick={() => activateTool(window.tool)}
            >
              <span className="right-dock-tab-status" aria-hidden="true" />
              <span className="right-dock-tab-label">{toolLabel(t, window.tool)}</span>
            </button>
            <button
              className="right-dock-tab-close"
              type="button"
              title={t("common.close")}
              aria-label={t("common.close")}
              onClick={() => closeWindow(window.id)}
            >
              <span className="dock-chrome-icon dock-chrome-close" aria-hidden="true" />
            </button>
          </div>
        ))}
        <div className="right-dock-add-menu-host" ref={menuRef}>
          <button
            className={menuOpen ? "right-dock-tab-add active" : "right-dock-tab-add"}
            type="button"
            title={t("rightDock.title")}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >
            +
          </button>
          {menuOpen ? (
            <div className="right-dock-add-menu" role="menu">
              {DOCK_TOOLS.map((tool) => (
                <button
                  className="right-dock-add-menu-item"
                  type="button"
                  role="menuitem"
                  onClick={() => chooseTool(tool.id)}
                  key={tool.id}
                >
                  <span className={`dock-command-icon ${tool.icon}`} aria-hidden="true" />
                  <span className="right-dock-add-menu-label">{t(tool.labelKey)}</span>
                  <span className="right-dock-add-menu-meta">
                    {openWindowTools.has(tool.id) ? "已打开" : t(tool.shortcutKey)}
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      <div className="right-dock-actions">
        <button className="dock-chrome-button" type="button" title={t("rightDock.collapse")} onClick={collapseDock}>
          <span className="dock-chrome-icon dock-chrome-minimize" aria-hidden="true" />
        </button>
        <button className="dock-chrome-button" type="button" title={t("common.close")} onClick={() => closeWindow(activeTool)}>
          <span className="dock-chrome-icon dock-chrome-close" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}

function RightDockHeader({
  t,
  collapseDock,
}: {
  t: Translator;
  collapseDock: () => void;
}) {
  return (
    <header className="right-dock-header compact">
      <div>
        <div className="right-dock-title">{t("rightDock.title")}</div>
        <div className="right-dock-subtitle">{t("rightDock.homeSubtitle")}</div>
      </div>
      <div className="right-dock-actions">
        <button
          className="dock-chrome-button"
          type="button"
          title={t("rightDock.collapse")}
          onClick={collapseDock}
        >
          <span className="dock-chrome-icon dock-chrome-minimize" aria-hidden="true" />
        </button>
        <button className="dock-chrome-button" type="button" title={t("common.close")} onClick={collapseDock}>
          <span className="dock-chrome-icon dock-chrome-close" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}

function RightDockHome({ t, activateTool }: { t: Translator; activateTool: (tool: RightDockWindowTool) => void }) {
  return (
    <div className="right-dock-home">
      <div className="right-dock-command-list">
        {DOCK_TOOLS.map((tool) => (
          <button className="dock-command-row" type="button" onClick={() => activateTool(tool.id)} key={tool.id}>
            <span className={`dock-command-icon ${tool.icon}`} aria-hidden="true" />
            <span className="dock-command-label">{t(tool.labelKey)}</span>
            <span className="dock-command-shortcut">{t(tool.shortcutKey)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function PlaceholderPanel({ t, tool }: { t: Translator; tool: DockTool }) {
  return (
    <div className="dock-placeholder">
      <div className={`dock-placeholder-icon ${tool}`} aria-hidden="true" />
      <div className="dock-placeholder-title">{toolLabel(t, tool)}</div>
      <p>{t(toolDescriptor(tool).placeholderKey)}</p>
    </div>
  );
}

function toolLabel(t: Translator, tool: Exclude<DockToolId, "home" | "">): string {
  return t(toolDescriptor(tool).labelKey);
}

function toolDescriptor(tool: DockTool) {
  return DOCK_TOOLS.find((item) => item.id === tool) ?? DOCK_TOOLS[0];
}
