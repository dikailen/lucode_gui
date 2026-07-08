from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agent.supervisor import WorkerReport
from runtime.execution.lead_reviewer import plan_lead_rework_actions
from runtime.evidence.schema import Claim, EvidenceGateResult, GateVerdict
from runtime.execution.multi_agent_runner import (
    _render_lead_supervisor_output,
    _render_supervisor_finalize_prompt,
    _review_full_worker_reports,
)
from runtime.execution.pipeline import PipelineRunState


def _task() -> PlannedTask:
    return PlannedTask(
        id="worker-a",
        title="Fix auth",
        instruction="Fix auth validation",
        skill_id="code_engineer",
        model="worker",
        mcp=["workspace_edit"],
        write_intent=["runtime/auth.py"],
    )


def _plan() -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent",
        reason="test",
        refined_request="fix auth",
        tasks=[_task()],
    )


def test_observe_evidence_gate_records_verdict_without_changing_findings(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "observe")
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Updated runtime/auth.py.",
        files_written=["runtime/auth.py"],
        tool_calls=[{"tool": "workspace_edit", "action": "write_file", "status": "completed"}],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")

    assert findings == []
    evidence = state.to_dict()["evidence"]
    assert evidence["mode"] == "observe"
    assert evidence["claims"]
    assert {item["status"] for item in evidence["verdicts"]} == {"accepted"}
    assert any(event["event_type"] == "EvidenceGateVerdict" for event in state.to_dict()["events"])


def test_evidence_gate_defaults_to_warn_for_lead_review(monkeypatch, tmp_path):
    monkeypatch.delenv("LUCODE_EVIDENCE_GATE", raising=False)
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Updated runtime/auth.py.",
        files_written=["runtime/auth.py"],
        tool_calls=[{"tool": "workspace_edit", "action": "write_file", "status": "completed"}],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")

    assert findings == []
    assert state.to_dict()["evidence"]["mode"] == "warn"
    assert any(event["event_type"] == "EvidenceGateVerdict" for event in state.to_dict()["events"])


def test_warn_evidence_gate_adds_non_reworkable_lead_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "warn")
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Claims a file change without runtime evidence.",
        artifacts=["claimed_completed: changed runtime/auth.py"],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")
    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 0}, max_attempts=2)

    assert any(finding.kind == "evidence_gate_needs_recheck" for finding in findings)
    assert actions == []
    assert limits == []


def test_enforce_high_risk_evidence_gate_adds_reworkable_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Claims a risky file change without runtime evidence.",
        artifacts=["claimed_changes: changed runtime/auth.py"],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")
    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 0}, max_attempts=2)

    enforced = [finding for finding in findings if finding.kind == "evidence_gate_enforced"]
    assert len(enforced) == 1
    assert enforced[0].severity == "error"
    assert "claim:worker-a:artifact:1" in enforced[0].evidence
    assert len(actions) == 1
    assert actions[0].task_id == "worker-a"
    assert "evidence_gate_enforced" in actions[0].finding_kinds
    assert limits == []


def test_enforce_high_risk_does_not_rework_low_risk_unverified_observation(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Noted a general implementation concern.",
        artifacts=["note: implementation risk needs later discussion"],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")
    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 0}, max_attempts=2)

    assert not any(finding.kind == "evidence_gate_enforced" for finding in findings)
    assert actions == []
    assert limits == []
    evidence = state.to_dict()["evidence"]
    assert evidence["mode"] == "enforce_high_risk"
    assert {item["status"] for item in evidence["verdicts"]} == {"needs_recheck"}


def test_enforce_all_reworks_unaccepted_low_risk_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_all")
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="Noted a general implementation concern.",
        artifacts=["note: implementation risk needs later discussion"],
    )

    findings = _review_full_worker_reports(plan, [report], state, mode="full")
    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 0}, max_attempts=2)

    assert any(finding.kind == "evidence_gate_enforced" for finding in findings)
    assert len(actions) == 1
    assert actions[0].task_id == "worker-a"
    assert limits == []


