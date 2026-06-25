from __future__ import annotations

import asyncio
from types import SimpleNamespace

from runtime.config.settings import RuntimeSettings
from runtime.execution import solo_runner
from runtime.kernel.strategies.base import ExecutionContext
from runtime.kernel.strategies.solo import SoloStrategy


class _ModelRegistry:
    def first_configured(self, priorities):
        return "model-a"

    def get_model_info(self, model_id):
        return {"id": model_id, "supports_tools": True}

    def get_model(self, model_id):
        return model_id


class _McpManager:
    def set_readonly_budget_profile(self, mcp_id, profile):
        pass

    async def get_many(self, mcp_ids):
        return []


def test_start_solo_rich_live_keeps_memory_pack(monkeypatch, tmp_path):
    memory_pack = object()
    captured = {}

    monkeypatch.setattr(solo_runner, "_should_use_solo_rich_live", lambda: True)

    def fake_refresh(self, run_state, **kwargs):
        captured["run_state"] = run_state
        return True

    monkeypatch.setattr(solo_runner.RichLiveRuntime, "refresh", fake_refresh)

    runtime, run_state, task = solo_runner._start_solo_rich_live(
        "inspect project",
        model_registry=_ModelRegistry(),
        model_id="model-a",
        mcp_ids=["project_filesystem_readonly"],
        settings=RuntimeSettings(execution_mode="solo"),
        project_root=tmp_path,
        memory_pack=memory_pack,
    )

    assert runtime is not None
    assert task is not None
    assert run_state is captured["run_state"]
    assert run_state.memory_pack is memory_pack


def test_run_solo_request_passes_memory_pack_to_rich_live(monkeypatch, tmp_path):
    memory_pack = object()
    captured = {}

    def fake_start_solo_rich_live(*args, **kwargs):
        captured["memory_pack"] = kwargs.get("memory_pack")
        return None, None, None

    async def fake_run_agent(agent, turn_input, hooks, **kwargs):
        return SimpleNamespace(final_output="ok")

    monkeypatch.setattr(solo_runner, "_start_solo_rich_live", fake_start_solo_rich_live)
    monkeypatch.setattr(solo_runner, "_solo_input_with_inline_context", lambda text, *args, **kwargs: text)
    monkeypatch.setattr(solo_runner, "_solo_mcp_ids_for_input", lambda text, settings: [])

    result = asyncio.run(
        solo_runner.run_solo_request(
            "inspect project",
            _ModelRegistry(),
            _McpManager(),
            hooks=None,
            run_agent=fake_run_agent,
            settings=RuntimeSettings(execution_mode="solo"),
            project_root=tmp_path,
            memory_pack=memory_pack,
        )
    )

    assert str(result) == "ok"
    assert captured["memory_pack"] is memory_pack


def test_solo_strategy_forwards_context_memory_pack(monkeypatch, tmp_path):
    memory_pack = object()
    captured = {}

    async def fake_run_solo_request(*args, **kwargs):
        captured["memory_pack"] = kwargs.get("memory_pack")
        return "ok"

    monkeypatch.setattr(solo_runner, "run_solo_request", fake_run_solo_request)

    context = ExecutionContext(
        request=SimpleNamespace(user_input="inspect project", workspace_root=tmp_path),
        model_registry=_ModelRegistry(),
        mcp_manager=_McpManager(),
        hooks=None,
        run_agent=None,
        settings=RuntimeSettings(execution_mode="solo"),
        memory_pack=memory_pack,
    )

    assert asyncio.run(SoloStrategy().execute(context)) == "ok"
    assert captured["memory_pack"] is memory_pack
