import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RuntimeActivityItem } from "../runtimeActivity";
import { RuntimeToastStack } from "./RuntimeToastStack";

describe("RuntimeToastStack", () => {
  it("renders at most two centered status rows without staggered levels", () => {
    const html = renderToStaticMarkup(
      React.createElement(RuntimeToastStack, {
        items: [
          activity("1", "正在读取页面", "https://example.com/form"),
          activity("2", "等待审批", "button.submit", "waiting"),
          activity("3", "正在执行工具", "desktop_browser.browser_get_page_summary"),
        ],
        onOpenReview: () => undefined,
      }),
    );

    expect(html).toContain('aria-label="打开运行审查"');
    expect(html.match(/runtime-toast-row/g)).toHaveLength(2);
    expect(html).toContain("runtime-toast-more");
    expect(html).toContain("还有 1 条");
    expect(html).not.toContain("条状态");
    expect(html).not.toContain("level-1");
    expect(html).not.toContain("level-2");
    expect(html).not.toContain("desktop_browser.browser_get_page_summary");
  });
});

function activity(
  id: string,
  title: string,
  detail: string,
  status: RuntimeActivityItem["status"] = "running",
): RuntimeActivityItem {
  return {
    id,
    runId: "run_1",
    kind: "browser",
    status,
    title,
    detail,
    url: detail.startsWith("http") ? detail : "",
    selector: detail.startsWith("http") ? "" : detail,
    timestamp: "2026-07-05T00:00:00+08:00",
  };
}
