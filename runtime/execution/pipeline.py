from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalog_system.model_catalog import load_model_catalog
from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agents.model_capability import ModelExecutionStrategy, strategy_for_model_info
from runtime.consistency.timeline import RunTimeline
from runtime.events import ExecutionEventBus
from runtime.execution.run_context import RunContextStore
from runtime.safety.verification_commands import (
    extract_explicit_verification_commands,
    format_verification_command_lock,
)
from runtime.skill_library.usage import SkillUsageTracker
from runtime.ui.output_controller import OutputController


CODE_MARKERS = {
    "代码",
    "函数",
    "类",
    "接口",
    "bug",
    "报错",
    "修复",
    "实现",
    "重构",
    "评审",
    "python",
    "java",
    "c++",
    "mcpservermanager",
    "code",
    "function",
    "class",
    "fix",
    "bug",
    "debug",
    "implement",
    "refactor",
    "review",
}
CODE_FILE_PATTERN = re.compile(
    r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|ini|cfg|txt)\b",
    re.IGNORECASE,
)
EDIT_MARKERS = {
    "修复",
    "修改",
    "实现",
    "重构",
    "创建",
    "写入",
    "编辑",
    "fix",
    "modify",
    "implement",
    "refactor",
    "create",
    "write",
    "edit",
}
TEST_MARKERS = {
    "测试",
    "验证",
    "运行测试",
    "执行测试",
    "运行验证",
    "执行验证",
    "node --check",
    "pytest",
    "unittest",
    "npm test",
    "pnpm test",
    "yarn test",
    "bun test",
    "test",
    "verify",
}


@dataclass
class GateDecision:
    """Deterministic B-pipeline gate result used to harden model plans."""

    needs_code_pipeline: bool
    edit_intent: bool
    test_intent: bool
    should_verify: bool
    risk_level: str
    reason: str
    applied_tasks: list[str] = field(default_factory=list)


@dataclass
class TaskRunRecord:
    """Serializable execution state for one planned task."""

    id: str
    title: str
    skill_id: str
    model: str
    mcp: list[str]
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    read_set: list[str] = field(default_factory=list)
    write_intent: list[str] = field(default_factory=list)
    status: str = "pending"
    output_preview: str = ""
    verification: str = ""
    error: str = ""


