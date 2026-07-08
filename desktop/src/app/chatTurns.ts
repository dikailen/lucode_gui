import type { WorkAreaSnapshot } from "./appState";
import type { ChatMessage } from "../shared/types";

export type ChatTurn = {
  id: string;
  user: ChatMessage | null;
  responses: ChatMessage[];
  process: WorkAreaSnapshot | null;
};

export function buildChatTurns(
  messages: ChatMessage[],
  activeProcess: WorkAreaSnapshot | null,
  historicalProcessByMessageId: Record<string, WorkAreaSnapshot | null> = {},
): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let current: ChatTurn | null = null;

  for (const message of messages) {
    if (message.role === "user") {
      current = {
        id: message.id,
        user: message,
        responses: [],
        process: null,
      };
      turns.push(current);
      continue;
    }

    if (!current) {
      current = {
        id: message.id,
        user: null,
        responses: [],
        process: null,
      };
      turns.push(current);
    }
    current.responses.push(message);
    const historicalProcess = historicalProcessByMessageId[message.id];
    if (historicalProcess) {
      current.process = historicalProcess;
    }
  }

  if (activeProcess) {
    const target = [...turns].reverse().find((turn) => turn.user) ?? turns.at(-1);
    if (target) {
      target.process = activeProcess;
    }
  }

  return turns;
}
