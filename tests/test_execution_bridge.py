from __future__ import annotations

from runtime.events import ExecutionEvent
from runtime.server.execution_bridge import event_type_for_execution_event, map_execution_event_type


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


def test_worker_context_observation_has_a_stable_runtime_event_name():
    assert map_execution_event_type("WorkerContextObserved") == "worker.context_observed"


def test_final_answer_delta_has_a_distinct_runtime_event_name():
    assert map_execution_event_type("FinalAnswerDelta") == "answer.delta"