@dataclass
class PipelineRunState:
    """Minimal B-pipeline state object for recovery and observability."""

    user_request: str
    route_type: str
    reason: str
    tasks: list[TaskRunRecord]
    gate: GateDecision | None = None
    errors: list[str] = field(default_factory=list)
    run_context: RunContextStore | None = None
    event_bus: ExecutionEventBus = field(default_factory=ExecutionEventBus)
    output_controller: OutputController = field(default_factory=OutputController)
    model_labels: dict[str, str] = field(default_factory=dict)
    memory_pack: Any | None = None
    worker_reports: list[Any] = field(default_factory=list)
    worker_context_observations: list[Any] = field(default_factory=list)
    timeline: RunTimeline | None = None
    evidence_mode: str = "off"
    evidence_claims: list[Any] = field(default_factory=list)
    evidence_refs: list[Any] = field(default_factory=list)
    evidence_verdicts: list[Any] = field(default_factory=list)
    accepted_evidence: dict[str, Any] = field(default_factory=dict)
    compute_placement_mode: str = "off"
    compute_placement_decisions: list[Any] = field(default_factory=list)
    project_root: Path | None = None
    skill_usage_tracker: SkillUsageTracker | None = None
    checkpoint_sink: Any | None = None
    tool_lifecycle_sink: Any | None = None
    checkpoint_compatibility: dict[str, Any] = field(default_factory=dict)
    recovery_plan: Any | None = None
    recovery_envelope: Any | None = None
    recovery_reused_outputs: dict[str, str] = field(default_factory=dict)
    recovered_accepted_evidence: dict[str, Any] = field(default_factory=dict)
    recovery_outcome: str = ""

    @classmethod
    def create(
        cls,
        user_request: str,
        plan: PlannerResult,
        project_root: Path | None = None,
        mode: str = "",
        output_controller: OutputController | None = None,
        event_bus: ExecutionEventBus | None = None,
        run_context: RunContextStore | None = None,
        memory_pack: Any | None = None,
        worker_reports: list[Any] | None = None,
    ) -> "PipelineRunState":
        controller = output_controller or OutputController(mode=mode, route=plan.route_type)
        controller.configure(mode=mode, route=plan.route_type)
        timeline = RunTimeline.create(project_root=project_root, user_request=user_request)
        resolved_run_context = run_context or (RunContextStore(project_root, timeline=timeline) if project_root else None)
        if resolved_run_context is not None and hasattr(resolved_run_context, "attach_timeline"):
            resolved_run_context.attach_timeline(timeline)
        state = cls(
            user_request=user_request,
            route_type=plan.route_type,
            reason=plan.reason,
            tasks=[
                TaskRunRecord(
                    id=task.id,
                    title=task.title,
                    skill_id=task.skill_id,
                    model=task.model,
                    mcp=list(task.mcp),
                    depends_on=list(task.depends_on),
                    acceptance_criteria=list(task.acceptance_criteria),
                    expected_outputs=list(task.expected_outputs),
                    read_set=list(task.read_set),
                    write_intent=list(task.write_intent),
                )
                for task in plan.tasks
            ],
            run_context=resolved_run_context,
            event_bus=event_bus or ExecutionEventBus(),
            output_controller=controller,
            memory_pack=memory_pack,
            worker_reports=list(worker_reports or []),
            timeline=timeline,
            project_root=project_root,
            skill_usage_tracker=SkillUsageTracker(project_root) if project_root else None,
        )
        state._record_skill_planner_rejections(plan)
        return state

    def record_gate(self, decision: GateDecision) -> None:
        self.gate = decision
        for record in self.tasks:
            if record.id in decision.applied_tasks:
                record.mcp = sorted(set(record.mcp) | {"code_locator", "project_filesystem_readonly"})

    def record_task_started(self, task: PlannedTask) -> None:
        self._timeline_record_task_started(task)
        record = self._find_task(task.id)
        if record:
            record.status = "running"
        self.output_controller.enter_running(
            task_id=str(getattr(task, "id", "") or ""),
            reason=str(getattr(task, "title", "") or ""),
        )
        self.emit_event(
            "TaskStarted",
            str(getattr(task, "title", "") or getattr(task, "id", "") or "任务开始"),
            task_id=str(getattr(task, "id", "") or ""),
            status="running",
        )

    def record_task_result(self, task: PlannedTask, output: str) -> None:
        record = self._find_task(task.id)
        if not record:
            return
        self._timeline_record_task_completed(task, output)
        record.status = "completed"
        record.error = ""
        record.output_preview = _preview(output)
        self._clear_task_error(task.id)
        self._record_bound_skill_task_usage(task, "success", reason="task completed")
        if self._all_tasks_terminal():
            if self.errors or any(str(getattr(item, "status", "")) == "failed" for item in self.tasks):
                self.output_controller.enter_failed("task failed")
            else:
                self.output_controller.enter_completed("tasks completed")
        self.emit_event(
            "TaskCompleted",
            str(getattr(task, "title", "") or getattr(task, "id", "") or "任务完成"),
            task_id=str(getattr(task, "id", "") or ""),
            status="completed",
        )
        self.record_checkpoint("task.terminal")

    def record_task_error(self, task: PlannedTask, error: Exception | str) -> None:
        self._timeline_record_task_failed(task, error)
        record = self._find_task(task.id)
        message = str(error)
        if record:
            record.status = "failed"
            record.error = message
        self.errors.append(f"{task.id}: {message}")
        self._record_bound_skill_task_usage(task, "failure", reason=message)
        self.output_controller.enter_failed(message)
        self.emit_event(
            "TaskFailed",
            message,
            task_id=str(getattr(task, "id", "") or ""),
            status="failed",
            payload={"reason": message[:200]},
        )
        self.record_checkpoint("task.terminal")

    def record_fast_path_used(self, task: PlannedTask, *, tool: str, action: str) -> None:
        self._timeline_record_fast_path_used(task, tool=tool, action=action)
        label = f"{tool} {action}".strip() or "只读快速路径"
        self.emit_event(
            "FastPathUsed",
            f"命中只读快速路径：{label}",
            task_id=str(getattr(task, "id", "") or ""),
            status="completed",
            payload={"tool": tool, "action": action},
        )

    def record_verification(self, task_id: str, report: str) -> None:
        record = self._find_task(task_id)
        if record:
            record.verification = report

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "route_type": self.route_type,
            "reason": self.reason,
            "gate": _gate_to_dict(self.gate) if self.gate else None,
            "tasks": [record.__dict__ for record in self.tasks],
            "errors": list(self.errors),
            "run_context": self.run_context.render_for_task() if self.run_context else "",
            "events": [event.to_dict() for event in self.event_bus.snapshot()],
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "evidence": {
                "mode": self.evidence_mode,
                "claims": [_object_to_dict(item) for item in self.evidence_claims],
                "evidence": [_object_to_dict(item) for item in self.evidence_refs],
                "verdicts": [_object_to_dict(item) for item in self.evidence_verdicts],
                "accepted_evidence": dict(self.accepted_evidence or {}),
            },
            "compute_placement": {
                "mode": self.compute_placement_mode,
                "decisions": [_object_to_dict(item) for item in self.compute_placement_decisions],
            },
            "worker_reports": [_worker_report_to_dict(report) for report in self.worker_reports],
            "worker_context_observations": [
                _object_to_dict(item) for item in self.worker_context_observations
            ],
            "output": self.output_controller.snapshot().to_dict(),
        }

    def record_worker_context_observation(self, observation: Any) -> None:
        payload = _object_to_dict(observation)
        self.worker_context_observations.append(observation)
        self.emit_event(
            "WorkerContextObserved",
            f"worker context observed: {payload.get('budget_mode') or 'normal'}",
            task_id=str(payload.get("task_id") or ""),
            status="completed",
            payload=payload,
        )

    def record_evidence_gate_result(self, result: Any) -> None:
        self.evidence_mode = str(getattr(result, "mode", "") or "off")
        self.evidence_claims = list(getattr(result, "claims", []) or [])
        self.evidence_refs = list(getattr(result, "evidence", []) or [])
        self.evidence_verdicts = list(getattr(result, "verdicts", []) or [])
        try:
            from runtime.evidence.gate import accepted_evidence_packet

            self.accepted_evidence = accepted_evidence_packet(result)
        except Exception:
            self.accepted_evidence = {}
        self.accepted_evidence = _merge_accepted_evidence_packets(
            self.recovered_accepted_evidence,
            self.accepted_evidence,
        )
        self.record_checkpoint("evidence.accepted")

    def record_checkpoint(self, kind: str, *, final_output: str = ""):
        sink = self.checkpoint_sink
        if sink is None:
            return None
        try:
            from runtime.recovery.checkpoint_codec import encode_pipeline_checkpoint

            payload = encode_pipeline_checkpoint(self, final_output=final_output)
            return sink(str(kind or ""), payload, dict(self.checkpoint_compatibility or {}))
        except Exception as exc:
            self.emit_event(
                "RecoveryCheckpointFailed",
                str(exc) or exc.__class__.__name__,
                agent="runtime",
                status="warning",
                payload={"kind": str(kind or ""), "reason": str(exc)[:240]},
            )
            return None

    def record_final_ready(self, final_output: str):
        return self.record_checkpoint("final.ready", final_output=str(final_output or ""))

    def apply_recovery_plan(self, decision: Any, *, envelope: Any) -> None:
        self.recovery_plan = decision
        self.recovery_envelope = envelope
        self.recovery_reused_outputs = dict(getattr(decision, "reused_outputs", {}) or {})
        checkpoint = getattr(envelope, "checkpoint", {}) if envelope is not None else {}
        packet = checkpoint.get("accepted_evidence") if isinstance(checkpoint, dict) else {}
        self.recovered_accepted_evidence = dict(packet or {}) if isinstance(packet, dict) else {}
        disposition = str(getattr(decision, "disposition", "") or "")
        if disposition != "continue_safe":
            self.recovery_outcome = "superseded"
            return
        try:
            from runtime.evidence.gate import recovery_reconciliation_evidence_packet

            reconciled_packet = recovery_reconciliation_evidence_packet(
                envelope,
                reusable_task_ids=tuple(getattr(decision, "reusable_task_ids", ()) or ()),
            )
            self.recovered_accepted_evidence = _merge_accepted_evidence_packets(
                self.recovered_accepted_evidence,
                reconciled_packet,
            )
        except Exception:
            # A failed evidence render must not transform a blocked mutation into accepted fact.
            pass
        self.recovery_outcome = "blocked" if tuple(getattr(decision, "blocked_task_ids", ()) or ()) else "completed"
        for task_id in tuple(getattr(decision, "reusable_task_ids", ()) or ()):
            record = self._find_task(str(task_id))
            if record is None:
                continue
            record.status = "completed"
            record.error = ""
            record.output_preview = _preview(self.recovery_reused_outputs.get(str(task_id), ""))
            self.emit_event(
                "TaskReused",
                f"reused accepted evidence for {task_id}",
                task_id=str(task_id),
                agent="runtime",
                status="completed",
                payload={"source": "accepted_recovery_evidence"},
            )
        self.accepted_evidence = _merge_accepted_evidence_packets(
            self.recovered_accepted_evidence,
            self.accepted_evidence,
        )

    def reused_task_output(self, task_id: str) -> str:
        return str(self.recovery_reused_outputs.get(str(task_id or ""), "") or "")

    def task_is_reused(self, task_id: str) -> bool:
        return bool(self.reused_task_output(task_id))

    def record_compute_placement_result(self, result: Any) -> None:
        self.compute_placement_mode = str(getattr(result, "mode", "") or "off")
        self.compute_placement_decisions = list(getattr(result, "decisions", []) or [])
        if self.compute_placement_mode == "off":
            return
        self.emit_event(
            "ComputePlacementObserved",
            f"compute placement {self.compute_placement_mode}: {len(self.compute_placement_decisions)} decision(s)",
            agent="runtime",
            status="completed",
            payload={
                "mode": self.compute_placement_mode,
                "decisions": [_object_to_dict(item) for item in self.compute_placement_decisions],
            },
        )

    def _find_task(self, task_id: str) -> TaskRunRecord | None:
        for record in self.tasks:
            if record.id == task_id:
                return record
        return None

    def _all_tasks_terminal(self) -> bool:
        if not self.tasks:
            return False
        return all(str(getattr(record, "status", "")) in {"completed", "failed"} for record in self.tasks)

    def _clear_task_error(self, task_id: str) -> None:
        prefix = f"{task_id}:"
        self.errors = [error for error in self.errors if not str(error).startswith(prefix)]

    def _record_skill_planner_rejections(self, plan: PlannerResult) -> None:
        tracker = self.skill_usage_tracker
        if tracker is None:
            return
        interface = plan.skill_interface if isinstance(plan.skill_interface, dict) else {}
        candidate_ids = set(_clean_string_list(interface.get("candidate_skill_ids")))
        adopted_ids = set(_clean_string_list(interface.get("adopted_skill_ids")))
        rejected_ids = set(_clean_string_list(interface.get("rejected_skill_ids")))
        rejected_ids.update(candidate_ids - adopted_ids)
        rejected_ids.difference_update(adopted_ids)
        if not rejected_ids:
            return
        reason_map = interface.get("rejection_reasons")
        if not isinstance(reason_map, dict):
            reason_map = {}
        for skill_id in sorted(rejected_ids):
            try:
                tracker.record(
                    skill_id=skill_id,
                    query=self.user_request,
                    task_id="",
                    result="rejected_by_planner",
                    reason=str(reason_map.get(skill_id) or "candidate not adopted by planner"),
                )
            except Exception:
                continue

    def _record_bound_skill_task_usage(self, task: PlannedTask, result: str, *, reason: str) -> None:
        tracker = self.skill_usage_tracker
        if tracker is None:
            return
        bound_skill_ids = _clean_string_list(getattr(task, "bound_skill_ids", None))
        if not bound_skill_ids:
            return
        record = self._find_task(str(getattr(task, "id", "") or ""))
        files_touched = list(record.write_intent) if record else list(getattr(task, "write_intent", []) or [])
        verification = record.verification if record and record.verification else []
        for skill_id in bound_skill_ids:
            try:
                tracker.record(
                    skill_id=skill_id,
                    query=self.user_request,
                    task_id=str(getattr(task, "id", "") or ""),
                    result=result,
                    files_touched=files_touched,
                    verification=verification,
                    reason=reason,
                )
            except Exception:
                continue

    def emit_event(self, event_type: str, message: str = "", **kwargs: Any):
        try:
            event = self.event_bus.emit(event_type, message, **kwargs)
            self._timeline_record_event(event_type, message, **kwargs)
            return event
        except Exception:
            return None

    def _timeline_record_event(self, event_type: str, message: str = "", **kwargs: Any) -> None:
        if self.timeline is None:
            return
        if str(event_type or "") in {"TaskStarted", "TaskCompleted", "TaskFailed", "FastPathUsed"}:
            return
        payload = dict(kwargs.get("payload") or {})
        tool = str(payload.get("tool") or payload.get("tool_name") or "").strip()
        resource_refs = [f"tool:{tool}"] if tool else []
        try:
            self.timeline.record(
                event_type,
                task_id=str(kwargs.get("task_id") or ""),
                status=str(kwargs.get("status") or ""),
                message=message,
                resource_refs=resource_refs,
                payload=payload,
            )
        except Exception:
            return

    def _timeline_record_task_started(self, task: PlannedTask) -> None:
        if self.timeline is None:
            return
        try:
            self.timeline.record_task_started(task)
        except Exception:
            return

    def _timeline_record_task_completed(self, task: PlannedTask, output: str) -> None:
        if self.timeline is None:
            return
        try:
            self.timeline.record_task_completed(task, output_preview=output)
        except Exception:
            return

    def _timeline_record_task_failed(self, task: PlannedTask, error: Exception | str) -> None:
        if self.timeline is None:
            return
        try:
            self.timeline.record_task_failed(task, error)
        except Exception:
            return

    def _timeline_record_fast_path_used(self, task: PlannedTask, *, tool: str, action: str) -> None:
        if self.timeline is None:
            return
        try:
            self.timeline.record_fast_path_used(task, tool=tool, action=action)
        except Exception:
            return


