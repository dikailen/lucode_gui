from __future__ import annotations

import asyncio

from runtime.execution.run_context import RunContextStore
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
