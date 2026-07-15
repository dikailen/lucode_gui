from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


def apply_skill_metadata_tuning(
    *,
    workspace_root: str | Path,
    skill_id: str,
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    use_when: list[str] | None = None,
    do_not_use_when: list[str] | None = None,
    negative_queries: list[str],
    distinguish_from: dict[str, str],
) -> dict[str, str]:
    root = Path(workspace_root).resolve() / ".lucode" / "skills"
    clean_id = str(skill_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", clean_id):
        raise ValueError("only workspace Skills can be edited")
    target = (root / clean_id / "SKILL.md").resolve()
    if not clean_id or target.parent.parent != root or not target.is_file():
        raise ValueError("only workspace Skills can be edited")

    safe_categories = _normalize_values(categories or [], label="category", limit=8, max_length=120)
    safe_tags = _normalize_values(tags or [], label="tag", limit=24, max_length=80)
    safe_use_when = _normalize_values(use_when or [], label="use_when", limit=16, max_length=320)
    safe_do_not_use_when = _normalize_values(
        do_not_use_when or [], label="do_not_use_when", limit=16, max_length=320
    )
    safe_negative_queries = _normalize_values(negative_queries, label="negative query", limit=40, max_length=320)
    safe_distinguish_from = _normalize_mapping(distinguish_from)

    text = target.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("Skill frontmatter is required")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("Skill frontmatter is invalid")

    metadata = text[3:end]
    body = text[end + 4 :]
    if categories is not None:
        metadata = _replace_field(metadata, "category", _list_value("category", safe_categories))
    if tags is not None:
        metadata = _replace_field(metadata, "tags", _list_value("tags", safe_tags))
    if use_when is not None:
        metadata = _replace_field(metadata, "use_when", _list_value("use_when", safe_use_when))
    if do_not_use_when is not None:
        metadata = _replace_field(metadata, "do_not_use_when", _list_value("do_not_use_when", safe_do_not_use_when))
    metadata = _replace_field(metadata, "negative_queries", _list_value("negative_queries", safe_negative_queries))
    metadata = _replace_field(metadata, "distinguish_from", _map_value(safe_distinguish_from))
    _atomic_write(target, f"---{metadata}\n---{body}")
    return {"skill_id": clean_id}


def _replace_field(metadata: str, key: str, replacement: list[str]) -> str:
    lines = metadata.splitlines()
    result: list[str] = []
    index = 0
    replaced = False
    while index < len(lines):
        line = lines[index]
        if line.startswith(f"{key}:"):
            result.extend(replacement)
            replaced = True
            index += 1
            while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
                index += 1
            continue
        result.append(line)
        index += 1
    if not replaced:
        result.extend(replacement)
    return "\n" + "\n".join(result).rstrip("\n") + "\n"


def _list_value(key: str, values: list[str]) -> list[str]:
    return [f"{key}:", *[f"  - {value.strip()}" for value in values if value.strip()]]


def _map_value(values: dict[str, str]) -> list[str]:
    return ["distinguish_from:", *[f"  {key.strip()}: {value.strip()}" for key, value in values.items() if key.strip() and value.strip()]]


def _normalize_values(values: list[str], *, label: str, limit: int, max_length: int) -> list[str]:
    normalized: list[str] = []
    for raw_value in list(values or []):
        value = _single_line_text(raw_value, label=label, max_length=max_length)
        if value and value not in normalized:
            normalized.append(value)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_mapping(values: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in dict(values or {}).items():
        key = str(raw_key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key):
            raise ValueError("distinguish_from keys must be identifiers")
        value = _single_line_text(raw_value, label="distinguish_from value", max_length=320)
        if value:
            normalized[key] = value
        if len(normalized) >= 40:
            break
    return normalized


def _single_line_text(value: object, *, label: str, max_length: int) -> str:
    text = str(value or "").strip()
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError(f"{label} must be a single line")
    return text[:max_length]


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)
