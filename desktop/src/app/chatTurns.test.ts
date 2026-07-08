import { describe, expect, it } from "vitest";

import type { WorkAreaSnapshot } from "./appState";
import { buildChatTurns } from "./chatTurns";
import type { ChatMessage } from "../shared/types";

describe("buildChatTurns", () => {
  it("attaches the active run process to the latest user turn before assistant responses", () => {
    const messages: ChatMessage[] = [
      message("u1", "user", "first question"),
      message("a1", "assistant", "first answer"),
      message("u2", "user", "second question"),
      message("a2", "assistant", "second answer"),
    ];
    const process = snapshot("completed");

    const turns = buildChatTurns(messages, process);

    expect(turns).toHaveLength(2);
    expect(turns[0].user?.content).toBe("first question");
    expect(turns[0].process).toBeNull();
    expect(turns[0].responses.map((item) => item.content)).toEqual(["first answer"]);
    expect(turns[1].user?.content).toBe("second question");
    expect(turns[1].process).toBe(process);
    expect(turns[1].responses.map((item) => item.content)).toEqual(["second answer"]);
  });

  it("keeps orphan system messages in their own turn", () => {
    const messages: ChatMessage[] = [message("s1", "system", "runtime failed"), message("u1", "user", "retry")];

    const turns = buildChatTurns(messages, null);

    expect(turns).toHaveLength(2);
    expect(turns[0].user).toBeNull();
    expect(turns[0].responses.map((item) => item.content)).toEqual(["runtime failed"]);
    expect(turns[1].user?.content).toBe("retry");
  });

  it("keeps a historical run process on the turn that owns the assistant response", () => {
    const messages: ChatMessage[] = [
      message("u1", "user", "first question"),
      message("a1", "assistant", "first answer"),
      message("u2", "user", "second question"),
      message("a2", "assistant", "second answer"),
    ];
    const historicalProcess = snapshot("completed");

    const turns = buildChatTurns(messages, null, { a1: historicalProcess });

    expect(turns).toHaveLength(2);
    expect(turns[0].process).toBe(historicalProcess);
    expect(turns[1].process).toBeNull();
  });
});

function message(id: string, role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id,
    sessionId: "s1",
    role,
    content,
    status: "completed",
  };
}

function snapshot(runStatus: WorkAreaSnapshot["runStatus"]): WorkAreaSnapshot {
  return {
    runStatus,
    routeType: "multi_agent",
    taskCount: 1,
    supervisorActivity: "",
    collapsedSummary: "1 task completed",
    tasks: [
      {
        id: "task-1",
        title: "Read files",
        model: "worker",
        mcp: ["project_filesystem_readonly"],
        dependsOn: [],
        parallelGroup: "1",
        status: "completed",
        latest: "done",
      },
    ],
  };
}
