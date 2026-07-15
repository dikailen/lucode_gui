from __future__ import annotations

import asyncio
from types import SimpleNamespace

from runtime.config.settings import RuntimeSettings
from runtime.execution import execute_dynamic_request
from runtime.kernel.strategies.auto import AutoStrategy
from runtime.kernel.strategies.base import ExecutionContext


def test_execution_wrapper_forwards_recovery_contract_to_dynamic_execution(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    recovery_envelope = {"schema_version": "recovery_envelope.v1", "source_run_id": "run-before-restart"}
    checkpoint_sink = object()
    tool_lifecycle_sink = object()

    async def fake_dynamic_request(*_args, **kwargs):
        captured.update(kwargs)
        return "dynamic result"

    monkeypatch.setattr("runtime.execution.dynamic.execute_dynamic_request", fake_dynamic_request)

    result = asyncio.run(
        execute_dynamic_request(
            "explain the current project structure",
            tmp_path,
            model_registry=object(),
            mcp_manager=object(),
            hooks=object(),
            run_agent=object(),
            recovery_envelope=recovery_envelope,
            checkpoint_sink=checkpoint_sink,
            tool_lifecycle_sink=tool_lifecycle_sink,
        )
    )

    assert result == "dynamic result"
    assert captured["recovery_envelope"] is recovery_envelope
    assert captured["checkpoint_sink"] is checkpoint_sink
    assert captured["tool_lifecycle_sink"] is tool_lifecycle_sink


def test_auto_strategy_reaches_dynamic_execution_with_the_recovery_contract(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    recovery_envelope = {"schema_version": "recovery_envelope.v1", "source_run_id": "run-before-restart"}
    checkpoint_sink = object()
    tool_lifecycle_sink = object()

    async def fake_dynamic_request(*_args, **kwargs):
        captured.update(kwargs)
        return "dynamic result"

    monkeypatch.setattr("runtime.execution.dynamic.execute_dynamic_request", fake_dynamic_request)
    context = ExecutionContext(
        request=SimpleNamespace(
            user_input="explain the current project structure",
            routing_input="explain the current project structure",
            workspace_root=tmp_path,
            show_plan=False,
            inline_files=(),
            recovery_envelope=recovery_envelope,
            checkpoint_sink=checkpoint_sink,
            tool_lifecycle_sink=tool_lifecycle_sink,
        ),
        model_registry=object(),
        mcp_manager=object(),
        hooks=object(),
        run_agent=object(),
        settings=RuntimeSettings(execution_mode="auto"),
    )

    assert asyncio.run(AutoStrategy().execute(context)) == "dynamic result"
    assert captured["recovery_envelope"] is recovery_envelope
    assert captured["checkpoint_sink"] is checkpoint_sink
    assert captured["tool_lifecycle_sink"] is tool_lifecycle_sink
