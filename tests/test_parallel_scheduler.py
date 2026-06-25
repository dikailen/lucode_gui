from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask
from runtime.execution.parallel_scheduler import (
    _execution_batches_for_mode,
    execution_batch_decisions_for_mode,
)


def _task(
    task_id: str,
    *,
    mcp: list[str] | None = None,
    depends_on: list[str] | None = None,
    write_intent: list[str] | None = None,
    read_set: list[str] | None = None,
    resource_locks: list[str] | None = None,
) -> PlannedTask:
    task = PlannedTask(
        id=task_id,
        title=task_id,
        instruction=f"run {task_id}",
        skill_id="code_engineer",
        model="worker",
        mcp=list(mcp or ["project_filesystem_readonly"]),
        depends_on=list(depends_on or []),
        write_intent=list(write_intent or []),
        read_set=list(read_set or []),
        parallel_group=1,
    )
    if resource_locks is not None:
        task.resource_locks = resource_locks
    return task


def _ids(decision) -> list[str]:
    return [task.id for task in decision.tasks]


def test_readonly_independent_tasks_can_run_as_one_parallel_batch():
    tasks = [
        _task("docs", read_set=["README.md"]),
        _task("tests", read_set=["tests/test_gui_control_panel.py"]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto")

    assert [decision.status for decision in decisions] == ["parallel"]
    assert decisions[0].reason == "readonly_no_write_conflict"
    assert _ids(decisions[0]) == ["docs", "tests"]
    assert _execution_batches_for_mode(tasks, "auto") == [tasks]


def test_dependencies_force_serial_batches_with_reason():
    tasks = [
        _task("scan", read_set=["runtime"]),
        _task("summarize", depends_on=["scan"], read_set=["runtime/execution"]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto")

    assert [_ids(decision) for decision in decisions] == [["scan"], ["summarize"]]
    assert decisions[1].status == "serialized"
    assert decisions[1].reason == "dependency"
    assert "scan" in " ".join(decisions[1].details)


def test_overlapping_write_intent_forces_serial_batches_with_reason():
    tasks = [
        _task("theme", mcp=["workspace_edit"], write_intent=["lucode/gui"]),
        _task("button", mcp=["workspace_edit"], write_intent=["lucode/gui/control_panel.py"]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto")

    assert [_ids(decision) for decision in decisions] == [["theme"], ["button"]]
    assert decisions[1].status == "serialized"
    assert decisions[1].reason == "write_conflict"
    assert "lucode/gui" in " ".join(decisions[1].details)


def test_workspace_edit_without_write_intent_is_serialized():
    tasks = [
        _task("unknown-edit", mcp=["workspace_edit"]),
        _task("readonly", read_set=["runtime"]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto")

    assert [_ids(decision) for decision in decisions] == [["unknown-edit"], ["readonly"]]
    assert decisions[0].status == "serialized"
    assert decisions[0].reason == "missing_write_intent"


def test_shared_resource_lock_forces_serial_batches_with_reason():
    tasks = [
        _task("terminal-a", mcp=["command_runner"], resource_locks=["terminal"]),
        _task("terminal-b", mcp=["command_runner"], resource_locks=["terminal"]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto")

    assert [_ids(decision) for decision in decisions] == [["terminal-a"], ["terminal-b"]]
    assert decisions[1].status == "serialized"
    assert decisions[1].reason == "resource_lock"
    assert "terminal" in " ".join(decisions[1].details)


def test_legacy_serial_modes_disable_parallel_without_being_user_visible_modes():
    tasks = [_task("a"), _task("b")]

    decisions = execution_batch_decisions_for_mode(tasks, "serial")

    assert [_ids(decision) for decision in decisions] == [["a"], ["b"]]
    assert [decision.reason for decision in decisions] == ["parallel_disabled", "parallel_disabled"]
    assert _execution_batches_for_mode(tasks, "serial") == [[tasks[0]], [tasks[1]]]

def test_multi_agent_runtime_emits_scheduler_decision_reasons(monkeypatch, tmp_path):
    from planning.planner_schema import PlannerResult
    from runtime.agent.supervisor import WorkerReport
    from runtime.execution import multi_agent_runner as runner
    from runtime.execution.pipeline import PipelineRunState

    tasks = [
        _task("theme", mcp=["workspace_edit"], write_intent=["lucode/gui"]),
        _task("button", mcp=["workspace_edit"], write_intent=["lucode/gui/control_panel.py"]),
    ]
    plan = PlannerResult(
        route_type="multi_agent",
        reason="scheduler event test",
        refined_request="edit gui",
        tasks=tasks,
        needs_synthesis=True,
        synthesis_instruction="summarize",
    )

    async def fake_run_planned_task(*args, **kwargs):
        del args, kwargs
        return "worker", "done"

    async def fake_run_agent(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(final_output="final")

    class FakeReadonlyServer:
        name = "run_workspace_readonly"

    class FakeServerContext:
        async def __aenter__(self):
            return FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeFactory:
        def create_synthesizer_agent(self, model_id, run_workspace_server):
            del model_id, run_workspace_server
            return SimpleNamespace(name="final_synthesizer_agent")

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(
        runner,
        "build_worker_report",
        lambda task, output, run_state=None: WorkerReport(task_id=task.id, status="completed", summary=output),
    )

    monkeypatch.setattr(
        runner,
        "create_readonly_filesystem_server",
        lambda run_dir, server_name: FakeServerContext(),
    )

    state = PipelineRunState.create("edit gui", plan, project_root=tmp_path, mode="auto")

    output = asyncio.run(
        runner._run_multi_agent(
            "edit gui",
            plan,
            tmp_path,
            "model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            execution_mode="auto",
            show_progress=False,
        )
    )

    events = [event.to_dict() for event in state.event_bus.snapshot()]
    serialized = [event for event in events if event["event_type"] == "ParallelBatchSerialized"]

    assert output == "final"
    assert len(serialized) == 1
    assert serialized[0]["payload"]["reason"] == "write_conflict"
    assert serialized[0]["payload"]["task_ids"] == ["button"]
    assert "lucode/gui" in " ".join(serialized[0]["payload"]["details"])
