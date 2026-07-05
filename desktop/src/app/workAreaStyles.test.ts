import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function readStyles(): string {
  return readFileSync(resolve(__dirname, "../styles.css"), "utf8");
}

describe("work area visual density styles", () => {
  it("keeps the execution tree compact and responsive", () => {
    const css = readStyles();

    expect(css).toContain("width: min(820px, 90%);");
    expect(css).toContain("min-height: 22px;");
    expect(css).toContain("font-size: 11px;");
    expect(css).toContain(".work-area.compact-density");
    expect(css).toContain("@media (max-width: 960px)");
    expect(css).toContain(".work-area {");
    expect(css).toContain("width: min(100%, 820px);");
  });
});
