import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WorkAreaActivity, WorkAreaSnapshot } from "../appState";
import { createTranslator } from "../i18n";
import { WorkAreaPanel } from "./WorkAreaPanel";

describe("WorkAreaPanel", () => {
  it("renders the lead summary, parallel groups, tasks, and latest activity as an execution tree", () => {
    const html = renderToStaticMarkup(
      React.createElement(WorkAreaPanel, {
        t: createTranslator("zh"),
        snapshot: {
          runStatus: "running",
          routeType: "multi_agent",
          taskCount: 3,
          supervisorActivity: "planner split into 3 tasks",
          collapsedSummary: "2 tasks running",
          tasks: [
            {
              id: "analyze_runner",
              title: "Inspect multi_agent_runner.py flow",
              model: "deepseek-v4-pro",
              mcp: ["project_filesystem_readonly", "code_locator"],
              dependsOn: [],
              parallelGroup: "1",
              status: "running",
              latest: "Read runtime/execution/multi_agent_runner.py",
            },
            {
              id: "analyze_approval",
              title: "Inspect approval policy",
              model: "deepseek-v4-pro",
              mcp: ["project_filesystem_readonly", "code_locator"],
              dependsOn: [],
              parallelGroup: "1",
              status: "running",
              latest: "Read runtime/agent/approval_policy.py",
            },
            {
              id: "write_handoff",
              title: "Write handoff note",
              model: "gpt-5.5",
              mcp: ["workspace_edit", "code_locator"],
              dependsOn: ["analyze_runner", "analyze_approval"],
              parallelGroup: "2",
              status: "waiting",
              latest: "",
            },
          ],
        },
      }),
    );

    expect(html).toContain("planner split into 3 tasks");
    expect(html).toContain("Inspect multi_agent_runner.py flow");
    expect(html).toContain("Inspect approval policy");
    expect(html).toContain("3. Write handoff note");
    expect(html).toContain("analyze_runner, analyze_approval");
    expect(html).toContain("Read runtime/execution/multi_agent_runner.py");
  });

  it("opens while running and collapses after completion", () => {
    const runningHtml = renderToStaticMarkup(
      React.createElement(WorkAreaPanel, {
        t: createTranslator("zh"),
        snapshot: minimalSnapshot("running"),
      }),
    );
    const completedHtml = renderToStaticMarkup(
      React.createElement(WorkAreaPanel, {
        t: createTranslator("zh"),
        snapshot: minimalSnapshot("completed"),
      }),
    );

    expect(runningHtml).toContain("<details");
    expect(runningHtml).toContain('open=""');
    expect(completedHtml).toContain("<details");
    expect(completedHtml).not.toContain('open=""');
  });

  it("renders completed runs as a Codex-like processed summary row", () => {
    const html = renderToStaticMarkup(
      React.createElement(WorkAreaPanel, {
        t: createTranslator("zh"),
        snapshot: {
          ...minimalSnapshot("completed"),
          durationText: "1m 49s",
          processSummary: "已处理 1m 49s",
          tasks: [
            {
              ...minimalSnapshot("completed").tasks[0],
              activities: [
                {
                  id: "run_1_4_tool.requested",
                  kind: "tool",
                  status: "running",
                  title: "运行命令",
                  detail: "npm test -- WorkAreaPanel",
                  timestamp: "2026-07-03T10:00:10+08:00",
                },
              ],
            },
          ],
        },
      }),
    );

    expect(html).toContain("run-process-summary-row");
    expect(html).toContain("已处理 1m 49s");
    expect(html).toContain("运行命令");
    expect(html).toContain("npm test -- WorkAreaPanel");
  });

  it("folds long task activity lists and renders global activities", () => {
    const html = renderToStaticMarkup(
      React.createElement(WorkAreaPanel, {
        t: createTranslator("zh"),
        snapshot: {
          ...minimalSnapshot("running"),
          globalActivities: [
            {
              id: "run_1_2_tool.requested",
              kind: "browser",
              status: "running",
              title: "打开网页",
              detail: "https://example.com",
              timestamp: "2026-07-03T10:00:02+08:00",
            },
          ],
          tasks: [
            {
              ...minimalSnapshot("running").tasks[0],
              activities: [
                activity("1", "进度", "Reading README.md", "thinking"),
                activity("2", "读取文件", "README.md"),
                activity("3", "运行命令", "npm test"),
                activity("4", "读取文件", "runtime/server/app.py"),
                activity("5", "运行命令", "npm run build"),
              ],
            },
          ],
        },
      }),
    );

    expect(html).toContain("global-activity-list");
    expect(html).toContain("打开网页");
    expect(html).toContain("https://example.com");
    expect(html).toContain("worker-activity-overflow");
    expect(html).toContain("还有 2 条活动");
    expect(html).toContain("Reading README.md");
    expect(html).toContain("npm test");
    expect(html).toContain("npm run build");
  });
});

function minimalSnapshot(runStatus: WorkAreaSnapshot["runStatus"]): WorkAreaSnapshot {
  return {
    runStatus,
    routeType: "single_agent",
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
        status: runStatus === "running" ? "running" : "completed",
        latest: "done",
      },
    ],
  };
}

function activity(id: string, title: string, detail: string, kind: WorkAreaActivity["kind"] = "tool"): WorkAreaActivity {
  return {
    id,
    kind,
    status: "running" as const,
    title,
    detail,
    timestamp: "2026-07-03T10:00:10+08:00",
  };
}
