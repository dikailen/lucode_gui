import type { Translator } from "./i18n";
import type {
  ChatMessage,
  PersistedChatMessage,
  RenderedMessagePart,
  RunEvent,
  ModelSettingsResponse,
  RuntimeModel,
  ServerSession,
} from "../shared/types";
import { displayModelNameForModel } from "./modelDisplay";

export type RunStatus = "idle" | "running" | "completed" | "failed" | "cancelled";

export type AppState = {
  sessions: ServerSession[];
  activeSessionId: string;
  activeRunId: string;
  runStatus: RunStatus;
  modelLabel: string;
  messages: ChatMessage[];
  events: RunEvent[];
  pendingDeleteSessionId: string;
};

export type StageMeta = {
  label: string;
  tone: "idle" | "running" | "done" | "error" | "stopped";
  detail: string;
};

export type WorkAreaActivity = {
  id: string;
  kind: "thinking" | "tool" | "browser" | "approval" | "error";
  status: "running" | "waiting" | "done" | "failed";
  title: string;
  detail: string;
  timestamp: string;
};

export type WorkAreaTask = {
  id: string;
  title: string;
  model: string;
  mcp: string[];
  dependsOn: string[];
  parallelGroup: string;
  status: "waiting" | "running" | "completed" | "failed";
  latest: string;
  activities?: WorkAreaActivity[];
};

export type WorkAreaSnapshot = {
  runStatus: RunStatus;
  routeType: string;
  taskCount: number;
  supervisorActivity: string;
  globalActivities?: WorkAreaActivity[];
  collapsedSummary: string;
  processSummary?: string;
  durationText?: string;
  startedAt?: string;
  endedAt?: string;
  tasks: WorkAreaTask[];
};

export function createInitialAppState(): AppState {
  return {
    sessions: [],
    activeSessionId: "",
    activeRunId: "",
    runStatus: "idle",
    modelLabel: "\u672a\u9009\u62e9\u6a21\u578b",
    messages: [],
    events: [],
    pendingDeleteSessionId: "",
  };
}

export function selectPrimaryModelLabel(models: RuntimeModel[]): string {
  const configured = models.find((model) => model.configured) ?? models[0];
  if (!configured) {
    return "\u672a\u914d\u7f6e\u6a21\u578b";
  }
  return displayModelNameForModel(configured);
}

export function selectOrchestratorModelLabel(settings: ModelSettingsResponse, fallback = "\u672a\u914d\u7f6e\u6a21\u578b"): string {
  const selectedModelId = settings.roles.find((role) => role.role === "orchestrator")?.selected_model_id || "";
  if (!selectedModelId) {
    return fallback;
  }
  const selectedModel = settings.models.find((model) => model.id === selectedModelId);
  if (!selectedModel) {
    return selectedModelId;
  }
  return displayModelNameForModel(selectedModel);
}

export function activeSessionTitle(state: AppState, t?: Translator): string {
  const active = state.sessions.find((session) => session.session_id === state.activeSessionId);
  return active?.display_title || active?.title || (t ? t("chat.newChatTitle") : "\u65b0\u4f1a\u8bdd");
}

export function setSessions(state: AppState, sessions: ServerSession[]): AppState {
  const activeSessionId = sessions.some((session) => session.session_id === state.activeSessionId)
    ? state.activeSessionId
    : sessions[0]?.session_id || "";
  return { ...state, sessions, activeSessionId };
}

export function setActiveSession(state: AppState, sessionId: string): AppState {
  return { ...state, activeSessionId: sessionId, pendingDeleteSessionId: "" };
}

export function setSessionMessages(
  state: AppState,
  sessionId: string,
  messages: PersistedChatMessage[],
): AppState {
  return {
    ...state,
    activeSessionId: sessionId,
    activeRunId: "",
    runStatus: "idle",
    events: [],
    pendingDeleteSessionId: "",
    messages: messages.map((message, index) => ({
      id: `history_${sessionId}_${index}`,
      sessionId,
      role: message.role,
      content: message.content,
      status: "completed",
    })),
  };
}

