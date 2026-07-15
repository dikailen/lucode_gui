from __future__ import annotations

import pytest

from runtime.skill_library.metadata_tuner import suggest_metadata_tuning


def test_metadata_tuner_suggests_negative_query_for_misfire():
    suggestions = suggest_metadata_tuning(
        [{"skill_id": "context_ledger", "query": "Fix the button layout", "misfire": True, "reason": "ui task"}]
    )

    assert suggestions[0].skill_id == "context_ledger"
    assert suggestions[0].suggested_negative_queries == ("Fix the button layout",)
    assert suggestions[0].suggested_distinguish_from["ui"] == "ui task"


def test_apply_metadata_tuning_updates_only_workspace_skill_frontmatter(tmp_path):
    from runtime.skill_library.metadata_editor import apply_skill_metadata_tuning

    skill_file = tmp_path / ".lucode" / "skills" / "release_review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nid: release_review\nname: Release Review\nnegative_queries:\n  - old query\n---\n\n# Body stays\n",
        encoding="utf-8",
    )

    result = apply_skill_metadata_tuning(
        workspace_root=tmp_path,
        skill_id="release_review",
        categories=["documentation", "planning"],
        negative_queries=["avoid marketing copy"],
        distinguish_from={"documentation": "Use planning for prose-only work."},
    )

    saved = skill_file.read_text(encoding="utf-8")
    assert result["skill_id"] == "release_review"
    assert "  - documentation" in saved
    assert "  - avoid marketing copy" in saved
    assert "documentation: Use planning for prose-only work." in saved
    assert "# Body stays" in saved


def test_apply_metadata_tuning_completes_imported_third_party_skill_metadata(tmp_path):
    """A copied third-party Skill stays inactive until the user explicitly completes its routing metadata."""
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.schema import normalize_skill_metadata
    from runtime.skill_library.metadata_editor import apply_skill_metadata_tuning

    skill_file = tmp_path / ".lucode" / "skills" / "humanizer-zh-main" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    original = (
        "---\n"
        "name: Humanizer\n"
        "description: Rewrite text to remove generic AI writing patterns.\n"
        "allowed-tools:\n"
        "  - Read\n"
        "  - Edit\n"
        "metadata:\n"
        "  trigger: Edit or review text to remove AI writing traces.\n"
        "---\n\n"
        "# Humanizer\n"
    )
    skill_file.write_text(original, encoding="utf-8")

    before = normalize_skill_metadata(
        read_skill_frontmatter(skill_file),
        source="workspace",
        body_path=str(skill_file),
        folder="humanizer-zh-main",
    )
    assert before.assignable is False
    assert set(before.missing_fields) >= {"category", "tags", "do_not_use_when"}
    assert before.use_when == ("Edit or review text to remove AI writing traces.",)
    assert skill_file.read_text(encoding="utf-8") == original

    apply_skill_metadata_tuning(
        workspace_root=tmp_path,
        skill_id="humanizer-zh-main",
        categories=["documentation"],
        tags=["editing", "writing", "humanizer"],
        use_when=["Edit or review text to remove AI writing traces."],
        do_not_use_when=["Pure backend, database, or infrastructure work."],
        negative_queries=[],
        distinguish_from={},
    )

    after = normalize_skill_metadata(
        read_skill_frontmatter(skill_file),
        source="workspace",
        body_path=str(skill_file),
        folder="humanizer-zh-main",
    )
    assert after.metadata_status == "ready"
    assert after.assignable is True
    assert after.tags == ("editing", "writing", "humanizer")
    assert after.use_when == ("Edit or review text to remove AI writing traces.",)


def test_apply_metadata_tuning_rejects_yaml_control_characters_and_non_workspace_ids(tmp_path):
    from runtime.skill_library.metadata_editor import apply_skill_metadata_tuning

    skill_file = tmp_path / ".lucode" / "skills" / "release_review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    original = "---\nid: release_review\n---\n\n# Original body\n"
    skill_file.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="single line"):
        apply_skill_metadata_tuning(
            workspace_root=tmp_path,
            skill_id="release_review",
            negative_queries=["safe\nfrontmatter: injected"],
            distinguish_from={},
        )
    with pytest.raises(ValueError, match="workspace Skills"):
        apply_skill_metadata_tuning(
            workspace_root=tmp_path,
            skill_id="../release_review",
            negative_queries=[],
            distinguish_from={},
        )

    assert skill_file.read_text(encoding="utf-8") == original


def test_metadata_tuner_uses_planner_rejection_without_changing_skill_files(tmp_path):
    from runtime.skill_library.metadata_tuner import suggest_metadata_tuning

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("original body", encoding="utf-8")
    suggestions = suggest_metadata_tuning(
        [
            {
                "skill_id": "context_ledger",
                "query": "Fix React component",
                "result": "rejected_by_planner",
                "reason": "frontend task",
            }
        ],
        existing_negative_queries={"context_ledger": ["old misfire"]},
    )

    assert suggestions[0].suggested_negative_queries == ("Fix React component",)
    assert skill_file.read_text(encoding="utf-8") == "original body"


def test_metadata_tuner_deduplicates_existing_queries_and_redacts_sensitive_text():
    from runtime.skill_library.metadata_tuner import suggest_metadata_tuning

    suggestions = suggest_metadata_tuning(
        [
            {"skill_id": "context_ledger", "query": "UI task", "misfire": True, "reason": "ui"},
            {"skill_id": "context_ledger", "query": "UI task", "misfire": True, "reason": "ui"},
            {"skill_id": "context_ledger", "query": "API_KEY=secret-value", "misfire": True, "reason": "secret"},
        ],
        existing_negative_queries={"context_ledger": ["UI task"]},
    )

    assert len(suggestions) == 1
    assert suggestions[0].suggested_negative_queries == ()
    assert "secret-value" not in str(suggestions[0])


def test_metadata_tuner_loads_usage_records_and_ignores_non_negative_outcomes(tmp_path):
    from runtime.skill_library.metadata_tuner import suggest_metadata_tuning_from_usage
    from runtime.skill_library.usage import SkillUsageTracker

    tracker = SkillUsageTracker(tmp_path)
    tracker.record(skill_id="ui_refactor", query="Fix UI", task_id="task", result="success")
    tracker.record(skill_id="ui_refactor", query="Run context compaction", task_id="", result="rejected_by_planner", reason="not UI")

    suggestions = suggest_metadata_tuning_from_usage(tmp_path)

    assert len(suggestions) == 1
    assert suggestions[0].skill_id == "ui_refactor"
    assert suggestions[0].suggested_negative_queries == ("Run context compaction",)
