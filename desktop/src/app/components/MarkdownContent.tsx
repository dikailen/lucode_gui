import type { ReactNode } from "react";

type MarkdownContentProps = {
  content: string;
};

type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "paragraph"; text: string }
  | { type: "blockquote"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "code"; language: string; code: string }
  | { type: "table"; headers: string[]; rows: string[][] };

export function MarkdownContent({ content }: MarkdownContentProps) {
  const blocks = parseMarkdownBlocks(cleanVisibleFinalAnswer(content));
  return (
    <div className="markdown-content">
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  );
}

function cleanVisibleFinalAnswer(content: string): string {
  const text = String(content || "");
  const successfulAuditStart = text.search(/(^|\n)\s*最终审核\s*[：:]\s*通过(?:\s|$)/);
  if (successfulAuditStart >= 0) {
    return text.slice(0, successfulAuditStart).trimEnd();
  }
  return text;
}

function parseMarkdownBlocks(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const codeFence = line.match(/^```([\w-]*)\s*$/);
    if (codeFence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push({ type: "code", language: codeFence[1] || "", code: codeLines.join("\n") });
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length as 1 | 2 | 3, text: heading[2].trim() });
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = parseTableRow(lines[index]);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && looksLikeTableRow(lines[index])) {
        rows.push(parseTableRow(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    const listMatch = line.match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[2]);
      const items: string[] = [];
      while (index < lines.length) {
        const current = lines[index].match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
        if (!current || /\d+\./.test(current[2]) !== ordered) {
          break;
        }
        items.push(current[3].trim());
        index += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, "").trim());
        index += 1;
      }
      blocks.push({ type: "blockquote", text: quoteLines.join("\n") });
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraphLines.join("\n") });
  }

  return blocks.length ? blocks : [{ type: "paragraph", text: content }];
}

function startsMarkdownBlock(lines: string[], index: number): boolean {
  const line = lines[index];
  return (
    /^```/.test(line) ||
    /^(#{1,3})\s+/.test(line) ||
    /^(\s*)([-*]|\d+\.)\s+/.test(line) ||
    /^>\s?/.test(line) ||
    isTableStart(lines, index)
  );
}

function isTableStart(lines: string[], index: number): boolean {
  return looksLikeTableRow(lines[index]) && index + 1 < lines.length && looksLikeSeparatorRow(lines[index + 1]);
}

function looksLikeTableRow(line: string): boolean {
  return line.includes("|") && parseTableRow(line).length > 1;
}

function looksLikeSeparatorRow(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function parseTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderBlock(block: MarkdownBlock, index: number): ReactNode {
  if (block.type === "heading") {
    const HeadingTag = `h${block.level}` as "h1" | "h2" | "h3";
    return <HeadingTag key={index}>{renderInline(block.text)}</HeadingTag>;
  }
  if (block.type === "blockquote") {
    return <blockquote key={index}>{renderInline(block.text)}</blockquote>;
  }
  if (block.type === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    return (
      <ListTag key={index}>
        {block.items.map((item, itemIndex) => (
          <li key={itemIndex}>{renderInline(item)}</li>
        ))}
      </ListTag>
    );
  }
  if (block.type === "code") {
    return (
      <pre className="markdown-code" key={index}>
        {block.language ? <code className="markdown-code-language">{block.language}</code> : null}
        <code>{block.code}</code>
      </pre>
    );
  }
  if (block.type === "table") {
    return (
      <div className="markdown-table-wrap" key={index}>
        <table>
          <thead>
            <tr>
              {block.headers.map((header, headerIndex) => (
                <th key={headerIndex}>{renderInline(header)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {block.headers.map((_, cellIndex) => (
                  <td key={cellIndex}>{renderInline(row[cellIndex] || "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return <p key={index}>{renderInline(block.text)}</p>;
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const codePattern = /`([^`]+)`/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = codePattern.exec(text))) {
    if (match.index > lastIndex) {
      nodes.push(...renderEmphasis(text.slice(lastIndex, match.index), nodes.length));
    }
    nodes.push(<code key={`code-${nodes.length}`}>{match[1]}</code>);
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    nodes.push(...renderEmphasis(text.slice(lastIndex), nodes.length));
  }
  return nodes;
}

function renderEmphasis(text: string, keySeed: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*([^*]+)\*\*|\*([^*]+)\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text))) {
    if (match.index > lastIndex) {
      nodes.push(...renderPlainText(text.slice(lastIndex, match.index), `text-${keySeed}-${nodes.length}`));
    }
    if (match[2]) {
      nodes.push(<strong key={`strong-${keySeed}-${nodes.length}`}>{match[2]}</strong>);
    } else if (match[3]) {
      nodes.push(<em key={`em-${keySeed}-${nodes.length}`}>{match[3]}</em>);
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    nodes.push(...renderPlainText(text.slice(lastIndex), `text-${keySeed}-${nodes.length}`));
  }
  return nodes;
}

function renderPlainText(text: string, keyPrefix: string): ReactNode[] {
  const pieces = text.split("\n");
  const nodes: ReactNode[] = [];
  pieces.forEach((piece, index) => {
    if (piece) {
      nodes.push(piece);
    }
    if (index < pieces.length - 1) {
      nodes.push(<br key={`${keyPrefix}-br-${index}`} />);
    }
  });
  return nodes;
}
