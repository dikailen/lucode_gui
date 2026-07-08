from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


SENSITIVITY_ORDER = {
    "public": 0,
    "project_private": 1,
    "secret": 2,
    "local_only": 3,
}

LOCAL_ONLY_SOURCE_TYPES = {
    "browser_dom",
    "browser_summary",
    "desktop_browser",
    "terminal_output",
    "command_output",
    "shell_output",
}

PROJECT_PRIVATE_SOURCE_TYPES = {
    "file_snapshot",
    "project_file",
    "read_content",
    "workspace_context",
    "context_pack",
    "tool_output",
}


@dataclass(frozen=True)
class ContextSourceLabel:
    source_type: str
    sensitivity: str
    reason: str
    text_preview: str = ""
    path: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "sensitivity": self.sensitivity,
            "reason": self.reason,
            "text_preview": self.text_preview,
            "path": self.path,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


def label_context_source(
    source_type: str,
    *,
    text: str = "",
    path: str = "",
    tags: Iterable[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ContextSourceLabel:
    clean_source = _clean_token(source_type or "unknown")
    clean_path = str(path or "").strip().replace("\\", "/")
    clean_tags = tuple(_clean_token(item) for item in list(tags or []) if str(item or "").strip())
    sensitivity, reason = _sensitivity_for_source(
        clean_source,
        text=str(text or ""),
        path=clean_path,
        tags=clean_tags,
    )
    return ContextSourceLabel(
        source_type=clean_source,
        sensitivity=sensitivity,
        reason=reason,
        text_preview=_preview(text),
        path=clean_path,
        tags=clean_tags,
        metadata=dict(metadata or {}),
    )


def label_memory_entry(entry) -> ContextSourceLabel:
    tags = tuple(str(item or "").strip() for item in list(getattr(entry, "tags", []) or []) if str(item or "").strip())
    return label_context_source(
        "memory_entry",
        text=str(getattr(entry, "summary", "") or ""),
        tags=tags,
        metadata={
            "id": str(getattr(entry, "id", "") or ""),
            "kind": str(getattr(entry, "kind", "") or ""),
            "source": str(getattr(entry, "source", "") or ""),
        },
    )


def labels_from_memory_pack(memory_pack) -> list[ContextSourceLabel]:
    if memory_pack is None:
        return []
    labels: list[ContextSourceLabel] = []
    for attr in ("entries", "failure_lesson_candidates"):
        for entry in list(getattr(memory_pack, attr, []) or []):
            labels.append(label_memory_entry(entry))
    if str(getattr(memory_pack, "session_summary", "") or "").strip():
        labels.append(label_context_source("memory_entry", text=getattr(memory_pack, "session_summary", "")))
    return labels


def strongest_context_sensitivity(labels: Iterable[ContextSourceLabel] | None) -> tuple[str, list[str]]:
    best = "public"
    reasons: list[str] = []
    for label in list(labels or []):
        sensitivity = _normalize_sensitivity(getattr(label, "sensitivity", "public"))
        if SENSITIVITY_ORDER[sensitivity] > SENSITIVITY_ORDER[best]:
            best = sensitivity
        source_type = str(getattr(label, "source_type", "") or "").strip()
        if source_type:
            reasons.append(f"source:{source_type}")
        reason = str(getattr(label, "reason", "") or "").strip()
        if reason:
            reasons.append(reason)
    return best, _dedupe(reasons)


def _sensitivity_for_source(
    source_type: str,
    *,
    text: str,
    path: str,
    tags: tuple[str, ...],
) -> tuple[str, str]:
    tag_set = {tag.lower() for tag in tags}
    if tag_set & {"local_only", "browser_dom", "terminal_output", "command_output", "shell_output"}:
        return "local_only", "tag_local_only"
    if tag_set & {"secret", "credential", "api_key", "token"}:
        return "secret", "tag_secret"
    if tag_set & {"public"}:
        return "public", "tag_public"
    if tag_set & {"project_private", "project", "repo", "workspace"}:
        return "project_private", "tag_project_private"

    if source_type in LOCAL_ONLY_SOURCE_TYPES:
        return "local_only", f"source_{source_type}"
    if _path_or_text_contains_secret(path, text):
        return "secret", "source_secret_marker"
    if source_type in PROJECT_PRIVATE_SOURCE_TYPES:
        return "project_private", f"source_{source_type}"
    if source_type == "memory_entry":
        return "project_private", "source_memory_entry"
    return "public", "source_public_default"


def _path_or_text_contains_secret(path: str, text: str) -> bool:
    value = f"{path}\n{text}".lower()
    if ".env" in value or "auth.json" in value:
        return True
    return bool(
        re.search(r"\b(api[_-]?key|secret|password|passwd|credential|private[_-]?key)\b", value)
        or re.search(r"\b(token|bearer)\s*[:=]\s*\S+", value)
        or re.search(r"\bsk-[a-z0-9_-]{6,}\b", value, flags=re.IGNORECASE)
    )


def _normalize_sensitivity(value: str) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in SENSITIVITY_ORDER else "public"


def _preview(value: str, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _clean_token(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result
