from __future__ import annotations

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState
from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.policy import evaluate_recovery_plan, recovery_compatibility_for_plan


def _reconciled_file_envelope(plan: PlannerResult) -> RecoveryEnvelope:
    return RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
        reconciled_items=(
            {
                "invocation_id": "invocation_1",
                "task_id": "write_task",
                "tool_name": "workspace_edit.write_file",
                "kind": "workspace_file_sha256.v1",
                "evidence_ref": "postcondition:invocation_1:0123456789abcdef",
                "source_run_id": "run_1",
                "checkpoint_id": "checkpoint_1",
                "event_seq": 17,
                "observed": {"path": "notes.txt", "sha256": "a" * 64},
            },
        ),
    )


def _reconciled_browser_envelope(plan: PlannerResult) -> RecoveryEnvelope:
    return RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
        reconciled_items=(
            {
                "invocation_id": "invocation_1",
                "task_id": "browser_task",
                "tool_name": "desktop_browser.browser_navigate",
                "kind": "browser_navigation_url_sha256.v1",
                "evidence_ref": "postcondition:invocation_1:0123456789abcdef",
                "source_run_id": "run_1",
                "checkpoint_id": "checkpoint_1",
                "event_seq": 17,
                "observed": {"tab_id": "tab_9", "url_sha256": "a" * 64},
            },
        ),
    )


def test_reconciled_file_write_enters_final_evidence_only_with_durable_bindings(tmp_path):
    task = PlannedTask(
        id="write_task",
        title="write notes",
        instruction="write the requested file",
        skill_id="code_engineer",
        model="model-a",
        mcp=["workspace_edit"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    envelope = _reconciled_file_envelope(plan)
    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    run_state = PipelineRunState.create("continue", plan, project_root=tmp_path)

    run_state.apply_recovery_plan(decision, envelope=envelope)

    packet = run_state.accepted_evidence
    assert packet["mode"] == "recovery_verified"
    assert packet["claims"] == [
        {
            "claim_id": "recovery-postcondition:invocation_1",
            "task_id": "write_task",
            "text": "Recovered verified workspace file postcondition: notes.txt",
            "claim_type": "code_change",
            "confidence": 1.0,
            "evidence_refs": ["postcondition:invocation_1:0123456789abcdef"],
            "risk_level": "high",
        }
    ]
    assert packet["evidence"] == [
        {
            "ref_id": "postcondition:invocation_1:0123456789abcdef",
            "kind": "file_write",
            "source": "recovery_postcondition",
            "event_seq": 17,
            "excerpt": "notes.txt",
            "sha256": "a" * 64,
            "invocation_id": "invocation_1",
            "checkpoint_id": "checkpoint_1",
            "run_id": "run_1",
        }
    ]
    assert packet["blocked_claims"] == []


def test_reconciled_browser_navigation_enters_final_evidence_without_a_raw_url(tmp_path):
    task = PlannedTask(
        id="browser_task",
        title="inspect report page",
        instruction="continue browser navigation",
        skill_id="browser_operator",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    envelope = _reconciled_browser_envelope(plan)
    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    run_state = PipelineRunState.create("continue", plan, project_root=tmp_path)

    run_state.apply_recovery_plan(decision, envelope=envelope)

    assert decision.reusable_task_ids == ("browser_task",)
    assert run_state.accepted_evidence == {
        "mode": "recovery_verified",
        "claims": [
            {
                "claim_id": "recovery-postcondition:invocation_1",
                "task_id": "browser_task",
                "text": "Recovered verified browser navigation: tab tab_9",
                "claim_type": "browser_fact",
                "confidence": 1.0,
                "evidence_refs": ["postcondition:invocation_1:0123456789abcdef"],
                "risk_level": "high",
            }
        ],
        "evidence": [
            {
                "ref_id": "postcondition:invocation_1:0123456789abcdef",
                "kind": "browser_summary",
                "source": "recovery_postcondition",
                "event_seq": 17,
                "excerpt": "tab_id=tab_9",
                "url_sha256": "a" * 64,
                "invocation_id": "invocation_1",
                "checkpoint_id": "checkpoint_1",
                "run_id": "run_1",
            }
        ],
        "blocked_claims": [],
    }


def test_malformed_reconciliation_hash_never_enters_recovery_or_final_evidence(tmp_path):
    task = PlannedTask(
        id="write_task",
        title="write notes",
        instruction="write the requested file",
        skill_id="code_engineer",
        model="model-a",
        mcp=["workspace_edit"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    payload = _reconciled_file_envelope(plan).to_dict()
    payload["reconciled_items"][0]["observed"]["sha256"] = "g" * 64
    envelope = RecoveryEnvelope.from_dict(payload)
    assert envelope is not None

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )
    run_state = PipelineRunState.create("continue", plan, project_root=tmp_path)
    run_state.apply_recovery_plan(decision, envelope=envelope)

    assert decision.reusable_task_ids == ()
    assert decision.blocked_task_ids == ("write_task",)
    assert run_state.accepted_evidence["evidence"] == []
