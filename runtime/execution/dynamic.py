from pathlib import Path
import re

from catalog_system.model_catalog import ModelRegistry
from planning.plan_normalizer import normalize_plan_for_execution
from planning.plan_reviewer import format_plan_review, review_plan
from planning.plan_validator import format_validation, validate_plan
from planning.planner import format_execution_plan, preview_plan
from runtime.agents.factory import AgentFactory
from runtime.safety.auditor import audit_execution, audit_plan_review_failure, format_final_report
from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint
from runtime.memory.flywheel import FlywheelStore
from runtime.memory.resolver import MemoryResolver
from runtime.execution.pipeline import (
    PipelineRunState,
    apply_pipeline_gate,
    format_gate_decision,
)
from runtime.execution.run_context import RunContextStore, seed_inline_files
# Keep private imports available here as compatibility re-exports while dynamic.py is being split.
from runtime.execution.fast_paths import (
    _build_url_search_query,
    _can_fast_path_git_status,
    _can_fast_path_url_search,
    _is_url_only_task,
    _log_runtime_fast_path,
    _parse_git_status_short,
    _run_git_status_fast_path,
    _run_url_search_fast_path,
    _runtime_operation_log,
)
from runtime.execution.failure_memory import (
    _audit_files_touched,
    _record_failure_case_safely,
    _record_flywheel_safely,
)
from runtime.execution.execution_contract import normalize_execution_contract, supervisor_route
from runtime.execution.inline_context import (
    _excerpt_query_tokens,
    _inline_project_file_context,
    _latest_workspace_context,
    _read_project_file_excerpt,
    _render_numbered_lines,
    _resolve_explicit_project_file_paths,
    _resolve_project_file_candidate,
    _safe_inline_project_file,
    _should_inline_readonly_file_task,
    _truncate_excerpt,
)
from runtime.execution.multi_agent_runner import (
    _ordered_tasks_for_execution,
    _run_multi_agent,
    _tasks_by_parallel_group,
)
from runtime.execution.parallel_scheduler import (
    _can_run_group_in_parallel,
    _execution_batches_for_group,
    _execution_batches_for_mode,
    _format_parallel_batch_audit,
    _normalize_write_path,
    _normalized_write_intent,
    _requires_serial_execution,
    _task_conflicts_with_batch,
    _write_paths_conflict,
    _write_sets_conflict,
)
from runtime.execution.progress import _print_progress_snapshot, _safe_print
from runtime.execution.single_agent_runner import _run_single_agent
from runtime.execution.task_runner import (
    _dependency_context_for_task,
    _direct_answer_input_with_inline_context,
    _max_turns_for_task,
    _run_direct_answer,
    _run_planned_task,
    _task_output_map,
    _task_prompt,
    _with_verification_report,
)
from runtime.safety.privacy import PrivacyPolicy
from runtime.safety.repair_loop import build_repair_request, should_retry
from runtime.config.settings import RuntimeSettings
from runtime.config.model_selection import model_usable_for_task
from runtime.compute.context_sources import labels_from_memory_pack
from runtime.compute.placement_guard import ComputePlacementGuard
from runtime.compute.placement_policy import (
    ComputePlacementViolation,
    observe_compute_placement_for_plan,
)
from runtime.consistency.timeline import reliability_flags_from_env
from runtime.events import ExecutionEventBus
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.policy import evaluate_recovery_plan, recovery_compatibility_for_plan
from runtime.ui.plan_display import planning_status, render_compact_plan_summary


