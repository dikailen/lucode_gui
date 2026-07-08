import { describe, expect, it } from "vitest";

import {
  appendUserMessage,
  createInitialAppState,
  markRunStarted,
  markRunStreamDisconnected,
  reduceRunEvent,
  runStageLabel,
  selectOrchestratorModelLabel,
  selectPrimaryModelLabel,
  formatSessionTime,
  markPendingDeleteSession,
  removeSession,
  setSessionMessages,
  workAreaSnapshotFromMessage,
} from "./appState";
import type { ModelSettingsModel, ModelSettingsResponse, RunEvent } from "../shared/types";

describe("appState", () => {
  it("selects a readable configured model label", () => {
    const label = selectPrimaryModelLabel([
      { id: "raw/model_a", display_name: "Raw Model", provider: "raw", configured: false },
      { id: "deepseek/deepseek-chat", display_name: "DeepSeek Chat", provider: "deepseek", configured: true },
    ]);

    expect(label).toBe("DeepSeek Chat");
  });

  it("hides internal custom provider prefixes in the primary model label", () => {
    const label = selectPrimaryModelLabel([
      {
        id: "custom_openai_compatible_deepseek_chat",
        display_name: "Custom Relay deepseek-chat",
        provider: "custom_openai_compatible",
        configured: true,
      },
    ]);

    expect(label).toBe("deepseek-chat");
  });

  it("uses the persisted orchestrator role model label from model settings", () => {
    const settings = modelSettingsFixture({
      selectedModelId: "openrouter/anthropic/claude-sonnet-4",
      models: [
        modelSettingsModel("deepseek/deepseek-chat", "DeepSeek Chat", "deepseek", "deepseek-chat"),
        modelSettingsModel(
          "openrouter/anthropic/claude-sonnet-4",
          "OpenRouter Claude",
          "openrouter",
          "claude-sonnet-4",
        ),
      ],
    });

    expect(selectOrchestratorModelLabel(settings, "DeepSeek Chat")).toBe("claude-sonnet-4");
  });

  it("keeps user message and folds streaming/final run events into chat", () => {
    let state = createInitialAppState();
    state = appendUserMessage(state, "session_1", "检查项目结构");
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "run.started",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: { input: "检查项目结构" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "worker.delta",
      created_at: "2026-06-30T00:00:01+08:00",
      payload: { text: "项目" },
    });
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "run.completed",
      created_at: "2026-06-30T00:00:02+08:00",
      payload: { final_output: "项目结构清晰" },
    });

    expect(state.activeSessionId).toBe("session_1");
    expect(state.activeRunId).toBe("");
    expect(state.runStatus).toBe("completed");
    expect(state.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(state.messages[0].content).toBe("检查项目结构");
    expect(state.messages[1].content).toBe("项目结构清晰");
    expect(state.events.map((event) => event.type)).toEqual(["run.started", "worker.delta", "run.completed"]);
  });

  it("marks a run as running immediately after startRun returns", () => {
    const state = markRunStarted(createInitialAppState(), "session_1", "run_1");

    expect(state.activeSessionId).toBe("session_1");
    expect(state.activeRunId).toBe("run_1");
    expect(state.runStatus).toBe("running");
  });

  it("formats session timestamps into compact labels", () => {
    expect(formatSessionTime("", new Date("2026-06-30T12:00:00Z"))).toBe("刚刚");
    expect(formatSessionTime("2026-06-30T11:59:20Z", new Date("2026-06-30T12:00:00Z"))).toBe("刚刚");
    expect(formatSessionTime("2026-06-30T11:30:00Z", new Date("2026-06-30T12:00:00Z"))).toBe("30分钟前");
    expect(formatSessionTime("2026-06-30T08:00:00Z", new Date("2026-06-30T12:00:00Z"))).toBe("4小时前");
    expect(formatSessionTime("2026-06-29T12:00:00Z", new Date("2026-06-30T12:00:00Z"))).toBe("昨天");
    expect(formatSessionTime("2026-06-27T12:00:00Z", new Date("2026-06-30T12:00:00Z"))).toBe("3天前");
  });

  it("marks a session as pending delete before destructive removal", () => {
    const state = markPendingDeleteSession(createInitialAppState(), "session_1");

    expect(state.pendingDeleteSessionId).toBe("session_1");
    expect(markPendingDeleteSession(state, "session_1").pendingDeleteSessionId).toBe("");
    expect(removeSession(state, "session_1").pendingDeleteSessionId).toBe("");
  });

  it("replaces visible messages with loaded history for the selected session", () => {
    const state = setSessionMessages(createInitialAppState(), "session_1", [
      { role: "user", content: "first question" },
      { role: "assistant", content: "first answer" },
    ]);

    expect(state.activeSessionId).toBe("session_1");
    expect(state.messages).toHaveLength(2);
    expect(state.messages.map((message) => message.sessionId)).toEqual(["session_1", "session_1"]);
    expect(state.messages.map((message) => message.content)).toEqual(["first question", "first answer"]);
  });

  it("preserves persisted run snapshots from history messages", () => {
    const events = runSnapshotEvents();
    const state = setSessionMessages(createInitialAppState(), "session_1", [
      { role: "user", content: "please inspect the app" },
      {
        role: "assistant",
        content: "inspection complete",
        metadata: {
          run_snapshot: {
            schema_version: "run_snapshot.v1",
            run_status: "completed",
            events,
          },
        },
      },
    ]);

    const assistantMessage = state.messages[1];
    const snapshot = workAreaSnapshotFromMessage(assistantMessage);

    expect(assistantMessage.metadata).toEqual({
      run_snapshot: {
        schema_version: "run_snapshot.v1",
        run_status: "completed",
        events,
      },
    });
    expect(snapshot?.runStatus).toBe("completed");
    expect(snapshot?.taskCount).toBe(1);
    expect(snapshot?.tasks[0].status).toBe("completed");
    expect(snapshot?.tasks[0].latest).toBe("Read README.md");
  });

  it("removes a deleted session and clears its visible messages", () => {
    const base = {
      ...createInitialAppState(),
      activeSessionId: "session_1",
      sessions: [
        {
          schema_version: "session.v1" as const,
          session_id: "session_1",
          title: "Session one",
          display_title: "Session one",
          created_at: "now",
          updated_at: "now",
        },
        {
          schema_version: "session.v1" as const,
          session_id: "session_2",
          title: "Session two",
          display_title: "Session two",
          created_at: "now",
          updated_at: "now",
        },
      ],
      messages: [
        { id: "m1", sessionId: "session_1", role: "user" as const, content: "remove me" },
        { id: "m2", sessionId: "session_2", role: "user" as const, content: "keep me" },
      ],
    };

    const state = removeSession(base, "session_1");

    expect(state.sessions.map((session) => session.session_id)).toEqual(["session_2"]);
    expect(state.activeSessionId).toBe("session_2");
    expect(state.messages).toEqual([]);
  });

  it("records failed and cancelled runs as visible status messages", () => {
    let state = createInitialAppState();
    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_failed",
      session_id: "session_1",
      seq: 1,
      type: "run.failed",
      created_at: "2026-06-30T00:00:00+08:00",
      payload: { error: "planner failed" },
    });

    expect(state.runStatus).toBe("failed");
    expect(state.messages.at(-1)?.content).toContain("planner failed");

    state = reduceRunEvent(state, {
      schema_version: "run_event.v1",
      run_id: "run_cancelled",
      session_id: "session_1",
      seq: 2,
      type: "run.cancelled",
      created_at: "2026-06-30T00:00:01+08:00",
      payload: { reason: "user_requested" },
    });

    expect(state.runStatus).toBe("cancelled");
    expect(state.messages.at(-1)?.content).toContain("已停止");
  });
});

