from types import SimpleNamespace

from runtime.skill_library.indexer import build_skill_index, load_skill_index
from runtime.skill_library.usage import SkillUsageTracker


def _context(tmp_path):
    app = tmp_path / "app"
    user = tmp_path / "user"
    workspace = tmp_path / "workspace"
    for root in (app / "core_skills", app / "skills", user / "skills", workspace / ".lucode" / "skills"):
        root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(app_home=app, user_home=user, workspace_root=workspace)


def _write_skill(root, folder, frontmatter, body="Skill body."):
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return skill_dir


def test_build_skill_index_discovers_workspace_skill_and_writes_jsonl(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "electron-ui-refactor",
        """
id: electron-ui-refactor
name: Electron UI Refactor
description: Improve Electron React UI.
category: [programming, frontend]
tags: [electron, react, ui]
use_when:
  - Modify Electron UI
do_not_use_when:
  - Modify Python runtime
negative_queries:
  - context compression
scope_paths:
  - desktop/src/**
""".strip(),
    )

    entries = build_skill_index(ctx, write=True)

    assert [entry.id for entry in entries] == ["electron_ui_refactor"]
    assert entries[0].metadata_status == "ready"
    assert entries[0].assignable is True
    index_path = ctx.workspace_root / ".lucode" / "skills" / "index.jsonl"
    assert index_path.exists()
    loaded = load_skill_index(ctx, rebuild_if_missing=False)
    assert [entry.id for entry in loaded] == ["electron_ui_refactor"]


def test_build_skill_index_marks_incomplete_skill_unassignable(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "thin-skill",
        """
id: thin-skill
name: Thin Skill
description: Missing routing metadata.
""".strip(),
    )

    entries = build_skill_index(ctx, write=False)

    assert entries[0].id == "thin_skill"
    assert entries[0].metadata_status == "incomplete"
    assert entries[0].assignable is False


def test_load_skill_index_applies_workspace_disabled_override_and_resolver_excludes_it(tmp_path):
    from lucode.gui.plugin_state import PluginStateStore
    from runtime.skill_library.retriever import retrieve_skill_candidates

    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "release-review",
        """
id: release_review
name: Release Review
description: Review a release before publishing it.
category: [programming, testing]
tags: [release, regression]
use_when: [Review a release]
do_not_use_when: [Write product copy]
""".strip(),
    )
    build_skill_index(ctx, write=True)
    PluginStateStore(ctx.workspace_root).set_skill_enabled("release_review", False)

    entries = load_skill_index(ctx)

    assert entries[0].enabled is False
    assert entries[0].assignable is False
    assert retrieve_skill_candidates("Review this release", entries) == []


def test_load_skill_index_reads_workspace_disabled_override_without_gui_dependency(tmp_path, monkeypatch):
    import builtins
    import json

    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "isolated-skill",
        """
id: isolated_skill
name: Isolated Skill
description: Keep runtime indexing independent from GUI modules.
category: [programming]
tags: [runtime]
use_when: [Index Skills]
do_not_use_when: [Write product copy]
""".strip(),
    )
    build_skill_index(ctx, write=True)
    state_path = ctx.workspace_root / ".lucode" / "gui_plugin_state.json"
    state_path.write_text(json.dumps({"disabled_skill_ids": ["isolated_skill"]}), encoding="utf-8")
    original_import = builtins.__import__

    def reject_gui_import(name, *args, **kwargs):
        if name == "lucode.gui.plugin_state":
            raise AssertionError("skill indexer must not import GUI state")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_gui_import)

    entries = load_skill_index(ctx)

    assert entries[0].enabled is False


def test_build_skill_index_keeps_core_skill_protected_from_workspace_override(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.app_home / "core_skills",
        "code-engineer",
        """
id: code_engineer
name: Code Engineer Core
description: Core code engineer.
category: [programming]
tags: [code]
use_when: [Implement code]
do_not_use_when: [Casual chat]
""".strip(),
    )
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "code-engineer",
        """
id: code_engineer
name: Fake Override
description: Attempted override.
category: [programming]
tags: [code]
use_when: [Override]
do_not_use_when: [Never]
""".strip(),
    )

    entries = build_skill_index(ctx, write=False)
    by_id = {entry.id: entry for entry in entries}

    assert by_id["code_engineer"].source == "core"
    assert by_id["code_engineer"].core is True
    assert by_id["code_engineer"].assignable is False


