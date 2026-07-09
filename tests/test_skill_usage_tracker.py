from __future__ import annotations

import json

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState


def _task(*, task_id: str = "task_ui", bound_skill_ids: list[str] | None = None) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title="Fix UI",
        instruction="Fix desktop/src/App.tsx layout.",
        skill_id="code_engineer",
        model="worker-model",
        bound_skill_ids=list(bound_skill_ids or []),
        write_intent=["desktop/src/App.tsx"],
        acceptance_criteria=["UI layout is stable."],
    )


def _read_usage(path):
    usage_path = path / ".lucode" / "skills" / "usage.jsonl"
    if not usage_path.exists():
        return []
    return [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_usage_tracker_writes_jsonl_records(tmp_path):
    from runtime.skill_library.usage import SkillUsageTracker

    tracker = SkillUsageTracker(tmp_path)

    tracker.record(
        skill_id="electron_ui_refactor",
        query="Fix desktop UI.",
        task_id="task_ui",
        result="success",
        files_touched=["desktop/src/App.tsx"],
        verification=["python -m pytest tests/test_ui.py -q"],
        reason="task completed",
    )
    tracker.record(
        skill_id="context_ledger",
        query="Fix desktop UI.",
        task_id="",
        result="rejected_by_planner",
        misfire=True,
        reason="candidate not adopted",
    )

    records = _read_usage(tmp_path)

    assert [record["schema_version"] for record in records] == ["skill_usage.v1", "skill_usage.v1"]
    assert records[0]["skill_id"] == "electron_ui_refactor"
    assert records[0]["result"] == "success"
    assert records[0]["misfire"] is False
    assert records[0]["files_touched"] == ["desktop/src/App.tsx"]
    assert records[0]["verification"] == ["python -m pytest tests/test_ui.py -q"]
    assert records[1]["skill_id"] == "context_ledger"
    assert records[1]["result"] == "rejected_by_planner"
    assert records[1]["misfire"] is True
    assert records[1]["reason"] == "candidate not adopted"


def test_usage_tracker_is_exported_from_skill_library_package():
    from runtime.skill_library import SkillUsageTracker

    assert SkillUsageTracker.__name__ == "SkillUsageTracker"


def test_usage_summary_aggregates_feedback_records(tmp_path):
    from runtime.skill_library.usage import SkillUsageTracker, load_usage_summary

    tracker = SkillUsageTracker(tmp_path)
    tracker.record(skill_id="electron-ui-refactor", query="Fix UI.", task_id="task_ui", result="success")
    tracker.record(skill_id="electron_ui_refactor", query="Fix UI.", task_id="task_ui", result="failure")
    tracker.record(
        skill_id="electron_ui_refactor",
        query="Fix UI.",
        task_id="task_ui",
        result="success",
        misfire=True,
    )
    tracker.record(
        skill_id="context_ledger",
        query="Fix UI.",
        task_id="",
        result="rejected_by_planner",
    )

    summary = load_usage_summary(tmp_path)

    assert summary["electron_ui_refactor"]["used_count"] == 3
    assert summary["electron_ui_refactor"]["success_count"] == 2
    assert summary["electron_ui_refactor"]["failure_count"] == 1
    assert summary["electron_ui_refactor"]["misfire_count"] == 1
    assert summary["electron_ui_refactor"]["rejected_by_planner_count"] == 0
    assert summary["electron_ui_refactor"]["last_used_at"]
    assert summary["context_ledger"]["used_count"] == 0
    assert summary["context_ledger"]["rejected_by_planner_count"] == 1


def test_usage_summary_accepts_workspace_or_skill_directory(tmp_path):
    from runtime.skill_library.usage import SkillUsageTracker, load_usage_summary

    tracker = SkillUsageTracker(tmp_path)
    tracker.record(skill_id="electron_ui_refactor", query="Fix UI.", task_id="task_ui", result="success")

    from_workspace = load_usage_summary(tmp_path)
    from_skill_dir = load_usage_summary(tmp_path / ".lucode" / "skills")

    assert from_workspace["electron_ui_refactor"]["used_count"] == 1
    assert from_skill_dir["electron_ui_refactor"]["used_count"] == 1


def test_pipeline_records_planner_rejections_and_bound_skill_success(tmp_path):
    task = _task(bound_skill_ids=["electron_ui_refactor"])
    plan = PlannerResult(
        route_type="single_agent",
        reason="planner selected a skill",
        refined_request="Fix desktop UI.",
        tasks=[task],
        skill_interface={
            "version": 1,
            "candidate_skill_ids": ["electron_ui_refactor", "context_ledger"],
            "adopted_skill_ids": ["electron_ui_refactor"],
            "task_bindings": {"task_ui": ["electron_ui_refactor"]},
            "rejection_reasons": {"context_ledger": "context skill does not match UI work"},
        },
    )

    state = PipelineRunState.create("Fix desktop UI.", plan, project_root=tmp_path, mode="auto")
    state.record_verification("task_ui", "python -m pytest tests/test_ui.py -q")
    state.record_task_result(task, "done")

    records = _read_usage(tmp_path)

    assert [(record["skill_id"], record["result"]) for record in records] == [
        ("context_ledger", "rejected_by_planner"),
        ("electron_ui_refactor", "success"),
    ]
    assert records[0]["reason"] == "context skill does not match UI work"
    assert records[1]["task_id"] == "task_ui"
    assert records[1]["files_touched"] == ["desktop/src/App.tsx"]
    assert records[1]["verification"] == ["python -m pytest tests/test_ui.py -q"]


def test_pipeline_records_failure_for_bound_skill(tmp_path):
    task = _task(bound_skill_ids=["electron_ui_refactor"])
    plan = PlannerResult(
        route_type="single_agent",
        reason="planner selected a skill",
        refined_request="Fix desktop UI.",
        tasks=[task],
        skill_interface={
            "version": 1,
            "candidate_skill_ids": ["electron_ui_refactor"],
            "adopted_skill_ids": ["electron_ui_refactor"],
            "task_bindings": {"task_ui": ["electron_ui_refactor"]},
        },
    )

    state = PipelineRunState.create("Fix desktop UI.", plan, project_root=tmp_path, mode="auto")
    state.record_task_error(task, "worker failed")

    records = _read_usage(tmp_path)

    assert len(records) == 1
    assert records[0]["skill_id"] == "electron_ui_refactor"
    assert records[0]["result"] == "failure"
    assert records[0]["reason"] == "worker failed"


def test_usage_tracker_failures_do_not_break_pipeline(monkeypatch, tmp_path):
    from runtime.skill_library import usage

    task = _task(bound_skill_ids=["electron_ui_refactor"])
    plan = PlannerResult(
        route_type="single_agent",
        reason="planner selected a skill",
        refined_request="Fix desktop UI.",
        tasks=[task],
        skill_interface={
            "version": 1,
            "candidate_skill_ids": ["electron_ui_refactor"],
            "adopted_skill_ids": ["electron_ui_refactor"],
            "task_bindings": {"task_ui": ["electron_ui_refactor"]},
        },
    )

    def broken_record(self, **kwargs):
        del self, kwargs
        raise OSError("disk full")

    monkeypatch.setattr(usage.SkillUsageTracker, "record", broken_record)
    state = PipelineRunState.create("Fix desktop UI.", plan, project_root=tmp_path, mode="auto")

    state.record_task_result(task, "done")

    assert state.tasks[0].status == "completed"
