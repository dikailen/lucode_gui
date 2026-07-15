from __future__ import annotations

import json
import uuid
from dataclasses import replace
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
from runtime.skill_library.usage import load_usage_summary


SOURCE_PRIORITY = {"workspace": 0, "user": 1, "sample": 2, "core": 3}
INDEX_MANIFEST_SCHEMA_VERSION = "skill_index_manifest.v1"


def build_skill_index(workspace_context=None, *, write: bool = True) -> list[SkillIndexEntry]:
    source_snapshot = _skill_source_snapshot(workspace_context)
    layers = discover_skill_layers(workspace_context)
    discovered: list[SkillIndexEntry] = []
    for source in ("core", "sample", "user", "workspace"):
        for item in list(layers.get(source) or []):
            entry = _entry_from_discovered_item(item)
            discovered.append(entry)

    entries = _apply_workspace_enabled_overrides(_dedupe_entries(discovered), workspace_context)
    entries.sort(key=lambda entry: (SOURCE_PRIORITY.get(entry.source, 9), entry.id))
    if write:
        try:
            _write_index(entries, workspace_context, source_snapshot=source_snapshot)
        except OSError:
            # The disk index is an optional cache; in-memory resolution remains authoritative.
            pass
    return _merge_usage_summary(entries, workspace_context)


def load_skill_index(workspace_context=None, *, rebuild_if_missing: bool = True) -> list[SkillIndexEntry]:
    path = _index_path(workspace_context)
    source_snapshot = _skill_source_snapshot(workspace_context)
    if not path.exists() or not _source_manifest_matches(workspace_context, source_snapshot):
        return build_skill_index(workspace_context, write=True) if rebuild_if_missing else []
    entries = _read_index(path)
    if entries is None or (source_snapshot and not entries):
        return build_skill_index(workspace_context, write=True) if rebuild_if_missing else []
    return _merge_usage_summary(_apply_workspace_enabled_overrides(entries, workspace_context), workspace_context)


def _read_index(path: Path) -> list[SkillIndexEntry] | None:
    entries: list[SkillIndexEntry] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            entries.append(skill_entry_from_dict(data))
        except Exception:
            return None
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


def _apply_workspace_enabled_overrides(
    entries: list[SkillIndexEntry],
    workspace_context=None,
) -> list[SkillIndexEntry]:
    """Apply workspace-only enablement without editing imported SKILL.md files."""

    disabled_ids = _workspace_disabled_skill_ids(workspace_context)
    if not disabled_ids:
        return entries
    return [
        replace(entry, enabled=False, assignable=False)
        if entry.source == "workspace" and not _is_protected_core(entry) and entry.id in disabled_ids
        else entry
        for entry in entries
    ]


def _workspace_disabled_skill_ids(workspace_context=None) -> set[str]:
    """Read the GUI-owned workspace preference without creating a GUI dependency."""

    state_path = extension_roots(workspace_context).workspace_root / ".lucode" / "gui_plugin_state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw_ids = data.get("disabled_skill_ids") if isinstance(data, dict) else []
    if not isinstance(raw_ids, list):
        return set()
    disabled: set[str] = set()
    for raw_id in raw_ids:
        text = str(raw_id or "").strip()
        if not text:
            continue
        disabled.add(normalize_skill_metadata({"id": text}, source="workspace", body_path="").id)
    return disabled


def _is_protected_core(entry: SkillIndexEntry) -> bool:
    return entry.source == "core" or entry.core or entry.id in PROTECTED_SYSTEM_SKILLS


def _write_index(
    entries: list[SkillIndexEntry],
    workspace_context=None,
    *,
    source_snapshot: list[dict[str, Any]] | None = None,
) -> None:
    path = _index_path(workspace_context)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(skill_entry_to_dict(entry), ensure_ascii=False, sort_keys=True) for entry in entries)
    _write_text_atomic(path, (text + "\n") if text else "")
    manifest = {
        "schema_version": INDEX_MANIFEST_SCHEMA_VERSION,
        "sources": list(source_snapshot if source_snapshot is not None else _skill_source_snapshot(workspace_context)),
    }
    _write_text_atomic(
        _manifest_path(workspace_context),
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _merge_usage_summary(entries: list[SkillIndexEntry], workspace_context=None) -> list[SkillIndexEntry]:
    try:
        summary = load_usage_summary(_usage_path(workspace_context))
    except Exception:
        return entries
    if not summary:
        return entries
    merged: list[SkillIndexEntry] = []
    for entry in entries:
        usage = summary.get(entry.id)
        if not usage:
            merged.append(entry)
            continue
        merged.append(replace(entry, usage={**dict(entry.usage or {}), **usage}))
    return merged


def _index_path(workspace_context=None) -> Path:
    roots = extension_roots(workspace_context)
    return roots.workspace_root / ".lucode" / "skills" / "index.jsonl"


def _usage_path(workspace_context=None) -> Path:
    return _index_path(workspace_context).parent / "usage.jsonl"


def _manifest_path(workspace_context=None) -> Path:
    return _index_path(workspace_context).parent / "index.manifest.json"


def _skill_source_snapshot(workspace_context=None) -> list[dict[str, Any]]:
    roots = extension_roots(workspace_context)
    source_roots = (
        ("core", roots.app_home / "core_skills"),
        ("sample", roots.app_home / "skills"),
        ("user", roots.user_home / "skills"),
        ("workspace", roots.workspace_root / ".lucode" / "skills"),
    )
    snapshot: list[dict[str, Any]] = []
    for source, root in source_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot.append(
                {
                    "source": source,
                    "path": path.resolve().as_posix(),
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
    return snapshot


def _source_manifest_matches(workspace_context, source_snapshot: list[dict[str, Any]]) -> bool:
    path = _manifest_path(workspace_context)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get("schema_version") != INDEX_MANIFEST_SCHEMA_VERSION:
        return False
    return data.get("sources") == source_snapshot


def _write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
