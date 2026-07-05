import type { AppState } from "./appState";
import type { RunEvent } from "../shared/types";

export type RuntimeActivityKind = "browser" | "tool" | "approval" | "error";
export type RuntimeActivityStatus = "running" | "waiting" | "done" | "failed";

export type RuntimeActivityItem = {
  id: string;
  runId: string;
  kind: RuntimeActivityKind;
  status: RuntimeActivityStatus;
  title: string;
  detail: string;
  url: string;
  selector: string;
  timestamp: string;
  approvalId?: string;
  prompt?: string;
  action?: string;
  risk?: string;
};

export type RuntimeReviewSnapshot = {
  runId: string;
  runStatus: AppState["runStatus"];
  latestAt: string;
  counts: {
    browser: number;
    tool: number;
    approval: number;
    failed: number;
    total: number;
  };
  pendingApprovals: RuntimeApprovalRequest[];
  items: RuntimeActivityItem[];
};

export type RuntimeApprovalRequest = {
  approvalId: string;
  runId: string;
  prompt: string;
  toolName: string;
  action: string;
  url: string;
  title: string;
  selector: string;
  risk: string;
  timestamp: string;
};

export function runtimeToastItems(state: AppState, limit = 3): RuntimeActivityItem[] {
  if (state.runStatus !== "running" || !state.activeRunId) {
    return [];
  }
  return runtimeActivitiesFromEvents(
    state.events.filter((event) => event.run_id === state.activeRunId),
    limit,
  );
}

export function runtimeReviewSnapshot(state: AppState, limit = 80): RuntimeReviewSnapshot {
  const runId = state.activeRunId || latestRunId(state.events);
  const events = runId ? state.events.filter((event) => event.run_id === runId) : [];
  const items = runtimeActivitiesFromEvents(events, limit);
  return {
    runId,
    runStatus: state.runStatus,
    latestAt: events.at(-1)?.created_at || "",
    counts: {
      browser: items.filter((item) => item.kind === "browser").length,
      tool: items.filter((item) => item.kind === "tool").length,
      approval: items.filter((item) => item.status === "waiting" || item.kind === "approval").length,
      failed: items.filter((item) => item.status === "failed").length,
      total: items.length,
    },
    pendingApprovals: pendingApprovalRequestsFromEvents(events),
    items,
  };
}

export function runtimeActivitiesFromEvents(events: RunEvent[], limit = 12): RuntimeActivityItem[] {
  const items = events
    .map(activityFromEvent)
    .filter((item): item is RuntimeActivityItem => Boolean(item))
    .reverse();
  return items.slice(0, Math.max(1, limit));
}

export function pendingApprovalRequestsFromEvents(events: RunEvent[]): RuntimeApprovalRequest[] {
  const pending = new Map<string, RuntimeApprovalRequest>();
  for (const event of events) {
    const approvalId = stringField(event.payload, "approval_id");
    if (!approvalId) {
      continue;
    }
    if (event.type === "approval.requested") {
      pending.set(approvalId, approvalRequestFromEvent(event, approvalId));
      continue;
    }
    if (event.type === "approval.resolved") {
      pending.delete(approvalId);
    }
  }
  return [...pending.values()].reverse();
}

function activityFromEvent(event: RunEvent): RuntimeActivityItem | null {
  if (event.type === "approval.requested" || event.type === "approval.resolved") {
    return approvalActivityFromEvent(event);
  }
  if (event.type.startsWith("tool.")) {
    return toolActivityFromEvent(event);
  }
  if (event.type === "run.failed") {
    return {
      id: activityId(event),
      runId: event.run_id,
      kind: "error",
      status: "failed",
      title: "运行失败",
      detail: stringField(event.payload, "error") || stringField(event.payload, "message"),
      url: "",
      selector: "",
      timestamp: event.created_at,
    };
  }
  return null;
}

function approvalActivityFromEvent(event: RunEvent): RuntimeActivityItem {
  const payload = event.payload || {};
  const args = objectField(payload, "arguments_summary");
  const toolName = stringField(payload, "tool_name") || stringField(payload, "tool");
  const action = stringField(payload, "action") || actionFromToolName(toolName);
  const status = event.type === "approval.requested" ? "waiting" : approvalResolvedStatus(payload);
  return {
    id: activityId(event),
    runId: event.run_id,
    kind: "approval",
    status,
    title: approvalTitle(action, status),
    detail:
      stringField(args, "selector") ||
      stringField(args, "url") ||
      stringField(payload, "prompt") ||
      stringField(payload, "preview"),
    url: stringField(args, "url") || stringField(payload, "url"),
    selector: stringField(args, "selector") || stringField(payload, "selector"),
    timestamp: event.created_at,
    approvalId: stringField(payload, "approval_id"),
    prompt: stringField(payload, "prompt"),
    action,
    risk: riskSummary(objectField(payload, "risk")),
  };
}

