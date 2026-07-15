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
  workAreaSnapshotFromMessage,
} from "./appState";

describe("stage 6 run state", () => {
  it("ignores a replayed event sequence so streamed answer text is not duplicated", () => {
    const initial = markRunStarted(createInitialAppState(), "session_1", "run_1");
    const delta = {
      schema_version: "run_event.v1" as const,
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "answer.delta",
      created_at: "2026-07-14T10:00:00+08:00",
      payload: { text: "partial answer" },
    };

    const once = reduceRunEvent(initial, delta);
    const replayed = reduceRunEvent(once, delta);

    expect(replayed.events).toHaveLength(1);
    expect(replayed.messages).toHaveLength(1);
    expect(replayed.messages[0].content).toBe("partial answer");
  });

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

  it("keeps a completed run process summary with real task activities", () => {
    let state = markRunStarted(createInitialAppState(), "session_1", "run_1");
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "run.started",
      created_at: "2026-07-03T10:00:00+08:00",
      payload: {},
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "planner.completed",
      created_at: "2026-07-03T10:00:02+08:00",
      payload: {
        route_type: "multi_agent",
        tasks: [{ id: "task_1", title: "Inspect chat UI", model: "gpt-5.5", mcp: ["code_locator"] }],
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "task.started",
      created_at: "2026-07-03T10:00:03+08:00",
      payload: { task_id: "task_1" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 4,
      type: "tool.requested",
      created_at: "2026-07-03T10:00:10+08:00",
      payload: {
        task_id: "task_1",
        tool_name: "command.run",
        action: "run",
        arguments_summary: { command: "npm test -- WorkAreaPanel" },
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 5,
      type: "tool.completed",
      created_at: "2026-07-03T10:00:30+08:00",
      payload: {
        task_id: "task_1",
        tool_name: "filesystem.read",
        arguments_summary: { path: "desktop/src/app/components/WorkAreaPanel.tsx" },
        status: "completed",
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 6,
      type: "task.completed",
      created_at: "2026-07-03T10:01:20+08:00",
      payload: { task_id: "task_1" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 7,
      type: "run.completed",
      created_at: "2026-07-03T10:01:49+08:00",
      payload: { final_output: "done" },
    });

    const snapshot = workAreaSnapshotFromMessage(state.messages.at(-1)!);

    expect(snapshot).toMatchObject({
      runStatus: "completed",
      durationText: "1m 49s",
      processSummary: "已处理 1m 49s",
    });
    expect(snapshot?.tasks[0].activities).toEqual([
      expect.objectContaining({
        kind: "tool",
        status: "running",
        title: "运行命令",
        detail: "npm test -- WorkAreaPanel",
      }),
      expect.objectContaining({
        kind: "tool",
        status: "done",
        title: "读取文件",
        detail: "desktop/src/app/components/WorkAreaPanel.tsx",
      }),
    ]);
  });

  it("adds worker progress and global tool activities to the work area snapshot", () => {
    let state = markRunStarted(createInitialAppState(), "session_1", "run_1");
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "planner.completed",
      created_at: "2026-07-03T10:00:00+08:00",
      payload: {
        route_type: "multi_agent",
        tasks: [{ id: "task_1", title: "Inspect runtime", model: "gpt-5.5", mcp: ["code_locator"] }],
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "task.started",
      created_at: "2026-07-03T10:00:01+08:00",
      payload: { task_id: "task_1" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "worker.delta",
      created_at: "2026-07-03T10:00:02+08:00",
      payload: { task_id: "task_1", text: "Reading runtime/server/app.py" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 4,
      type: "tool.requested",
      created_at: "2026-07-03T10:00:03+08:00",
      payload: {
        tool_name: "desktop_browser.browser_navigate",
        action: "browser_navigate",
        arguments_summary: { url: "https://example.com" },
      },
    });

    const snapshot = workAreaSnapshot(state);

    expect(snapshot?.tasks[0].activities).toEqual([
      expect.objectContaining({
        kind: "thinking",
        status: "running",
        title: "进度",
        detail: "Reading runtime/server/app.py",
      }),
    ]);
    expect(snapshot?.globalActivities).toEqual([
      expect.objectContaining({
        kind: "browser",
        status: "running",
        title: "打开网页",
        detail: "https://example.com",
      }),
    ]);
  });

  it("keeps parallel groups and dependencies so the work area can render an execution tree", () => {
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
        tasks: [
          {
            id: "analyze_runner",
            title: "梳理 multi_agent_runner.py 的收口与返回流程",
            model: "deepseek-v4-pro",
            mcp: ["project_filesystem_readonly", "code_locator"],
            parallel_group: "1",
          },
          {
            id: "analyze_approval",
            title: "梳理 approval_policy.py 的审批判定逻辑",
            model: "deepseek-v4-pro",
            mcp: ["project_filesystem_readonly", "code_locator"],
            parallel_group: "1",
          },
          {
            id: "write_handoff",
            title: "编写衔接说明文档",
            model: "gpt-5.5",
            mcp: ["workspace_edit", "code_locator"],
            parallel_group: "2",
            depends_on: ["analyze_runner", "analyze_approval"],
          },
        ],
      },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "task.started",
      created_at: "2026-06-30T00:00:01+08:00",
      payload: { task_id: "analyze_runner" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "worker.delta",
      created_at: "2026-06-30T00:00:02+08:00",
      payload: { task_id: "analyze_runner", text: "读取 runtime/execution/multi_agent_runner.py" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 4,
      type: "task.started",
      created_at: "2026-06-30T00:00:03+08:00",
      payload: { task_id: "analyze_approval" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 5,
      type: "planner.observation",
      created_at: "2026-06-30T00:00:04+08:00",
      payload: { message: "主管已拆分 3 个任务" },
    });

    const snapshot = workAreaSnapshot(state);

    expect(snapshot?.routeType).toBe("multi_agent");
    expect(snapshot?.taskCount).toBe(3);
    expect(snapshot?.supervisorActivity).toBe("主管已拆分 3 个任务");
    expect(snapshot?.tasks).toEqual([
      expect.objectContaining({
        id: "analyze_runner",
        parallelGroup: "1",
        dependsOn: [],
        status: "running",
        latest: "读取 runtime/execution/multi_agent_runner.py",
      }),
      expect.objectContaining({
        id: "analyze_approval",
        parallelGroup: "1",
        dependsOn: [],
        status: "running",
      }),
      expect.objectContaining({
        id: "write_handoff",
        parallelGroup: "2",
        dependsOn: ["analyze_runner", "analyze_approval"],
        status: "waiting",
      }),
    ]);
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
