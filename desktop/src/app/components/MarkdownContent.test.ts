import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownContent } from "./MarkdownContent";

describe("MarkdownContent", () => {
  it("renders technical markdown as document flow", () => {
    const html = renderToStaticMarkup(
      React.createElement(MarkdownContent, {
        content: [
          "## 验证结果",
          "",
          "这次 **通过**，请看 `runtime`。",
          "",
          "- 读取页面",
          "- 等待审批",
          "",
          "> 审批前不提交表单",
          "",
          "| 项目 | 状态 |",
          "| --- | --- |",
          "| browser | ok |",
          "",
          "```ts",
          "const status = 'ok';",
          "```",
        ].join("\n"),
      }),
    );

    expect(html).toContain('class="markdown-content"');
    expect(html).toContain("<h2>验证结果</h2>");
    expect(html).toContain("<strong>通过</strong>");
    expect(html).toContain("<code>runtime</code>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<table>");
    expect(html).toContain("<th>项目</th>");
    expect(html).toContain("<td>browser</td>");
    expect(html).toContain('class="markdown-code"');
    expect(html).toContain("const status = &#x27;ok&#x27;;");
  });

  it("hides successful audit trailers from the visible final answer", () => {
    const html = renderToStaticMarkup(
      React.createElement(MarkdownContent, {
        content: [
          "### 结果",
          "",
          "已经完成页面检查。",
          "",
          "最终审核：通过",
          "本轮执行满足计划验收要求。",
          "",
          "修改内容：",
          "- Use embedded desktop browser: 读取页面摘要",
          "",
          "审核提醒（不影响通过）：",
          "- 任务 desktop_browser_task 的语义验收未完全确认",
        ].join("\n"),
      }),
    );

    expect(html).toContain("已经完成页面检查");
    expect(html).not.toContain("最终审核");
    expect(html).not.toContain("修改内容");
    expect(html).not.toContain("审核提醒");
  });

  it("preserves paragraph line breaks instead of merging long technical lines together", () => {
    const html = renderToStaticMarkup(
      React.createElement(MarkdownContent, {
        content: ["第一行说明", "selector: #app form input[name='query']", "URL: https://example.com/search?q=lucode"].join("\n"),
      }),
    );

    expect(html).toContain("第一行说明");
    expect(html).toContain("<br/>");
    expect(html).toContain("selector:");
    expect(html).toContain("https://example.com/search");
  });
});
