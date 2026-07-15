from __future__ import annotations

from dataclasses import dataclass, field

PARALLEL_SAFE_MCPS = {
    "project_filesystem_readonly",
    "code_locator",
    "web_search",
    "context7_docs",
    "grep_code_search",
    "git_tools",
}
DECLARED_WRITE_MCPS = {"workspace_edit"}
EXCLUSIVE_TOOL_LOCKS = {
    "command_runner": "terminal",
    "safe_backup": "workspace_backup",
}


@dataclass(frozen=True)
class CapabilityBindingView:
    mcp: tuple[str, ...] = ()
    read_set: tuple[str, ...] = ()
    write_intent: tuple[str, ...] = ()
    resource_locks: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionBatchDecision:
    tasks: list = field(default_factory=list)
    status: str = "serialized"
    reason: str = "single_task"
    details: tuple[str, ...] = ()

    @property
    def parallel(self) -> bool:
        return self.status == "parallel"


def _can_run_group_in_parallel(tasks: list) -> bool:
    decisions = execution_batch_decisions_for_group(tasks)
    return len(decisions) == 1 and decisions[0].parallel and len(tasks) > 1


def _format_parallel_batch_audit(group_id: int, batch: list) -> str:
    parts = []
    for task in batch:
        writes = _normalized_write_intent(task)
        read_set = [str(item).strip().replace("\\", "/") for item in list(getattr(task, "read_set", []) or [])]
        scope = ", ".join(writes or [item for item in read_set if item] or ["readonly/no declared writes"])
        parts.append(f"{getattr(task, 'id', 'unknown')}[{scope}]")
    return (
        f"parallel group {group_id}: safe parallel batch started for {len(batch)} workers. "
        "Reason: no in-batch dependencies, no overlapping write scopes, no shared resource locks. "
        f"Batch scope: {'; '.join(parts)}"
    )


def execution_batch_decisions_for_mode(
    tasks: list,
    execution_mode: str,
    capability_resolver=None,
) -> list[ExecutionBatchDecision]:
    del execution_mode
    task_list = list(tasks or [])
    if not task_list:
        return []
    return execution_batch_decisions_for_group(task_list, capability_resolver=capability_resolver)


def execution_batch_decisions_for_group(tasks: list, capability_resolver=None) -> list[ExecutionBatchDecision]:
    task_list = list(tasks or [])
    if not task_list:
        return []

    binding_by_task = _capability_bindings_for_tasks(task_list, capability_resolver)
    decisions: list[ExecutionBatchDecision] = []
    current_batch: list = []
    current_writes: set[str] = set()
    current_locks: set[str] = set()
    pending_reason = ""
    pending_details: tuple[str, ...] = ()

    for task in task_list:
        serial_reason, serial_details = _task_serial_reason(task, binding_by_task)
        if serial_reason:
            _append_batch_decision(
                decisions,
                current_batch,
                reason=pending_reason,
                details=pending_details,
            )
            current_batch = []
            current_writes = set()
            current_locks = set()
            pending_reason = ""
            pending_details = ()
            decisions.append(
                ExecutionBatchDecision(
                    tasks=[task],
                    status="serialized",
                    reason=serial_reason,
                    details=serial_details,
                )
            )
            continue

        if not current_batch:
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task, binding_by_task))
            current_locks = set(_normalized_resource_locks(task, binding_by_task))
            continue

        conflict_reason, conflict_details = _task_conflict_reason(
            task,
            current_batch,
            current_writes,
            current_locks,
            binding_by_task,
        )
        if conflict_reason:
            _append_batch_decision(
                decisions,
                current_batch,
                reason=pending_reason,
                details=pending_details,
            )
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task, binding_by_task))
            current_locks = set(_normalized_resource_locks(task, binding_by_task))
            pending_reason = conflict_reason
            pending_details = conflict_details
            continue

        current_batch.append(task)
        current_writes.update(_normalized_write_intent(task, binding_by_task))
        current_locks.update(_normalized_resource_locks(task, binding_by_task))
        if len(current_batch) > 1:
            pending_reason = ""
            pending_details = ()

    _append_batch_decision(decisions, current_batch, reason=pending_reason, details=pending_details)
    return decisions


def _execution_batches_for_mode(tasks: list, execution_mode: str) -> list[list]:
    return [decision.tasks for decision in execution_batch_decisions_for_mode(tasks, execution_mode)]


def _execution_batches_for_group(tasks: list) -> list[list]:
    return [decision.tasks for decision in execution_batch_decisions_for_group(tasks)]


