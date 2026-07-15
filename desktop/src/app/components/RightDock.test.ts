import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createTranslator } from "../i18n";
import { RightDock } from "./RightDock";

describe("RightDock home", () => {
  it("only exposes the supported workspace tools", () => {
    const html = renderToStaticMarkup(
      createElement(RightDock, {
        t: createTranslator("en"),
        activeTool: "home",
        windows: [],
        activateTool: () => undefined,
        collapseDock: () => undefined,
        closeWindow: () => undefined,
        startResize: () => undefined,
        resetWidth: () => undefined,
      }),
    );

    expect(html).toContain("Review");
    expect(html).toContain("Terminal");
    expect(html).toContain("Browser");
    expect(html).toContain("Files");
    expect(html).not.toContain("Side chat");
  });
});
