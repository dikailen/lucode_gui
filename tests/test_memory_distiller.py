from __future__ import annotations

from runtime.execution.pipeline import PipelineRunState, TaskRunRecord
from runtime.memory.distiller import MemoryCandidate, distill_run_experience, evaluate_memory_candidate
from runtime.safety.auditor import AuditResult


def test_distiller_rejects_natural_language_only_without_scope_or_evidence():
    run_state = PipelineRunState(
        user_request="Remember that future work should be careful.",
        route_type="single_agent",
        reason="natural language only",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Summarize preference",
                skill_id="generalist",
                model="gpt",
                mcp=[],
                status="completed",
                output_preview="In the future be more careful and explain more.",
            )
        ],
    )
    audit = AuditResult(
        passed=True,
        summary="No reusable tool evidence.",
    )

    decisions = distill_run_experience(run_state, audit)

    assert len(decisions) == 1
    decision = decisions[0]
    assert not decision.accepted
    assert decision.status == "rejected"
    assert "missing_scope" in decision.reasons
    assert "missing_evidence" in decision.reasons
    assert decision.entry is None
    assert decision.scope == ()
    assert decision.injection_policy == "none"
    assert decision.fingerprint.startswith("natural_language_note:")
    assert decision.decision_reasons == decision.reasons


def test_distiller_rejects_unsupported_candidate_but_keeps_decision_metadata():
    audit = AuditResult(passed=True, summary="passed")
    candidate = MemoryCandidate(
        kind="custom_unsupported_kind",
        summary="Unsupported but structured candidate.",
        action="custom action",
        scope=("runtime/custom.py",),
        evidence=("tool output",),
        metadata={"injection_policy": "auto", "decision_reasons": ["custom_reason"]},
    )

    decision = evaluate_memory_candidate(candidate, audit)

    assert not decision.accepted
    assert decision.status == "rejected"
    assert decision.scope == ("runtime/custom.py",)
    assert decision.injection_policy == "auto"
    assert decision.fingerprint.startswith("custom_unsupported_kind:")
    assert decision.decision_reasons == ("unsupported_kind",)
    assert decision.entry is None


def test_distiller_accepts_project_fact_candidate_kind_for_phase_4d():
    audit = AuditResult(passed=True, summary="passed")
    candidate = MemoryCandidate(
        kind="project_fact",
        summary="Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
        action="project fact: memory resolver module",
        scope=("runtime/memory/resolver.py",),
        evidence=("file_exists=runtime/memory/resolver.py", "audit_passed"),
        metadata={"injection_policy": "auto", "decision_reasons": ["file_scope_present"]},
    )

    decision = evaluate_memory_candidate(candidate, audit)

    assert decision.accepted
    assert decision.entry is not None
    assert decision.entry["kind"] == "project_fact"
    assert "unsupported_kind" not in decision.decision_reasons


def test_distiller_extracts_project_fact_from_repeated_completed_file_touches():
    run_state = PipelineRunState(
        user_request="Improve memory resolver context rendering.",
        route_type="multi_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-read",
                title="Memory resolver context rendering",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/resolver.py"],
                status="completed",
            ),
            TaskRunRecord(
                id="task-wire",
                title="Memory resolver planner pack",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                write_intent=["runtime/memory/resolver.py"],
                status="completed",
            ),
        ],
    )
    audit = AuditResult(
        passed=True,
        summary="passed",
        files_touched=["runtime/memory/resolver.py"],
    )

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "project_fact"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.confidence >= 0.75
    assert decision.injection_policy == "auto"
    assert decision.scope == ("runtime/memory/resolver.py",)
    assert decision.entry is not None
    assert decision.entry["kind"] == "project_fact"
    assert "runtime/memory/resolver.py" in decision.entry["summary"]
    assert decision.entry["metadata"]["fact_source"] == "repeated_task_touch"
    assert "repeated_path_touch" in decision.decision_reasons
    assert "file_evidence_present" in decision.decision_reasons


