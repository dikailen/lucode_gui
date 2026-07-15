from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


REQUIRED_METADATA_FIELDS = (
    "id",
    "name",
    "description",
    "category",
    "tags",
    "use_when",
    "do_not_use_when",
)


@dataclass(frozen=True)
class SkillIndexEntry:
    id: str
    name: str
    summary: str
    source: str
    folder: str = ""
    category: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    use_when: tuple[str, ...] = field(default_factory=tuple)
    do_not_use_when: tuple[str, ...] = field(default_factory=tuple)
    negative_queries: tuple[str, ...] = field(default_factory=tuple)
    distinguish_from: dict[str, str] = field(default_factory=dict)
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    verification: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "unknown"
    enabled: bool = True
    core: bool = False
    assignable: bool = True
    body_path: str = ""
    metadata_status: str = "ready"
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillCandidate:
    entry: SkillIndexEntry
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    penalties: tuple[str, ...] = field(default_factory=tuple)
    explicit: bool = False


def normalize_skill_metadata(
    raw: dict[str, Any] | None,
    *,
    source: str,
    body_path: str,
    folder: str = "",
) -> SkillIndexEntry:
    raw = dict(raw or {})
    clean_source = str(source or "unknown").strip() or "unknown"
    raw_id = _text(raw, "id", "skill_id") or folder or _folder_from_body_path(body_path)
    skill_id = _normalize_id(raw_id)
    name = _text(raw, "name", "display_name_zh", "display_name") or ""
    summary = _text(raw, "description", "summary", "summary_zh") or ""
    category = _tuple(raw, "category", "categories")
    tags = _tuple(raw, "tags")
    use_when = _tuple(raw, "use_when", "use-when", "good_for", "trigger", "triggers")
    if not use_when and isinstance(raw.get("metadata"), dict):
        use_when = _tuple(dict(raw["metadata"]), "trigger", "triggers", "use_when", "use-when")
    do_not_use_when = _tuple(raw, "do_not_use_when", "do-not-use-when", "not_for")
    missing_fields = _missing_fields(
        skill_id=skill_id,
        name=name,
        summary=summary,
        category=category,
        tags=tags,
        use_when=use_when,
        do_not_use_when=do_not_use_when,
    )
    metadata_status = "incomplete" if missing_fields else "ready"
    enabled = _bool(raw.get("enabled"), default=True)
    core = bool(raw.get("core") or raw.get("internal") or clean_source == "core")
    raw_assignable = _bool(raw.get("assignable"), default=True)
    if raw.get("borrowable") is False:
        raw_assignable = False
    assignable = bool(enabled and raw_assignable and not core and metadata_status == "ready")

    return SkillIndexEntry(
        id=skill_id,
        name=name or skill_id,
        summary=summary,
        source=clean_source,
        folder=str(folder or raw.get("folder") or _folder_from_body_path(body_path) or "").strip(),
        category=category,
        tags=tags,
        use_when=use_when,
        do_not_use_when=do_not_use_when,
        negative_queries=_tuple(raw, "negative_queries", "negative-queries"),
        distinguish_from=_string_dict(raw.get("distinguish_from") or raw.get("distinguish-from")),
        scope_paths=_tuple(raw, "scope_paths", "scope-paths"),
        verification=_tuple(raw, "verification", "verify"),
        risk_level=_text(raw, "risk_level", "risk-level") or "unknown",
        enabled=enabled,
        core=core,
        assignable=assignable,
        body_path=str(body_path or "").strip(),
        metadata_status=metadata_status,
        missing_fields=missing_fields,
        usage=dict(raw.get("usage") or {}) if isinstance(raw.get("usage"), dict) else {},
    )


def skill_entry_to_dict(entry: SkillIndexEntry) -> dict[str, Any]:
    data = asdict(entry)
    for key in (
        "category",
        "tags",
        "use_when",
        "do_not_use_when",
        "negative_queries",
        "scope_paths",
        "verification",
        "missing_fields",
    ):
        data[key] = list(data.get(key) or [])
    return data


def skill_entry_from_dict(data: dict[str, Any]) -> SkillIndexEntry:
    return SkillIndexEntry(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or ""),
        summary=str(data.get("summary") or ""),
        source=str(data.get("source") or "unknown"),
        folder=str(data.get("folder") or ""),
        category=tuple(str(item) for item in list(data.get("category") or [])),
        tags=tuple(str(item) for item in list(data.get("tags") or [])),
        use_when=tuple(str(item) for item in list(data.get("use_when") or [])),
        do_not_use_when=tuple(str(item) for item in list(data.get("do_not_use_when") or [])),
        negative_queries=tuple(str(item) for item in list(data.get("negative_queries") or [])),
        distinguish_from=_string_dict(data.get("distinguish_from")),
        scope_paths=tuple(str(item) for item in list(data.get("scope_paths") or [])),
        verification=tuple(str(item) for item in list(data.get("verification") or [])),
        risk_level=str(data.get("risk_level") or "unknown"),
        enabled=bool(data.get("enabled", True)),
        core=bool(data.get("core", False)),
        assignable=bool(data.get("assignable", True)),
        body_path=str(data.get("body_path") or ""),
        metadata_status=str(data.get("metadata_status") or "ready"),
        missing_fields=tuple(str(item) for item in list(data.get("missing_fields") or [])),
        usage=dict(data.get("usage") or {}) if isinstance(data.get("usage"), dict) else {},
    )


def _missing_fields(
    *,
    skill_id: str,
    name: str,
    summary: str,
    category: tuple[str, ...],
    tags: tuple[str, ...],
    use_when: tuple[str, ...],
    do_not_use_when: tuple[str, ...],
) -> tuple[str, ...]:
    values = {
        "id": bool(skill_id),
        "name": bool(name),
        "description": bool(summary),
        "category": bool(category),
        "tags": bool(tags),
        "use_when": bool(use_when),
        "do_not_use_when": bool(do_not_use_when),
    }
    return tuple(field for field in REQUIRED_METADATA_FIELDS if not values[field])


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        if value is not None:
            return str(value).strip()
    return ""


def _tuple(raw: dict[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        if key in raw:
            return tuple(_as_list(raw.get(key)))
    return ()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            stripped = stripped[1:-1]
        return [item.strip().strip("'\"") for item in re.split(r"[,;\n]+", stripped) if item.strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).strip(): str(raw).strip() for key, raw in value.items() if str(key).strip() and str(raw).strip()}


def _bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _folder_from_body_path(body_path: str) -> str:
    text = str(body_path or "").replace("\\", "/").rstrip("/")
    if text.endswith("/SKILL.md"):
        text = text[: -len("/SKILL.md")]
    return text.rsplit("/", 1)[-1] if text else ""


def _normalize_id(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_") or "unnamed"
