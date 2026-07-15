import { describe, expect, it, vi } from "vitest";

import { resolveDroppedFilePaths } from "./localFileDrop";

describe("resolveDroppedFilePaths", () => {
  it("resolves every native file independently and omits unresolved browser-only files", () => {
    const files = [
      { name: "notes.md" },
      { name: "screen.png" },
      { name: "virtual.txt" },
    ] as File[];
    const resolver = vi.fn((file: File) => {
      if (file.name === "notes.md") return "D:/docs/notes.md";
      if (file.name === "screen.png") return "D:/images/screen.png";
      return "";
    });

    expect(resolveDroppedFilePaths(files, resolver)).toEqual([
      "D:/docs/notes.md",
      "D:/images/screen.png",
    ]);
    expect(resolver).toHaveBeenCalledTimes(3);
  });
});