export function removeSession(state: AppState, sessionId: string): AppState {
  const sessions = state.sessions.filter((session) => session.session_id !== sessionId);
  const removingActive = state.activeSessionId === sessionId;
  const activeSessionId = removingActive ? sessions[0]?.session_id || "" : state.activeSessionId;
  return {
    ...state,
    sessions,
    activeSessionId,
    activeRunId: removingActive ? "" : state.activeRunId,
    runStatus: removingActive ? "idle" : state.runStatus,
    events: removingActive ? [] : state.events,
    pendingDeleteSessionId: state.pendingDeleteSessionId === sessionId ? "" : state.pendingDeleteSessionId,
    messages: removingActive ? [] : state.messages.filter((message) => message.sessionId !== sessionId),
  };
}

export function markPendingDeleteSession(state: AppState, sessionId: string): AppState {
  return {
    ...state,
    pendingDeleteSessionId: state.pendingDeleteSessionId === sessionId ? "" : sessionId,
  };
}

export function setModelLabel(state: AppState, label: string): AppState {
  return { ...state, modelLabel: label || "\u672a\u914d\u7f6e\u6a21\u578b" };
}

export function appendUserMessage(state: AppState, sessionId: string, content: string): AppState {
  const message: ChatMessage = {
    id: nextMessageId("user"),
    sessionId,
    role: "user",
    content: content.trim(),
    status: "completed",
  };
  return {
    ...state,
    activeSessionId: sessionId,
    pendingDeleteSessionId: "",
    messages: [...state.messages, message],
  };
}

export function markRunStarted(state: AppState, sessionId: string, runId: string): AppState {
  return {
    ...state,
    activeSessionId: sessionId,
    activeRunId: runId,
    runStatus: "running",
    pendingDeleteSessionId: "",
  };
}

export function markRunStreamDisconnected(state: AppState, sessionId: string, reason: string): AppState {
  if (state.runStatus !== "running") {
    return state;
  }
  return {
    ...state,
    activeRunId: "",
    runStatus: "failed",
    messages: [
      ...state.messages,
      systemMessage(
        sessionId || state.activeSessionId,
        `\u4e8b\u4ef6\u6d41\u8fde\u63a5\u4e2d\u65ad\uff1a${reason || "\u8fde\u63a5\u5df2\u5173\u95ed"}\u3002\u8bf7\u67e5\u770b Runtime \u65e5\u5fd7\u6216\u91cd\u65b0\u53d1\u9001\u4efb\u52a1\u3002`,
        "failed",
      ),
    ],
  };
}

export function runStageLabel(state: AppState): string {
  return runStageMeta(state).label;
}

