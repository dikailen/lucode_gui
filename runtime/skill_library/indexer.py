from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.config.extensions import discover_skill_layers, extension_roots
from runtime.config.skill_frontmatter import read_skill_frontmatter
from runtime.config.skill_policy import PROTECTED_SYSTEM_SKILLS
from runtime.skill_library.schema import (
    SkillIndexEntry,
    normalize_skill_metadata,
    skill_entry_from_dict,
    skill_entry_to_dict,
)


SOURCE_PRIORITY = {"workspace": 0, "user": 1, "sample": 2, "core": 3}


def build_skill_index(workspace_context=None, *, write: bool = True) -> list[SkillIndexEntry]:
    layers = discover_skill_layers(workspace_context)
    discovered: list[SkillIndexEntry] = []
    for source in ("core", "sample", "user", "workspace"):
        for item in list(layers.get(source) or []):
            entry = _entry_from_discovered_item(item)
            discovered.append(entry)

    entries = _dedupe_entries(discovered)
    entries.sort(key=lambda entry: (SOURCE_PRIORITY.get(entry.source, 9), entry.id))
    if write:
        _write_index(entries, workspace_context)
    return entries


def load_skill_index(workspace_context=None, *, rebuild_if_missing: bool = True) -> list[SkillIndexEntry]:
    path = _index_path(workspace_context)
    if not path.exists():
        return build_skill_index(workspace_context, write=True) if rebuild_if_missing else []
    entries: list[SkillIndexEntry] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(skill_entry_from_dict(data))
    return entries


def _entry_from_discovered_item(item: dict[str, Any]) -> SkillIndexEntry:
    source = str(item.get("source") or "unknown")
    folder = str(item.get("folder") or "")
    body_path = _body_path_for_item(item)
    frontmatter = read_skill_frontmatter(Path(body_path)) if body_path else {}
    raw = {
        **frontmatter,
        "id": frontmatter.get("id") or item.get("id"),
        "folder": folder,
        "name": frontmatter.get("name") or item.get("display_name_zh"),
        "description": frontmatter.get("description") or item.get("summary_zh"),
        "source": source,
        "internal": bool(item.get("internal")),
        "borrowable": item.get("borrowable"),
        "assignable": item.get("assignable"),
        "enabled": item.get("enabled", True),
    }
    return normalize_skill_metadata(raw, source=source, body_path=body_path, folder=folder)


def _body_path_for_item(item: dict[str, Any]) -> str:
    path = str(item.get("path") or "").strip()
    if not path:
        return ""
    candidate = Path(path)
    if candidate.name == "SKILL.md":
        return str(candidate)
    return str(candidate / "SKILL.md")


def _dedupe_entries(entries: list[SkillIndexEntry]) -> list[SkillIndexEntry]:
    chosen: dict[str, SkillIndexEntry] = {}
    for entry in entries:
        existing = chosen.get(entry.id)
        if existing is None:
            chosen[entry.id] = entry
            continue
        if _is_protected_core(entry) and not _is_protected_core(existing):
            chosen[entry.id] = entry
            continue
        if _is_protected_core(existing):
            continue
        if SOURCE_PRIORITY.get(entry.source, 9) < SOURCE_PRIORITY.get(existing.source, 9):
            chosen[entry.id] = entry
    return list(chosen.values())


def _is_protected_core(entry: SkillIndexEntry) -> bool:
    return entry.source == "core" or entry.core or entry.id in PROTECTED_SYSTEM_SKILLS


def _write_index(entries: list[SkillIndexEntry], workspace_context=None) -> None:
    path = _index_path(workspace_context)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(skill_entry_to_dict(entry), ensure_ascii=False, sort_keys=True) for entry in entries)
    path.write_text((text + "\n") if text else "", encoding="utf-8")


def _index_path(workspace_context=None) -> Path:
    roots = extension_roots(workspace_context)
    return roots.workspace_root / ".lucode" / "skills" / "index.jsonl"