def test_distiller_extracts_project_fact_from_worker_report_role_statement():
    from runtime.agent.supervisor import WorkerReport

    run_state = PipelineRunState(
        user_request="Inspect memory resolver implementation.",
        route_type="multi_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="worker-memory",
                title="Inspect memory resolver",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/resolver.py"],
                status="completed",
            )
        ],
    )
    run_state.worker_reports = [
        WorkerReport(
            task_id="worker-memory",
            status="completed",
            summary="Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
            files_read=["runtime/memory/resolver.py"],
        )
    ]
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/memory/resolver.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "project_fact"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.entry is not None
    assert decision.entry["metadata"]["fact_source"] == "worker_report_role"
    assert decision.confidence == 0.65
    assert decision.scope == ("runtime/memory/resolver.py",)
    assert "worker_report_role_statement" in decision.decision_reasons


def test_distiller_prefers_repeated_project_fact_over_worker_report_for_same_path():
    from runtime.agent.supervisor import WorkerReport

    run_state = PipelineRunState(
        user_request="Improve memory resolver implementation.",
        route_type="multi_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-read",
                title="Memory resolver context rendering",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/resolver.py"],
                status="completed",
            ),
            TaskRunRecord(
                id="task-wire",
                title="Memory resolver planner pack",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                write_intent=["runtime/memory/resolver.py"],
                status="completed",
            ),
        ],
    )
    run_state.worker_reports = [
        WorkerReport(
            task_id="task-read",
            status="completed",
            summary="Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
            files_read=["runtime/memory/resolver.py"],
        )
    ]
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/memory/resolver.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "project_fact"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.entry is not None
    assert decision.entry["metadata"]["fact_source"] == "repeated_task_touch"
    assert decision.confidence >= 0.75


