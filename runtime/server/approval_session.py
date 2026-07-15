from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from runtime.hooks.tool_events import build_tool_event


@dataclass
class PendingApproval:
    approval_id: str
    prompt: str
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[str]
    payload: dict[str, Any]


class RuntimeApprovalSession:
    """GUI-facing approval surface used by runtime.agent.approval.

    The Agents SDK still performs the interruption. This session only bridges
    that interruption into the runtime event stream and waits for the GUI API to
    resolve it.
    """

    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        run_events,
        event_observer=None,
        attempt_id: str = "",
    ) -> None:
        self.run_id = str(run_id or "")
        self.session_id = str(session_id or "")
        self.attempt_id = str(attempt_id or f"attempt:{self.run_id}")
        self._run_events = run_events
        self._event_observer = event_observer
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    async def request_approval(self, prompt: str) -> str:
        return await self.request_tool_approval(prompt)

    async def request_tool_approval(
        self,
        prompt: str,
        *,
        tool_name: str = "",
        arguments: str | None = None,
        tool_rule: str = "",
        preview: str = "",
        invocation_id: str = "",
    ) -> str:
        approval_id = f"approval_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        payload = self._approval_payload(
            approval_id=approval_id,
            prompt=prompt,
            tool_name=tool_name,
            arguments=arguments,
            tool_rule=tool_rule,
            preview=preview,
            invocation_id=invocation_id,
        )
        pending = PendingApproval(
            approval_id=approval_id,
            prompt=str(prompt or ""),
            loop=loop,
            future=future,
            payload=payload,
        )
        with self._lock:
            self._pending[approval_id] = pending
        self._emit("approval.requested", payload)
        try:
            return await future
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, decision: str) -> dict[str, Any]:
        clean_id = str(approval_id or "").strip()
        normalized = _normalize_decision(decision)
        with self._lock:
            pending = self._pending.pop(clean_id, None)
        if pending is None:
            raise ValueError(f"unknown approval_id: {approval_id}")

        answer = _answer_for_decision(normalized)
        if not pending.future.done():
            pending.loop.call_soon_threadsafe(pending.future.set_result, answer)
        payload = {
            **pending.payload,
            "status": "approved" if normalized == "approve" else "rejected",
            "decision": normalized,
            "answer": answer,
        }
        self._emit("approval.resolved", payload)
        return payload

    def cancel_pending(self, reason: str = "run_finished") -> None:
        with self._lock:
            pending_items = list(self._pending.values())
        for pending in pending_items:
            if pending.future.done():
                continue
            pending.loop.call_soon_threadsafe(pending.future.set_result, "no")
            self._emit(
                "approval.resolved",
                {
                    **pending.payload,
                    "status": "cancelled",
                    "decision": "reject",
                    "answer": "no",
                    "reason": str(reason or "run_finished"),
                },
            )

    def _approval_payload(
        self,
        *,
        approval_id: str,
        prompt: str,
        tool_name: str,
        arguments: str | None,
        tool_rule: str,
        preview: str,
        invocation_id: str,
    ) -> dict[str, Any]:
        event = build_tool_event(
            "pre_tool_use",
            tool_name,
            arguments,
            tool_rule=tool_rule,
            status="pending",
        )
        return {
            "approval_id": approval_id,
            "invocation_id": str(invocation_id or ""),
            "attempt_id": self.attempt_id,
            "prompt": str(prompt or ""),
            "status": "pending",
            "decision": "",
            "tool_name": str(tool_name or ""),
            "tool": str(tool_name or ""),
            "tool_rule": str(tool_rule or ""),
            "action": _tool_action(tool_name),
            "arguments_summary": dict(event.arguments_summary or {}),
            "risk": dict(event.risk or {}),
            "preview": str(preview or ""),
        }

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._run_events is None:
            return
        event = self._run_events.emit(
            run_id=self.run_id,
            session_id=self.session_id,
            event_type=event_type,
            payload=payload,
        )
        if self._event_observer is not None:
            self._event_observer(event)


def _normalize_decision(value: str) -> str:
    clean = str(value or "").strip().lower()
    if clean in {"approve", "approved", "allow", "yes", "y", "once", "1"}:
        return "approve"
    if clean in {"reject", "rejected", "deny", "denied", "no", "n", "0"}:
        return "reject"
    raise ValueError("decision must be approve or reject")


def _answer_for_decision(decision: str) -> str:
    return "yes" if decision == "approve" else "no"


def _tool_action(tool_name: str) -> str:
    clean = str(tool_name or "").strip()
    if "." in clean:
        return clean.rsplit(".", 1)[-1]
    return clean
