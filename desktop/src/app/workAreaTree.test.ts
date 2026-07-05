import { describe, expect, it } from "vitest";

import type { WorkAreaTask } from "./appState";
import { buildWorkAreaGroups } from "./workAreaTree";

function task(input: Partial<WorkAreaTask> & Pick<WorkAreaTask, "id" | "title">): WorkAreaTask {
  return {
    id: input.id,
    title: input.title,
    model: input.model ?? "",
    mcp: input.mcp ?? [],
    dependsOn: input.dependsOn ?? [],
    parallelGroup: input.parallelGroup ?? "",
    status: input.status ?? "waiting",
    latest: input.latest ?? "",
  };
}

describe("work area tree grouping", () => {
  it("groups tasks by parallel batch while preserving planner order", () => {
    const groups = buildWorkAreaGroups([
      task({
        id: "analyze_runner",
        title: "梳理 multi_agent_runner.py 的收口与返回流程",
        model: "deepseek-v4-pro",
        mcp: ["project_filesystem_readonly", "code_locator"],
        parallelGroup: "1",
      }),
      task({
        id: "analyze_approval",
        title: "梳理 approval_policy.py 的审批判定逻辑",
        model: "deepseek-v4-pro",
        mcp: ["project_filesystem_readonly", "code_locator"],
        parallelGroup: "1",
      }),
      task({
        id: "write_handoff",
        title: "编写衔接说明文档",
        model: "gpt-5.5",
        mcp: ["workspace_edit", "code_locator"],
        parallelGroup: "2",
        dependsOn: ["analyze_runner", "analyze_approval"],
      }),
    ]);

    expect(groups.map((group) => group.parallelGroup)).toEqual(["1", "2"]);
    expect(groups[0].tasks.map((item) => item.id)).toEqual(["analyze_runner", "analyze_approval"]);
    expect(groups[1].tasks.map((item) => item.id)).toEqual(["write_handoff"]);
  });
});
