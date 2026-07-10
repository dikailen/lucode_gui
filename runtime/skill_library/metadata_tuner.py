from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.context.compaction import redact_sensitive_text


@dataclass(frozen=True)
class MetadataTuningSuggestion:
    skill_id: str
    suggested_negative_queries: tuple[str, ...] = field(default_factory=tuple)
    suggested_distinguish_from: dict[str, str] = field(default_factory=dict)
    source_count: int = 0


def suggest_metadata_tuning(
    records: list[dict[str, Any]],
    *,
    existing_negative_queries: dict[str, list[str] | tuple[str, ...]] | None = None,
    max_queries_per_skill: int = 5,
) -> list[MetadataTuningSuggestion]:
    existing = {
        _skill_id(skill_id): {_normalize_text(item) for item in values}
        for skill_id, values in dict(existing_negative_queries or {}).items()
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if isinstance(record, dict) and _negative_signal(record):
            skill_id = _skill_id(record.get("skill_id"))
            if skill_id:
                grouped.setdefault(skill_id, []).append(record)
    suggestions: list[MetadataTuningSuggestion] = []
    for skill_id, items in sorted(grouped.items()):
        queries: list[str] = []
        seen = set(existing.get(skill_id, set()))
        distinctions: dict[str, str] = {}
        for item in items:
            query = _safe_text(item.get("query"))
            normalized = _normalize_text(query)
            if query and normalized not in seen and len(queries) < max(1, int(max_queries_per_skill)):
                seen.add(normalized)
                queries.append(query)
            reason = _safe_text(item.get("reason"))
            category = _reason_category(reason)
            if reason and category and category not in distinctions:
                distinctions[category] = reason
        suggestions.append(MetadataTuningSuggestion(skill_id, tuple(queries), distinctions, len(items)))
    return suggestions


def suggest_metadata_tuning_from_usage(workspace_root: str | Path, **kwargs) -> list[MetadataTuningSuggestion]:
    path = Path(workspace_root).resolve() / ".lucode" / "skills" / "usage.jsonl"
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return suggest_metadata_tuning([item for item in records if isinstance(item, dict)], **kwargs)


def _negative_signal(record: dict[str, Any]) -> bool:
    return bool(record.get("misfire")) or str(record.get("result") or "") == "rejected_by_planner"


def _safe_text(value: Any) -> str:
    original = str(value or "").replace("\x00", "").strip()
    if not original:
        return ""
    redacted = redact_sensitive_text(original).strip()
    return original[:240] if redacted == original else ""


def _reason_category(reason: str) -> str:
    value = str(reason or "").casefold()
    if any(marker in value for marker in ("ui", "frontend", "react", "界面")):
        return "ui"
    if any(marker in value for marker in ("context", "上下文", "ledger")):
        return "context"
    return ""


def _skill_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())