export function runStageMeta(state: AppState, t?: Translator): StageMeta {
  if (state.runStatus === "idle") {
    return stage(t, "runStage.idleLabel", "\u7a7a\u95f2", "idle", "runStage.idleDetail", "\u7b49\u5f85\u8f93\u5165\u4efb\u52a1");
  }
  if (state.runStatus === "completed") {
    return stage(t, "runStage.completedLabel", "\u5df2\u5b8c\u6210", "done", "runStage.completedDetail", "\u672c\u8f6e\u56de\u7b54\u5df2\u751f\u6210");
  }
  if (state.runStatus === "failed") {
    return stage(t, "runStage.failedLabel", "\u5931\u8d25", "error", "runStage.failedDetail", "\u8bf7\u67e5\u770b\u9519\u8bef\u4fe1\u606f");
  }
  if (state.runStatus === "cancelled") {
    return stage(t, "runStage.cancelledLabel", "\u5df2\u505c\u6b62", "stopped", "runStage.cancelledDetail", "\u7528\u6237\u5df2\u53d6\u6d88\u8fd0\u884c");
  }
  const lastType = state.events.at(-1)?.type || "";
  if (!lastType || lastType === "run.started") {
    return stage(t, "runStage.startingLabel", "\u542f\u52a8\u4e2d", "running", "runStage.startingDetail", "\u6b63\u5728\u521b\u5efa\u8fd0\u884c\u4e0a\u4e0b\u6587");
  }
  if (lastType.startsWith("planner.")) {
    return stage(t, "runStage.planningLabel", "\u89c4\u5212\u4e2d", "running", "runStage.planningDetail", "\u4e3b\u8111\u6b63\u5728\u5224\u65ad\u4efb\u52a1\u8def\u7ebf");
  }
  if (lastType.startsWith("audit.")) {
    return stage(t, "runStage.auditLabel", "\u5ba1\u67e5\u4e2d", "running", "runStage.auditDetail", "\u6b63\u5728\u68c0\u67e5\u7ed3\u679c\u8d28\u91cf");
  }
  if (lastType.startsWith("tool.")) {
    return stage(t, "runStage.toolLabel", "\u5de5\u5177\u8c03\u7528\u4e2d", "running", "runStage.toolDetail", "\u6b63\u5728\u7b49\u5f85\u5de5\u5177\u6216\u5ba1\u6279\u7ed3\u679c");
  }
  if (lastType.startsWith("task.") || lastType === "worker.delta") {
    return stage(t, "runStage.executingLabel", "\u6267\u884c\u4e2d", "running", "runStage.executingDetail", "Worker \u6b63\u5728\u5904\u7406\u4efb\u52a1");
  }
  return stage(t, "runStage.runningLabel", "\u8fd0\u884c\u4e2d", "running", "runStage.runningDetail", "Agent Loop \u6b63\u5728\u5de5\u4f5c");
}

export function recentRunEvents(state: AppState, limit = 4): RunEvent[] {
  return state.events.filter((event) => event.type !== "worker.delta").slice(-Math.max(1, limit));
}

export function workAreaSnapshot(state: AppState, t?: Translator): WorkAreaSnapshot | null {
  const planEvent = [...state.events].reverse().find((event) => {
    const tasks = event.payload?.tasks;
    return event.type === "planner.completed" && Array.isArray(tasks) && tasks.length > 0;
  });
  if (!planEvent) {
    return null;
  }
  const routeType = stringValue(planEvent.payload.route_type) || "task_graph";
  const tasks = normalizePlanTasks(planEvent.payload.tasks);
  if (!tasks.length) {
    return null;
  }
  const taskById = new Map(tasks.map((task) => [task.id, task]));
  const runEvents = state.events.filter((event) => event.run_id === planEvent.run_id);
  const startedAt = runStartTime(runEvents);
  const endedAt = runEndTime(state.runStatus, runEvents);
  const durationText = formatRunDuration(startedAt, endedAt || runEvents.at(-1)?.created_at || "");
  let supervisorActivity = "";
  const globalActivities: WorkAreaActivity[] = [];
  for (const event of runEvents.filter((item) => item.seq >= planEvent.seq)) {
    const taskId = taskIdFromEvent(event);
    if (taskId && taskById.has(taskId)) {
      const task = taskById.get(taskId)!;
      if (event.type === "task.started") {
        task.status = "running";
      } else if (event.type === "task.completed") {
        task.status = "completed";
      } else if (event.type === "task.failed") {
        task.status = "failed";
      } else if (event.type === "worker.delta") {
        task.status = task.status === "waiting" ? "running" : task.status;
        task.latest = truncateLine(textPayload(event, "text") || textPayload(event, "message") || task.latest);
        const activity = workerProgressActivityFromEvent(event);
        if (activity) {
          task.activities = [...(task.activities ?? []), activity];
        }
      } else if (event.type.startsWith("tool.")) {
        const activity = workAreaActivityFromEvent(event);
        if (activity) {
          task.activities = [...(task.activities ?? []), activity];
          task.latest = activity.detail ? `${activity.title}: ${activity.detail}` : activity.title;
        } else {
          task.latest = eventDetail(event, t) || eventLabel(event, t);
        }
      }
    } else if (event.type.startsWith("tool.")) {
      const activity = workAreaActivityFromEvent(event);
      if (activity) {
        globalActivities.push(activity);
        supervisorActivity = activity.detail ? `${activity.title}: ${activity.detail}` : activity.title;
      }
    } else if (event.type.startsWith("audit.") || event.type.startsWith("planner.")) {
      supervisorActivity = eventDetail(event, t) || eventLabel(event, t);
    }
  }
  return {
    runStatus: state.runStatus,
    routeType,
    taskCount: tasks.length,
    supervisorActivity,
    globalActivities,
    collapsedSummary: workAreaSummary(state, tasks, t),
    processSummary: processSummary(state.runStatus, durationText, t),
    durationText,
    startedAt,
    endedAt,
    tasks,
  };
}

