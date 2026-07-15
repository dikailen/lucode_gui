from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask
from runtime.config.execution_mode import (
    DEFAULT_EXECUTION_MODE,
    execution_mode_label_zh,
    execution_mode_policy,
    normalize_execution_mode,
    runtime_route_for_input,
)
from runtime.config.settings import RuntimeSettings
from runtime.config.model_config import load_lucode_config, set_execution_mode
from runtime.config.cli import parse_writable_config_command
from runtime.commands.completion import command_completion_items
from runtime.execution.parallel_scheduler import _execution_batches_for_mode
from runtime.kernel.strategies import create_execution_strategy
from runtime.kernel.strategies.base import ExecutionContext


def _task(task_id: str, *, write_intent: list[str] | None = None) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=task_id,
        instruction="read only",
        skill_id="code_engineer",
        model="m",
        mcp=["project_filesystem_readonly"],
        write_intent=write_intent or [],
    )


def test_execution_mode_defaults_to_unified_auto():
    assert DEFAULT_EXECUTION_MODE == "auto"
    assert normalize_execution_mode("") == "auto"
    assert normalize_execution_mode("bogus") == "auto"
    assert RuntimeSettings().execution_mode == "auto"
    assert execution_mode_label_zh("auto") == "\u81ea\u52a8\u6267\u884c"


def test_all_mode_inputs_map_to_the_single_auto_policy():
    solo = execution_mode_policy("solo")
    serial = execution_mode_policy("serial")
    full = execution_mode_policy("full")

    for policy in (solo, serial, full):
        assert policy.canonical_mode == "auto"
        assert policy.legacy_mode == ""
        assert policy.fast_single_agent is False
        assert policy.parallel_enabled is True
        assert policy.supervisor_enabled is True


def test_all_modes_route_through_unified_dynamic_loop():
    for mode in ("auto", "solo", "serial", "full"):
        assert runtime_route_for_input("fix code", mode) == "dynamic"
        strategy = create_execution_strategy(routing_input="fix code", execution_mode=mode)
        assert strategy.mode_name == "auto"


def test_scheduler_uses_task_conflicts_not_legacy_mode_values():
    tasks = [_task("a"), _task("b")]

    for mode in ("auto", "full", "serial", "solo"):
        assert _execution_batches_for_mode(tasks, mode) == [tasks]

def test_mode_labels_always_show_unified_auto_execution():
    assert execution_mode_label_zh("solo") == "\u81ea\u52a8\u6267\u884c"
    assert execution_mode_label_zh("serial") == "\u81ea\u52a8\u6267\u884c"
    assert execution_mode_label_zh("full") == "\u81ea\u52a8\u6267\u884c"


def test_mode_configuration_rejects_legacy_switches(tmp_path):
    assert parse_writable_config_command("/mode auto") is None
    assert parse_writable_config_command("/mode full") is None

    set_execution_mode("auto", workspace_root=tmp_path)
    assert load_lucode_config(workspace_root=tmp_path)["mode"] == "auto"
    try:
        set_execution_mode("FULL", workspace_root=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("legacy mode must not be writable")


def test_mode_completion_shows_auto_as_primary_user_choice():
    items = command_completion_items("/mode a")
    texts = [item.text for item in items]
    assert "/mode auto" in texts
    assert "/mode solo" not in texts
    assert "/mode serial" not in texts
    assert "/mode full" not in texts

def test_auto_strategy_executes_dynamic_loop_with_normalized_runtime_mode(monkeypatch, tmp_path):
    import runtime.execution as execution_package

    captured = []

    async def fake_execute_dynamic_request(raw_user_input, project_root, model_registry, mcp_manager, hooks, **kwargs):
        captured.append(kwargs["settings"].execution_mode)
        return "ok"

    monkeypatch.setattr(execution_package, "execute_dynamic_request", fake_execute_dynamic_request)
    strategy = create_execution_strategy(routing_input="fix", execution_mode="auto")

    for configured_mode in ("auto", "serial"):
        request = SimpleNamespace(
            user_input="fix",
            routing_input="fix",
            workspace_root=tmp_path,
            show_plan=False,
        )
        context = ExecutionContext(
            request=request,
            model_registry=None,
            mcp_manager=None,
            hooks=None,
            run_agent=None,
            settings=RuntimeSettings(execution_mode=configured_mode),
        )
        assert asyncio.run(strategy.execute(context)) == "ok"

    assert captured == ["auto", "auto"]
