from __future__ import annotations

from runtime.execution.pipeline import PipelineRunState, TaskRunRecord
from runtime.memory.distiller import distill_run_experience
from runtime.memory.compressor import Compressor, compress_distilled_entry
from runtime.memory.flywheel import FlywheelStore
from runtime.memory.maintenance import decay_stale_entries, expire_stale_entries, record_memory_usage
from runtime.safety.checkpoint import RollbackResult


def _record_flywheel_safely(
    flywheel: FlywheelStore,
    run_state: PipelineRunState,
    audit=None,
    *,
    summary_compressor: Compressor | None = None,
    run_memory_maintenance: bool = False,
) -> None:
    try:
        flywheel.record_pipeline_state(run_state)
    except Exception as exc:
        print(f"Flywheel recording failed and was skipped: {exc}")
    if audit is not None:
        _record_distilled_experience_safely(
            flywheel,
            run_state,
            audit,
            summary_compressor=summary_compressor,
        )
    _record_pending_memory_usage_safely(flywheel, run_state)
    if run_memory_maintenance:
        _run_memory_maintenance_safely(flywheel)


def _record_failure_case_safely(
    flywheel: FlywheelStore,
    raw_user_input: str,
    attempt_count: int,
    audit,
    rollback: RollbackResult,
) -> None:
    try:
        flywheel.record_failure_case(
            user_request=raw_user_input,
            attempt_count=attempt_count,
            models_used=list(getattr(audit, "models_used", []) or []),
            files_touched=list(getattr(audit, "files_touched", []) or []),
            failure_reasons=list(audit.remaining_issues),
            rollback_status="rolled_back" if rollback.rolled_back else "not_rolled_back",
            lesson=(
                "Automatic repair reached the retry limit but still failed Final Auditor. "
                "Future planning should narrow task scope, add acceptance criteria, or use a stronger model."
            ),
        )
    except Exception as exc:
        print(f"Failure memory recording failed and was skipped: {exc}")

    try:
        audit.rollback_happened = bool(rollback.rolled_back)
        audit.rollback_message = rollback.message
    except Exception:
        pass
    failure_state = _failure_run_state(raw_user_input, attempt_count, audit)
    _record_distilled_experience_safely(flywheel, failure_state, audit)


def _record_distilled_experience_safely(
    flywheel: FlywheelStore,
    run_state: PipelineRunState,
    audit,
    *,
    summary_compressor: Compressor | None = None,
) -> None:
    try:
        decisions = distill_run_experience(run_state, audit)
        for decision in decisions:
            if not getattr(decision, "accepted", False):
                continue
            entry = getattr(decision, "entry", None)
            if not isinstance(entry, dict):
                continue
            compressed_entry = compress_distilled_entry(entry, compressor=summary_compressor)
            flywheel.upsert_distilled_entry(compressed_entry)
    except Exception as exc:
        print(f"Experience distiller recording failed and was skipped: {exc}")


def _run_memory_maintenance_safely(flywheel: FlywheelStore) -> None:
    try:
        decay_stale_entries(flywheel)
        expire_stale_entries(flywheel)
    except Exception as exc:
        print(f"Memory maintenance failed and was skipped: {exc}")


def _record_pending_memory_usage_safely(flywheel: FlywheelStore, run_state: PipelineRunState) -> None:
    memory_pack = getattr(run_state, "memory_pack", None)
    usage_exporter = getattr(memory_pack, "pop_pending_usage", None)
    if not callable(usage_exporter):
        return
    try:
        usage_by_source = usage_exporter()
        for source, entry_ids in usage_by_source.items():
            record_memory_usage(flywheel, entry_ids, source=source)
    except Exception as exc:
        print(f"Memory usage recording failed and was skipped: {exc}")


def _failure_run_state(raw_user_input: str, attempt_count: int, audit) -> PipelineRunState:
    files_touched = _audit_files_touched(audit)
    task = TaskRunRecord(
        id="final_failure",
        title="Final failed repair attempt",
        skill_id="repair_loop",
        model=",".join(list(getattr(audit, "models_used", []) or [])),
        mcp=[],
        read_set=files_touched,
        write_intent=files_touched,
        status="failed",
        error="; ".join(list(getattr(audit, "remaining_issues", []) or [])),
    )
    return PipelineRunState(
        user_request=raw_user_input,
        route_type="failure_repair",
        reason=f"repair loop exhausted after {attempt_count} attempts",
        tasks=[task],
        errors=list(getattr(audit, "remaining_issues", []) or []),
    )


def _audit_files_touched(audit) -> list[str]:
    values = []
    for raw in list(getattr(audit, "files_touched", []) or []):
        path = str(raw or "").strip().replace("\\", "/")
        if path and path not in values:
            values.append(path)
    return values
