import { describe, expect, it } from "vitest";

import {
  activeSessionTitle,
  createInitialAppState,
  markRunStarted,
  markRunStreamDisconnected,
  eventLabel,
  reduceRunEvent,
  renderMessageParts,
  runStageMeta,
  runStageLabel,
  workAreaSnapshot,
} from "./appState";

describe("stage 6 run state", () => {
  it("uses the active session display title for the chat header", () => {
    const state = {
      ...createInitialAppState(),
      activeSessionId: "session_2",
      sessions: [
        {
          schema_version: "session.v1" as const,
          session_id: "session_1",
          title: "旧标题",
          display_title: "旧显示标题",
          created_at: "2026-06-30T00:00:00+08:00",
          updated_at: "2026-06-30T00:00:00+08:00",
        },
        {
          schema_version: "session.v1" as const,
          session_id: "session_2",
          title: "原始标题",
          display_title: "当前显示标题",
          created_at: "2026-06-30T00:00:00+08:00",
          updated_at: "2026-06-30T00:00:00+08:00",
        },
      ],
    };

    expect(activeSessionTitle(state)).toBe("当前显示标题");
    expect(activeSessionTitle(createInitialAppState())).toBe("新会话");
  });

  it("derives visible run stage labels from run events", () => {
    let state = markRunStarted(createInitialAppState(), "session_1", "run_1");
    expect(runStageLabel(state)).toBe("启动中");
    expect(runStageMeta(state)).toMatchObject({ label: "启动中", tone: "running" });

    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "planner.started",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: {},
    });
    expect(runStageLabel(state)).toBe("规划中");

    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "worker.delta",
      created_at: "2026-06-30T00:00:01+08:00",
      payload: { text: "ok" },
    });
    expect(runStageLabel(state)).toBe("执行中");

    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "audit.started",
      created_at: "2026-06-30T00:00:02+08:00",
      payload: {},
    });
    expect(runStageLabel(state)).toBe("审查中");
  });

  it("summarizes run events for the stage 7 activity strip", () => {
    expect(
      eventLabel({
        schema_version: "run_event.v1",
        run_id: "run_1",
        session_id: "session_1",
        seq: 1,
        type: "planner.started",
        created_at: "2026-06-30T00:00:00+08:00",
        payload: {},
      }),
    ).toBe("主脑规划");
  });

  it("does not create a work area for direct answers without tasks", () => {
    const state = reduceRunEvent(markRunStarted(createInitialAppState(), "session_1", "run_1"), {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "planner.completed",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: { route_type: "direct_answer", tasks: [] },
    });

    expect(workAreaSnapshot(state)).toBeNull();
  });

  it("derives a work area task tree from planner and worker events", () => {
    let state = markRunStarted(createInitialAppState(), "session_1", "run_1");
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "planner.completed",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: {
        route_type: "multi_agent",
        tasks: [{ id: "task_1", title: "检查 Runtime", model: "gpt-5.5", mcp: ["git"] }],
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "task.started",
      created_at: "2026-06-30T00:00:01+08:00",
      payload: { task_id: "task_1" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "worker.delta",
      created_at: "2026-06-30T00:00:02+08:00",
      payload: { task_id: "task_1", text: "正在读取 runtime/server/app.py" },
    });

    const snapshot = workAreaSnapshot(state);
    expect(snapshot?.routeType).toBe("multi_agent");
    expect(snapshot?.tasks[0]).toMatchObject({
      id: "task_1",
      title: "检查 Runtime",
      status: "running",
      latest: "正在读取 runtime/server/app.py",
    });
    expect(state.messages).toEqual([]);
  });

  it("splits fenced code blocks for message rendering", () => {
    expect(renderMessageParts("说明\n```ts\nconst ok = true;\n```\n结束")).toEqual([
      { type: "text", content: "说明\n" },
      { type: "code", language: "ts", content: "const ok = true;\n" },
      { type: "text", content: "\n结束" },
    ]);
  });

  it("marks terminal events as no longer stoppable", () => {
    const running = markRunStarted(createInitialAppState(), "session_1", "run_1");
    const completed = reduceRunEvent(running, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "run.completed",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: { final_output: "done" },
    });

    expect(completed.runStatus).toBe("completed");
    expect(completed.activeRunId).toBe("");
  });

  it("marks disconnected run streams as failed with a visible message", () => {
    const running = markRunStarted(createInitialAppState(), "session_1", "run_1");
    const state = markRunStreamDisconnected(running, "session_1", "WebSocket closed");

    expect(state.runStatus).toBe("failed");
    expect(state.activeRunId).toBe("");
    expect(state.messages.at(-1)?.role).toBe("system");
    expect(state.messages.at(-1)?.content).toContain("事件流连接中断");
  });
});
