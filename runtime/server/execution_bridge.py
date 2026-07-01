from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from runtime.events import ExecutionEvent, ExecutionEventBus


@dataclass(frozen=True)
class RunExecutionResult:
    final_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunExecutionRequest:
    run_id: str
    session_id: str
    user_input: str
    workspace_root: Path
    event_bus: ExecutionEventBus
    cancel_requested: asyncio.Event


class RunExecutor(Protocol):
    def __call__(self, request: RunExecutionRequest) -> Awaitable[RunExecutionResult | str | None]:
        ...


class KernelAgentLoopExecutor:
    """Default Runtime Server executor backed by the existing Python Agent Loop."""

    async def __call__(self, request: RunExecutionRequest) -> RunExecutionResult:
        from runtime.config.app_home import get_app_home
        from runtime.config.settings import RuntimeSettings
        from runtime.config.workspace import discover_workspace_context
        from runtime.kernel import KernelFacade

        context = discover_workspace_context(
            get_app_home(),
            cwd=request.workspace_root,
            explicit_workspace=True,
        )
        response = await KernelFacade(context).run_once(
            request.user_input,
            show_plan=True,
            settings=RuntimeSettings.from_env(workspace_root=request.workspace_root),
            event_bus=request.event_bus,
        )
        return RunExecutionResult(
            final_output=str(getattr(response, "final_output", "") or ""),
            metadata={
                "turn_status": str(getattr(response, "turn_status", "") or ""),
                "stopped": bool(getattr(response, "stopped", False)),
                "mcp_ids_used": list(getattr(response, "mcp_ids_used", []) or []),
                "output_already_printed": bool(getattr(response, "output_already_printed", False)),
            },
        )


def emit_execution_event_as_run_event(
    *,
    run_events,
    run_id: str,
    session_id: str,
    event: ExecutionEvent,
):
    payload = execution_event_payload(event)
    return run_events.emit(
        run_id=run_id,
        session_id=session_id,
        event_type=map_execution_event_type(event.event_type),
        payload=payload,
    )


def execution_event_payload(event: ExecutionEvent) -> dict[str, Any]:
    payload = dict(getattr(event, "payload", {}) or {})
    payload.update(
        {
            "source_event_type": str(getattr(event, "event_type", "") or ""),
            "message": str(getattr(event, "message", "") or ""),
            "mode": str(getattr(event, "mode", "") or ""),
            "agent": str(getattr(event, "agent", "") or ""),
            "task_id": str(getattr(event, "task_id", "") or ""),
            "status": str(getattr(event, "status", "") or ""),
            "source_time": str(getattr(event, "timestamp", "") or ""),
        }
    )
    return payload


def map_execution_event_type(event_type: str) -> str:
    raw = str(event_type or "").strip()
    if not raw:
        return "runtime.event"
    mapping = {
        "TurnStarted": "kernel.turn_started",
        "TurnEnded": "kernel.turn_ended",
        "PlanningStarted": "planner.started",
        "PlanningCompleted": "planner.completed",
        "PlanningFailed": "planner.failed",
        "PlannerFailed": "planner.failed",
        "TaskStarted": "task.started",
        "TaskCompleted": "task.completed",
        "TaskFailed": "task.failed",
        "AgentMessageDelta": "worker.delta",
        "ToolInvoked": "tool.requested",
        "ToolApprovalPre": "tool.approval_required",
        "ToolApprovalPost": "tool.completed",
        "AuditStarted": "audit.started",
        "AuditCompleted": "audit.completed",
        "FinalAuditStarted": "audit.started",
        "FinalAuditCompleted": "audit.completed",
    }
    return mapping.get(raw, _camel_to_event_type(raw))


async def call_run_executor(executor: RunExecutor, request: RunExecutionRequest) -> RunExecutionResult:
    result = executor(request)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, RunExecutionResult):
        return result
    if result is None:
        return RunExecutionResult()
    return RunExecutionResult(final_output=str(result))


def _camel_to_event_type(value: str) -> str:
    pieces: list[str] = []
    current = ""
    for char in value:
        if char.isupper() and current:
            pieces.append(current.lower())
            current = char
        else:
            current += char
    if current:
        pieces.append(current.lower())
    if not pieces:
        return "runtime.event"
    if len(pieces) == 1:
        return f"runtime.{pieces[0]}"
    return f"{pieces[0]}.{ '_'.join(pieces[1:]) }"
