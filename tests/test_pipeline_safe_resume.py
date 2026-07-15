from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.policy import evaluate_recovery_plan, recovery_compatibility_for_plan
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.execution_contract import normalize_execution_contract
from runtime.execution.multi_agent_runner import _run_multi_agent
from runtime.execution.single_agent_runner import _run_single_agent


def _task(
    task_id: str,
    *,
    mcp: list[str] | None = None,
    write_intent: list[str] | None = None,
) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=f"Task {task_id}",
        instruction=f"Complete {task_id}",
        skill_id="project_explorer",
        model="model-a",
        mcp=list(mcp or []),
        write_intent=list(write_intent or []),
    )


def _plan(*tasks: PlannedTask) -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent" if len(tasks) > 1 else "single_agent",
        reason="test plan",
        refined_request="continue safely",
        tasks=list(tasks),
        needs_synthesis=len(tasks) > 1,
    )


def _envelope(plan: PlannerResult, *, checkpoint_tasks: list[dict], accepted_evidence: dict) -> RecoveryEnvelope:
    compatibility = recovery_compatibility_for_plan(plan)
    return RecoveryEnvelope(
        source_run_id="run_interrupted",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="evidence.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": plan.route_type,
            "reason": "previous plan",
            "tasks": checkpoint_tasks,
            "accepted_evidence": accepted_evidence,
            "blocked_items": [],
        },
        compatibility=compatibility,
    )