def _append_batch_decision(
    decisions: list[ExecutionBatchDecision],
    batch: list,
    *,
    reason: str = "",
    details: tuple[str, ...] = (),
) -> None:
    if not batch:
        return
    if len(batch) > 1:
        decisions.append(
            ExecutionBatchDecision(
                tasks=list(batch),
                status="parallel",
                reason=_parallel_batch_reason(batch),
                details=_parallel_batch_details(batch),
            )
        )
        return
    decisions.append(
        ExecutionBatchDecision(
            tasks=list(batch),
            status="serialized",
            reason=reason or "single_task",
            details=details or ("single task has no parallel peer",),
        )
    )


def _task_conflicts_with_batch(task, batch: list, batch_writes: set[str]) -> bool:
    binding_by_task = _capability_bindings_for_tasks([task, *list(batch or [])], None)
    batch_locks = set()
    for existing in list(batch or []):
        batch_locks.update(_normalized_resource_locks(existing, binding_by_task))
    reason, _details = _task_conflict_reason(task, batch, batch_writes, batch_locks, binding_by_task)
    return bool(reason)


def _task_conflict_reason(
    task,
    batch: list,
    batch_writes: set[str],
    batch_locks: set[str],
    binding_by_task: dict[int, CapabilityBindingView] | None = None,
) -> tuple[str, tuple[str, ...]]:
    task_deps = set(getattr(task, "depends_on", []) or [])
    batch_ids = {getattr(existing, "id", "") for existing in batch}
    dependency_hits = sorted(str(item) for item in task_deps & batch_ids if str(item).strip())
    if dependency_hits:
        return (
            "dependency",
            (f"{getattr(task, 'id', 'unknown')} depends on in-batch task(s): {', '.join(dependency_hits)}",),
        )
    for existing in batch:
        existing_deps = set(getattr(existing, "depends_on", []) or [])
        if getattr(task, "id", "") in existing_deps:
            return (
                "dependency",
                (
                    f"{getattr(existing, 'id', 'unknown')} depends on "
                    f"{getattr(task, 'id', 'unknown')}",
                ),
            )

    task_locks = set(_normalized_resource_locks(task, binding_by_task))
    lock_hits = sorted(task_locks & set(batch_locks or []))
    if lock_hits:
        return "resource_lock", (f"shared resource lock(s): {', '.join(lock_hits)}",)

    writes = set(_normalized_write_intent(task, binding_by_task))
    conflict = _first_write_conflict(writes, set(batch_writes or []))
    if conflict:
        return "write_conflict", (f"overlapping write scope: {conflict[0]} <-> {conflict[1]}",)
    for existing in batch:
        conflict = _first_write_conflict(writes, set(_normalized_write_intent(existing, binding_by_task)))
        if conflict:
            return (
                "write_conflict",
                (
                    f"overlapping write scope with {getattr(existing, 'id', 'unknown')}: "
                    f"{conflict[0]} <-> {conflict[1]}",
                ),
            )
    return "", ()


def _requires_serial_execution(task) -> bool:
    reason, _details = _task_serial_reason(task)
    return bool(reason)


def _task_serial_reason(
    task,
    binding_by_task: dict[int, CapabilityBindingView] | None = None,
) -> tuple[str, tuple[str, ...]]:
    mcp_ids = _normalized_mcp_ids(task, binding_by_task)
    if "workspace_edit" in mcp_ids and not _normalized_write_intent(task, binding_by_task):
        return (
            "missing_write_intent",
            (f"{getattr(task, 'id', 'unknown')} uses workspace_edit without declared write_intent",),
        )

    exclusive_tools = sorted(mcp_id for mcp_id in mcp_ids if mcp_id in EXCLUSIVE_TOOL_LOCKS)
    unknown_tools = sorted(mcp_id for mcp_id in mcp_ids if mcp_id not in _parallel_allowed_mcps())
    if exclusive_tools or unknown_tools:
        locks = _normalized_resource_locks(task, binding_by_task)
        lock_text = ", ".join(locks or [f"mcp:{mcp_id}" for mcp_id in exclusive_tools + unknown_tools])
        tool_text = ", ".join(exclusive_tools + unknown_tools)
        return (
            "resource_lock",
            (f"{getattr(task, 'id', 'unknown')} requires exclusive resource lock(s): {lock_text}; tools: {tool_text}",),
        )
    return "", ()


def _parallel_allowed_mcps() -> set[str]:
    return PARALLEL_SAFE_MCPS | DECLARED_WRITE_MCPS


def _normalized_mcp_ids(task, binding_by_task: dict[int, CapabilityBindingView] | None = None) -> set[str]:
    binding = _binding_for_task(task, binding_by_task)
    if binding is not None:
        return {str(mcp_id or "").strip() for mcp_id in binding.mcp if str(mcp_id or "").strip()}
    return {
        str(mcp_id or "").strip()
        for mcp_id in list(getattr(task, "mcp", []) or [])
        if str(mcp_id or "").strip()
    }


