import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function readStyles() {
  return readFileSync(resolve(__dirname, "../styles.css"), "utf8");
}

function ruleFor(selector: string, source: string) {
  for (const match of source.matchAll(/([^{}]+)\{([^{}]+)\}/g)) {
    const selectors = match[1].split(",").map((item) => item.trim());
    if (selectors.includes(selector)) {
      return `${match[1]}{${match[2]}}`;
    }
  }
  return "";
}

function narrowMedia(source: string) {
  return source.match(/@media\s*\(max-width:\s*960px\)\s*\{([\s\S]*)\}\s*$/)?.[1] ?? "";
}

function pluginContainer(source: string) {
  return source.match(/@container\s*\(max-width:\s*960px\)\s*\{([\s\S]*?)\n\}\s*\n\n@media/)?.[1] ?? "";
}

describe("page responsive styles", () => {
  it("collapses settings and provider controls in narrow layouts", () => {
    const media = narrowMedia(readStyles());

    expect(ruleFor(".settings-workspace", media)).toContain("padding: 24px 18px 18px");
    expect(ruleFor(".settings-config-row", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".settings-wide-select", media)).toContain("width: 100%");
    expect(ruleFor(".provider-form", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".settings-provider-row", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".provider-row-actions", media)).toContain("justify-content: flex-start");
  });

  it("keeps plugin management usable without horizontal overflow", () => {
    const styles = readStyles();
    const media = narrowMedia(styles);

    expect(ruleFor(".plugins-content", styles)).toContain("align-content: start");
    expect(ruleFor(".plugins-content", styles)).toContain("grid-auto-rows: max-content");
    expect(ruleFor(".plugins-content", styles)).toContain("overflow: auto");
    expect(ruleFor(".plugin-column", styles)).toContain("overflow: visible");
    expect(ruleFor(".plugin-list", styles)).toContain("overflow: visible");
    expect(styles).toContain(".plugins-pane {\n  container-type: inline-size;");
    expect(styles).toContain("@container (max-width: 960px)");
    expect(styles).toContain(".comfyui-mcp-fields,\n  .external-mcp-grid {\n    grid-template-columns: minmax(0, 1fr);");
    expect(ruleFor(".plugins-content", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".plugins-content", media)).toContain("align-content: start");
    expect(ruleFor(".plugins-content", media)).toContain("grid-auto-rows: max-content");
    expect(ruleFor(".plugins-content", media)).toContain("overflow: auto");
    expect(ruleFor(".plugin-column", media)).toContain("min-height: 0");
    expect(ruleFor(".plugin-column", media)).toContain("grid-template-rows: auto auto auto");
    expect(ruleFor(".mcp-plugin-column", styles)).toContain("grid-template-rows: auto auto auto auto");
    expect(ruleFor(".mcp-plugin-column", media)).toContain("grid-template-rows: auto auto auto auto");
    expect(ruleFor(".runtime-capability-column", styles)).toContain("grid-column: 1 / -1");
    expect(ruleFor(".runtime-capability-list", styles)).toContain("grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))");
    expect(ruleFor(".runtime-capability-list", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".runtime-capability-row", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".comfyui-mcp-compact-form", styles)).toContain("min-width: 0");
    expect(ruleFor(".comfyui-mcp-fields", styles)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".comfyui-mcp-fields", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".comfyui-mcp-actions", media)).toContain("justify-content: flex-start");
    expect(ruleFor(".comfyui-mcp-actions .secondary-button", styles)).toContain("white-space: nowrap");
    expect(ruleFor(".external-mcp-grid", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".plugin-row", media)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".external-mcp-form-header", styles)).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(ruleFor(".external-mcp-form-footer", styles)).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(ruleFor(".plugin-row", styles)).toContain("min-height: 98px");
    expect(ruleFor(".plugin-row", styles)).toContain("align-items: start");
    expect(ruleFor(".plugin-row-footer", styles)).toContain("justify-content: space-between");
    expect(ruleFor(".plugin-row-description", styles)).toContain("overflow-wrap: anywhere");
    expect(ruleFor(".plugin-delete-pill", styles)).toContain("flex: 0 0 auto");
    expect(ruleFor(".plugin-delete-pill", styles)).toContain("min-width: 44px");
    expect(ruleFor(".plugin-row-title", media)).toContain("white-space: normal");
    expect(ruleFor(".plugin-row-footer", media)).toContain("flex-wrap: wrap");
  });

  it("keeps plugin action rows stable inside a narrow right dock container", () => {
    const styles = readStyles();
    const container = pluginContainer(styles);

    expect(ruleFor(".plugin-row-footer", styles)).toContain("flex-wrap: wrap");
    expect(ruleFor(".plugin-delete-pill", styles)).toContain("white-space: nowrap");
    expect(ruleFor(".plugin-chip", styles)).toContain("min-width: 0");
    expect(ruleFor(".external-mcp-form-footer span", styles)).toContain("overflow-wrap: anywhere");
    expect(ruleFor(".external-mcp-form-footer .secondary-button", styles)).toContain("white-space: nowrap");
    expect(ruleFor(".comfyui-error", styles)).toContain("overflow-wrap: anywhere");
    expect(ruleFor(".external-mcp-form-header", container)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".external-mcp-form-footer", container)).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(ruleFor(".external-mcp-form select", container)).toContain("justify-self: start");
    expect(ruleFor(".external-mcp-form-footer .secondary-button", container)).toContain("justify-self: start");
    expect(ruleFor(".comfyui-error", container)).toContain("white-space: normal");
  });
});