def test_distiller_does_not_extract_project_fact_from_single_natural_language_guess():
    run_state = PipelineRunState(
        user_request="Guess where GUI mode controls live.",
        route_type="single_agent",
        reason="analysis task",
        tasks=[
            TaskRunRecord(
                id="task-guess",
                title="Guess GUI mode controls",
                skill_id="generalist",
                model="gpt",
                mcp=[],
                read_set=["lucode/gui/control_panel.py"],
                status="completed",
                output_preview="GUI mode controls probably live in lucode/gui/control_panel.py.",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["lucode/gui/control_panel.py"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "project_fact"] == []


def test_distiller_does_not_extract_project_fact_from_sensitive_worker_report():
    from runtime.agent.supervisor import WorkerReport

    run_state = PipelineRunState(
        user_request="Inspect auth token location.",
        route_type="multi_agent",
        reason="analysis task",
        tasks=[],
    )
    run_state.worker_reports = [
        WorkerReport(
            task_id="worker-auth",
            status="completed",
            summary="The auth token sk-secret123456 is stored in .env.",
            files_read=[".env"],
        )
    ]
    audit = AuditResult(passed=True, summary="passed", files_touched=[".env"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "project_fact"] == []
    assert "sk-secret123456" not in str([item.entry for item in decisions])


def test_distiller_rejects_candidate_without_scope():
    audit = AuditResult(passed=True, summary="passed")
    candidate = MemoryCandidate(
        kind="verification_command",
        summary="Successful command without scope should not be reusable.",
        action="python -m pytest tests/test_memory_distiller.py -q",
        evidence=("returncode=0",),
        metadata={"injection_policy": "auto"},
    )

    decision = evaluate_memory_candidate(candidate, audit)

    assert not decision.accepted
    assert decision.status == "rejected"
    assert decision.scope == ()
    assert "missing_scope" in decision.decision_reasons
    assert decision.entry is None


def test_distiller_rejects_candidate_without_evidence():
    audit = AuditResult(passed=True, summary="passed")
    candidate = MemoryCandidate(
        kind="verification_command",
        summary="Scoped command without evidence should not be reusable.",
        action="python -m pytest tests/test_memory_distiller.py -q",
        scope=("tests/test_memory_distiller.py",),
        metadata={"injection_policy": "auto"},
    )

    decision = evaluate_memory_candidate(candidate, audit)

    assert not decision.accepted
    assert decision.status == "rejected"
    assert decision.scope == ("tests/test_memory_distiller.py",)
    assert "missing_evidence" in decision.decision_reasons
    assert decision.entry is None


def test_distiller_accepts_successful_configured_verification_command():
    verification = "\n".join(
        [
            "## Verifier summary",
            "- Configured verification commands:",
            "  - command=python -m pytest tests/test_gui_minimal_theme.py -q",
            "    returncode=0",
            "    stdout=1 passed",
        ]
    )
    run_state = PipelineRunState(
        user_request="Update GUI theme and verify it.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="GUI theme",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["lucode/gui/theme.py"],
                write_intent=["lucode/gui/theme.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(
        passed=True,
        summary="Final audit passed.",
        verifications=[f"GUI theme: {verification}"],
        files_touched=["lucode/gui/theme.py"],
    )

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "verification_command"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.candidate.kind == "verification_command"
    assert decision.confidence >= 0.75
    assert decision.status == "active"
    assert decision.entry is not None
    assert decision.entry["kind"] == "verification_command"
    assert decision.entry["metadata"]["scope"] == ["lucode/gui/theme.py"]
    assert decision.entry["metadata"]["command"] == "python -m pytest tests/test_gui_minimal_theme.py -q"
    assert decision.entry["metadata"]["injection_policy"] == "auto"


def test_distiller_extracts_path_mapping_from_successful_task_paths():
    run_state = PipelineRunState(
        user_request="Update GUI control panel layout.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="GUI control panel layout",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                read_set=["lucode/gui/control_panel.py"],
                write_intent=["lucode/gui/control_panel.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(
        passed=True,
        summary="Final audit passed.",
        files_touched=["lucode/gui/control_panel.py"],
    )

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "path_mapping"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.confidence >= 0.75
    assert decision.injection_policy == "auto"
    assert decision.scope == ("lucode/gui/control_panel.py",)
    assert decision.entry is not None
    assert decision.entry["kind"] == "path_mapping"
    assert decision.entry["metadata"]["path"] == "lucode/gui/control_panel.py"
    assert decision.entry["metadata"]["task_id"] == "task-1"
    assert "audit_passed" in decision.decision_reasons
    assert "task_completed" in decision.decision_reasons
    assert "path_scope_present" in decision.decision_reasons


def test_distiller_does_not_extract_path_mapping_when_audit_failed():
    run_state = PipelineRunState(
        user_request="Update scheduler.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Scheduler",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                read_set=["runtime/execution/parallel_scheduler.py"],
                write_intent=["runtime/execution/parallel_scheduler.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=False, summary="failed", files_touched=["runtime/execution/parallel_scheduler.py"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "path_mapping"] == []


def test_distiller_does_not_extract_path_mapping_without_path_evidence():
    run_state = PipelineRunState(
        user_request="Explain project architecture.",
        route_type="single_agent",
        reason="analysis task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Architecture",
                skill_id="generalist",
                model="gpt",
                mcp=[],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed")

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "path_mapping"] == []


def test_distiller_extracts_tool_hint_from_successful_verification_command():
    verification = "\n".join(
        [
            "- Configured verification commands:",
            "  - command=python -m pytest tests/test_parallel_scheduler.py -q",
            "    returncode=0",
            "    stdout=6 passed",
        ]
    )
    run_state = PipelineRunState(
        user_request="Update scheduler batching.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Scheduler batching",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["runtime/execution/parallel_scheduler.py"],
                write_intent=["runtime/execution/parallel_scheduler.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/execution/parallel_scheduler.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "tool_hint"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.confidence >= 0.75
    assert decision.injection_policy == "auto"
    assert "runtime/execution/parallel_scheduler.py" in decision.scope
    assert "tests/test_parallel_scheduler.py" in decision.scope
    assert decision.entry is not None
    assert decision.entry["kind"] == "tool_hint"
    assert decision.entry["metadata"]["command"] == "python -m pytest tests/test_parallel_scheduler.py -q"
    assert decision.entry["metadata"]["tool"] == "pytest"
    assert "verification_returncode_0" in decision.decision_reasons
    assert "scoped_command" in decision.decision_reasons


def test_distiller_does_not_extract_tool_hint_when_verification_command_failed():
    verification = "\n".join(
        [
            "- Configured verification commands:",
            "  - command=python -m pytest tests/test_parallel_scheduler.py -q",
            "    returncode=1",
            "    stderr=failed",
        ]
    )
    run_state = PipelineRunState(
        user_request="Update scheduler batching.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Scheduler batching",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["runtime/execution/parallel_scheduler.py"],
                write_intent=["runtime/execution/parallel_scheduler.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/execution/parallel_scheduler.py"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "tool_hint"] == []


def test_distiller_does_not_extract_tool_hint_without_tool_evidence():
    run_state = PipelineRunState(
        user_request="Update scheduler batching.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Scheduler batching",
                skill_id="code_engineer",
                model="gpt",
                mcp=[],
                read_set=["runtime/execution/parallel_scheduler.py"],
                write_intent=["runtime/execution/parallel_scheduler.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/execution/parallel_scheduler.py"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "tool_hint"] == []


def test_distiller_redacts_secret_in_tool_hint_command():
    verification = "\n".join(
        [
            "- Configured verification commands:",
            "  - command=python scripts/check_auth.py --token sk-test1234567890",
            "    returncode=0",
        ]
    )
    run_state = PipelineRunState(
        user_request="Verify auth script.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Auth verification",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["runtime/auth.py"],
                write_intent=["runtime/auth.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/auth.py"])

    decision = [item for item in distill_run_experience(run_state, audit) if item.accepted and item.candidate.kind == "tool_hint"][0]

    payload = str(decision.entry)
    assert "sk-test" not in payload
    assert "redacted" in payload.lower()


def test_distiller_extracts_tool_hint_from_fast_path_event():
    task = TaskRunRecord(
        id="task-1",
        title="Inspect pipeline fast path",
        skill_id="code_engineer",
        model="gpt",
        mcp=["project_filesystem_readonly"],
        read_set=["runtime/execution/pipeline.py"],
        status="completed",
    )
    run_state = PipelineRunState(
        user_request="Inspect fast path behavior.",
        route_type="single_agent",
        reason="code task",
        tasks=[task],
    )
    run_state.record_fast_path_used(task, tool="git", action="status")
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/execution/pipeline.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "tool_hint"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.entry is not None
    assert decision.entry["metadata"]["tool"] == "git"
    assert decision.entry["metadata"]["action"] == "status"
    assert decision.entry["metadata"]["source"] == "fast_path_event"
    assert "runtime/execution/pipeline.py" in decision.scope
    assert "fast_path_success" in decision.decision_reasons


def test_distiller_extracts_tool_hint_from_successful_tool_event():
    run_state = PipelineRunState(
        user_request="Read memory distiller implementation.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Read distiller",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/distiller.py"],
                status="completed",
            )
        ],
    )
    run_state.event_bus.emit(
        "ToolInvoked",
        "inline readonly read: runtime/memory/distiller.py",
        agent="runtime",
        task_id="task-1",
        status="completed",
        payload={
            "tool": "project_filesystem_readonly.read_file",
            "tool_name": "project_filesystem_readonly.read_file",
            "action": "read_file",
            "outcome": "completed",
            "arguments_summary": {"path": "runtime/memory/distiller.py"},
            "files_touched": [{"path": "runtime/memory/distiller.py", "access": "read"}],
        },
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/memory/distiller.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "tool_hint"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.entry is not None
    assert decision.entry["metadata"]["tool"] == "project_filesystem_readonly.read_file"
    assert decision.entry["metadata"]["action"] == "read_file"
    assert decision.entry["metadata"]["source"] == "tool_event"
    assert "runtime/memory/distiller.py" in decision.scope
    assert "tool_event_success" in decision.decision_reasons


def test_pipeline_run_state_exposes_worker_report_buffer():
    run_state = PipelineRunState(
        user_request="Run workers.",
        route_type="multi_agent",
        reason="code task",
        tasks=[],
    )

    assert run_state.worker_reports == []
def test_distiller_extracts_tool_hint_from_worker_report_tool_call():
    from runtime.agent.supervisor import WorkerReport

    run_state = PipelineRunState(
        user_request="Read memory resolver implementation.",
        route_type="multi_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="worker-memory",
                title="Read resolver",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/resolver.py"],
                status="completed",
            )
        ],
    )
    run_state.worker_reports = [
        WorkerReport(
            task_id="worker-memory",
            status="completed",
            summary="Read resolver implementation.",
            files_read=["runtime/memory/resolver.py"],
            tool_calls=[
                {
                    "tool": "project_filesystem_readonly.read_file",
                    "action": "read_file",
                    "status": "completed",
                    "outcome": "completed",
                    "arguments_summary": {"path": "runtime/memory/resolver.py"},
                    "files_touched": [{"path": "runtime/memory/resolver.py", "access": "read"}],
                }
            ],
        )
    ]
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/memory/resolver.py"])

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted and item.candidate.kind == "tool_hint"]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.entry is not None
    assert decision.entry["metadata"]["tool"] == "project_filesystem_readonly.read_file"
    assert decision.entry["metadata"]["source"] == "worker_report"
    assert "runtime/memory/resolver.py" in decision.scope
    assert "worker_report_tool_success" in decision.decision_reasons


def test_distiller_ignores_failed_tool_event_for_tool_hint():
    run_state = PipelineRunState(
        user_request="Read memory distiller implementation.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Read distiller",
                skill_id="code_engineer",
                model="gpt",
                mcp=["project_filesystem_readonly"],
                read_set=["runtime/memory/distiller.py"],
                status="completed",
            )
        ],
    )
    run_state.event_bus.emit(
        "ToolInvoked",
        "tool rejected",
        task_id="task-1",
        status="failed",
        payload={
            "tool": "project_filesystem_readonly.read_file",
            "action": "read_file",
            "outcome": "rejected",
            "arguments_summary": {"path": "runtime/memory/distiller.py"},
            "files_touched": [{"path": "runtime/memory/distiller.py", "access": "read"}],
        },
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/memory/distiller.py"])

    decisions = distill_run_experience(run_state, audit)

    assert [item for item in decisions if item.candidate.kind == "tool_hint"] == []
def test_distiller_records_failure_lesson_as_planner_candidate_only():
    run_state = PipelineRunState(
        user_request="Fix auth retry loop.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Auth retry",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                read_set=["runtime/auth.py"],
                write_intent=["runtime/auth.py"],
                status="failed",
                error="Retry limit reached after verifier failure.",
            )
        ],
        errors=["task-1: Retry limit reached after verifier failure."],
    )
    audit = AuditResult(
        passed=False,
        summary="Final audit failed.",
        remaining_issues=["Verifier still fails after repair retry limit."],
        files_touched=["runtime/auth.py"],
        rollback_happened=True,
        rollback_message="rolled back checkpoint",
    )

    decisions = distill_run_experience(run_state, audit)

    accepted = [item for item in decisions if item.accepted]
    assert len(accepted) == 1
    decision = accepted[0]
    assert decision.candidate.kind == "failure_lesson"
    assert 0.50 <= decision.confidence < 0.75
    assert decision.entry is not None
    assert decision.entry["kind"] == "failure_lesson"
    assert decision.entry["metadata"]["scope"] == ["runtime/auth.py"]
    assert decision.entry["metadata"]["injection_policy"] == "planner_candidate"
    assert "planner_candidate_only" in decision.entry["metadata"]["decision_reasons"]


def test_record_flywheel_safely_keeps_pipeline_summary_and_upserts_distilled_entries(monkeypatch, tmp_path):
    from runtime.execution import failure_memory
    from runtime.memory.flywheel import FlywheelStore

    run_state = PipelineRunState(
        user_request="Update GUI theme and verify it.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="GUI theme",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["lucode/gui/theme.py"],
                write_intent=["lucode/gui/theme.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed")
    upserted = []

    def fake_distill(state, audit_result):
        assert state is run_state
        assert audit_result is audit
        return [
            type(
                "Decision",
                (),
                {
                    "accepted": True,
                    "entry": {
                        "kind": "verification_command",
                        "summary": "Run pytest",
                        "tags": ["verification"],
                        "source": "distiller",
                        "metadata": {
                            "fingerprint": "verification_command:test",
                            "confidence": 0.85,
                            "scope": ["lucode/gui/theme.py"],
                            "status": "active",
                        },
                    },
                },
            )()
        ]

    def fake_upsert(self, entry):
        upserted.append(entry)
        return entry

    monkeypatch.setattr(failure_memory, "distill_run_experience", fake_distill)
    monkeypatch.setattr(FlywheelStore, "upsert_distilled_entry", fake_upsert)

    flywheel = FlywheelStore(tmp_path)
    failure_memory._record_flywheel_safely(flywheel, run_state, audit)

    loaded = flywheel.load_entries()
    assert len(loaded) == 1
    assert loaded[0]["kind"] == "pipeline_summary"
    assert len(upserted) == 1
    assert upserted[0]["kind"] == "verification_command"


def test_record_flywheel_safely_compresses_distilled_entry_before_upsert(monkeypatch, tmp_path):
    from runtime.execution import failure_memory
    from runtime.memory.flywheel import FlywheelStore

    run_state = PipelineRunState(
        user_request="Update memory resolver docs.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Memory resolver docs",
                skill_id="code_engineer",
                model="gpt",
                mcp=[],
                read_set=["runtime/memory/resolver.py"],
                write_intent=["runtime/memory/resolver.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed")
    upserted = []

    def fake_distill(state, audit_result):
        return [
            type(
                "Decision",
                (),
                {
                    "accepted": True,
                    "entry": {
                        "kind": "project_fact",
                        "summary": "Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
                        "tags": ["memory"],
                        "source": "distiller",
                        "metadata": {
                            "fingerprint": "project_fact:resolver",
                            "confidence": 0.85,
                            "scope": ["runtime/memory/resolver.py"],
                            "status": "active",
                            "action": "module_role:runtime/memory/resolver.py",
                            "evidence": ["audit_file=runtime/memory/resolver.py"],
                        },
                    },
                },
            )()
        ]

    def fake_upsert(self, entry):
        upserted.append(entry)
        return entry

    def fake_compressor(payload):
        return "Memory resolver facts live in runtime/memory/resolver.py."

    monkeypatch.setattr(failure_memory, "distill_run_experience", fake_distill)
    monkeypatch.setattr(FlywheelStore, "upsert_distilled_entry", fake_upsert)

    flywheel = FlywheelStore(tmp_path)
    failure_memory._record_flywheel_safely(
        flywheel,
        run_state,
        audit,
        summary_compressor=fake_compressor,
    )

    assert len(upserted) == 1
    assert upserted[0]["summary"] == "Memory resolver facts live in runtime/memory/resolver.py."
    assert upserted[0]["metadata"]["summary_compressed"] is True
    assert upserted[0]["metadata"]["original_summary"].startswith("Memory resolver context rendering")


def test_record_flywheel_safely_runs_memory_maintenance_after_distillation(monkeypatch, tmp_path):
    from runtime.execution import failure_memory
    from runtime.memory.flywheel import FlywheelStore

    run_state = PipelineRunState(
        user_request="Update GUI theme.",
        route_type="single_agent",
        reason="code task",
        tasks=[],
    )
    audit = AuditResult(passed=True, summary="passed")
    calls = []

    def fake_decay(flywheel, **kwargs):
        assert isinstance(flywheel, FlywheelStore)
        calls.append("decay")
        return []

    def fake_expire(flywheel, **kwargs):
        assert isinstance(flywheel, FlywheelStore)
        calls.append("expire")
        return []

    monkeypatch.setattr(failure_memory, "decay_stale_entries", fake_decay)
    monkeypatch.setattr(failure_memory, "expire_stale_entries", fake_expire)

    flywheel = FlywheelStore(tmp_path)
    failure_memory._record_flywheel_safely(flywheel, run_state, audit, run_memory_maintenance=True)

    assert calls == ["decay", "expire"]


def test_record_failure_case_safely_distills_only_after_final_rollback(monkeypatch, tmp_path):
    from runtime.execution import failure_memory
    from runtime.memory.flywheel import FlywheelStore
    from runtime.safety.checkpoint import RollbackResult

    audit = AuditResult(
        passed=False,
        summary="failed",
        remaining_issues=["still failing"],
        files_touched=["runtime/auth.py"],
    )
    upserted = []

    def fake_distill(state, audit_result):
        assert audit_result.rollback_happened is True
        assert state.user_request == "Fix auth retry loop."
        return [
            type(
                "Decision",
                (),
                {
                    "accepted": True,
                    "entry": {
                        "kind": "failure_lesson",
                        "summary": "Retry limit failed",
                        "tags": ["failure"],
                        "source": "distiller",
                        "metadata": {
                            "fingerprint": "failure_lesson:auth",
                            "confidence": 0.6,
                            "scope": ["runtime/auth.py"],
                            "status": "active",
                            "injection_policy": "planner_candidate",
                        },
                    },
                },
            )()
        ]

    def fake_upsert(self, entry):
        upserted.append(entry)
        return entry

    monkeypatch.setattr(failure_memory, "distill_run_experience", fake_distill)
    monkeypatch.setattr(FlywheelStore, "upsert_distilled_entry", fake_upsert)

    flywheel = FlywheelStore(tmp_path)
    failure_memory._record_failure_case_safely(
        flywheel,
        "Fix auth retry loop.",
        3,
        audit,
        RollbackResult(rolled_back=True, message="rolled back"),
    )

    loaded = flywheel.load_entries()
    assert len(loaded) == 1
    assert loaded[0]["kind"] == "failure_case"
    assert audit.rollback_happened is True
    assert len(upserted) == 1
    assert upserted[0]["kind"] == "failure_lesson"


def test_distilled_tool_hint_roundtrips_through_flywheel_store(tmp_path):
    from runtime.memory.flywheel import FlywheelStore
    from runtime.memory.resolver import MemoryResolver

    verification = "\n".join(
        [
            "- Configured verification commands:",
            "  - command=python -m pytest tests/test_parallel_scheduler.py -q",
            "    returncode=0",
        ]
    )
    run_state = PipelineRunState(
        user_request="Update scheduler batching.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="Scheduler batching",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["runtime/execution/parallel_scheduler.py"],
                write_intent=["runtime/execution/parallel_scheduler.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["runtime/execution/parallel_scheduler.py"])
    decision = [item for item in distill_run_experience(run_state, audit) if item.accepted and item.candidate.kind == "tool_hint"][0]

    store = FlywheelStore(tmp_path)
    store.upsert_distilled_entry(decision.entry)
    pack = MemoryResolver(tmp_path, flywheel=store).resolve_for_planner("scheduler pytest")

    entries = [entry for entry in pack.entries if entry.kind == "tool_hint"]
    assert entries
    assert entries[0].injection_policy == "auto"
    assert "tests/test_parallel_scheduler.py" in pack.render_for_planner()


def test_distilled_path_mapping_roundtrips_through_flywheel_store(tmp_path):
    from runtime.memory.flywheel import FlywheelStore
    from runtime.memory.resolver import MemoryResolver

    run_state = PipelineRunState(
        user_request="Update GUI control panel layout.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="GUI control panel layout",
                skill_id="code_engineer",
                model="gpt",
                mcp=["workspace_edit"],
                read_set=["lucode/gui/control_panel.py"],
                write_intent=["lucode/gui/control_panel.py"],
                status="completed",
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["lucode/gui/control_panel.py"])
    decision = [item for item in distill_run_experience(run_state, audit) if item.accepted and item.candidate.kind == "path_mapping"][0]

    store = FlywheelStore(tmp_path)
    store.upsert_distilled_entry(decision.entry)
    pack = MemoryResolver(tmp_path, flywheel=store).resolve_for_planner("GUI control panel layout")

    assert pack.entries
    assert pack.entries[0].kind == "path_mapping"
    assert pack.entries[0].injection_policy == "auto"
    assert "lucode/gui/control_panel.py" in pack.render_for_planner()


def test_distilled_verification_entry_roundtrips_through_flywheel_store(tmp_path):
    from runtime.memory.flywheel import FlywheelStore
    from runtime.memory.resolver import MemoryResolver

    verification = "\n".join(
        [
            "- Configured verification commands:",
            "  - command=python -m pytest tests/test_gui_minimal_theme.py -q",
            "    returncode=0",
        ]
    )
    run_state = PipelineRunState(
        user_request="Update GUI theme and verify it.",
        route_type="single_agent",
        reason="code task",
        tasks=[
            TaskRunRecord(
                id="task-1",
                title="GUI theme",
                skill_id="code_engineer",
                model="gpt",
                mcp=["command_runner"],
                read_set=["lucode/gui/theme.py"],
                write_intent=["lucode/gui/theme.py"],
                status="completed",
                verification=verification,
            )
        ],
    )
    audit = AuditResult(passed=True, summary="passed", files_touched=["lucode/gui/theme.py"])
    decision = [item for item in distill_run_experience(run_state, audit) if item.accepted][0]

    store = FlywheelStore(tmp_path)
    store.upsert_distilled_entry(decision.entry)
    pack = MemoryResolver(tmp_path, flywheel=store).resolve_for_planner("GUI theme pytest")

    assert pack.entries
    assert pack.entries[0].kind == "verification_command"
    assert pack.entries[0].injection_policy == "auto"
    assert "python -m pytest tests/test_gui_minimal_theme.py -q" in pack.render_for_planner()