def apply_pipeline_gate(plan: PlannerResult, refined_request: str) -> GateDecision:
    """Apply a small KWCode-style Gate pass to code tasks before validation."""

    text = _combined_plan_text(plan, refined_request)
    if _is_explicit_readonly_analysis(text):
        _ensure_readonly_locator_tools(plan)
        return GateDecision(
            needs_code_pipeline=False,
            edit_intent=False,
            test_intent=False,
            should_verify=False,
            risk_level="low",
            reason="用户明确要求只读分析或不要运行测试，无需代码流水线。",
        )

    code_tasks = [task for task in plan.tasks if _is_code_task(task)]
    is_code = bool(code_tasks) or _contains_any(text, CODE_MARKERS)
    code_text = "\n".join([refined_request, *(_task_text(task) for task in code_tasks)]).lower()
    declared_write_intent = any(getattr(task, "write_intent", []) for task in code_tasks)
    edit_intent = bool(code_tasks) and (_contains_edit_intent(code_text) or declared_write_intent)
    test_intent = bool(code_tasks) and _contains_test_execution_intent(code_text)
    needs_code_pipeline = plan.route_type in {"single_agent", "multi_agent"} and is_code
    should_verify = needs_code_pipeline and (edit_intent or test_intent)

    decision = GateDecision(
        needs_code_pipeline=needs_code_pipeline,
        edit_intent=edit_intent,
        test_intent=test_intent,
        should_verify=should_verify,
        risk_level="medium" if edit_intent else "low",
        reason="代码任务需要 Gate/Locator/Verifier 骨架兜底。" if needs_code_pipeline else "无需代码流水线。",
    )

    if not needs_code_pipeline:
        return decision

    explicit_verification_commands = extract_explicit_verification_commands(text)

    for task in plan.tasks:
        if not _is_code_task(task):
            continue

        strategy = _strategy_for_task(task)
        task_text = _task_text(task).lower()
        task_edit_intent = bool(
            getattr(task, "write_intent", [])
            or (task.skill_id == "code_engineer" and _contains_edit_intent(task_text))
        )
        task_test_intent = _contains_test_execution_intent(task_text)
        _append_unique(task.mcp, "code_locator")
        _append_unique(task.mcp, "project_filesystem_readonly")
        if edit_intent and task_edit_intent:
            _append_unique(task.mcp, "workspace_edit")

        if test_intent and (task_test_intent or task_edit_intent):
            _append_unique(task.mcp, "command_runner")

        task.instruction = _append_gate_instruction(
            task.instruction,
            decision,
            strategy,
            task_edit_intent=task_edit_intent,
            task_test_intent=task_test_intent,
            explicit_verification_commands=explicit_verification_commands,
        )
        task.risk_notes = _append_note(
            task.risk_notes,
            "Gate 已启用代码流水线兜底：先定位，再少量读取，修改后由 Verifier 做只读核验。",
        )
        task.risk_notes = _append_note(task.risk_notes, strategy.note_zh)
        decision.applied_tasks.append(task.id)

    return decision


