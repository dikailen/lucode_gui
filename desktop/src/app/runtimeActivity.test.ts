import { describe, expect, it } from "vitest";

import type { AppState } from "./appState";
import {
  pendingApprovalRequestsFromEvents,
  runtimeActivitiesFromEvents,
  runtimeReviewSnapshot,
  runtimeToastItems,
} from "./runtimeActivity";
import type { RunEvent } from "../shared/types";

describe("runtimeActivity", () => {
  it("extracts browser capability activities from real run event payloads", () => {
    const activities = runtimeActivitiesFromEvents([
      runEvent(1, "tool.requested", {
        tool_name: "desktop_browser.browser_navigate",
        action: "browser_navigate",
        arguments_summary: { url: "https://example.com" },
        status: "pending",
      }),
      runEvent(2, "tool.requested", {
        tool_name: "desktop_browser.browser_get_page_summary",
        action: "browser_get_page_summary",
        arguments_summary: { tab_id: "tab_1" },
      }),
      runEvent(3, "tool.approval_required", {
        tool_name: "desktop_browser.browser_click_element",
        action: "browser_click_element",
        arguments_summary: { selector: "button.submit", url: "https://example.com/form" },
        status: "pending",
      }),
    ]);

    expect(activities.map((item) => item.title)).toEqual(["等待审批", "正在读取页面", "正在打开网页"]);
    expect(activities[0]).toMatchObject({
      kind: "browser",
      status: "waiting",
      detail: "button.submit",
      url: "https://example.com/form",
      selector: "button.submit",
    });
    expect(activities[1].detail).toBe("tab_1");
    expect(activities[2].detail).toBe("https://example.com");
  });

  it("keeps the latest three running browser/tool activities available to the toast renderer", () => {
    const state: AppState = {
      sessions: [],
      activeSessionId: "session_1",
      activeRunId: "run_1",
      runStatus: "running",
      modelLabel: "gpt",
      messages: [],
      pendingDeleteSessionId: "",
      events: [
        runEvent(1, "planner.started", {}),
        runEvent(2, "tool.requested", {
          tool_name: "desktop_browser.browser_navigate",
          action: "browser_navigate",
          arguments_summary: { url: "https://a.example" },
        }),
        runEvent(3, "tool.requested", {
          tool_name: "desktop_browser.browser_get_page_summary",
          action: "browser_get_page_summary",
          arguments_summary: { url: "https://a.example" },
        }),
        runEvent(4, "tool.completed", {
          tool_name: "desktop_browser.browser_click_element",
          action: "browser_click_element",
          outcome: "approved",
          arguments_summary: { selector: "#ok" },
        }),
        runEvent(5, "tool.requested", {
          tool_name: "command.run",
          action: "run",
          arguments_summary: { command: "npm test" },
        }),
      ],
    };

    const items = runtimeToastItems(state);

    expect(items).toHaveLength(3);
    expect(items.map((item) => item.title)).toEqual(["正在执行工具", "操作完成", "正在读取页面"]);
    expect(items.every((item) => item.runId === "run_1")).toBe(true);
  });

  it("returns no toast items outside active runs", () => {
    const state: AppState = {
      sessions: [],
      activeSessionId: "session_1",
      activeRunId: "",
      runStatus: "completed",
      modelLabel: "gpt",
      messages: [],
      pendingDeleteSessionId: "",
      events: [
        runEvent(1, "tool.requested", {
          tool_name: "desktop_browser.browser_navigate",
          action: "browser_navigate",
          arguments_summary: { url: "https://example.com" },
        }),
      ],
    };

    expect(runtimeToastItems(state)).toEqual([]);
  });

  it("builds a review snapshot from the latest run after the active run is cleared", () => {
    const state: AppState = {
      sessions: [],
      activeSessionId: "session_1",
      activeRunId: "",
      runStatus: "completed",
      modelLabel: "gpt",
      messages: [],
      pendingDeleteSessionId: "",
      events: [
        runEvent(1, "tool.requested", {
          tool_name: "desktop_browser.browser_navigate",
          action: "browser_navigate",
          arguments_summary: { url: "https://old.example" },
        }),
        runEvent(
          2,
          "tool.requested",
          {
            tool_name: "desktop_browser.browser_navigate",
            action: "browser_navigate",
            arguments_summary: { url: "https://new.example" },
          },
          "run_2",
        ),
        runEvent(
          3,
          "tool.approval_required",
          {
            tool_name: "desktop_browser.browser_click_element",
            action: "browser_click_element",
            arguments_summary: { selector: "#submit", url: "https://new.example" },
          },
          "run_2",
        ),
        runEvent(
          4,
          "tool.requested",
          {
            tool_name: "command.run",
            action: "run",
            arguments_summary: { command: "npm test" },
          },
          "run_2",
        ),
      ],
    };

    const snapshot = runtimeReviewSnapshot(state);

    expect(snapshot.runId).toBe("run_2");
    expect(snapshot.runStatus).toBe("completed");
    expect(snapshot.counts).toEqual({ browser: 2, tool: 1, approval: 1, failed: 0, total: 3 });
    expect(snapshot.items.map((item) => item.title)).toEqual(["正在执行工具", "等待审批", "正在打开网页"]);
    expect(snapshot.latestAt).toBe("2026-07-03T00:00:04+08:00");
  });

  it("tracks pending approval requests until the matching approval is resolved", () => {
    const events = [
      runEvent(1, "approval.requested", {
        approval_id: "approval_1",
        prompt: "Approve browser click?",
        tool_name: "desktop_browser.browser_click_element",
        action: "browser_click_element",
        arguments_summary: {
          selector: "button.submit",
          url: "https://example.com/form",
          title: "Example Form",
        },
        risk: { level: "medium", summary: "will click a page element" },
      }),
      runEvent(2, "approval.requested", {
        approval_id: "approval_2",
        prompt: "Approve input change?",
        tool_name: "desktop_browser.browser_set_input_value",
        action: "browser_set_input_value",
        arguments_summary: { selector: "input[name=email]" },
      }),
    ];

    expect(pendingApprovalRequestsFromEvents(events)).toEqual([
      expect.objectContaining({
        approvalId: "approval_2",
        action: "browser_set_input_value",
        selector: "input[name=email]",
      }),
      expect.objectContaining({
        approvalId: "approval_1",
        prompt: "Approve browser click?",
        url: "https://example.com/form",
        title: "Example Form",
        selector: "button.submit",
        risk: "will click a page element",
      }),
    ]);

    const resolved = [
      ...events,
      runEvent(3, "approval.resolved", {
        approval_id: "approval_1",
        decision: "approve",
        status: "approved",
      }),
    ];
    const snapshot = runtimeReviewSnapshot({
      sessions: [],
      activeSessionId: "session_1",
      activeRunId: "run_1",
      runStatus: "running",
      modelLabel: "gpt",
      messages: [],
      pendingDeleteSessionId: "",
      events: resolved,
    });

    expect(pendingApprovalRequestsFromEvents(resolved)).toEqual([expect.objectContaining({ approvalId: "approval_2" })]);
    expect(snapshot.counts.approval).toBe(3);
    expect(snapshot.pendingApprovals).toEqual([expect.objectContaining({ approvalId: "approval_2" })]);
    expect(snapshot.items.map((item) => item.title)).toEqual(["审批已通过", "等待审批", "等待审批"]);
  });

  it("treats SDK completed tool events as finished even when older runtimes label them requested", () => {
    const activities = runtimeActivitiesFromEvents([
      runEvent(1, "tool.requested", {
        source_event_type: "ToolInvoked",
        event_type: "sdk_tool_end",
        tool_name: "browser_navigate",
        action: "navigate",
        status: "completed",
        decision: "completed",
        arguments_summary: { url: "https://example.com" },
      }),
    ]);

    expect(activities[0]).toMatchObject({
      kind: "browser",
      status: "done",
      title: "操作完成",
      detail: "https://example.com",
    });
  });
});

function runEvent(seq: number, type: string, payload: Record<string, unknown>, runId = "run_1"): RunEvent {
  return {
    schema_version: "run_event.v1",
    run_id: runId,
    session_id: "session_1",
    seq,
    type,
    created_at: `2026-07-03T00:00:0${seq}+08:00`,
    payload,
  };
}