class DynamicExecutionResult(str):
    """String-compatible execution output with optional runtime context metadata."""

    def __new__(
        cls,
        value: str,
        *,
        run_context_summary: str = "",
        tool_dehydration_items: list[dict[str, object]] | None = None,
        recovery_outcome: str = "",
    ):
        obj = str.__new__(cls, str(value or ""))
        obj.run_context_summary = str(run_context_summary or "")
        obj.tool_dehydration_items = list(tool_dehydration_items or [])
        obj.recovery_outcome = str(recovery_outcome or "")
        return obj


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool = False,
    settings: RuntimeSettings | None = None,
    display_input: str | None = None,
    routing_input: str | None = None,
    output_controller=None,
    event_bus=None,
    inline_files=None,
    recovery_envelope=None,
    checkpoint_sink=None,
    tool_lifecycle_sink=None,
) -> str:
    """Plan and execute a request using dynamic Agents."""

    settings = settings or RuntimeSettings.from_env()
    privacy_policy = PrivacyPolicy(settings.privacy_mode)
    checkpoint = create_checkpoint(project_root)
    flywheel = FlywheelStore(project_root)
    current_input = raw_user_input
    stable_routing_input = str(routing_input or raw_user_input or "").strip()
    last_output = ""
    last_audit = None
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        output, audit = await _execute_dynamic_attempt(
            current_input,
            project_root,
            model_registry,
            mcp_manager,
            hooks,
            run_agent,
            show_plan=show_plan,
            settings=settings,
            privacy_policy=privacy_policy,
            flywheel=flywheel,
            attempt=attempt,
            display_input=display_input,
            routing_input=stable_routing_input,
            output_controller=output_controller,
            event_bus=event_bus,
            inline_files=inline_files,
            recovery_envelope=recovery_envelope,
            checkpoint_sink=checkpoint_sink,
            tool_lifecycle_sink=tool_lifecycle_sink,
        )
        last_output = output
        last_audit = audit
        if audit is not None and checkpoint.mode == "git_dirty_protected":
            scoped_paths = _audit_files_touched(audit)
            if scoped_paths:
                checkpoint = create_checkpoint(project_root, scoped_paths=scoped_paths)
        if audit is None or audit.passed:
            return output
        if not should_retry(attempt, max_attempts, audit):
            break
        print(f"最终审核未通过，开始第 {attempt + 1} 轮重规划。")
        current_input = build_repair_request(raw_user_input, audit, attempt + 1)

    if last_audit is not None and not last_audit.passed:
        rollback = rollback_checkpoint(checkpoint)
        _record_failure_case_safely(flywheel, raw_user_input, max_attempts, last_audit, rollback)
        last_audit.rollback_happened = rollback.rolled_back
        last_audit.rollback_message = rollback.message
        return DynamicExecutionResult(
            format_final_report(last_output, last_audit),
            run_context_summary=str(getattr(last_output, "run_context_summary", "") or ""),
            tool_dehydration_items=list(getattr(last_output, "tool_dehydration_items", []) or []),
        )

    return last_output or DynamicExecutionResult("主脑没有给出可执行路线。")


