import { describe, expect, it } from "vitest";

import { errorBoundaryMessage } from "./ErrorBoundary";

describe("ErrorBoundary", () => {
  it("formats render failures into a readable fallback message", () => {
    expect(errorBoundaryMessage(new Error("settings exploded"))).toContain("settings exploded");
  });

  it("handles non-error render failures", () => {
    expect(errorBoundaryMessage("boom")).toContain("boom");
  });
});
