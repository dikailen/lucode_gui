import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function readStyles(): string {
  return readFileSync(resolve(__dirname, "../styles.css"), "utf8");
}

describe("chat presentation styles", () => {
  it("uses document-flow assistant output and lightweight user prompts", () => {
    const css = readStyles();

    expect(css).toContain(".message.assistant {");
    expect(css).toContain("border-color: transparent;");
    expect(css).toContain("background: transparent;");
    expect(css).toContain(".message.user {");
    expect(css).toContain("max-width: min(620px, 64%);");
    expect(css).toContain(".markdown-content");
    expect(css).toContain(".markdown-code");
    expect(css).toContain(".markdown-table-wrap");
  });

  it("centers the runtime toast and limits it to a compact two-row strip", () => {
    const css = readStyles();

    expect(css).toContain(".runtime-toast-layer");
    expect(css).toContain("justify-content: center;");
    expect(css).toContain("pointer-events: none;");
    expect(css).toContain(".runtime-toast-stack");
    expect(css).toContain("width: min(560px, calc(100% - 48px));");
    expect(css).toContain("max-height: 82px;");
    expect(css).toContain(".runtime-toast-more");
    expect(css).toContain("white-space: nowrap;");
    expect(css).toContain("@media (max-width: 1200px)");
    expect(css).toContain(".runtime-toast-detail {");
    expect(css).toContain("display: none;");
    expect(css).toContain("padding-right: 72px;");
    expect(css).toContain("max-width: 64px;");
    expect(css).not.toContain(".runtime-toast-row.level-1");
    expect(css).not.toContain(".runtime-toast-row.level-2");
  });

  it("keeps the B workbench controls compact and exposes settings navigation in the sidebar", () => {
    const css = readStyles();

    expect(css).toContain(".settings-sidebar-tabs {");
    expect(css).toContain(".composer-shell:focus-within {");
    expect(css).toContain("min-height: 112px;");
    expect(css).toContain(".session-row:hover .session-delete");
  });

  it("keeps attachment drafts compact, scrollable, and separate from message text", () => {
    const css = readStyles();

    expect(css).toContain(".composer-attachment-tray {");
    expect(css).toContain("overflow-x: auto;");
    expect(css).toContain(".composer-attachment-chip {");
    expect(css).toContain(".message-attachment-list {");
    expect(css).toContain("text-overflow: ellipsis;");
  });
});