async def _execute_dynamic_attempt(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool,
    settings: RuntimeSettings,
    privacy_policy: PrivacyPolicy,
    flywheel: FlywheelStore,
    attempt: int,
    display_input: str | None = None,
    routing_input: str | None = None,
    output_controller=None,
    event_bus=None,
    inline_files=None,
    recovery_envelope=None,
    checkpoint_sink=None,
    tool_lifecycle_sink=None,
) -> tuple[str, object | None]:
    route_input = str(routing_input or raw_user_input or "").strip()
    visible_input = str(display_input or route_input or raw_user_input or "").strip()
    compute_placement_mode = reliability_flags_from_env().compute_placement
    planner_input = raw_user_input
    planner_refiner_enabled = settings.query_refiner_enabled
    planner_allow_project_scout = True
    planner_placement_decision = None
    memory_pack = _resolve_planner_memory_pack(project_root, flywheel, route_input)
    planning_run_context = RunContextStore(project_root) if project_root else None
    seed_inline_files(planning_run_context, project_root, inline_files)
    context_labels = labels_from_memory_pack(memory_pack)
    if planning_run_context is not None:
        context_labels.extend(planning_run_context.source_labels())
    compute_guard = ComputePlacementGuard(
        model_registry=model_registry,
        privacy_mode=settings.privacy_mode,
        mode=compute_placement_mode,
        context_labels=context_labels,
    )
    try:
        if compute_placement_mode == "enforce":
            planner_placement_decision = compute_guard.guard_planner_request(
                raw_user_input,
                settings.model_priority_for("orchestrator"),
            )
            planner_model_id = planner_placement_decision.model_id
            planner_input = compute_guard.guard_planning_packet(planner_placement_decision)
            planner_refiner_enabled = settings.query_refiner_enabled and planner_placement_decision.refiner_enabled
            planner_allow_project_scout = planner_placement_decision.allow_project_scout
        else:
            planner_model_id = settings.select_model_id(model_registry, "orchestrator")
    except ComputePlacementViolation as exc:
        event_bus = event_bus or ExecutionEventBus()
        event_bus.emit(
            "ComputePlacementBlocked",
            str(exc),
            mode=settings.execution_mode,
            agent="orchestrator",
            status="blocked",
            payload={"privacy_mode": settings.privacy_mode, "reason": str(exc)[:300]},
        )
        return DynamicExecutionResult(
            _format_compute_placement_error(exc),
            run_context_summary=_render_event_summary(event_bus),
        ), None
    refiner_model_id = (
        settings.select_model_id(model_registry, "query_refiner") if planner_refiner_enabled else None
    )
    synthesizer_model_id = settings.select_model_id(model_registry, "final_synthesizer")
    event_bus = event_bus or ExecutionEventBus()
    event_bus.emit(
        "PlanningStarted",
        "开始规划本轮任务",
        mode=settings.execution_mode,
        agent="orchestrator",
        payload={"planner_model_id": planner_model_id},
    )
    try:
        with planning_status(visible_input, mode=settings.execution_mode, enabled=show_plan):
            refined, plan = await preview_plan(
                planner_input,
                refiner_model=model_registry.get_model(refiner_model_id) if refiner_model_id else None,
                planner_model=model_registry.get_model(planner_model_id),
                hooks=hooks,
                refiner_enabled=planner_refiner_enabled,
                allowed_worker_models=settings.worker_model_pool(model_registry),
                project_root=project_root,
                run_context=planning_run_context,
                memory_pack=memory_pack,
                allow_project_scout=planner_allow_project_scout,
                routing_input=route_input,
                recovery_envelope=recovery_envelope,
            )
    except Exception as exc:
        event_bus.emit(
            "PlanningFailed",
            str(exc),
            mode=settings.execution_mode,
            agent="orchestrator",
            status="failed",
            payload={"planner_model_id": planner_model_id, "reason": str(exc)[:200]},
        )
        return DynamicExecutionResult(
            format_planning_error(exc, planner_model_id=planner_model_id),
            run_context_summary=_render_event_summary(event_bus),
        ), None
    try:
        _apply_executor_model_defaults_with_mode(
            plan,
            settings,
            model_registry,
            compute_placement_mode=compute_placement_mode,
        )
    except ComputePlacementViolation as exc:
        event_bus.emit(
            "ComputePlacementBlocked",
            str(exc),
            mode=settings.execution_mode,
            agent="supervisor",
            status="blocked",
            payload={"privacy_mode": settings.privacy_mode, "reason": str(exc)[:300]},
        )
        return DynamicExecutionResult(
            _format_compute_placement_error(exc),
            run_context_summary=_render_event_summary(event_bus),
        ), None
    plan_before_normalize = plan
    plan, normalization_notes = normalize_plan_for_execution(plan)
    plan_was_normalized = plan is not plan_before_normalize
    execution_contract = normalize_execution_contract(
        plan,
        "\n".join([route_input, refined.refined_request]),
        mode=settings.execution_mode,
    )
    gate_decision = apply_pipeline_gate(plan, refined.refined_request)
    run_state = PipelineRunState.create(
        refined.refined_request,
        plan,
        project_root=project_root,
        mode=settings.execution_mode,
        output_controller=output_controller,
        event_bus=event_bus,
        run_context=planning_run_context,
        memory_pack=memory_pack,
    )
    run_state.model_labels = _model_label_map(
        model_registry,
        [planner_model_id, synthesizer_model_id, *(getattr(task, "model", "") for task in plan.tasks)],
    )
    run_state.checkpoint_sink = checkpoint_sink
    run_state.tool_lifecycle_sink = tool_lifecycle_sink
    run_state.checkpoint_compatibility = recovery_compatibility_for_plan(
        plan,
        execution_mode=settings.execution_mode,
    )
    if planner_placement_decision is not None:
        run_state.emit_event(
            "ComputePlacementEnforced",
            "compute placement enforced for planner",
            mode=settings.execution_mode,
            agent="orchestrator",
            status="completed",
            payload=planner_placement_decision.to_dict(),
        )
    _record_compute_placement_observation(
        plan,
        refined.refined_request,
        run_state,
        privacy_mode=settings.privacy_mode,
    )
    run_state.output_controller.enter_planning("planning completed")
    if plan_was_normalized:
        run_state.emit_event(
            "PlanNormalized",
            "\u8ba1\u5212\u5df2\u89c4\u6574",
            mode=settings.execution_mode,
            agent="orchestrator",
            status="completed",
            payload={
                "notes": list(normalization_notes),
                "route_type": plan.route_type,
                "task_count": len(plan.tasks),
            },
        )
    run_state.emit_event(
        "ExecutionContractApplied",
        "执行契约已收口",
        mode=settings.execution_mode,
        agent="supervisor",
        status="completed",
        payload=execution_contract.to_dict(),
    )
    run_state.emit_event(
        "PlanningCompleted",
        "规划完成",
        mode=settings.execution_mode,
        agent="orchestrator",
        status="completed",
        payload={
            "route_type": plan.route_type,
            "task_count": len(plan.tasks),
            "tasks": _planning_event_tasks(plan),
        },
    )
    run_state.record_gate(gate_decision)
    validation = validate_plan(plan, privacy_policy=privacy_policy)
    review = review_plan(plan)
    if show_plan:
        if attempt > 1:
            print(f"========== 第 {attempt} 轮重规划 ==========")
        detail_selector = _save_plan_detail(
            project_root,
            refined,
            plan,
            validation,
            review,
            gate_decision,
        )
        _safe_print(
            render_compact_plan_summary(
                refined,
                plan,
                validation,
                review,
                gate_decision,
                mode=settings.execution_mode,
                detail_selector=detail_selector,
            )
        )

    if not validation.valid:
        run_state.output_controller.enter_failed("plan validation failed")
        return (
            _dynamic_result(
            "主脑规划未通过校验，已停止执行。\n\n"
            f"{format_validation(validation)}\n\n"
            "你可以用 /plan 查看规划详情，或把问题说得更具体一点。",
            run_state,
            ),
            None,
        )

    if not review.approved:
        run_state.output_controller.enter_failed("plan review failed")
        message = (
            "计划审查未通过，已停止执行，避免按不安全或不完整的计划修改项目。\n\n"
            f"{format_plan_review(review)}\n\n"
            "系统将把这些问题回传给主脑进行重规划。"
        )
        return _dynamic_result(message, run_state), audit_plan_review_failure(review)

    resolved_recovery = RecoveryEnvelope.from_dict(recovery_envelope) if recovery_envelope else None
    if resolved_recovery is not None:
        recovery_plan = evaluate_recovery_plan(
            resolved_recovery,
            plan,
            planner_disposition=str(
                (getattr(plan, "recovery_interface", {}) or {}).get("disposition") or "replan"
            ),
            current_compatibility=run_state.checkpoint_compatibility,
        )
        run_state.apply_recovery_plan(recovery_plan, envelope=resolved_recovery)
        if recovery_plan.disposition == "continue_safe" and recovery_plan.blocked_task_ids:
            run_state.record_checkpoint("plan.accepted")
            blocked = ", ".join(recovery_plan.blocked_task_ids)
            message = (
                "检测到中断任务包含不能自动重放的写入或未知副作用步骤，已停止自动续接。\n"
                f"阻断任务：{blocked}\n"
                "请在当前输入框中给出新的要求；后续执行仍会重新经过审批和安全检查。"
            )
            return _final_dynamic_result(message, run_state), None

    run_state.record_checkpoint("plan.accepted")

    factory = AgentFactory(model_registry, mcp_manager, workspace_root=project_root)

    if plan.route_type == "direct_answer":
        run_state.output_controller.enter_running(reason="direct answer")
        direct_input = _direct_answer_input_with_inline_context(
            raw_user_input,
            refined.refined_request,
            project_root,
            planner_model_id,
            run_state,
        )
        output = await _run_direct_answer(
            direct_input,
            plan,
            planner_model_id,
            factory,
            hooks,
            run_agent,
            execution_mode=settings.execution_mode,
            run_state=run_state,
        )
        run_state.output_controller.enter_completed("direct answer completed")
        return _final_dynamic_result(output, run_state), None

    if plan.route_type == "clarify":
        run_state.output_controller.enter_completed("clarification requested")
        return _final_dynamic_result(plan.clarifying_question or "这个问题还需要你补充一点信息。", run_state), None

    if plan.route_type == "single_agent":
        run_state.output_controller.enter_running(reason="single agent")
        output, audit = await _run_single_agent(
            refined.refined_request,
            plan,
            project_root,
            factory,
            hooks,
            run_agent,
            run_state,
            flywheel,
            execution_mode=settings.execution_mode,
            show_plan=show_plan,
            attempt=attempt,
        )
        if getattr(audit, "passed", True):
            run_state.output_controller.enter_completed("single agent completed")
        else:
            run_state.output_controller.enter_failed("single agent audit failed")
        result = _dynamic_result(output, run_state)
        if getattr(audit, "passed", True):
            result = _final_dynamic_result(output, run_state)
        return result, audit

    if plan.route_type == "multi_agent":
        run_state.output_controller.enter_running(reason="multi agent")
        try:
            output = await _run_multi_agent(
                refined.refined_request,
                plan,
                project_root,
                synthesizer_model_id,
                factory,
                hooks,
                run_agent,
                run_state,
                execution_mode=settings.execution_mode,
                show_progress=show_plan,
                attempt=attempt,
                approval_policy_factory=_supervisor_approval_policy_factory(plan),
            )
        except Exception:
            _record_flywheel_safely(flywheel, run_state)
            raise
        audit = audit_execution(plan, run_state, output)
        _record_flywheel_safely(flywheel, run_state, audit)
        if getattr(audit, "passed", True):
            run_state.output_controller.enter_completed("multi agent completed")
        else:
            run_state.output_controller.enter_failed("multi agent audit failed")
        final_report = format_final_report(output, audit)
        result = _dynamic_result(final_report, run_state)
        if getattr(audit, "passed", True):
            result = _final_dynamic_result(final_report, run_state)
        return result, audit

    run_state.output_controller.enter_failed("unknown route")
    return _dynamic_result("主脑没有给出可执行路线。", run_state), None


