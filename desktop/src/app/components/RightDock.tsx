import type { ReactNode } from "react";

import type { Translator } from "../i18n";
import type { DockToolId } from "../useLucodeApp";

export type RightDockProps = {
  t: Translator;
  activeTool: DockToolId;
  openDock: (tool: DockToolId) => void;
  children?: ReactNode;
};

const TOOL_KEYS: Record<Exclude<DockToolId, "">, "rightDock.terminal" | "rightDock.browser" | "rightDock.files"> = {
  terminal: "rightDock.terminal",
  browser: "rightDock.browser",
  files: "rightDock.files",
};

export function RightDock({ t, activeTool, openDock, children }: RightDockProps) {
  if (!activeTool) {
    return null;
  }

  return (
    <aside className="right-dock" aria-label={t("rightDock.aria")}>
      <header className="right-dock-header">
        <div>
          <div className="right-dock-title">{toolLabel(t, activeTool)}</div>
          <div className="right-dock-subtitle">{t("rightDock.subtitle")}</div>
        </div>
        <div className="right-dock-actions">
          <button className="square-icon-button compact" type="button" title={t("common.close")} onClick={() => openDock(activeTool)}>
            x
          </button>
        </div>
      </header>
      <nav className="right-dock-tabs" aria-label={t("rightDock.tabsAria")}>
        {(Object.keys(TOOL_KEYS) as Exclude<DockToolId, "">[]).map((tool) => (
          <button
            key={tool}
            className={activeTool === tool ? "right-dock-tab active" : "right-dock-tab"}
            type="button"
            onClick={() => openDock(tool)}
          >
            {toolLabel(t, tool)}
          </button>
        ))}
      </nav>
      <section className="right-dock-body">{children ?? <PlaceholderPanel t={t} tool={activeTool} />}</section>
    </aside>
  );
}

function PlaceholderPanel({ t, tool }: { t: Translator; tool: Exclude<DockToolId, ""> }) {
  const text =
    tool === "terminal"
      ? t("rightDock.terminalPlaceholder")
      : tool === "browser"
        ? t("rightDock.browserPlaceholder")
        : t("rightDock.filesPlaceholder");

  return (
    <div className="dock-placeholder">
      <div className="dock-placeholder-icon">{tool === "terminal" ? ">" : tool === "browser" ? "o" : "#"}</div>
      <div className="dock-placeholder-title">{toolLabel(t, tool)}</div>
      <p>{text}</p>
    </div>
  );
}

function toolLabel(t: Translator, tool: Exclude<DockToolId, "">): string {
  return t(TOOL_KEYS[tool]);
}
