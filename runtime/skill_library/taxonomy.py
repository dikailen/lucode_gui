from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "catalogs" / "skill_taxonomy.json"


@dataclass(frozen=True)
class SkillTaxonomy:
    schema_version: str
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)

    def has_category(self, category_id: str) -> bool:
        return str(category_id or "").strip() in self.categories


def load_skill_taxonomy(path: Path | None = None) -> SkillTaxonomy:
    taxonomy_path = Path(path) if path is not None else DEFAULT_TAXONOMY_PATH
    data = json.loads(taxonomy_path.read_text(encoding="utf-8-sig"))
    categories: dict[str, dict[str, Any]] = {}
    for raw in list(data.get("categories") or []):
        _flatten_category(raw, parent="", categories=categories)
    return SkillTaxonomy(
        schema_version=str(data.get("schema_version") or "skill_taxonomy.v1"),
        categories=categories,
    )


def classify_query_by_rules(query: str, taxonomy: SkillTaxonomy) -> list[str]:
    text = str(query or "").casefold()
    scores: dict[str, int] = {}
    for category_id, keywords in _CATEGORY_KEYWORDS.items():
        if not taxonomy.has_category(category_id):
            continue
        score = sum(1 for keyword in keywords if keyword.casefold() in text)
        if score:
            scores[category_id] = score
    return [category_id for category_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _flatten_category(raw: dict[str, Any], *, parent: str, categories: dict[str, dict[str, Any]]) -> None:
    if not isinstance(raw, dict):
        return
    local_id = str(raw.get("id") or "").strip()
    if not local_id:
        return
    category_id = local_id if not parent or "/" in local_id else f"{parent}/{local_id}"
    if category_id in categories:
        raise ValueError(f"duplicate category id: {category_id}")
    item = dict(raw)
    item["id"] = category_id
    categories[category_id] = item
    for child in list(raw.get("children") or []):
        _flatten_category(child, parent=category_id, categories=categories)


_CATEGORY_KEYWORDS = {
    "programming/frontend": [
        "electron",
        "react",
        "css",
        "ui",
        "layout",
        "renderer",
        "frontend",
        "desktop/src",
        "界面",
        "样式",
        "前端",
    ],
    "programming/backend": ["api", "server", "runtime", "backend", "fastapi", "后端", "服务端"],
    "programming/agent_loop": [
        "agent loop",
        "planner",
        "worker",
        "scheduler",
        "context",
        "ledger",
        "agent",
        "上下文",
        "规划",
    ],
    "programming/testing": ["test", "pytest", "unit test", "verification", "测试", "验证"],
    "design/ui_design": ["figma", "visual", "interaction", "design", "ui design", "视觉", "交互"],
    "design/image_generation": ["image", "generate image", "stable diffusion", "图片", "画图"],
    "design/comfyui": ["comfyui", "workflow", "node graph", "节点", "工作流"],
    "documentation/readme": ["readme", "documentation", "docs", "文档"],
    "documentation/planning": ["plan", "proposal", "architecture plan", "计划", "方案"],
    "tools/git": ["git", "commit", "branch", "push", "diff"],
    "tools/terminal": ["terminal", "shell", "powershell", "command", "终端", "命令"],
    "tools/browser": ["browser", "tab", "selector", "form", "click", "dom", "浏览器", "网页", "表单"],
    "mcp/integration": ["mcp", "server", "stdio", "sse", "tool server", "集成", "接入"],
}