def _resolve_planner_memory_pack(project_root: Path, flywheel: FlywheelStore, raw_user_input: str):
    if project_root is None:
        return None
    try:
        return MemoryResolver(project_root, flywheel=flywheel).resolve_for_planner(raw_user_input)
    except Exception:
        return None


def _dynamic_result(output: str, run_state: PipelineRunState | None) -> DynamicExecutionResult:
    run_context = getattr(run_state, "run_context", None)
    summary = ""
    tool_dehydration_items: list[dict[str, object]] = []
    if run_context is not None and hasattr(run_context, "render_for_task"):
        try:
            summary = run_context.render_for_task()
        except Exception:
            summary = ""
    if run_context is not None and hasattr(run_context, "dehydrated_tool_results"):
        try:
            tool_dehydration_items = list(run_context.dehydrated_tool_results() or [])
        except Exception:
            tool_dehydration_items = []
    event_summary = _render_event_summary(getattr(run_state, "event_bus", None))
    if event_summary:
        summary = "\n\n".join(part for part in [summary, event_summary] if part)
    return DynamicExecutionResult(
        str(output or ""),
        run_context_summary=summary,
        tool_dehydration_items=tool_dehydration_items,
        recovery_outcome=str(getattr(run_state, "recovery_outcome", "") or ""),
    )