export function eventLabel(event: RunEvent, t?: Translator): string {
  if (event.type === "run.started") {
    return translate(t, "runEvent.started", "\u542f\u52a8\u8fd0\u884c");
  }
  if (event.type === "run.completed") {
    return translate(t, "runEvent.completed", "\u5b8c\u6210\u56de\u7b54");
  }
  if (event.type === "run.failed") {
    return translate(t, "runEvent.failed", "\u8fd0\u884c\u5931\u8d25");
  }
  if (event.type === "run.cancelled") {
    return translate(t, "runEvent.cancelled", "\u5df2\u505c\u6b62");
  }
  if (event.type.startsWith("planner.")) {
    return event.type.endsWith("completed")
      ? translate(t, "runEvent.plannerCompleted", "\u89c4\u5212\u5b8c\u6210")
      : event.type.endsWith("failed")
        ? translate(t, "runEvent.plannerFailed", "\u89c4\u5212\u5931\u8d25")
        : translate(t, "runEvent.planner", "\u4e3b\u8111\u89c4\u5212");
  }
  if (event.type.startsWith("task.")) {
    return event.type.endsWith("completed")
      ? translate(t, "runEvent.taskCompleted", "\u4efb\u52a1\u5b8c\u6210")
      : event.type.endsWith("failed")
        ? translate(t, "runEvent.taskFailed", "\u4efb\u52a1\u5931\u8d25")
        : translate(t, "runEvent.task", "\u4efb\u52a1\u6267\u884c");
  }
  if (event.type.startsWith("tool.")) {
    return translate(t, "runEvent.tool", "\u5de5\u5177\u8c03\u7528");
  }
  if (event.type.startsWith("audit.")) {
    return translate(t, "runEvent.audit", "\u7ed3\u679c\u5ba1\u67e5");
  }
  return event.type;
}

export function eventDetail(event: RunEvent, t?: Translator): string {
  const payload = event.payload || {};
  const routeType = stringValue(payload.route_type);
  const taskTitle = stringValue(payload.title) || stringValue(payload.task_title);
  const agent = stringValue(payload.agent);
  const message = stringValue(payload.message);
  const error = stringValue(payload.error);
  if (routeType) {
    return t ? t("runEvent.route", { route: routeType }) : `\u8def\u7ebf\uff1a${routeType}`;
  }
  if (taskTitle) {
    return taskTitle;
  }
  if (agent) {
    return agent;
  }
  if (error) {
    return error;
  }
  if (message && message.length <= 72) {
    return message;
  }
  return "";
}

export function renderMessageParts(content: string): RenderedMessagePart[] {
  const text = String(content || "");
  if (!text.includes("```")) {
    return [{ type: "text", content: text }];
  }
  const parts: RenderedMessagePart[] = [];
  const pattern = /```([A-Za-z0-9_-]*)\n?([\s\S]*?)```/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const before = text.slice(cursor, match.index);
    if (before) {
      parts.push({ type: "text", content: before });
    }
    parts.push({
      type: "code",
      language: match[1] || "",
      content: match[2] || "",
    });
    cursor = match.index + match[0].length;
  }
  const after = text.slice(cursor);
  if (after) {
    parts.push({ type: "text", content: after });
  }
  return parts.length ? parts : [{ type: "text", content: text }];
}