def test_skill_index_merges_usage_summary_and_refreshes_loaded_index(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "electron-ui-refactor",
        """
id: electron-ui-refactor
name: Electron UI Refactor
description: Improve Electron React UI.
category: [programming, frontend]
tags: [electron, react, ui]
use_when: [Modify Electron UI]
do_not_use_when: [Modify Python runtime]
""".strip(),
    )
    tracker = SkillUsageTracker(ctx.workspace_root)
    tracker.record(skill_id="electron_ui_refactor", query="Fix UI.", task_id="task_ui", result="success")
    tracker.record(skill_id="electron_ui_refactor", query="Fix UI.", task_id="task_ui", result="failure")
    tracker.record(
        skill_id="electron_ui_refactor",
        query="Fix UI.",
        task_id="task_ui",
        result="success",
        misfire=True,
    )

    built = build_skill_index(ctx, write=True)

    assert built[0].usage["used_count"] == 3
    assert built[0].usage["success_count"] == 2
    assert built[0].usage["failure_count"] == 1
    assert built[0].usage["misfire_count"] == 1

    tracker.record(skill_id="electron_ui_refactor", query="Fix UI.", task_id="", result="rejected_by_planner")
    loaded = load_skill_index(ctx, rebuild_if_missing=False)

    assert loaded[0].usage["used_count"] == 3
    assert loaded[0].usage["rejected_by_planner_count"] == 1


def test_skill_index_writes_source_manifest_but_usage_updates_do_not_invalidate_it(tmp_path, monkeypatch):
    import runtime.config.extensions as extensions
    import runtime.skill_library.indexer as indexer

    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "usage-skill",
        """
id: usage-skill
name: Usage Skill
description: Track usage without rescanning source.
category: [programming]
tags: [usage]
use_when: [Track usage]
do_not_use_when: [Casual chat]
""".strip(),
    )
    build_skill_index(ctx, write=True)
    manifest_path = ctx.workspace_root / ".lucode" / "skills" / "index.manifest.json"
    assert manifest_path.exists()

    def fail_read(_path):
        raise AssertionError("usage-only changes must not reread SKILL.md")

    monkeypatch.setattr(extensions, "read_skill_frontmatter", fail_read)
    monkeypatch.setattr(indexer, "read_skill_frontmatter", fail_read)
    SkillUsageTracker(ctx.workspace_root).record(
        skill_id="usage_skill",
        query="Track usage",
        task_id="task-usage",
        result="success",
    )

    loaded = load_skill_index(ctx)

    assert loaded[0].usage["used_count"] == 1


def test_skill_index_does_not_freeze_usage_data_inside_source_cache(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "dynamic-usage",
        """
id: dynamic-usage
name: Dynamic Usage
description: Keep usage separate from source metadata.
category: [programming]
tags: [usage]
use_when: [Track dynamic usage]
do_not_use_when: [Casual chat]
""".strip(),
    )
    tracker = SkillUsageTracker(ctx.workspace_root)
    tracker.record(
        skill_id="dynamic_usage",
        query="Track dynamic usage",
        task_id="task-dynamic",
        result="success",
    )
    built = build_skill_index(ctx, write=True)
    assert built[0].usage["used_count"] == 1

    tracker.usage_path.unlink()
    loaded = load_skill_index(ctx)

    assert loaded[0].usage == {}


def test_load_skill_index_rebuilds_a_corrupt_cached_index(tmp_path):
    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "repair-cache",
        """
id: repair-cache
name: Repair Cache
description: Rebuild a corrupt metadata cache.
category: [programming]
tags: [cache]
use_when: [Repair cache]
do_not_use_when: [Casual chat]
""".strip(),
    )
    build_skill_index(ctx, write=True)
    index_path = ctx.workspace_root / ".lucode" / "skills" / "index.jsonl"
    index_path.write_text("{not-json\n", encoding="utf-8")

    loaded = load_skill_index(ctx)

    assert [entry.id for entry in loaded] == ["repair_cache"]


def test_load_skill_index_keeps_working_when_cache_write_fails(tmp_path, monkeypatch):
    import runtime.skill_library.indexer as indexer

    ctx = _context(tmp_path)
    _write_skill(
        ctx.workspace_root / ".lucode" / "skills",
        "readonly-cache",
        """
id: readonly-cache
name: Readonly Cache
description: Resolve even when cache files cannot be written.
category: [programming]
tags: [cache]
use_when: [Resolve readonly cache]
do_not_use_when: [Casual chat]
""".strip(),
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("workspace is read-only")

    monkeypatch.setattr(indexer, "_write_index", fail_write)

    loaded = load_skill_index(ctx)

    assert [entry.id for entry in loaded] == ["readonly_cache"]