def _final_dynamic_result(output: str, run_state: PipelineRunState | None) -> DynamicExecutionResult:
    result = _dynamic_result(output, run_state)
    if run_state is not None:
        run_state.record_final_ready(str(result))
    return result


def _save_plan_detail(
    project_root: Path,
    refined,
    plan,
    validation,
    review,
    gate_decision,
) -> str:
    selector = "plan-last"
    try:
        from runtime.history.expand_store import ExpandBlockStore

        detail = "\n".join(
            [
                format_execution_plan(refined, plan, validation),
                format_plan_review(review),
                format_gate_decision(gate_decision),
            ]
        )
        ExpandBlockStore(project_root).save_text(
            selector,
            detail,
            kind="plan",
            title="上一轮完整规划",
            preview=f"{getattr(plan, 'route_type', '')} · {len(getattr(plan, 'tasks', []) or [])} task(s)",
        )
    except Exception:
        return ""
    return selector


def _supervisor_approval_policy_factory(plan):
    if supervisor_route(plan) != "team":
        return None

    from runtime.agent.approval_policy import SupervisorApprovalPolicy

    return SupervisorApprovalPolicy.from_task


def _record_compute_placement_observation(
    plan,
    refined_request: str,
    run_state: PipelineRunState | None,
    *,
    privacy_mode: str,
    mode: str | None = None,
) -> None:
    if run_state is None:
        return
    try:
        result = observe_compute_placement_for_plan(
            plan,
            refined_request,
            privacy_mode=privacy_mode,
            mode=mode,
        )
        run_state.record_compute_placement_result(result)
    except Exception as exc:
        run_state.emit_event(
            "ComputePlacementFailed",
            str(exc) or exc.__class__.__name__,
            agent="runtime",
            status="warning",
            payload={"reason": str(exc)[:240]},
        )


