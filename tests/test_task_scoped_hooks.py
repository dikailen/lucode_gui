from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannerResult
from runtime.execution.run_context import RunContextStore
from runtime.execution.task_runner import _run_direct_answer
from runtime.events import ExecutionEventBus
from runtime.hooks.task_scope import TaskScopedHooks


class _BaseHooks:
    def __init__(self):
        self.results = []

    async def on_tool_end(self, context, agent, tool, result):
        self.results.append(result)


class _ToolContext:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name


class _Tool:
    def __init__(self, name: str):
        self.name = name


def test_sdk_browser_tool_result_is_dehydrated_before_shared_context(tmp_path):
    base = _BaseHooks()
    run_context = RunContextStore(tmp_path)
    hooks = TaskScopedHooks(base, task_id="browser-task", run_context=run_context)
    secret = "sk-browser-secret-12345678"
    dom = "<html>" + ("private dashboard markup " * 400) + "</html>"

    asyncio.run(
        hooks.on_tool_end(
            _ToolContext("browser_get_page_summary"),
            None,
            _Tool("browser_get_page_summary"),
            {
                "url": "https://example.test/dashboard",
                "title": "Dashboard",
                "text": "Build status is green.",
                "dom": dom,
                "api_key": secret,
                "evidence_ref": "evidence:browser:hook",
                "raw_artifact_ref": "artifact:browser:hook",
                "elements": [{"selector": "#refresh", "tag": "button", "text": "Refresh"}],
            },
        )
    )

    assert base.results[0]["dom"] == dom
    assert len(run_context.tool_outputs) == 1
    artifact = run_context.tool_outputs[0]
    assert artifact.tool == "browser_get_page_summary"
    assert "https://example.test/dashboard" in artifact.summary
    assert "Dashboard" in artifact.summary
    assert dom not in artifact.summary
    assert secret not in artifact.summary
    labels = run_context.source_labels()
    assert labels[0].source_type == "browser_summary"
    assert labels[0].sensitivity == "local_only"
    envelope = run_context.context_envelopes()[0]
    assert envelope.evidence_refs == ("evidence:browser:hook",)
    assert envelope.raw_artifact_ref == "artifact:browser:hook"


def test_sdk_terminal_tool_result_keeps_only_redacted_bounded_tail(tmp_path):
    run_context = RunContextStore(tmp_path)
    hooks = TaskScopedHooks(_BaseHooks(), task_id="terminal-task", run_context=run_context)
    stdout = "\n".join([f"line {index}" for index in range(200)] + ["token=terminal-secret-value"])

    asyncio.run(
        hooks.on_tool_end(
            _ToolContext("run_command"),
            None,
            _Tool("run_command"),
            {
                "command": "pytest -q",
                "returncode": 0,
                "stdout": stdout,
                "stderr": "",
            },
        )
    )

    artifact = run_context.tool_outputs[0]
    assert artifact.tool == "run_command"
    assert "pytest -q" in artifact.summary
    assert "terminal-secret-value" not in artifact.summary
    assert "line 0" not in artifact.summary
    assert len(artifact.summary) <= 280
    assert run_context.source_labels()[0].sensitivity == "local_only"


def test_sdk_tool_results_export_safe_persistent_metadata_for_browser_terminal_and_mcp(tmp_path):
    run_context = RunContextStore(tmp_path)
    hooks = TaskScopedHooks(_BaseHooks(), task_id="tool-task", run_context=run_context)
    browser_dom = "<html>private browser DOM</html>"
    terminal_stdout = "first line\napi_key=terminal-secret\nlast line"

    for tool_name, payload in (
        (
            "browser_get_page_summary",
            {
                "url": "https://example.test/private",
                "title": "Private page",
                "dom": browser_dom,
                "evidence_ref": "evidence:browser:1",
                "raw_artifact_ref": "artifact:browser:1",
            },
        ),
        (
            "run_command",
            {
                "command": "pytest -q",
                "returncode": 0,
                "stdout": terminal_stdout,
                "evidence_ref": "evidence:terminal:1",
                "raw_artifact_ref": "artifact:terminal:1",
            },
        ),
        (
            "mcp::list_models",
            {
                "result": {"status": "ok", "models": ["model-a"]},
                "jsonrpc": "2.0",
                "evidence_ref": "evidence:mcp:1",
                "raw_artifact_ref": "artifact:mcp:1",
            },
        ),
    ):
        asyncio.run(
            hooks.on_tool_end(
                _ToolContext(tool_name),
                None,
                _Tool(tool_name),
                payload,
            )
        )

    items = run_context.dehydrated_tool_results()

    assert [item["tool"] for item in items] == [
        "browser_get_page_summary",
        "run_command",
        "mcp__list_models",
    ]
    assert [item["evidence_ref"] for item in items] == [
        "evidence:browser:1",
        "evidence:terminal:1",
        "evidence:mcp:1",
    ]
    assert items[0]["key_fields"] == {"url": "https://example.test/private", "title": "Private page"}
    assert "browser_dom_omitted" in items[0]["omitted_fields"]
    assert items[1]["redacted"] is True
    assert "protocol_fields_omitted" in items[2]["omitted_fields"]

    exported = str(items)
    assert browser_dom not in exported
    assert "terminal-secret" not in exported


