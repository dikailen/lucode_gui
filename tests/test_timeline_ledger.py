from __future__ import annotations

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.consistency.timeline import (
    RunTimeline,
    reliability_flags_from_env,
    timeline_mode_from_env,
)
from runtime.execution.pipeline import PipelineRunState


def _task(task_id: str = "inspect") -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title="Inspect project",
        instruction="Inspect project files",
        skill_id="project_explorer",
        model="worker",
        mcp=["project_filesystem_readonly"],
        read_set=["runtime"],
    )


def _plan() -> PlannerResult:
    return PlannerResult(
        route_type="single_agent",
        reason="timeline test",
        refined_request="inspect",
        tasks=[_task()],
    )


def test_timeline_mode_defaults_to_observe(monkeypatch):
    monkeypatch.delenv("LUCODE_TIMELINE_LEDGER", raising=False)

    assert timeline_mode_from_env() == "observe"


def test_reliability_flags_default_to_safe_modes(monkeypatch):
    monkeypatch.delenv("LUCODE_TIMELINE_LEDGER", raising=False)
    monkeypatch.delenv("LUCODE_EVIDENCE_GATE", raising=False)
    monkeypatch.delenv("LUCODE_COMPUTE_PLACEMENT", raising=False)

    flags = reliability_flags_from_env()

    assert flags.timeline_ledger == "observe"
    assert flags.evidence_gate == "warn"
    assert flags.compute_placement == "observe"


def test_timeline_records_monotonic_events_and_task_snapshot(tmp_path):
    timeline = RunTimeline.create(project_root=tmp_path, user_request="inspect")
    task = _task()

    started = timeline.record_task_started(task)
    completed = timeline.record_task_completed(task)

    assert started.event_seq == 1
    assert completed.event_seq == 2
    assert timeline.snapshot_for_task("inspect") is not None
    assert timeline.snapshot_for_task("inspect").created_at_event_seq == 1
    assert [event.event_type for event in timeline.snapshot()] == [
        "TaskStarted",
        "TaskCompleted",
    ]


def test_timeline_off_mode_records_no_events(tmp_path):
    timeline = RunTimeline.create(project_root=tmp_path, user_request="inspect", mode="off")

    event = timeline.record_task_started(_task())

    assert event is None
    assert timeline.to_dict()["mode"] == "off"
    assert timeline.to_dict()["events"] == []
    assert timeline.to_dict()["task_snapshots"] == []


def test_pipeline_run_state_creates_observe_timeline(tmp_path):
    state = PipelineRunState.create("inspect", _plan(), project_root=tmp_path, mode="auto")
    task = _task()

    state.record_task_started(task)
    state.record_task_result(task, "done")

    timeline = state.to_dict()["timeline"]
    assert timeline["mode"] == "observe"
    assert [event["event_type"] for event in timeline["events"]] == [
        "TaskStarted",
        "TaskCompleted",
    ]
    assert timeline["task_snapshots"][0]["task_id"] == "inspect"


def test_pipeline_run_context_records_tool_and_file_events(tmp_path):
    state = PipelineRunState.create("inspect", _plan(), project_root=tmp_path, mode="auto")
    target = tmp_path / "runtime.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    state.run_context.record_file_snapshot(path=target, task_id="inspect", summary="read runtime")
    state.run_context.record_tool_output(
        tool="project_filesystem_readonly",
        action="read_file",
        summary="read runtime.py",
        task_id="inspect",
    )

    timeline = state.to_dict()["timeline"]
    event_types = [event["event_type"] for event in timeline["events"]]
    assert "FileSnapshotRecorded" in event_types
    assert "ToolOutputRecorded" in event_types


def test_pipeline_mirrors_generic_tool_events_to_timeline(tmp_path):
    state = PipelineRunState.create("inspect", _plan(), project_root=tmp_path, mode="auto")

    state.emit_event(
        "ToolInvoked",
        "browser summary read",
        task_id="inspect",
        status="completed",
        payload={"tool": "desktop_browser", "action": "browser_get_page_summary"},
    )

    timeline = state.to_dict()["timeline"]
    assert timeline["events"][0]["event_type"] == "ToolInvoked"
    assert timeline["events"][0]["resource_refs"] == ["tool:desktop_browser"]