def _render_event_summary(event_bus) -> str:
    if event_bus is None or not hasattr(event_bus, "snapshot"):
        return ""
    try:
        from runtime.ui.event_render import render_execution_events

        events = event_bus.snapshot()
        if not events:
            return ""
        return render_execution_events(events, limit=8)
    except Exception:
        return ""


def _format_compute_placement_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return (
        "Compute placement blocked this run before model/tool execution.\n"
        f"Reason: {message}\n"
        "What to do: configure a local model for sensitive/private planning or a tool-capable local executor, "
        "or turn off LUCODE_COMPUTE_PLACEMENT=enforce if you are intentionally running the legacy route."
    )


def format_planning_error(exc: Exception, planner_model_id: str = "") -> str:
    message = str(exc).strip() or exc.__class__.__name__
    hint = "请检查主脑模型配置、Provider base_url 和模型兼容性。"
    if "tuple" in message and "choices" in message:
        hint = (
            "模型服务返回格式与当前 OpenAI-compatible 适配器不兼容。"
            "建议使用 /models brain 主脑 切换到已验证模型，"
            "或运行 /models probe 重新探测该模型，并检查自定义中转 base_url 是否为 /v1 兼容地址。"
        )
    return (
        "规划阶段失败，Lucode 已停止本轮执行，项目文件没有被修改。\n"
        f"当前主脑模型：{planner_model_id or '未知'}\n"
        f"错误类型：{exc.__class__.__name__}\n"
        f"原因：{message}\n"
        f"建议：{hint}"
    )


def _apply_executor_model_defaults_with_mode(
    plan,
    settings,
    model_registry,
    *,
    compute_placement_mode: str,
) -> None:
    try:
        _apply_executor_model_defaults(
            plan,
            settings,
            model_registry,
            compute_placement_mode=compute_placement_mode,
        )
    except TypeError as exc:
        if (
            str(compute_placement_mode or "").strip().lower() != "enforce"
            and "compute_placement_mode" in str(exc)
        ):
            _apply_executor_model_defaults(plan, settings, model_registry)
            return
        raise