def test_safe_resume_reuses_only_completed_tasks_with_accepted_evidence():
    task_a = _task("task_a", mcp=["project_filesystem_readonly"])
    task_b = _task("task_b", mcp=["code_locator"])
    task_c = _task("task_c", mcp=["project_filesystem_readonly"])
    plan = _plan(task_a, task_b, task_c)
    envelope = _envelope(
        plan,
        checkpoint_tasks=[
            {
                "id": "task_a",
                "title": "Task task_a",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["project_filesystem_readonly"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "completed",
                "side_effect_class": "read_only",
            },
            {
                "id": "task_b",
                "title": "Task task_b",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["code_locator"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "running",
                "side_effect_class": "read_only",
            },
            {
                "id": "task_c",
                "title": "Task task_c",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["project_filesystem_readonly"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "completed",
                "side_effect_class": "read_only",
            },
        ],
        accepted_evidence={
            "mode": "enforce_all",
            "claims": [
                {
                    "claim_id": "claim:task_a:summary",
                    "task_id": "task_a",
                    "text": "Task A inspected the file.",
                    "evidence_refs": ["read:a.txt"],
                }
            ],
            "evidence": [
                {
                    "ref_id": "read:a.txt",
                    "kind": "file_snapshot",
                    "source": "task_a",
                    "excerpt": "sha256:abc",
                }
            ],
            "blocked_claims": [],
        },
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.disposition == "continue_safe"
    assert decision.reusable_task_ids == ("task_a",)
    assert decision.executable_task_ids == ("task_b", "task_c")
    assert decision.blocked_task_ids == ()
    assert "Task A inspected the file." in decision.reused_outputs["task_a"]


def test_safe_resume_blocks_unknown_or_write_tasks_instead_of_replaying_them():
    readonly = _task("read", mcp=["project_filesystem_readonly"])
    browser = _task("browser", mcp=["desktop_browser"])
    writer = _task("writer", mcp=["workspace_edit"], write_intent=["src/app.py"])
    plan = _plan(readonly, browser, writer)
    envelope = _envelope(
        plan,
        checkpoint_tasks=[
            {
                "id": task.id,
                "title": task.title,
                "skill_id": task.skill_id,
                "model": task.model,
                "mcp": list(task.mcp),
                "depends_on": [],
                "read_set": [],
                "write_intent": list(task.write_intent),
                "status": "pending",
                "side_effect_class": "read_only" if task.id == "read" else "unknown",
            }
            for task in plan.tasks
        ],
        accepted_evidence={"mode": "enforce_all", "claims": [], "evidence": [], "blocked_claims": []},
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.executable_task_ids == ("read",)
    assert decision.blocked_task_ids == ("browser", "writer")


def test_safe_resume_forces_replan_when_compatibility_changes():
    plan = _plan(_task("task_a", mcp=["project_filesystem_readonly"]))
    envelope = _envelope(
        plan,
        checkpoint_tasks=[],
        accepted_evidence={"mode": "enforce_all", "claims": [], "evidence": [], "blocked_claims": []},
    )
    changed = dict(recovery_compatibility_for_plan(plan))
    changed["tool_registry_hash"] = "changed-tools"

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=changed,
    )

    assert decision.disposition == "replan"
    assert decision.reusable_task_ids == ()
    assert decision.reason_code == "compatibility_mismatch"


def test_recovery_compatibility_changes_when_a_bound_skill_body_changes(monkeypatch):
    task = _task("task_a", mcp=["project_filesystem_readonly"])
    task.bound_skill_ids = ["example_skill"]
    plan = _plan(task)
    bodies = {"project_explorer": "primary v1", "example_skill": "bound v1"}
    monkeypatch.setattr("skills.loader.load_skill", lambda skill_id: bodies[skill_id])

    first = recovery_compatibility_for_plan(plan)
    bodies["example_skill"] = "bound v2"
    second = recovery_compatibility_for_plan(plan)

    assert first["skill_versions"] != second["skill_versions"]


def test_recovery_compatibility_changes_when_core_mcp_metadata_changes(monkeypatch):
    from runtime.tools.registry import CORE_SERVER_METADATA

    plan = _plan(_task("task_a", mcp=["project_filesystem_readonly"]))
    first = recovery_compatibility_for_plan(plan)
    monkeypatch.setitem(
        CORE_SERVER_METADATA["project_filesystem_readonly"],
        "budget_policy",
        "changed readonly budget",
    )
    second = recovery_compatibility_for_plan(plan)

    assert first["tool_registry_hash"] != second["tool_registry_hash"]
    assert first["mcp_manifest_hashes"] != second["mcp_manifest_hashes"]


def test_single_agent_reuses_accepted_evidence_without_calling_the_worker(tmp_path, monkeypatch):
    task = _task("task_a", mcp=["project_filesystem_readonly"])
    plan = _plan(task)
    envelope = _envelope(
        plan,
        checkpoint_tasks=[
            {
                "id": "task_a",
                "title": "Task task_a",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["project_filesystem_readonly"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "completed",
                "side_effect_class": "read_only",
            }
        ],
        accepted_evidence={
            "mode": "enforce_all",
            "claims": [
                {
                    "claim_id": "claim:task_a:summary",
                    "task_id": "task_a",
                    "text": "Recovered accepted fact.",
                    "evidence_refs": ["read:a.txt"],
                }
            ],
            "evidence": [
                {"ref_id": "read:a.txt", "kind": "file_snapshot", "source": "task_a", "excerpt": "hash"}
            ],
            "blocked_claims": [],
        },
    )
    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    state = PipelineRunState.create("continue safely", plan, project_root=tmp_path)
    state.apply_recovery_plan(decision, envelope=envelope)
    worker_calls = []

    async def run_agent(*args, **kwargs):
        worker_calls.append((args, kwargs))
        raise AssertionError("reused task must not call the worker")

    monkeypatch.setattr(
        "runtime.execution.single_agent_runner.audit_execution",
        lambda *args, **kwargs: SimpleNamespace(passed=True),
    )
    monkeypatch.setattr(
        "runtime.execution.single_agent_runner.format_final_report",
        lambda output, audit: output,
    )
    output, audit = asyncio.run(
        _run_single_agent(
            "continue safely",
            plan,
            tmp_path,
            factory=SimpleNamespace(),
            hooks=SimpleNamespace(),
            run_agent=run_agent,
            run_state=state,
            flywheel=SimpleNamespace(),
            execution_mode="auto",
            show_plan=False,
            attempt=1,
        )
    )

    assert worker_calls == []
    assert "Recovered accepted fact." in output
    assert audit.passed


def test_multi_agent_partial_resume_does_not_reschedule_the_accepted_task(tmp_path, monkeypatch):
    task_a = _task("task_a", mcp=["project_filesystem_readonly"])
    task_b = _task("task_b", mcp=["code_locator"])
    task_a.parallel_group = 1
    task_b.parallel_group = 1
    plan = _plan(task_a, task_b)
    normalize_execution_contract(plan, "continue the readonly inspection", mode="auto")
    envelope = _envelope(
        plan,
        checkpoint_tasks=[
            {
                "id": "task_a",
                "title": "Task task_a",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["project_filesystem_readonly"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "completed",
                "side_effect_class": "read_only",
            },
            {
                "id": "task_b",
                "title": "Task task_b",
                "skill_id": "project_explorer",
                "model": "model-a",
                "mcp": ["code_locator"],
                "depends_on": [],
                "read_set": [],
                "write_intent": [],
                "status": "running",
                "side_effect_class": "read_only",
            },
        ],
        accepted_evidence={
            "mode": "enforce_all",
            "claims": [
                {
                    "claim_id": "claim:task_a:summary",
                    "task_id": "task_a",
                    "text": "Task A accepted result.",
                    "evidence_refs": ["read:a.txt"],
                }
            ],
            "evidence": [
                {"ref_id": "read:a.txt", "kind": "file_snapshot", "source": "task_a", "excerpt": "hash"}
            ],
            "blocked_claims": [],
        },
    )
    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    state = PipelineRunState.create("continue safely", plan, project_root=tmp_path)
    state.apply_recovery_plan(decision, envelope=envelope)
    worker_calls: list[str] = []

    async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, run_state, ledger, **kwargs):
        worker_calls.append(task.id)
        output = f"fresh output for {task.id}"
        run_state.record_task_result(task, output)
        return task.title, output

    monkeypatch.setattr("runtime.execution.multi_agent_runner._run_planned_task", fake_run_planned_task)
    monkeypatch.setattr("runtime.execution.multi_agent_runner._apply_supervisor_budget_profile", lambda *args: False)
    monkeypatch.setattr("runtime.execution.multi_agent_runner._review_worker_reports", lambda *args, **kwargs: [])

    output = asyncio.run(
        _run_multi_agent(
            "continue safely",
            plan,
            tmp_path,
            "model-a",
            SimpleNamespace(mcp_manager=None, capability_resolver=None),
            SimpleNamespace(),
            run_agent=None,
            run_state=state,
            execution_mode="auto",
            show_progress=False,
        )
    )

    assert worker_calls == ["task_b"]
    assert state.tasks[0].status == "completed"
    assert state.tasks[1].status == "completed"
    assert "Task A accepted result." in output
