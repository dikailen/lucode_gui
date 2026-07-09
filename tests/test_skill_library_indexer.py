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