export function reduceRunEvent(state: AppState, event: RunEvent): AppState {
  const base = {
    ...state,
    activeSessionId: event.session_id || state.activeSessionId,
    activeRunId: event.run_id || state.activeRunId,
    events: [...state.events, event],
  };

  if (event.type === "run.started") {
    return { ...base, runStatus: "running" };
  }

  if (event.type === "worker.delta") {
    const text = textPayload(event, "text") || textPayload(event, "message");
    if (!text) {
      return base;
    }
    if (taskIdFromEvent(event)) {
      return { ...base, runStatus: "running" };
    }
    return {
      ...base,
      runStatus: "running",
      messages: mergeAssistantMessage(base.messages, event.session_id, text, "streaming", true),
    };
  }

  if (event.type === "run.completed") {
    const finalOutput = textPayload(event, "final_output");
    return {
      ...base,
      activeRunId: "",
      runStatus: "completed",
      messages: finalOutput
        ? mergeAssistantMessage(base.messages, event.session_id, finalOutput, "completed", false)
        : markLastAssistant(base.messages, "completed"),
    };
  }

  if (event.type === "run.failed") {
    const error = textPayload(event, "error") || textPayload(event, "message") || "\u672a\u77e5\u9519\u8bef";
    return {
      ...base,
      activeRunId: "",
      runStatus: "failed",
      messages: [...base.messages, systemMessage(event.session_id, `\u8fd0\u884c\u5931\u8d25\uff1a${error}`, "failed")],
    };
  }

  if (event.type === "run.cancelled") {
    return {
      ...base,
      activeRunId: "",
      runStatus: "cancelled",
      messages: [...base.messages, systemMessage(event.session_id, "\u5df2\u505c\u6b62\u5f53\u524d\u8fd0\u884c\u3002", "cancelled")],
    };
  }

  return base;
}

export function formatSessionTime(value: string, now: Date = new Date(), t?: Translator): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return translate(t, "time.justNow", "\u521a\u521a");
  }
  const diffMs = Math.max(0, now.getTime() - timestamp);
  const diffMinutes = Math.floor(diffMs / 60000);
  if (diffMinutes < 1) {
    return translate(t, "time.justNow", "\u521a\u521a");
  }
  if (diffMinutes < 60) {
    return translate(t, "time.minutesAgo", `${diffMinutes}\u5206\u949f\u524d`, { count: diffMinutes });
  }
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) {
    return translate(t, "time.hoursAgo", `${diffHours}\u5c0f\u65f6\u524d`, { count: diffHours });
  }
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) {
    return translate(t, "time.yesterday", "\u6628\u5929");
  }
  if (diffDays < 7) {
    return translate(t, "time.daysAgo", `${diffDays}\u5929\u524d`, { count: diffDays });
  }
  return new Intl.DateTimeFormat(t ? "en-US" : "zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestamp));
}

function mergeAssistantMessage(
  messages: ChatMessage[],
  sessionId: string,
  content: string,
  status: ChatMessage["status"],
  append: boolean,
): ChatMessage[] {
  const index = lastAssistantIndex(messages, sessionId);
  if (index < 0) {
    return [
      ...messages,
      {
        id: nextMessageId("assistant"),
        sessionId,
        role: "assistant",
        content,
        status,
      },
    ];
  }
  return messages.map((message, currentIndex) => {
    if (currentIndex !== index) {
      return message;
    }
    return {
      ...message,
      content: append ? `${message.content}${content}` : content,
      status,
    };
  });
}

function markLastAssistant(messages: ChatMessage[], status: ChatMessage["status"]): ChatMessage[] {
  const index = lastAssistantIndex(messages, "");
  if (index < 0) {
    return messages;
  }
  return messages.map((message, currentIndex) => (currentIndex === index ? { ...message, status } : message));
}

function lastAssistantIndex(messages: ChatMessage[], sessionId: string): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && (!sessionId || message.sessionId === sessionId)) {
      return index;
    }
  }
  return -1;
}

function systemMessage(sessionId: string, content: string, status: ChatMessage["status"]): ChatMessage {
  return {
    id: nextMessageId("system"),
    sessionId,
    role: "system",
    content,
    status,
  };
}

