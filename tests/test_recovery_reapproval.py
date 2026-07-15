from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime.server.approval_session import RuntimeApprovalSession


class _RunEvents:
    def emit(self, **kwargs):
        return SimpleNamespace(payload=dict(kwargs.get("payload") or {}))


async def _request_and_approve(session: RuntimeApprovalSession, events: list[dict], *, invocation_id: str) -> dict:
    pending = asyncio.create_task(
        session.request_tool_approval(
            "approve this tool",
            tool_name="workspace_edit.write_file",
            arguments='{"target_path":"notes.txt","content":"value"}',
            invocation_id=invocation_id,
        )
    )
    while not events:
        await asyncio.sleep(0)
    requested = events[-1]
    session.resolve(requested["approval_id"], "approve")
    assert await pending == "yes"
    return requested


def test_recovery_attempt_reissues_approval_with_a_new_attempt_identity():
    async def scenario():
        first_events: list[dict] = []
        second_events: list[dict] = []
        first = RuntimeApprovalSession(
            run_id="run_1",
            session_id="session_1",
            attempt_id="attempt_1",
            run_events=_RunEvents(),
            event_observer=lambda event: first_events.append(dict(event.payload)),
        )
        second = RuntimeApprovalSession(
            run_id="run_2",
            session_id="session_1",
            attempt_id="attempt_2",
            run_events=_RunEvents(),
            event_observer=lambda event: second_events.append(dict(event.payload)),
        )
        first_request = await _request_and_approve(first, first_events, invocation_id="invocation_old")
        second_request = await _request_and_approve(second, second_events, invocation_id="invocation_new")
        return first, second, first_request, second_request

    first, second, first_request, second_request = asyncio.run(scenario())

    assert first_request["attempt_id"] == "attempt_1"
    assert second_request["attempt_id"] == "attempt_2"
    assert first_request["approval_id"] != second_request["approval_id"]
    assert first_request["invocation_id"] != second_request["invocation_id"]
    with pytest.raises(ValueError, match="unknown approval_id"):
        second.resolve(first_request["approval_id"], "approve")
