import { describe, expect, it, vi } from "vitest";

import { chooseAttachmentFiles, choosePluginSource, resolveDroppedFilePath } from "./filePicker.js";

describe("chooseAttachmentFiles", () => {
  it("uses the native file picker with multi-selection and returns every selected file", async () => {
    const showOpenDialog = vi.fn(async () => ({
      canceled: false,
      filePaths: ["D:/docs/notes.md", "D:/images/screen.png"],
    }));

    await expect(chooseAttachmentFiles(showOpenDialog)).resolves.toEqual([
      "D:/docs/notes.md",
      "D:/images/screen.png",
    ]);
    expect(showOpenDialog).toHaveBeenCalledWith({
      title: "Select files to attach",
      properties: ["openFile", "multiSelections"],
    });
  });

  it("returns an empty list when attachment selection is cancelled", async () => {
    await expect(chooseAttachmentFiles(async () => ({ canceled: true, filePaths: [] }))).resolves.toEqual([]);
  });
});

describe("choosePluginSource", () => {
  it("allows either a folder or a zip/Skill file and returns the selected path", async () => {
    const showOpenDialog = vi.fn(async () => ({ canceled: false, filePaths: ["D:/skills/browser_helper"] }));

    await expect(choosePluginSource(showOpenDialog)).resolves.toBe("D:/skills/browser_helper");
    expect(showOpenDialog).toHaveBeenCalledWith({
      title: "Select Lucode plugin source",
      properties: ["openFile", "openDirectory"],
      filters: [{ name: "Lucode sources", extensions: ["zip", "md"] }],
    });
  });

  it("returns an empty path when the native dialog is cancelled", async () => {
    await expect(choosePluginSource(async () => ({ canceled: true, filePaths: [] }))).resolves.toBe("");
  });

  it("allows MCP JSON files without exposing unrelated file types", async () => {
    const showOpenDialog = vi.fn(async () => ({ canceled: false, filePaths: ["D:/mcp/servers.json"] }));

    await expect(choosePluginSource(showOpenDialog, "mcp")).resolves.toBe("D:/mcp/servers.json");
    expect(showOpenDialog).toHaveBeenCalledWith({
      title: "Select Lucode MCP source",
      properties: ["openFile", "openDirectory"],
      filters: [{ name: "MCP sources", extensions: ["json"] }],
    });
  });
});

describe("resolveDroppedFilePath", () => {
  it("resolves one native File at a time through Electron webUtils", () => {
    const file = { name: "SKILL.md" } as File;
    const getPathForFile = vi.fn(() => "D:/skills/demo/SKILL.md");

    expect(resolveDroppedFilePath(file, getPathForFile)).toBe("D:/skills/demo/SKILL.md");
    expect(getPathForFile).toHaveBeenCalledWith(file);
  });

  it("returns an empty path when Electron rejects a non-native File", () => {
    const file = { name: "generated.json" } as File;

    expect(
      resolveDroppedFilePath(file, () => {
        throw new TypeError("not backed by disk");
      }),
    ).toBe("");
  });
});
