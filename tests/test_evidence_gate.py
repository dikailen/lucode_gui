from __future__ import annotations

from runtime.agent.supervisor import WorkerReport
from runtime.evidence.gate import accepted_evidence_packet, evaluate_claims, run_evidence_gate_for_reports
from runtime.evidence.schema import Claim, EvidenceRef
from runtime.evidence.extractor import extract_claims_from_worker_report, evidence_refs_from_worker_report
from runtime.consistency.timeline import RunTimeline
from runtime.execution.pipeline import PipelineRunState
from planning.planner_schema import PlannedTask, PlannerResult


def test_claim_without_runtime_evidence_needs_recheck():
    claim = Claim(
        claim_id="claim:worker-a:summary",
        task_id="worker-a",
        text="Worker says the file was changed.",
        claim_type="code_change",
        risk_level="high",
        evidence_refs=[],
    )

    verdicts = evaluate_claims([claim], available_evidence=[])

    assert verdicts[0].status == "needs_recheck"
    assert "missing_evidence" in verdicts[0].reasons


def test_claim_with_file_and_tool_evidence_is_accepted():
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Updated runtime/auth.py.",
        files_written=["runtime/auth.py"],
        tool_calls=[{"tool": "workspace_edit", "action": "write_file", "status": "completed"}],
    )
    evidence = evidence_refs_from_worker_report(report)
    claims = extract_claims_from_worker_report(report, available_evidence=evidence)

    verdicts = evaluate_claims(claims, available_evidence=evidence)

    assert any(claim.claim_type == "code_change" for claim in claims)
    assert {verdict.status for verdict in verdicts} == {"accepted"}


def test_browser_summary_tool_output_is_typed_as_browser_evidence():
    report = WorkerReport(
        task_id="browser",
        status="completed",
        summary="Read browser page title Example Domain.",
        tool_calls=[{"tool": "desktop_browser", "action": "browser_get_page_summary", "status": "completed"}],
    )

    evidence = evidence_refs_from_worker_report(report)
    claims = extract_claims_from_worker_report(report, available_evidence=evidence)
    verdicts = evaluate_claims(claims, available_evidence=evidence)

    assert any(item.kind == "browser_summary" for item in evidence)
    assert {verdict.status for verdict in verdicts} == {"accepted"}


def test_command_output_tool_output_is_typed_as_command_evidence():
    report = WorkerReport(
        task_id="verify",
        status="completed",
        summary="pytest passed.",
        tool_calls=[{"tool": "command_runner", "action": "run_command", "status": "completed"}],
    )

    evidence = evidence_refs_from_worker_report(report)
    claims = extract_claims_from_worker_report(report, available_evidence=evidence)
    verdicts = evaluate_claims(claims, available_evidence=evidence)

    assert any(item.kind == "command_output" for item in evidence)
    assert {verdict.status for verdict in verdicts} == {"accepted"}


def test_forged_evidence_ref_is_rejected():
    claim = Claim(
        claim_id="claim:worker-a:summary",
        task_id="worker-a",
        text="Worker cites a made up evidence id.",
        claim_type="observation",
        risk_level="normal",
        evidence_refs=["tool:fake_tool"],
    )
    evidence = [EvidenceRef(ref_id="tool:workspace_edit", kind="tool_output", source="worker-a")]

    verdicts = evaluate_claims([claim], available_evidence=evidence)

    assert verdicts[0].status == "rejected"
    assert "unknown_evidence_ref:tool:fake_tool" in verdicts[0].reasons


def test_planned_timeline_resources_do_not_count_as_actual_evidence():
    timeline = RunTimeline.create(user_request="fix auth")
    task = type("Task", (), {"id": "worker-a", "title": "Fix auth", "read_set": [], "write_intent": ["runtime/auth.py"], "mcp": []})()
    timeline.record_task_started(task)
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Claims runtime/auth.py was changed.",
    )

    evidence = evidence_refs_from_worker_report(report, timeline=timeline)
    claims = extract_claims_from_worker_report(report, available_evidence=evidence)
    verdicts = evaluate_claims(claims, available_evidence=evidence)

    assert "write:runtime/auth.py" not in {item.ref_id for item in evidence}
    assert verdicts[0].status == "needs_recheck"


def test_accepted_evidence_packet_excludes_unaccepted_claim_text():
    accepted = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Updated runtime/auth.py.",
        files_written=["runtime/auth.py"],
        tool_calls=[{"tool": "workspace_edit", "action": "write_file", "status": "completed"}],
    )
    blocked = WorkerReport(
        task_id="worker-b",
        status="completed",
        summary="No runtime evidence.",
        artifacts=["claimed_changes: secretly changed runtime/secrets.py"],
    )

    result = run_evidence_gate_for_reports([accepted, blocked], mode="enforce_high_risk")
    packet = accepted_evidence_packet(result)
    rendered = str(packet)

    assert any(claim["claim_id"] == "claim:worker-a:code_change" for claim in packet["claims"])
    assert "write:runtime/auth.py" in {item["ref_id"] for item in packet["evidence"]}
    assert "claim:worker-b:artifact:1" in {item["claim_id"] for item in packet["blocked_claims"]}
    assert "secretly changed runtime/secrets.py" not in rendered


def test_pipeline_state_persists_evidence_and_accepted_packet(tmp_path):
    task = PlannedTask(
        id="worker-a",
        title="Fix auth",
        instruction="Fix auth validation",
        skill_id="code_engineer",
        model="worker",
        mcp=["workspace_edit"],
        write_intent=["runtime/auth.py"],
    )
    plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="fix auth", tasks=[task])
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Updated runtime/auth.py.",
        files_written=["runtime/auth.py"],
        tool_calls=[{"tool": "workspace_edit", "action": "write_file", "status": "completed"}],
    )

    run_evidence_gate_for_reports([report], run_state=state, mode="enforce_high_risk")
    evidence = state.to_dict()["evidence"]

    assert evidence["evidence"]
    assert evidence["accepted_evidence"]["claims"]
    assert evidence["accepted_evidence"]["evidence"]
