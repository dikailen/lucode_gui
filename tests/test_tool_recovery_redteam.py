from __future__ import annotations

import pytest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.journal import RunJournal
from runtime.recovery.policy import (
    evaluate_recovery_plan,
    recovery_blocked_items_for_tool_invocations,
    recovery_compatibility_for_plan,
)


@pytest.mark.parametrize(
    ("task_id", "mcp_id", "tool_name", "side_effect_class"),
    [
        ("browser_task", "desktop_browser", "desktop_browser.browser_submit_form", "non_idempotent"),
        ("terminal_task", "command_runner", "command_runner.run_command", "unknown"),
        ("mcp_task", "private_crm", "private_crm.update_record", "non_idempotent"),
    ],
)
def test_dispatched_mutation_is_unknown_after_restart_and_never_reenters_a_safe_plan(
    tmp_path,
    task_id,
    mcp_id,
    tool_name,
    side_effect_class,
):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id=f"invocation_{task_id}",
        run_id="run_1",
        task_id=task_id,
        attempt=1,
        tool_name=tool_name,
        arguments_hash="hash_a",
        side_effect_class=side_effect_class,
    )
    journal.mark_tool_invocation_dispatched(f"invocation_{task_id}")

    decisions = RecoveryCoordinator(journal).scan_startup()
    assert [decision.run_id for decision in decisions] == ["run_1"]
    invocations = journal.tool_invocations_for_run("run_1")
    assert invocations[0]["status"] == "unknown"

    task = PlannedTask(
        id=task_id,
        title="unsafe task",
        instruction="continue prior task",
        skill_id="project_explorer",
        model="model-a",
        mcp=[mcp_id],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    blocked_items = recovery_blocked_items_for_tool_invocations(invocations)
    envelope = RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": blocked_items,
        },
        compatibility=recovery_compatibility_for_plan(plan),
        blocked_items=tuple(blocked_items),
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == (task_id,)


def test_recovery_claim_carries_an_unknown_tool_blocker_into_the_envelope(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )
    journal.prepare_tool_invocation(
        invocation_id="invocation_browser",
        run_id="run_1",
        task_id="browser_task",
        attempt=1,
        tool_name="desktop_browser.browser_submit_form",
        arguments_hash="hash_a",
        side_effect_class="non_idempotent",
    )
    journal.mark_tool_invocation_dispatched("invocation_browser")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    assert claim.envelope.blocked_items == (
        {"task_id": "browser_task", "reason_code": "tool_unknown_side_effect"},
    )
    assert claim.envelope.checkpoint["blocked_items"] == [
        {"task_id": "browser_task", "reason_code": "tool_unknown_side_effect"},
    ]


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        ("completed", "tool_completed_mutation"),
        ("failed", "tool_failed_mutation"),
    ],
)
def test_terminal_mutation_blocks_a_task_even_when_its_planned_mcp_scope_looks_readonly(status, reason_code):
    task = PlannedTask(
        id="browser_task",
        title="browser task",
        instruction="submit the form through the embedded browser",
        skill_id="project_explorer",
        model="model-a",
        mcp=["project_filesystem_readonly"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    blocked_items = recovery_blocked_items_for_tool_invocations(
        [
            {
                "task_id": "browser_task",
                "status": status,
                "side_effect_class": "non_idempotent",
            }
        ]
    )
    envelope = RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": blocked_items,
        },
        compatibility=recovery_compatibility_for_plan(plan),
        blocked_items=tuple(blocked_items),
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert blocked_items == [{"task_id": "browser_task", "reason_code": reason_code}]
    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == ("browser_task",)


def test_unavailable_tool_lifecycle_data_blocks_all_safe_resume_tasks(tmp_path, monkeypatch):
    task = PlannedTask(
        id="read_task",
        title="read task",
        instruction="inspect the project",
        skill_id="project_explorer",
        model="model-a",
        mcp=["project_filesystem_readonly"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
    )
    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()

    def unavailable(_run_id):
        raise OSError("journal unavailable")

    monkeypatch.setattr(journal, "tool_invocations_for_run", unavailable)
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    assert claim.envelope.blocked_items == (
        {"task_id": "", "reason_code": "tool_lifecycle_unavailable"},
    )
    decision = evaluate_recovery_plan(
        claim.envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == ("read_task",)


def test_unscoped_dispatched_mutation_blocks_all_safe_resume_tasks(tmp_path):
    """A missing task scope cannot make a persisted mutation disappear at recovery."""

    task = PlannedTask(
        id="read_task",
        title="read task",
        instruction="inspect the project",
        skill_id="project_explorer",
        model="model-a",
        mcp=["project_filesystem_readonly"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
    )
    journal.prepare_tool_invocation(
        invocation_id="invocation_unscoped",
        run_id="run_1",
        task_id="",
        attempt=1,
        tool_name="workspace_edit.write_file",
        arguments_hash="hash_a",
        side_effect_class="non_idempotent",
    )
    journal.mark_tool_invocation_dispatched("invocation_unscoped")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    decision = evaluate_recovery_plan(
        claim.envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert claim.envelope.blocked_items == (
        {"task_id": "", "reason_code": "tool_unknown_side_effect"},
    )
    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == ("read_task",)


def test_checkpoint_read_failure_releases_a_recovery_lease_before_degrading(tmp_path, monkeypatch):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()

    def unavailable(_run_id):
        raise OSError("checkpoint storage unavailable")

    monkeypatch.setattr(journal, "latest_checkpoint_for_run", unavailable)

    with pytest.raises(OSError, match="checkpoint storage unavailable"):
        coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    record = journal.recovery_run("run_1")
    assert record is not None
    assert record["recovery_state"] == "interrupted"