def _ensure_readonly_locator_tools(plan: PlannerResult) -> None:
    for task in plan.tasks:
        if "project_filesystem_readonly" not in task.mcp or "code_locator" in task.mcp:
            continue
        if not _readonly_task_mentions_file_or_code(task):
            continue
        _append_unique(task.mcp, "code_locator")
        task.risk_notes = _append_note(
            task.risk_notes,
            "只读代码/文件分析已自动加入 code_locator，用于先定位后少量读取；未启用写入或命令工具。",
        )


def format_gate_decision(decision: GateDecision) -> str:
    if not decision.needs_code_pipeline:
        return "Gate：无需代码流水线。"
    tasks = ", ".join(decision.applied_tasks) if decision.applied_tasks else "无可应用任务"
    return (
        "Gate：已启用代码流水线兜底\n"
        f"- 风险等级：{decision.risk_level}\n"
        f"- 修改意图：{decision.edit_intent}\n"
        f"- 验证建议：{decision.should_verify}\n"
        f"- 应用任务：{tasks}"
    )


def should_verify_task(task: PlannedTask) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    if _is_explicit_readonly_analysis(text):
        return False
    return (
        task.skill_id == "code_engineer"
        and ("workspace_edit" in task.mcp or "command_runner" in task.mcp or _contains_edit_intent(text))
    )