def test_supervisor_finalize_prompt_includes_evidence_gate_summary_and_rejected_claim_warning(tmp_path):
    plan = _plan()
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    claim = Claim(
        claim_id="claim:worker-a:artifact:1",
        task_id="worker-a",
        text="claimed_changes: changed runtime/auth.py",
        claim_type="code_change",
        risk_level="high",
    )
    state.record_evidence_gate_result(
        EvidenceGateResult(
            mode="enforce_high_risk",
            claims=[claim],
            verdicts=[
                GateVerdict(
                    claim_id=claim.claim_id,
                    status="needs_recheck",
                    reasons=["missing_evidence"],
                    required_rework="Attach runtime-owned file evidence.",
                )
            ],
        )
    )
    report = WorkerReport(
        task_id="worker-a",
        status="completed",
        summary="claimed_changes: changed runtime/auth.py",
        artifacts=["claimed_changes: changed runtime/auth.py"],
    )

    prompt = _render_supervisor_finalize_prompt(
        refined_request="fix auth",
        plan=plan,
        run_state=state,
        mode="full",
        worker_reports=[report],
        lead_review_findings=[],
    )
    fallback_output = _render_lead_supervisor_output(
        plan,
        tmp_path,
        state,
        "full",
        worker_reports=[report],
        lead_review_findings=[],
    )

    assert "## Evidence Gate" in prompt
    assert "Do not use rejected or needs_recheck claims as facts." in prompt
    assert "claim:worker-a:artifact:1" in prompt
    assert "claimed_changes: changed runtime/auth.py" not in prompt
    assert "## Evidence Gate" in fallback_output
    assert "claim:worker-a:artifact:1" in fallback_output
    assert "claimed_changes: changed runtime/auth.py" not in fallback_output


def test_summary_helper_uses_sanitized_accepted_evidence_workspace(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "warn")
    task = _task()
    plan = PlannerResult(
        route_type="multi_agent",
        reason="summary helper",
        refined_request="summarize",
        tasks=[task],
        needs_synthesis=True,
        synthesis_instruction="merge worker outputs",
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": True, "reason": "explicit synthesis"},
            }
        },
    )
    state = PipelineRunState.create("summarize", plan, project_root=tmp_path, mode="full")
    captured = {}

    class FakeFactory:
        def create_synthesizer_agent(self, model_id, run_workspace_server):
            del model_id
            captured["server"] = run_workspace_server
            return SimpleNamespace(name="final_synthesizer_agent")

    class FakeReadonlyServer:
        name = "run_workspace_readonly"

    class FakeServer:
        async def __aenter__(self):
            return FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_create_readonly_server(run_dir, server_name):
        captured["run_dir"] = run_dir
        captured["server_name"] = server_name
        captured["sanitized_files"] = [path.name for path in run_dir.glob("*.md")]
        captured["sanitized_text"] = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.glob("*.md"))
        return FakeServer()

    async def fake_run_planned_task(*args, **kwargs):
        del args, kwargs
        return ("Worker A", "claimed_changes: changed runtime/auth.py")

    def fake_build_worker_report(task, output, *, run_state=None):
        del output, run_state
        return WorkerReport(
            task_id=task.id,
            status="completed",
            summary="claimed_changes: changed runtime/auth.py",
            artifacts=["claimed_changes: changed runtime/auth.py"],
        )

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        del agent, hooks, kwargs
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="safe summary")

    monkeypatch.setattr(runner, "create_readonly_filesystem_server", fake_create_readonly_server)
    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    output = asyncio.run(
        runner._run_multi_agent(
            "summarize",
            plan,
            tmp_path,
            "summary-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            execution_mode="full",
            show_progress=False,
        )
    )

    sanitized_dir = captured["run_dir"]
    assert output == "safe summary"
    assert sanitized_dir.name == "_accepted_evidence"
    assert captured["sanitized_files"]
    assert "Accepted Evidence" in captured["prompt"]
    assert "claim:worker-a:artifact:1" in captured["prompt"]
    assert "claimed_changes: changed runtime/auth.py" not in captured["prompt"]
    assert "claimed_changes: changed runtime/auth.py" not in captured["sanitized_text"]
