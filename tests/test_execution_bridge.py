from __future__ import annotations

from runtime.events import ExecutionEvent
from runtime.server.execution_bridge import event_type_for_execution_event


def test_sdk_tool_end_is_a_completed_tool_run_event():
    event = ExecutionEvent(
        "ToolInvoked",
        payload={
            "event_type": "sdk_tool_end",
            "tool_name": "browser_navigate",
            "arguments_summary": {"url": "https://example.com"},
        },
        status="completed",
    )

    assert event_type_for_execution_event(event) == "tool.completed"