def _apply_executor_model_defaults(
    plan,
    settings,
    model_registry,
    *,
    compute_placement_mode: str = "off",
) -> None:
    """Fill empty / invalid / out-of-pool task.model, distributing across models.

    Strategy:
    1. A task.model is kept only if it is configured-usable AND (when the user set
       a worker model pool) it belongs to that pool.
    2. Tasks needing a backfill are assigned by round-robin over the candidate
       models so a multi-worker team gets different models instead of collapsing
       onto one shared executor default.
    3. Candidates = the worker pool when set (never extended past it), otherwise
       the executor priority list plus the executor default as last resort.
    """
    if not plan.tasks:
        return

    privacy_mode = getattr(settings, "privacy_mode", "local_first")
    pool = list(settings.worker_model_pool(model_registry)) if hasattr(settings, "worker_model_pool") else []

    candidate_ids = list(pool) if pool else list(getattr(settings, "executor_model_priority", []) or [])
    if not pool:
        try:
            executor_model_id = settings.select_model_id(model_registry, "executor")
        except Exception:
            executor_model_id = None
        if executor_model_id and executor_model_id not in candidate_ids:
            candidate_ids.append(executor_model_id)
    if not candidate_ids:
        return

    enforce_compute_placement = str(compute_placement_mode or "").strip().lower() == "enforce"
    compute_guard = (
        ComputePlacementGuard(
            model_registry=model_registry,
            privacy_mode=privacy_mode,
            mode=compute_placement_mode,
        )
        if enforce_compute_placement
        else None
    )
    rr_index = 0
    for task in plan.tasks:
        needs_tools = bool(task.mcp)
        in_pool = (not pool) or (str(task.model or "") in pool)
        if (
            task.model
            and in_pool
            and _executor_model_allowed_for_mode(
                model_registry,
                task,
                task.model,
                privacy_mode=privacy_mode,
                requires_tools=needs_tools,
                compute_guard=compute_guard,
            )
        ):
            continue
        chosen = _next_usable_candidate(
            candidate_ids,
            rr_index,
            model_registry,
            privacy_mode,
            task,
            needs_tools,
            compute_guard,
        )
        if chosen is not None:
            task.model = chosen.model_id
            rr_index = chosen.candidate_index + 1
        elif str(compute_placement_mode or "").strip().lower() == "enforce":
            raise ComputePlacementViolation(
                "Compute placement blocked: no configured execution model satisfies "
                f"task={getattr(task, 'id', '') or 'unknown'}; "
                f"requires_tools={needs_tools}; privacy={privacy_mode}."
            )


def _next_usable_candidate(
    candidate_ids,
    start,
    model_registry,
    privacy_mode,
    task,
    needs_tools,
    compute_guard,
):
    if compute_guard is not None:
        return compute_guard.select_executor_model(
            task,
            candidate_ids,
            start_index=start,
            requires_tools=needs_tools,
        )
    count = len(candidate_ids)
    for offset in range(count):
        idx = (start + offset) % count
        model_id = candidate_ids[idx]
        if _task_model_is_usable(
            model_registry,
            model_id,
            privacy_mode=privacy_mode,
            requires_tools=needs_tools,
        ):
            from types import SimpleNamespace

            return SimpleNamespace(model_id=model_id, candidate_index=idx)
    return None


def _executor_model_allowed_for_mode(
    model_registry,
    task,
    model_id: str,
    *,
    privacy_mode: str,
    requires_tools: bool,
    compute_guard,
) -> bool:
    if compute_guard is not None:
        return compute_guard.executor_model_allowed(task, model_id, requires_tools=requires_tools)
    return _task_model_is_usable(
        model_registry,
        model_id,
        privacy_mode=privacy_mode,
        requires_tools=requires_tools,
    )


