import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("right dock styles", () => {
  it("does not clip the add-window menu inside the tab strip", () => {
    const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");
    const tabRule = css.match(/\.right-dock-tabs\s*\{[^}]+\}/)?.[0] ?? "";

    expect(tabRule).toContain("overflow: visible");
    expect(tabRule).not.toContain("overflow: hidden");
  });

  it("opens the add-window menu toward the right side of the plus button", () => {
    const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");
    const menuRule = css.match(/\.right-dock-add-menu\s*\{[^}]+\}/)?.[0] ?? "";

    expect(menuRule).toContain("left: 0");
    expect(menuRule).not.toContain("right: 0");
  });

  it("does not force a desktop-only body width", () => {
    const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");
    const bodyRule = css.match(/body\s*\{[^}]+\}/)?.[0] ?? "";

    expect(bodyRule).toContain("min-width: 0");
    expect(bodyRule).not.toContain("min-width: 1060px");
  });

  it("lets the right dock column shrink below the regular desktop minimum", () => {
    const css = readFileSync(resolve(__dirname, "../styles.css"), "utf8");
    const dockRule = css.match(/\.app-shell\.dock-open\s*\{[^}]+\}/)?.[0] ?? "";

    expect(dockRule).toContain("min(320px, var(--right-dock-width");
    expect(dockRule).not.toContain("minmax(380px");
  });
});
