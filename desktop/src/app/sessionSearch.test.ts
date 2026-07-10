import { afterEach, describe, expect, it, vi } from "vitest";

import { createSessionSearchDebouncer } from "./sessionSearch";


describe("createSessionSearchDebouncer", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs only the latest scheduled search after the delay", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const debouncer = createSessionSearchDebouncer(250);

    debouncer.schedule(() => calls.push("first"));
    debouncer.schedule(() => calls.push("second"));
    await vi.advanceTimersByTimeAsync(249);
    expect(calls).toEqual([]);

    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toEqual(["second"]);
  });

  it("cancels a pending search", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const debouncer = createSessionSearchDebouncer(250);

    debouncer.schedule(() => calls.push("search"));
    debouncer.cancel();
    await vi.advanceTimersByTimeAsync(250);

    expect(calls).toEqual([]);
  });
});