def _model_label_map(model_registry, model_ids) -> dict[str, str]:
    labels: dict[str, str] = {}
    for model_id in model_ids:
        clean_id = str(model_id or "").strip()
        if not clean_id or clean_id in labels:
            continue
        labels[clean_id] = _friendly_model_label(model_registry, clean_id)
    return labels


def _friendly_model_label(model_registry, model_id: str) -> str:
    try:
        info = model_registry.get_model_info(model_id)
    except Exception:
        info = {}
    for key in ("display_name_zh", "display_name", "model_name", "provider_ref"):
        value = str((info or {}).get(key) or "").strip()
        if value:
            return _prettify_model_label(value, info or {})
    return model_id


def _prettify_model_label(value: str, info: dict) -> str:
    text = str(value or "").strip()
    provider = str((info or {}).get("provider") or "").strip()
    model_name = str((info or {}).get("model_name") or "").strip()
    if provider and model_name and text.lower() == f"{provider} {model_name}".lower():
        return _provider_model_label(provider, model_name)
    if provider and model_name and text.lower() == f"{provider} {model_name.replace('-', '_')}".lower():
        return _provider_model_label(provider, model_name)
    return _title_model_token(text) if _looks_like_model_slug(text) else text


def _looks_like_model_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.:/ -]+", str(value or "").strip())) and any(
        marker in str(value or "") for marker in ("_", "-", "/")
    )


def _title_model_token(value: str) -> str:
    text = str(value or "").strip()
    if "/" in text:
        text = text.split("/")[-1]
    parts = [part for part in re.split(r"[_\-\s]+", text) if part]
    if not parts:
        return text
    return " ".join(_title_model_part(part) for part in parts)


def _provider_model_label(provider: str, model_name: str) -> str:
    provider_label = _title_model_part(provider)
    model_label = _title_model_token(model_name)
    provider_prefix = _title_model_token(provider)
    if model_label.lower().startswith(provider_prefix.lower() + " "):
        return model_label
    return f"{provider_label} {model_label}"


def _title_model_part(part: str) -> str:
    upper_values = {"v1", "v2", "v3", "v4", "k2", "r1", "gpt", "api"}
    text = str(part or "")
    special = {"deepseek": "DeepSeek", "openai": "OpenAI", "qwen": "Qwen", "kimi": "Kimi"}
    if text.lower() in special:
        return special[text.lower()]
    if text.lower() in upper_values:
        return text.upper()
    if text.isupper():
        return text
    return text[:1].upper() + text[1:]


def _planning_event_tasks(plan) -> list[dict[str, object]]:
    """Return the compact task shape consumed by the desktop WorkArea tree."""

    tasks = []
    for index, task in enumerate(list(getattr(plan, "tasks", []) or [])):
        task_id = str(getattr(task, "id", "") or f"task_{index + 1}")
        tasks.append(
            {
                "id": task_id,
                "title": str(getattr(task, "title", "") or task_id),
                "model": str(getattr(task, "model", "") or ""),
                "mcp": [str(item) for item in list(getattr(task, "mcp", []) or []) if str(item).strip()],
                "parallel_group": str(getattr(task, "parallel_group", "") or "1"),
                "depends_on": [
                    str(item) for item in list(getattr(task, "depends_on", []) or []) if str(item).strip()
                ],
            }
        )
    return tasks


def _task_model_is_usable(
    model_registry,
    model_id: str,
    *,
    privacy_mode: str = "local_first",
    requires_tools: bool = False,
) -> bool:
    if not model_id:
        return False
    try:
        info = model_registry.get_model_info(model_id)
    except Exception:
        return False
    return _model_info_usable_for_task(
        info,
        privacy_mode=privacy_mode,
        requires_tools=requires_tools,
    )


def _model_info_usable_for_task(
    info: dict | None,
    *,
    privacy_mode: str = "local_first",
    requires_tools: bool = False,
) -> bool:
    if not info:
        return False
    return model_usable_for_task(
        info,
        PrivacyPolicy(privacy_mode),
        requires_tools=requires_tools,
    )