function modelSettingsFixture({
  selectedModelId,
  models,
}: {
  selectedModelId: string;
  models: ModelSettingsModel[];
}): ModelSettingsResponse {
  return {
    schema_version: "model_settings.v1",
    summary: {
      model_count: models.length,
      configured_model_count: models.filter((model) => model.configured).length,
      provider_count: 1,
      configured_provider_count: 1,
    },
    models,
    providers: [],
    roles: [
      {
        role: "orchestrator",
        label: "主脑模型",
        model_priority: [selectedModelId],
        selected_model_id: selectedModelId,
      },
    ],
  };
}

function modelSettingsModel(
  id: string,
  displayName: string,
  provider: string,
  modelName: string,
): ModelSettingsModel {
  return {
    id,
    ref: id,
    display_name: displayName,
    provider,
    configured: true,
    available: true,
    backend_type: "openai",
    model_name: modelName,
    privacy_level: "cloud",
    supports_tools: true,
    reasoning_level: "medium",
    cost_level: "medium",
    model_tier: "standard",
  };
}

function runSnapshotEvents(): RunEvent[] {
  return [
    {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 1,
      type: "run.started",
      created_at: "2026-07-07T10:00:00+08:00",
      payload: { input: "please inspect the app" },
    },
    {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 2,
      type: "planner.completed",
      created_at: "2026-07-07T10:00:01+08:00",
      payload: {
        route_type: "single_agent",
        tasks: [
          {
            id: "inspect",
            title: "Inspect project",
            model: "worker",
            mcp: ["project_filesystem_readonly"],
            depends_on: [],
            parallel_group: "1",
          },
        ],
      },
    },
    {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 3,
      type: "worker.delta",
      created_at: "2026-07-07T10:00:02+08:00",
      payload: { task_id: "inspect", text: "Read README.md" },
    },
    {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 4,
      type: "task.completed",
      created_at: "2026-07-07T10:00:03+08:00",
      payload: { task_id: "inspect", title: "Inspect project" },
    },
    {
      schema_version: "run_event.v1",
      run_id: "run_1",
      session_id: "session_1",
      seq: 5,
      type: "run.completed",
      created_at: "2026-07-07T10:00:04+08:00",
      payload: { final_output_preview: "inspection complete" },
    },
  ];
}