function toolActivityFromEvent(event: RunEvent): RuntimeActivityItem {
  const payload = event.payload || {};
  const toolName = stringField(payload, "tool_name") || stringField(payload, "tool");
  const action = stringField(payload, "action") || actionFromToolName(toolName);
  const args = objectField(payload, "arguments_summary");
  const isBrowser = isBrowserTool(toolName, action);
  const status = statusFromToolEvent(event);
  const detail = browserDetail(action, args) || toolDetail(toolName, args);
  return {
    id: activityId(event),
    runId: event.run_id,
    kind: isBrowser ? "browser" : event.type === "tool.approval_required" ? "approval" : "tool",
    status,
    title: isBrowser ? browserTitle(action, status, event.type) : toolTitle(status, event.type),
    detail,
    url: stringField(args, "url"),
    selector: stringField(args, "selector"),
    timestamp: event.created_at,
  };
}

function approvalRequestFromEvent(event: RunEvent, approvalId: string): RuntimeApprovalRequest {
  const payload = event.payload || {};
  const args = objectField(payload, "arguments_summary");
  const toolName = stringField(payload, "tool_name") || stringField(payload, "tool");
  return {
    approvalId,
    runId: event.run_id,
    prompt: stringField(payload, "prompt"),
    toolName,
    action: stringField(payload, "action") || actionFromToolName(toolName),
    url: stringField(args, "url") || stringField(payload, "url"),
    title: stringField(args, "title") || stringField(payload, "title"),
    selector: stringField(args, "selector") || stringField(payload, "selector"),
    risk: riskSummary(objectField(payload, "risk")),
    timestamp: event.created_at,
  };
}

function approvalResolvedStatus(payload: Record<string, unknown>): RuntimeActivityStatus {
  const status = `${stringField(payload, "status")} ${stringField(payload, "decision")}`.toLowerCase();
  if (/(reject|rejected|cancelled|denied|failed|error)/.test(status)) {
    return "failed";
  }
  return "done";
}

function approvalTitle(_action: string, status: RuntimeActivityStatus): string {
  if (status === "waiting") {
    return "等待审批";
  }
  if (status === "failed") {
    return "审批已拒绝";
  }
  return "审批已通过";
}

function statusFromToolEvent(event: RunEvent): RuntimeActivityStatus {
  const outcome = `${stringField(event.payload, "outcome")} ${stringField(event.payload, "decision")} ${stringField(
    event.payload,
    "status",
  )}`.toLowerCase();
  if (/(reject|denied|failed|error)/.test(outcome) || event.type.includes("failed")) {
    return "failed";
  }
  if (event.type === "tool.approval_required") {
    return "waiting";
  }
  if (event.type === "tool.completed" || /(completed|success|approved)/.test(outcome)) {
    return "done";
  }
  return "running";
}

function browserTitle(action: string, status: RuntimeActivityStatus, eventType: string): string {
  if (status === "waiting" || eventType === "tool.approval_required") {
    return "等待审批";
  }
  if (status === "failed") {
    return "操作失败";
  }
  if (status === "done") {
    return "操作完成";
  }
  if (action === "browser_navigate") {
    return "正在打开网页";
  }
  if (action === "browser_get_page_summary") {
    return "正在读取页面";
  }
  if (action === "browser_list_tabs") {
    return "正在读取标签";
  }
  if (action === "browser_click_element") {
    return "正在点击";
  }
  if (action === "browser_set_input_value") {
    return "正在填写";
  }
  if (action === "browser_submit_form") {
    return "正在提交";
  }
  return "正在操作浏览器";
}

function toolTitle(status: RuntimeActivityStatus, eventType: string): string {
  if (eventType === "tool.approval_required" || status === "waiting") {
    return "等待审批";
  }
  if (status === "done") {
    return "工具完成";
  }
  if (status === "failed") {
    return "工具失败";
  }
  return "正在执行工具";
}

function browserDetail(action: string, args: Record<string, unknown>): string {
  if (action === "browser_navigate") {
    return stringField(args, "url") || stringField(args, "tab_id");
  }
  if (action === "browser_get_page_summary") {
    return stringField(args, "url") || stringField(args, "tab_id");
  }
  if (
    action === "browser_click_element" ||
    action === "browser_set_input_value" ||
    action === "browser_submit_form"
  ) {
    return stringField(args, "selector") || stringField(args, "url") || stringField(args, "tab_id");
  }
  return stringField(args, "url") || stringField(args, "selector") || stringField(args, "tab_id");
}

function toolDetail(toolName: string, args: Record<string, unknown>): string {
  return (
    stringField(args, "command") ||
    stringField(args, "path") ||
    stringField(args, "target_path") ||
    stringField(args, "file_path") ||
    stringField(args, "message") ||
    toolName
  );
}

function riskSummary(risk: Record<string, unknown>): string {
  return (
    stringField(risk, "summary") ||
    stringField(risk, "reason") ||
    stringField(risk, "level") ||
    stringField(risk, "risk_level")
  );
}

function isBrowserTool(toolName: string, action: string): boolean {
  const haystack = `${toolName} ${action}`.toLowerCase();
  return haystack.includes("desktop_browser") || haystack.includes("browser_");
}

function actionFromToolName(toolName: string): string {
  const clean = String(toolName || "").trim();
  if (!clean) {
    return "";
  }
  return clean.includes(".") ? clean.split(".").at(-1) || "" : clean;
}

function activityId(event: RunEvent): string {
  return `${event.run_id}_${event.seq}_${event.type}`;
}

function latestRunId(events: RunEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const runId = events[index].run_id;
    if (runId) {
      return runId;
    }
  }
  return "";
}

function objectField(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value.trim() : "";
}
