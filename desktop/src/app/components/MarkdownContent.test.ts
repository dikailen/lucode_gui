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
});
