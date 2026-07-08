from __future__ import annotations

from runtime.context.middleware import ContextCompressionMiddleware


class FakeHistory:
    def __init__(self, *, messages=None, summary="", fail=False):
        self.messages = list(messages or [])
        self.summary = summary
        self.fail = fail

    def load_messages(self, session_id, limit=None):
        if self.fail:
            raise RuntimeError("history unavailable")
        return self.messages[-limit:] if limit else list(self.messages)

    def load_context_summary(self, session_id, max_chars=2400):
        if self.fail:
            raise RuntimeError("history unavailable")
        return self.summary[:max_chars]


def test_observe_mode_builds_ledger_without_replacing_run_input_or_routing_input():
    history = FakeHistory(
        messages=[
            {"role": "user", "content": "Use the embedded browser to open https://example.com"},
            {"role": "assistant", "content": "Browser task completed."},
        ],
        summary="Old summary says: open browser and click a form.",
    )
    user_input = "你好"

    result = ContextCompressionMiddleware(history=history, mode="observe").prepare_run_input(
        session_id="s1",
        user_input=user_input,
        model_info={"context_window_tokens": 10_000},
    )

    assert result.mode == "observe"
    assert result.run_input == user_input
    assert result.routing_input == user_input
    assert result.applied is False
    assert result.observed is True
    assert result.ledger_result is not None
    assert "Old summary says" in result.ledger_result.run_input
    assert result.ledger_result.run_input.rstrip().endswith(user_input)
    assert result.metadata["context_ledger"]["mode"] == result.ledger_result.mode


def test_enforce_mode_uses_ledger_run_input_but_keeps_routing_input_original():
    history = FakeHistory(messages=[{"role": "user", "content": "old context"}])
    user_input = "Only answer this new question."

    result = ContextCompressionMiddleware(history=history, mode="enforce").prepare_run_input(
        session_id="s1",
        user_input=user_input,
        model_info={"context_window_tokens": 10_000},
    )

    assert result.mode == "enforce"
    assert result.run_input != user_input
    assert "old context" in result.run_input
    assert result.run_input.rstrip().endswith(user_input)
    assert result.routing_input == user_input
    assert result.applied is True


def test_off_mode_returns_user_input_without_history_or_tool_processing():
    history = FakeHistory(messages=[{"role": "user", "content": "old context"}])
    user_input = "hello"

    result = ContextCompressionMiddleware(history=history, mode="off").prepare_run_input(
        session_id="s1",
        user_input=user_input,
        tool_results=[
            {
                "tool": "terminal",
                "action": "run_command",
                "raw_result": {"command": "pytest", "stdout": "large"},
                "evidence_ref": "tool:t",
                "raw_artifact_ref": "artifact:t",
            }
        ],
    )

    assert result.run_input == user_input
    assert result.routing_input == user_input
    assert result.ledger_result is None
    assert result.tool_summaries == []
    assert result.metadata["context_ledger"]["mode"] == "off"


def test_observe_mode_dehydrates_tool_results_but_does_not_replace_evidence_refs():
    user_input = "summarize the command result"

    result = ContextCompressionMiddleware(mode="observe").prepare_run_input(
        session_id="s1",
        user_input=user_input,
        tool_results=[
            {
                "tool": "terminal",
                "action": "run_command",
                "raw_result": {
                    "command": "python -m pytest",
                    "returncode": 1,
                    "stdout": "\n".join(f"line {index}" for index in range(100)),
                    "stderr": "FAILED test_x",
                },
                "evidence_ref": "tool:terminal:1",
                "raw_artifact_ref": "artifact:terminal:1",
            }
        ],
    )

    assert result.run_input == user_input
    assert result.routing_input == user_input
    assert len(result.tool_summaries) == 1
    summary = result.tool_summaries[0]
    assert summary.evidence_ref == "tool:terminal:1"
    assert summary.raw_artifact_ref == "artifact:terminal:1"
    assert "line 0" not in summary.summary
    assert result.metadata["tool_dehydration"]["count"] == 1
    assert result.metadata["tool_dehydration"]["items"][0]["evidence_ref"] == "tool:terminal:1"


def test_history_failure_degrades_to_original_input_with_error_metadata():
    user_input = "hello"

    result = ContextCompressionMiddleware(history=FakeHistory(fail=True), mode="observe").prepare_run_input(
        session_id="s1",
        user_input=user_input,
    )

    assert result.run_input == user_input
    assert result.routing_input == user_input
    assert result.ledger_result is None
    assert result.observed is False
    assert "history unavailable" in result.metadata["context_ledger"]["error"]


def test_history_failure_still_dehydrates_tool_results_for_observation():
    result = ContextCompressionMiddleware(history=FakeHistory(fail=True), mode="observe").prepare_run_input(
        session_id="s1",
        user_input="hello",
        tool_results=[
            {
                "tool": "terminal",
                "action": "run_command",
                "raw_result": {"command": "pytest", "returncode": 0, "stdout": "ok"},
                "evidence_ref": "tool:terminal:history-failed",
                "raw_artifact_ref": "artifact:terminal:history-failed",
            }
        ],
    )

    assert result.run_input == "hello"
    assert result.routing_input == "hello"
    assert result.ledger_result is None
    assert len(result.tool_summaries) == 1
    assert result.tool_summaries[0].evidence_ref == "tool:terminal:history-failed"
    assert result.metadata["tool_dehydration"]["count"] == 1
