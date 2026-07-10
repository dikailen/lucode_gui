from __future__ import annotations


def test_metadata_tuner_suggests_negative_query_for_misfire():
    from runtime.skill_library.metadata_tuner import suggest_metadata_tuning

    suggestions = suggest_metadata_tuning(
        [
            {
                "skill_id": "context_ledger",
                "query": "按钮样式不好看",
                "misfire": True,
                "reason": "这是 UI 任务",
            }
        ]
    )

    assert suggestions[0].skill_id == "context_ledger"
    assert suggestions[0].suggested_negative_queries == ("按钮样式不好看",)
    assert suggestions[0].suggested_distinguish_from["ui"] == "这是 UI 任务"


def test_metadata_tuner_uses_planner_rejection_without_changing_skill_files(tmp_path):
    from runtime.skill_library.metadata_tuner import suggest_metadata_tuning

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("original body", encoding="utf-8")
    suggestions = suggest_metadata_tuning(
        [
            {
                "skill_id": "context_ledger",
                "query": "修复 React 组件",
                "result": "rejected_by_planner",
                "reason": "frontend task",
            }
        ],
        existing_negative_queries={"context_ledger": ["旧误召回"]},
    )

    assert suggestions[0].suggested_negative_queries == ("修复 React 组件",)
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