def test_direct_answer_tool_callback_is_scoped_into_run_context(tmp_path):
    class Factory:
        def create_direct_answer_agent(self, *args, **kwargs):
            return object()

    class BaseHooks:
        async def on_tool_end(self, context, agent, tool, result):
            pass

    class ToolContext:
        tool_name = "mcp::list_models"

    class Tool:
        name = "mcp::list_models"

    async def fake_run_agent(agent, turn_input, hooks, **kwargs):
        await hooks.on_tool_end(
            ToolContext(),
            agent,
            Tool(),
            {
                "result": {"status": "ok", "models": ["model-a"]},
                "jsonrpc": "2.0",
                "evidence_ref": "evidence:mcp:direct",
                "raw_artifact_ref": "artifact:mcp:direct",
            },
        )
        return SimpleNamespace(final_output="ok")

    run_context = RunContextStore(tmp_path)
    run_state = SimpleNamespace(run_context=run_context, event_bus=None)
    plan = PlannerResult(
        route_type="direct_answer",
        reason="direct test",
        refined_request="list models",
        direct_answer_instruction="Answer directly.",
    )

    result = asyncio.run(
        _run_direct_answer(
            "list models",
            plan,
            "model-a",
            Factory(),
            BaseHooks(),
            fake_run_agent,
            run_state=run_state,
        )
    )

    assert result == "ok"
    assert run_context.dehydrated_tool_results()[0]["evidence_ref"] == "evidence:mcp:direct"


def test_direct_answer_wraps_hooks_when_only_a_tool_lifecycle_sink_is_present():
    class Factory:
        def create_direct_answer_agent(self, model_id, instruction, execution_mode=""):
            del model_id, instruction, execution_mode
            return object()

    lifecycle_sink = object()
    captured = {}

    async def fake_run_agent(agent, turn_input, hooks, **kwargs):
        del agent, turn_input, kwargs
        captured["hooks"] = hooks
        return SimpleNamespace(final_output="ok")

    plan = PlannerResult(
        route_type="direct_answer",
        reason="lifecycle contract",
        refined_request="answer",
        direct_answer_instruction="Answer directly.",
    )
    run_state = SimpleNamespace(
        event_bus=None,
        run_context=None,
        tool_lifecycle_sink=lifecycle_sink,
    )

    result = asyncio.run(
        _run_direct_answer(
            "answer",
            plan,
            "model-a",
            Factory(),
            _BaseHooks(),
            fake_run_agent,
            run_state=run_state,
        )
    )

    assert result == "ok"
    assert isinstance(captured["hooks"], TaskScopedHooks)
    assert captured["hooks"].tool_lifecycle_sink is lifecycle_sink


def test_direct_answer_emits_final_answer_delta_when_streaming_is_available(tmp_path):
    class Factory:
        def create_direct_answer_agent(self, *args, **kwargs):
            return object()

    async def fake_run_agent(agent, turn_input, hooks, **kwargs):
        assert kwargs["stream_output"] is True
        kwargs["on_delta"]("partial answer")
        return SimpleNamespace(final_output="final answer")

    event_bus = ExecutionEventBus()
    run_state = SimpleNamespace(run_context=RunContextStore(tmp_path), event_bus=event_bus)
    plan = PlannerResult(
        route_type="direct_answer",
        reason="direct test",
        refined_request="explain the result",
        direct_answer_instruction="Answer directly.",
    )

    result = asyncio.run(
        _run_direct_answer(
            "explain the result",
            plan,
            "model-a",
            Factory(),
            object(),
            fake_run_agent,
            run_state=run_state,
        )
    )

    assert result == "final answer"
    event = event_bus.snapshot()[0]
    assert event.event_type == "FinalAnswerDelta"
    assert event.payload == {"text": "partial answer"}
    assert not event.task_id