function textPayload(event: RunEvent, key: string): string {
  const value = event.payload[key];
  return typeof value === "string" ? value : "";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function taskIdFromEvent(event: RunEvent): string {
  return stringValue(event.payload?.task_id) || stringValue(event.payload?.id);
}

function workerProgressActivityFromEvent(event: RunEvent): WorkAreaActivity | null {
  if (event.type !== "worker.delta") {
    return null;
  }
  const detail = truncateLine(textPayload(event, "text") || textPayload(event, "message"), 140);
  if (!detail) {
    return null;
  }
  return {
    id: `${event.run_id}_${event.seq}_${event.type}`,
    kind: "thinking",
    status: "running",
    title: "进度",
    detail,
    timestamp: event.created_at,
  };
}

function workAreaActivityFromEvent(event: RunEvent): WorkAreaActivity | null {
  if (!event.type.startsWith("tool.")) {
    return null;
  }
  const payload = event.payload || {};
  const args = objectValue(payload.arguments_summary);
  const toolName = stringValue(payload.tool_name) || stringValue(payload.tool);
  const action = stringValue(payload.action) || actionFromToolName(toolName);
  const status = toolActivityStatus(event);
  const detail = activityDetail(args, payload);
  const isBrowser = isBrowserAction(toolName, action);
  return {
    id: `${event.run_id}_${event.seq}_${event.type}`,
    kind: event.type === "tool.approval_required" ? "approval" : isBrowser ? "browser" : "tool",
    status,
    title: activityTitle({ toolName, action, detail, status, eventType: event.type, isBrowser }),
    detail,
    timestamp: event.created_at,
  };
}

function activityTitle({
  toolName,
  action,
  detail,
  status,
  eventType,
  isBrowser,
}: {
  toolName: string;
  action: string;
  detail: string;
  status: WorkAreaActivity["status"];
  eventType: string;
  isBrowser: boolean;
}): string {
  if (eventType === "tool.approval_required" || status === "waiting") {
    return isBrowser ? "等待浏览器审批" : "等待工具审批";
  }
  if (status === "failed") {
    return isBrowser ? "浏览器操作失败" : "工具调用失败";
  }
  if (isBrowser) {
    if (action === "browser_navigate") {
      return "打开网页";
    }
    if (action === "browser_get_page_summary") {
      return "读取页面";
    }
    if (action === "browser_click_element") {
      return "点击页面";
    }
    if (action === "browser_set_input_value") {
      return "填写页面";
    }
    if (action === "browser_submit_form") {
      return "提交表单";
    }
    return "浏览器操作";
  }
  const haystack = `${toolName} ${action}`.toLowerCase();
  if (detail && haystack.includes("command")) {
    return "运行命令";
  }
  if (detail && /(read|filesystem|file|path)/.test(haystack)) {
    return "读取文件";
  }
  if (detail && /(write|edit|patch|delete|create)/.test(haystack)) {
    return "修改文件";
  }
  return status === "done" ? "工具调用完成" : "执行工具";
}

function toolActivityStatus(event: RunEvent): WorkAreaActivity["status"] {
  const marker = `${event.type} ${stringValue(event.payload.status)} ${stringValue(event.payload.outcome)} ${stringValue(
    event.payload.decision,
  )}`.toLowerCase();
  if (/(failed|error|reject|denied)/.test(marker)) {
    return "failed";
  }
  if (event.type === "tool.approval_required" || /(approval|required|waiting|pending)/.test(marker)) {
    return "waiting";
  }
  if (event.type === "tool.completed" || /(completed|success|approved)/.test(marker)) {
    return "done";
  }
  return "running";
}

function activityDetail(args: Record<string, unknown>, payload: Record<string, unknown>): string {
  return (
    stringValue(args.command) ||
    stringValue(args.url) ||
    stringValue(args.selector) ||
    stringValue(args.path) ||
    stringValue(args.target_path) ||
    stringValue(args.file_path) ||
    stringValue(args.tab_id) ||
    stringValue(args.message) ||
    stringValue(payload.message)
  );
}

function isBrowserAction(toolName: string, action: string): boolean {
  const haystack = `${toolName} ${action}`.toLowerCase();
  return haystack.includes("desktop_browser") || haystack.includes("browser_");
}

function actionFromToolName(toolName: string): string {
  const clean = toolName.trim();
  if (!clean) {
    return "";
  }
  return clean.includes(".") ? clean.split(".").at(-1) || "" : clean;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function normalizePlanTasks(value: unknown): WorkAreaTask[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item, index) => {
      const id = stringValue(item.id) || `task_${index + 1}`;
      return {
        id,
        title: stringValue(item.title) || id,
        model: stringValue(item.model),
        mcp: stringList(item.mcp),
        dependsOn: stringList(item.depends_on),
        parallelGroup: stringValue(item.parallel_group),
        status: "waiting",
        latest: "",
        activities: [],
      };
    });
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function truncateLine(value: string, limit = 96): string {
  const line = String(value || "").trim().split(/\r?\n/).at(-1) || "";
  return line.length > limit ? `${line.slice(0, limit - 1)}...` : line;
}

function workAreaSummary(state: AppState, tasks: WorkAreaTask[], t?: Translator): string {
  if (state.runStatus === "running") {
    const running = tasks.filter((task) => task.status === "running").length;
    return running
      ? translate(t, "workArea.runningSummary", `${running} \u4e2a\u4efb\u52a1\u8fd0\u884c\u4e2d`, { count: running })
      : translate(t, "workArea.waitingSummary", `${tasks.length} \u4e2a\u4efb\u52a1\u7b49\u5f85\u6267\u884c`, { count: tasks.length });
  }
  const completed = tasks.filter((task) => task.status === "completed").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  if (failed) {
    return translate(t, "workArea.failedSummary", `${completed}/${tasks.length} \u5b8c\u6210\uff0c${failed} \u5931\u8d25`, {
      completed,
      total: tasks.length,
      failed,
    });
  }
  if (state.runStatus === "cancelled") {
    return translate(t, "workArea.cancelledSummary", "\u5df2\u505c\u6b62");
  }
  return translate(t, "workArea.completedSummary", `${tasks.length} \u4e2a\u4efb\u52a1\u5b8c\u6210`, { count: tasks.length });
}

function processSummary(status: RunStatus, durationText: string, t?: Translator): string {
  const duration = durationText || "0s";
  if (status === "completed") {
    return translate(t, "workArea.processCompleted", `已处理 ${duration}`, { duration });
  }
  if (status === "failed") {
    return translate(t, "workArea.processFailed", `处理失败 ${duration}`, { duration });
  }
  if (status === "cancelled") {
    return translate(t, "workArea.processCancelled", `已停止 ${duration}`, { duration });
  }
  if (status === "running") {
    return translate(t, "workArea.processRunning", `处理中 ${duration}`, { duration });
  }
  return translate(t, "workArea.processIdle", "执行过程", { duration });
}

function runStartTime(events: RunEvent[]): string {
  return events.find((event) => event.type === "run.started")?.created_at || events[0]?.created_at || "";
}

function runEndTime(status: RunStatus, events: RunEvent[]): string {
  if (!["completed", "failed", "cancelled"].includes(status)) {
    return "";
  }
  return [...events].reverse().find((event) => /^run\.(completed|failed|cancelled)$/.test(event.type))?.created_at || "";
}

function formatRunDuration(startedAt: string, endedAt: string): string {
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return "0s";
  }
  const totalSeconds = Math.max(0, Math.round((end - start) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

function stage(
  t: Translator | undefined,
  labelKey: Parameters<Translator>[0],
  fallbackLabel: string,
  tone: StageMeta["tone"],
  detailKey: Parameters<Translator>[0],
  fallbackDetail: string,
): StageMeta {
  return {
    label: translate(t, labelKey, fallbackLabel),
    tone,
    detail: translate(t, detailKey, fallbackDetail),
  };
}

function translate(
  t: Translator | undefined,
  key: Parameters<Translator>[0],
  fallback: string,
  values?: Record<string, string | number>,
): string {
  return t ? t(key, values) : fallback;
}

function nextMessageId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}
