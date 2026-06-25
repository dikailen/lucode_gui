from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask
from runtime.config.execution_mode import (
    DEFAULT_EXECUTION_MODE,
    execution_mode_label_zh,
    execution_mode_policy,
    effective_runtime_mode,
    normalize_execution_mode,
    runtime_route_for_input,
    should_use_solo_mode,
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


def test_legacy_modes_map_to_unified_policy_not_user_modes():
    solo = execution_mode_policy("solo")
    serial = execution_mode_policy("serial")
    full = execution_mode_policy("full")

    assert solo.canonical_mode == "auto"
    assert solo.legacy_mode == "solo"
    assert solo.fast_single_agent is True
    assert solo.parallel_enabled is False

    assert serial.canonical_mode == "auto"
    assert serial.legacy_mode == "serial"
    assert serial.fast_single_agent is False
    assert serial.parallel_enabled is False

    assert full.canonical_mode == "auto"
    assert full.legacy_mode == "full"
    assert full.fast_single_agent is False
    assert full.parallel_enabled is True




def test_auto_uses_full_runtime_strategy_while_legacy_modes_keep_compatibility():
    assert effective_runtime_mode("auto") == "full"
    assert effective_runtime_mode("bogus") == "full"
    assert effective_runtime_mode("full") == "full"
    assert effective_runtime_mode("serial") == "serial"
    assert effective_runtime_mode("solo") == "solo"
def test_all_modes_route_through_unified_dynamic_loop():
    for mode in ("auto", "solo", "serial", "full"):
        assert runtime_route_for_input("fix code", mode) == "dynamic"
        assert should_use_solo_mode("fix code", mode) is False
        strategy = create_execution_strategy(routing_input="fix code", execution_mode=mode)
        assert strategy.mode_name == "auto"


def test_scheduler_policy_replaces_user_visible_serial_full_modes():
    tasks = [_task("a"), _task("b")]

    assert len(_execution_batches_for_mode(tasks, "auto")) == 1
    assert len(_execution_batches_for_mode(tasks, "full")) == 1
    assert _execution_batches_for_mode(tasks, "serial") == [[tasks[0]], [tasks[1]]]
    assert _execution_batches_for_mode(tasks, "solo") == [[tasks[0]], [tasks[1]]]

def test_legacy_mode_labels_are_compatibility_aliases():
    assert execution_mode_label_zh("solo") == "\u81ea\u52a8\u6267\u884c\uff08solo \u517c\u5bb9\uff09"
    assert execution_mode_label_zh("serial") == "\u81ea\u52a8\u6267\u884c\uff08serial \u517c\u5bb9\uff09"
    assert execution_mode_label_zh("full") == "\u81ea\u52a8\u6267\u884c\uff08full \u517c\u5bb9\uff09"


def test_mode_command_accepts_auto_and_legacy_compatibility(tmp_path):
    assert parse_writable_config_command("/mode auto") == ("mode", "auto")
    assert parse_writable_config_command("/mode full") == ("mode", "full")

    set_execution_mode("auto", workspace_root=tmp_path)
    assert load_lucode_config(workspace_root=tmp_path)["mode"] == "auto"
    set_execution_mode("FULL", workspace_root=tmp_path)
    assert load_lucode_config(workspace_root=tmp_path)["mode"] == "full"


def test_mode_completion_shows_auto_as_primary_user_choice():
    items = command_completion_items("/mode a")
    texts = [item.text for item in items]
    assert "/mode auto" in texts
    assert "/mode solo" not in texts
    assert "/mode serial" not in texts
    assert "/mode full" not in texts

def test_auto_strategy_executes_dynamic_loop_with_effective_runtime_mode(monkeypatch, tmp_path):
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

    assert captured == ["full", "serial"]
