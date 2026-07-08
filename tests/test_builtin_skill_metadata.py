from pathlib import Path
from types import SimpleNamespace

from runtime.skill_library.indexer import build_skill_index
from runtime.skill_library.retriever import retrieve_skill_candidates


def _repo_context(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    user_home = tmp_path / "user"
    workspace_root = tmp_path / "workspace"
    (user_home / "skills").mkdir(parents=True)
    (workspace_root / ".lucode" / "skills").mkdir(parents=True)
    return SimpleNamespace(app_home=repo_root, user_home=user_home, workspace_root=workspace_root)


def test_builtin_assignable_skills_have_minimum_ready_metadata(tmp_path):
    entries = build_skill_index(_repo_context(tmp_path), write=False)
    by_id = {entry.id: entry for entry in entries}

    for skill_id in ("code_engineer", "project_explorer", "skill_creator"):
        entry = by_id[skill_id]
        assert entry.metadata_status == "ready"
        assert entry.assignable is True
        assert not entry.missing_fields


def test_builtin_rule_only_skill_is_metadata_ready_but_not_assignable(tmp_path):
    entries = build_skill_index(_repo_context(tmp_path), write=False)
    cli_safety = {entry.id: entry for entry in entries}["cli_command_safety"]

    assert cli_safety.metadata_status == "ready"
    assert cli_safety.assignable is False


def test_builtin_skill_retrieval_has_practical_candidates(tmp_path):
    entries = build_skill_index(_repo_context(tmp_path), write=False)

    code_candidates = retrieve_skill_candidates("fix a Python runtime bug and add pytest coverage", entries)
    project_candidates = retrieve_skill_candidates("inspect this project structure and explain package entry points", entries)
    skill_candidates = retrieve_skill_candidates("create a new SKILL.md for a reusable workflow", entries)

    assert code_candidates[0].entry.id == "code_engineer"
    assert project_candidates[0].entry.id == "project_explorer"
    assert skill_candidates[0].entry.id == "skill_creator"
