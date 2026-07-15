import { describe, expect, it } from "vitest";

import { runEventSequenceAction, runStreamReconnectDelay, shouldReconnectRunStream } from "./runStreamRecovery";

describe("run stream recovery", () => {
  it("uses bounded exponential reconnect delays", () => {
    expect(runStreamReconnectDelay(1)).toBe(250);
    expect(runStreamReconnectDelay(2)).toBe(500);
    expect(runStreamReconnectDelay(3)).toBe(1000);
    expect(runStreamReconnectDelay(4)).toBe(2000);
    expect(runStreamReconnectDelay(5)).toBeNull();
  });

  it("only reconnects the currently running non-terminal run", () => {
    expect(shouldReconnectRunStream({ activeRunId: "run_1", runStatus: "running", runId: "run_1", sawTerminalEvent: false })).toBe(true);
    expect(shouldReconnectRunStream({ activeRunId: "run_1", runStatus: "completed", runId: "run_1", sawTerminalEvent: false })).toBe(false);
    expect(shouldReconnectRunStream({ activeRunId: "run_2", runStatus: "running", runId: "run_1", sawTerminalEvent: false })).toBe(false);
    expect(shouldReconnectRunStream({ activeRunId: "run_1", runStatus: "running", runId: "run_1", sawTerminalEvent: true })).toBe(false);
  });

  it("replays from the last cursor when an event sequence has a gap", () => {
    expect(runEventSequenceAction(1, 1)).toBe("duplicate");
    expect(runEventSequenceAction(1, 2)).toBe("accept");
    expect(runEventSequenceAction(1, 3)).toBe("replay_from_cursor");
  });
});