def build_verification_report(project_root: Path, task: PlannedTask) -> str:
    """Run a read-only Verifier pass after likely code modifications."""

    if not should_verify_task(task):
        return ""

    status = _run_git(project_root, ["status", "--short"])
    diff_stat = _run_git(project_root, ["diff", "--stat"])
    changed_files = _parse_status_files(status["stdout"]) if status["returncode"] == 0 else []

    lines = [
        "## Verifier 校验摘要",
        "- 已执行只读 git status / diff --stat 核验。",
    ]
    if status["returncode"] != 0:
        lines.append(f"- git status 失败：{status['stderr'] or status['stdout'] or '无详细输出'}")
    elif changed_files:
        lines.append("- 当前工作区改动文件：")
        lines.extend(f"  - {item}" for item in changed_files[:30])
        if len(changed_files) > 30:
            lines.append(f"  - ...另有 {len(changed_files) - 30} 个文件")
    else:
        lines.append("- 当前工作区没有检测到文件改动。")

    if diff_stat["returncode"] == 0 and diff_stat["stdout"].strip():
        lines.append("- diff --stat：")
        for line in diff_stat["stdout"].strip().splitlines()[:20]:
            lines.append(f"  {line}")
    elif diff_stat["returncode"] != 0:
        lines.append(f"- git diff --stat 失败：{diff_stat['stderr'] or diff_stat['stdout'] or '无详细输出'}")

    command_reports = _run_configured_verification_commands(project_root)
    if command_reports:
        lines.append("- Configured verification commands:")
        for report in command_reports:
            lines.append(f"  - command={report['command']}")
            lines.append(f"    returncode={report['returncode']}")
            if report["stdout"]:
                lines.append(f"    stdout={report['stdout']}")
            if report["stderr"]:
                lines.append(f"    stderr={report['stderr']}")
    elif "command_runner" not in task.mcp:
        lines.append("- 未自动运行测试命令；如需执行测试，请在任务中明确要求或让主脑加入 command_runner。")
    return "\n".join(lines)


