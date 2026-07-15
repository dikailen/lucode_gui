import { describe, expect, it } from "vitest";

import {
  MAX_ATTACHMENT_DRAFTS,
  mergeAttachmentDrafts,
  removeAttachmentDraft,
} from "./attachmentDrafts";

describe("attachmentDrafts", () => {
  it("derives display names from Windows and Unix paths, deduplicates, and preserves order", () => {
    const result = mergeAttachmentDrafts([], [
      "D:\\docs\\notes.md",
      "/tmp/screen.png",
      "D:\\docs\\notes.md",
    ]);

    expect(result.drafts.map((item) => item.name)).toEqual(["notes.md", "screen.png"]);
    expect(result.rejectedCount).toBe(0);
  });

  it("caps a composer turn at the backend attachment limit", () => {
    const paths = Array.from({ length: MAX_ATTACHMENT_DRAFTS + 3 }, (_, index) => `D:\\files\\${index}.txt`);

    const result = mergeAttachmentDrafts([], paths);

    expect(result.drafts).toHaveLength(MAX_ATTACHMENT_DRAFTS);
    expect(result.rejectedCount).toBe(3);
  });

  it("removes only the selected draft", () => {
    const result = mergeAttachmentDrafts([], ["D:\\a.txt", "D:\\b.txt"]);

    expect(removeAttachmentDraft(result.drafts, result.drafts[0].id).map((item) => item.name)).toEqual(["b.txt"]);
  });
});
