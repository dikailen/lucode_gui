from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from pathlib import Path

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.config.cli import parse_writable_config_command
from runtime.config.execution_mode import execution_mode_policy, normalize_execution_mode
from runtime.config.settings import RuntimeSettings
from runtime.execution.execution_contract import normalize_execution_contract, supervisor_route
from runtime.execution.parallel_scheduler import execution_batch_decisions_for_mode
from runtime.execution.supervisor_observer import emit_supervisor_observation
from runtime.kernel.strategies import create_execution_strategy
from runtime.kernel.strategies.base import ExecutionContext
from lucode.gui.chat_session import _settings_for_turn


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_AREAS = (
    "runtime/config",
    "runtime/kernel",
    "runtime/execution",
    "runtime/agents",
    "runtime/agent",
    "mcp_servers",
    "lucode/gui",
    "lucode/shell",
    "planning",
)


def test_production_paths_do_not_reintroduce_legacy_execution_mode_contracts():
    patterns = (
        r"\bshould_use_solo_mode\b",
        r"\bfull_supervisor\b",
        r"\bfull_worker_contract\b",
        r"\bserial_executor_contract\b",
        r"\bsolo_executor_contract\b",
        r"\bsupervisor_execution_batches_for_full\b",
        r"\b_review_full_worker_reports\b",
        r"\bmode\s*(?:==|!=|in|not\s+in)\s*[\"'](?:solo|serial|full)[\"']",
    )
    violations: list[str] = []
    for area in PRODUCTION_AREAS:
        for path in (ROOT / area).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if re.search(pattern, text):
                    violations.append(f"{path.relative_to(ROOT)}: {pattern}")

    assert violations == []


def _readonly_task(task_id: str) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=task_id,
        instruction="Inspect only.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["project_filesystem_readonly"],
        read_set=[f"{task_id}.md"],
    )


def _team_plan() -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent",
        reason="two independent checks",
        refined_request="inspect the project",
        tasks=[_readonly_task("docs"), _readonly_task("tests")],
        needs_synthesis=True,
        synthesis_instruction="Summarize verified results.",
    )


def test_all_execution_mode_inputs_converge_to_auto_without_legacy_behavior():
    for value in ("", "auto", "solo", "serial", "full", "unexpected"):
        policy = execution_mode_policy(value)

        assert normalize_execution_mode(value) == "auto"
        assert policy.canonical_mode == "auto"
        assert policy.legacy_mode == ""
        assert policy.parallel_enabled is True
        assert policy.supervisor_enabled is True


def test_auto_strategy_preserves_auto_for_the_dynamic_loop(monkeypatch, tmp_path):
    import runtime.execution as execution_package

    captured_modes: list[str] = []

    async def fake_execute_dynamic_request(raw_user_input, project_root, model_registry, mcp_manager, hooks, **kwargs):
        del raw_user_input, project_root, model_registry, mcp_manager, hooks
        captured_modes.append(kwargs["settings"].execution_mode)
        return "ok"

    monkeypatch.setattr(execution_package, "execute_dynamic_request", fake_execute_dynamic_request)
    strategy = create_execution_strategy(routing_input="inspect", execution_mode="auto")
    context = ExecutionContext(
        request=SimpleNamespace(
            user_input="inspect",
            routing_input="inspect",
            workspace_root=tmp_path,
            show_plan=False,
        ),
        model_registry=None,
        mcp_manager=None,
        hooks=None,
        run_agent=None,
        settings=RuntimeSettings(execution_mode="auto"),
    )

    assert asyncio.run(strategy.execute(context)) == "ok"
    assert captured_modes == ["auto"]


def test_prompt_mode_words_do_not_override_the_current_turn_settings():
    settings = RuntimeSettings(execution_mode="auto")

    turn_settings = _settings_for_turn(settings, "请使用 full 模式检查项目")

    assert turn_settings is settings
    assert turn_settings.execution_mode == "auto"


def test_mode_command_is_not_a_writable_compatibility_switch():
    assert parse_writable_config_command("/mode full") is None
    assert parse_writable_config_command("/mode serial") is None
    assert parse_writable_config_command("/mode solo") is None


def test_legacy_mode_values_cannot_disable_parallelism_for_independent_readonly_tasks():
    tasks = [_readonly_task("docs"), _readonly_task("tests")]

    for mode in ("auto", "solo", "serial", "full"):
        decisions = execution_batch_decisions_for_mode(tasks, mode)

        assert len(decisions) == 1
        assert decisions[0].status == "parallel"
        assert [task.id for task in decisions[0].tasks] == ["docs", "tests"]


def test_auto_multi_agent_route_keeps_supervisor_observation_and_team_contract():
    plan = _team_plan()

    decision = normalize_execution_contract(plan, "inspect the project", mode="auto")
    observation = emit_supervisor_observation(plan, mode="auto")

    assert decision.supervisor_route == "team"
    assert supervisor_route(plan) == "team"
    assert observation is not None
    assert observation.route_type == "multi_agent"


def test_only_team_routes_receive_the_supervisor_approval_policy():
    from runtime.execution import dynamic

    team_plan = _team_plan()
    normalize_execution_contract(team_plan, "inspect the project", mode="auto")
    team_policy_factory = getattr(dynamic, "_supervisor_approval_policy_factory", None)

    assert callable(team_policy_factory)
    assert team_policy_factory(team_plan) is not None
    single_plan = PlannerResult(
        route_type="single_agent",
        reason="one check",
        refined_request="inspect one file",
        tasks=[_readonly_task("one")],
    )
    normalize_execution_contract(single_plan, "inspect one file", mode="auto")
    assert team_policy_factory(single_plan) is None


def test_runtime_uses_neutral_supervisor_and_worker_contract_identifiers():
    from runtime.agent import approval_policy
    from runtime.config import skill_policy
    from skills.loader import load_skill

    assert callable(getattr(approval_policy, "SupervisorApprovalPolicy", None))
    assert not hasattr(approval_policy, "FullModeApprovalPolicy")
    assert "execution_supervisor" in skill_policy.INTERNAL_SKILLS
    assert "worker_contract" in skill_policy.INTERNAL_SKILLS
    assert "full_supervisor" not in skill_policy.INTERNAL_SKILLS
    assert "solo_executor_contract" not in skill_policy.INTERNAL_SKILLS
    assert "serial_executor_contract" not in skill_policy.INTERNAL_SKILLS
    assert "Unified Agent Loop Supervisor Contract" in load_skill("execution_supervisor")
    assert "Worker 角色契约" in load_skill("worker_contract")