def _run_configured_verification_commands(project_root: Path) -> list[dict[str, str | int]]:
    raw = str(os.environ.get("AGENTS_VERIFY_COMMANDS") or "").strip()
    if not raw:
        return []

    reports: list[dict[str, str | int]] = []
    commands = [item.strip() for item in raw.splitlines() if item.strip()]
    for command in commands:
        try:
            args = shlex.split(command, posix=True)
        except ValueError as exc:
            reports.append({"command": command, "returncode": 2, "stdout": "", "stderr": str(exc)})
            continue
        if not args:
            continue
        try:
            result = subprocess.run(
                args,
                cwd=project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
                shell=False,
            )
        except FileNotFoundError:
            reports.append({"command": command, "returncode": 127, "stdout": "", "stderr": "command not found"})
            continue
        except subprocess.TimeoutExpired:
            reports.append(
                {"command": command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            )
            continue

        reports.append(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout": _preview(result.stdout.strip(), 400),
                "stderr": _preview(result.stderr.strip(), 400),
            }
        )
    return reports


def _worker_report_to_dict(report: Any) -> dict[str, Any]:
    if hasattr(report, "to_dict"):
        try:
            value = report.to_dict()
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}
    if isinstance(report, dict):
        return dict(report)
    return {
        "task_id": str(getattr(report, "task_id", "") or ""),
        "status": str(getattr(report, "status", "") or ""),
        "summary": str(getattr(report, "summary", "") or ""),
        "files_read": list(getattr(report, "files_read", []) or []),
        "files_written": list(getattr(report, "files_written", []) or []),
        "tool_calls": list(getattr(report, "tool_calls", []) or []),
    }


def _object_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        try:
            data = value.to_dict()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    if isinstance(value, dict):
        return dict(value)
    return dict(getattr(value, "__dict__", {}) or {})


