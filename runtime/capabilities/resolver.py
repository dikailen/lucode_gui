from __future__ import annotations

from dataclasses import dataclass


EXCLUSIVE_MCP_RESOURCE_LOCKS = {
    "command_runner": "terminal",
    "safe_backup": "workspace_backup",
}


@dataclass(frozen=True)
class CapabilityBinding:
    task_id: str
    mcp: tuple[str, ...] = ()
    read_set: tuple[str, ...] = ()
    write_intent: tuple[str, ...] = ()
    resource_locks: tuple[str, ...] = ()
    source: str = "planner_task"
    reasons: tuple[str, ...] = ()

    @property
    def requires_tools(self) -> bool:
        return bool(self.mcp)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "mcp": list(self.mcp),
            "read_set": list(self.read_set),
            "write_intent": list(self.write_intent),
            "resource_locks": list(self.resource_locks),
            "source": self.source,
            "reasons": list(self.reasons),
        }


class CapabilityResolver:
    """Resolve task capabilities before binding MCP servers.

    Stage 7 deliberately preserves planner-provided task.mcp behavior. The
    resolver is the stable seam future plugins can extend without adding new
    execution modes.
    """

    def resolve_task(self, task) -> CapabilityBinding:
        mcp = _string_tuple(getattr(task, "mcp", []) or [])
        read_set = _string_tuple(getattr(task, "read_set", []) or [])
        write_intent = _string_tuple(getattr(task, "write_intent", []) or [])
        resource_locks = _resource_locks_for_task(task, mcp)
        reasons = ["planner_task_mcp_passthrough"]
        if write_intent and "workspace_edit" in mcp:
            reasons.append("declared_write_scope")
        if read_set:
            reasons.append("declared_read_scope")
        if resource_locks:
            reasons.append("exclusive_resource_lock")
        return CapabilityBinding(
            task_id=str(getattr(task, "id", "") or ""),
            mcp=mcp,
            read_set=read_set,
            write_intent=write_intent,
            resource_locks=resource_locks,
            source="planner_task",
            reasons=tuple(reasons),
        )


def _resource_locks_for_task(task, mcp: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for item in list(getattr(task, "resource_locks", []) or []):
        clean = _normalize_resource(item)
        if clean:
            values.append(clean)
    for mcp_id in mcp:
        lock = EXCLUSIVE_MCP_RESOURCE_LOCKS.get(mcp_id)
        if lock:
            values.append(lock)
    return _dedupe_tuple(values)


def _string_tuple(values) -> tuple[str, ...]:
    return _dedupe_tuple(str(item or "").strip() for item in list(values or []) if str(item or "").strip())


def _normalize_resource(value) -> str:
    clean = str(value or "").strip().replace("\\", "/").strip("/")
    while "//" in clean:
        clean = clean.replace("//", "/")
    return clean.lower()


def _dedupe_tuple(values) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return tuple(result)
