import type { RunStatus } from "./appState";

const MAX_RECONNECT_ATTEMPTS = 4;
const INITIAL_RECONNECT_DELAY_MS = 250;

export function runEventSequenceAction(lastSeq: number, nextSeq: number): "duplicate" | "accept" | "replay_from_cursor" {
  if (nextSeq <= lastSeq) {
    return "duplicate";
  }
  return nextSeq === lastSeq + 1 ? "accept" : "replay_from_cursor";
}

export function runStreamReconnectDelay(attempt: number): number | null {
  const normalizedAttempt = Math.max(1, Math.floor(Number(attempt) || 1));
  if (normalizedAttempt > MAX_RECONNECT_ATTEMPTS) {
    return null;
  }
  return INITIAL_RECONNECT_DELAY_MS * (2 ** (normalizedAttempt - 1));
}

export function shouldReconnectRunStream({
  activeRunId,
  runStatus,
  runId,
  sawTerminalEvent,
}: {
  activeRunId: string;
  runStatus: RunStatus;
  runId: string;
  sawTerminalEvent: boolean;
}): boolean {
  return !sawTerminalEvent && runStatus === "running" && activeRunId === runId;
}