def _merge_accepted_evidence_packets(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old = dict(previous or {}) if isinstance(previous, dict) else {}
    new = dict(current or {}) if isinstance(current, dict) else {}
    if not old:
        return new
    if not new:
        return old
    old_mode = str(old.get("mode") or "off")
    new_mode = str(new.get("mode") or "off")
    return {
        "mode": new_mode if new_mode != "off" else old_mode,
        "claims": _merge_packet_items(old.get("claims"), new.get("claims"), key="claim_id"),
        "evidence": _merge_packet_items(old.get("evidence"), new.get("evidence"), key="ref_id"),
        "blocked_claims": _merge_packet_items(
            old.get("blocked_claims"),
            new.get("blocked_claims"),
            key="claim_id",
        ),
    }


def _merge_packet_items(previous: Any, current: Any, *, key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*list(previous or []), *list(current or [])]:
        if not isinstance(item, dict):
            continue
        identity = str(item.get(key) or "")
        if not identity:
            continue
        if identity not in merged:
            order.append(identity)
        merged[identity] = dict(item)
    return [merged[identity] for identity in order]


def _gate_to_dict(decision: GateDecision) -> dict[str, Any]:
    return {
        "needs_code_pipeline": decision.needs_code_pipeline,
        "edit_intent": decision.edit_intent,
        "test_intent": decision.test_intent,
        "should_verify": decision.should_verify,
        "risk_level": decision.risk_level,
        "reason": decision.reason,
        "applied_tasks": list(decision.applied_tasks),
    }


def _preview(value: str, limit: int = 800) -> str:
    value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in seen:
            items.append(text)
            seen.add(text)
    return items


def _append_gate_instruction(
    instruction: str,
    decision: GateDecision,
    strategy: ModelExecutionStrategy | None = None,
    *,
    task_edit_intent: bool = False,
    task_test_intent: bool = False,
    explicit_verification_commands: list[str] | None = None,
) -> str:
    addition = (
        "\n\n## Gate 兜底要求\n"
        "- 这是代码流水线任务：先用 code_locator 定位，再少量读取目标文件。\n"
        "- 如果需要修改，优先小范围 patch/replace，不要整文件重写。\n"
        "- 完成后输出修改点、风险和验证建议。"
    )
    if strategy is not None:
        addition += (
            "\n"
            f"- 当前模型能力档位：{strategy.tier.value}；"
            f"最多读取 {strategy.max_files_per_task} 个核心文件，"
            f"单文件建议不超过 {strategy.max_read_chars_per_file} 字符，"
            f"总读取建议不超过 {strategy.max_total_read_chars} 字符。"
        )
        if strategy.force_plan_before_edit:
            addition += "\n- 该模型需要先列出简短修改计划，再执行文件修改。"
    if decision.edit_intent and not task_edit_intent:
        addition += "\n- 本任务未声明写入意图：保持只读定位，不要申请写入工具。"
    if decision.test_intent and (task_test_intent or task_edit_intent):
        addition += "\n- 用户表达了测试/验证意图，可在获得审批后运行必要测试命令。"
        command_lock = format_verification_command_lock(explicit_verification_commands or [])
        if command_lock:
            addition += f"\n- {command_lock}"
    if "## Gate 兜底要求" in instruction:
        return instruction
    return instruction + addition


def _strategy_for_task(task: PlannedTask) -> ModelExecutionStrategy:
    try:
        catalog = load_model_catalog()
    except Exception:
        return strategy_for_model_info({"model_name": task.model})
    model_infos = {item["id"]: item for item in catalog.get("models", [])}
    return strategy_for_model_info(model_infos.get(task.model) or {"model_name": task.model})


def _combined_plan_text(plan: PlannerResult, refined_request: str) -> str:
    parts = [refined_request, plan.refined_request, plan.reason]
    for task in plan.tasks:
        parts.extend([task.title, task.instruction])
    return "\n".join(str(item).lower() for item in parts if item)


def _is_code_task(task: PlannedTask) -> bool:
    if task.skill_id == "code_engineer":
        return True
    return "code_locator" in task.mcp or "workspace_edit" in task.mcp or "command_runner" in task.mcp


def _readonly_task_mentions_file_or_code(task: PlannedTask) -> bool:
    text = _task_text(task).lower()
    if CODE_FILE_PATTERN.search(text):
        return True
    return _contains_any(text, CODE_MARKERS)


def _task_text(task: PlannedTask) -> str:
    return "\n".join(
        [
            task.title,
            task.instruction,
            " ".join(task.write_intent),
            " ".join(getattr(task, "acceptance_criteria", []) or []),
            " ".join(getattr(task, "expected_outputs", []) or []),
        ]
    )


def _contains_any(text: str, markers: set[str]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _contains_edit_intent(text: str) -> bool:
    return _contains_any(_without_negated_action_phrases(text), EDIT_MARKERS)


def _contains_test_execution_intent(text: str) -> bool:
    lowered = text.lower()
    if not _contains_any(lowered, TEST_MARKERS):
        return False
    return not _is_verification_recommendation_only(lowered)


def _without_negated_action_phrases(text: str) -> str:
    lowered = text.lower()
    patterns = [
        r"不要\s*(?:修改|改动|改文件|编辑|写入|创建|删除|修复|提交|安装)",
        r"不\s*(?:修改|改动|改文件|编辑|写入|创建|删除|修复|提交|安装)",
        r"不需要\s*(?:修改|改动|改文件|编辑|写入|创建|删除|修复|提交|安装)",
        r"无需\s*(?:修改|改动|改文件|编辑|写入|创建|删除|修复|提交|安装)",
        r"do not\s+(?:modify|edit|write|create|delete|fix|repair|commit|install)",
        r"don't\s+(?:modify|edit|write|create|delete|fix|repair|commit|install)",
        r"no\s+(?:modification|edit|write|commit|install)",
    ]
    for pattern in patterns:
        lowered = re.sub(pattern, " ", lowered)
    return lowered


def _is_verification_recommendation_only(text: str) -> bool:
    lowered = text.lower()
    recommendation_markers = [
        "建议验证命令",
        "建议的验证命令",
        "下一步建议验证命令",
        "列出建议验证命令",
        "只列出验证命令",
        "推荐验证命令",
        "给出验证命令",
        "输出验证命令",
        "验证建议",
        "verification commands to try",
        "suggest verification command",
        "suggested verification command",
        "recommended verification command",
        "recommend verification command",
    ]
    if not any(marker in lowered for marker in recommendation_markers):
        return False

    execution_markers = [
        "运行测试",
        "执行测试",
        "运行验证",
        "执行验证",
        "运行 pytest",
        "执行 pytest",
        "run pytest",
        "run tests",
        "execute tests",
        "execute pytest",
    ]
    stripped = _without_negated_run_phrases(lowered)
    return not any(marker in stripped for marker in execution_markers)


def _without_negated_run_phrases(text: str) -> str:
    lowered = text.lower()
    patterns = [
        r"不要\s*(?:运行|执行)\s*(?:测试|验证|命令|pytest)?",
        r"不\s*(?:运行|执行)\s*(?:测试|验证|命令|pytest)?",
        r"不需要\s*(?:运行|执行)\s*(?:测试|验证|命令|pytest)?",
        r"无需\s*(?:运行|执行)\s*(?:测试|验证|命令|pytest)?",
        r"do not\s+(?:run|execute)\s*(?:tests?|verification|commands?|pytest)?",
        r"don't\s+(?:run|execute)\s*(?:tests?|verification|commands?|pytest)?",
    ]
    for pattern in patterns:
        lowered = re.sub(pattern, " ", lowered)
    return lowered


def _is_explicit_readonly_analysis(text: str) -> bool:
    lowered = text.lower()
    readonly_markers = [
        "不要修改",
        "不修改",
        "不要改文件",
        "不要运行测试",
        "不要运行",
        "不要提交",
        "不要安装",
        "不提交",
        "不安装",
        "无需修改",
        "不需要修改",
        "只读",
        "read-only",
        "readonly",
        "do not modify",
        "do not edit",
        "do not run",
        "do not commit",
        "do not install",
    ]
    analysis_markers = [
        "分析",
        "检查",
        "查看",
        "阅读",
        "体检",
        "排查",
        "覆盖",
        "总结",
        "analyze",
        "inspect",
        "review",
        "coverage",
        "summary",
    ]
    edit_markers = [
        "需要修改",
        "请修改",
        "请修复",
        "可以修改",
        "允许修改",
        "需要修复",
        "实际修改",
        "修复",
        "修复代码",
        "写入文件",
        "创建文件",
        "删除文件",
        "fix",
        "repair",
        "please modify",
        "please edit",
        "fix the code",
    ]
    return (
        any(marker in lowered for marker in readonly_markers)
        and any(marker in lowered for marker in analysis_markers)
        and not any(marker in lowered for marker in edit_markers)
    )


def _append_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return existing.rstrip() + " " + note


def _run_git(project_root: Path, args: list[str]) -> dict[str, str | int]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            shell=False,
        )
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "git executable was not found in PATH."}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "git command timed out."}
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _parse_status_files(stdout: str) -> list[str]:
    files = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        files.append(path)
    return files