def _normalized_resource_locks(task, binding_by_task: dict[int, CapabilityBindingView] | None = None) -> list[str]:
    binding = _binding_for_task(task, binding_by_task)
    if binding is not None:
        return list(binding.resource_locks)
    values: list[str] = []
    for item in list(getattr(task, "resource_locks", []) or []):
        value = _normalize_write_path(item).lower()
        if value:
            values.append(value)
    for mcp_id in sorted(_normalized_mcp_ids(task, binding_by_task)):
        if mcp_id in EXCLUSIVE_TOOL_LOCKS:
            values.append(EXCLUSIVE_TOOL_LOCKS[mcp_id])
        elif mcp_id not in _parallel_allowed_mcps():
            values.append(f"mcp:{mcp_id}")
    return _dedupe(values)


def _parallel_batch_reason(batch: list) -> str:
    has_writes = any(_normalized_write_intent(task) for task in list(batch or []))
    if has_writes:
        return "declared_write_no_conflict"
    return "readonly_no_write_conflict"


def _parallel_batch_details(batch: list) -> tuple[str, ...]:
    task_ids = ", ".join(str(getattr(task, "id", "unknown") or "unknown") for task in list(batch or []))
    if any(_normalized_write_intent(task) for task in list(batch or [])):
        return (
            f"parallel batch tasks: {task_ids}",
            "all workspace edits declare write_intent and write scopes do not overlap",
        )
    return (
        f"parallel batch tasks: {task_ids}",
        "readonly or read-oriented tools only; no dependency or resource-lock conflict",
    )


def _normalized_write_intent(task, binding_by_task: dict[int, CapabilityBindingView] | None = None) -> list[str]:
    binding = _binding_for_task(task, binding_by_task)
    if binding is not None:
        return [str(item or "").strip().replace("\\", "/").strip("/").lower() for item in binding.write_intent if str(item or "").strip()]
    values = []
    for item in list(getattr(task, "write_intent", []) or []):
        value = _normalize_write_path(item)
        if value:
            values.append(value.lower())
    return values



def _capability_bindings_for_tasks(tasks: list, capability_resolver=None) -> dict[int, CapabilityBindingView]:
    result: dict[int, CapabilityBindingView] = {}
    for task in list(tasks or []):
        binding = _resolve_capability_binding(task, capability_resolver)
        result[id(task)] = binding
    return result


def _resolve_capability_binding(task, capability_resolver=None) -> CapabilityBindingView:
    if capability_resolver is not None and hasattr(capability_resolver, "resolve_task"):
        try:
            binding = capability_resolver.resolve_task(task)
            return CapabilityBindingView(
                mcp=tuple(str(item or "").strip() for item in list(getattr(binding, "mcp", []) or []) if str(item or "").strip()),
                read_set=tuple(str(item or "").strip() for item in list(getattr(binding, "read_set", []) or []) if str(item or "").strip()),
                write_intent=tuple(str(item or "").strip() for item in list(getattr(binding, "write_intent", []) or []) if str(item or "").strip()),
                resource_locks=tuple(str(item or "").strip().lower() for item in list(getattr(binding, "resource_locks", []) or []) if str(item or "").strip()),
            )
        except Exception:
            pass
    return CapabilityBindingView(
        mcp=tuple(str(item or "").strip() for item in list(getattr(task, "mcp", []) or []) if str(item or "").strip()),
        read_set=tuple(str(item or "").strip() for item in list(getattr(task, "read_set", []) or []) if str(item or "").strip()),
        write_intent=tuple(str(item or "").strip() for item in list(getattr(task, "write_intent", []) or []) if str(item or "").strip()),
        resource_locks=tuple(str(item or "").strip().lower() for item in list(getattr(task, "resource_locks", []) or []) if str(item or "").strip()),
    )


def _binding_for_task(task, binding_by_task: dict[int, CapabilityBindingView] | None = None) -> CapabilityBindingView | None:
    if not binding_by_task:
        return None
    return binding_by_task.get(id(task))

def _normalize_write_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _write_sets_conflict(first: set[str], second: set[str]) -> bool:
    return _first_write_conflict(first, second) is not None


def _first_write_conflict(first: set[str], second: set[str]) -> tuple[str, str] | None:
    for left in sorted(first):
        for right in sorted(second):
            if _write_paths_conflict(left, right):
                return left, right
    return None


def _write_paths_conflict(left: str, right: str) -> bool:
    left = _normalize_write_path(left).lower()
    right = _normalize_write_path(right).lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
