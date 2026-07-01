from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask, PlannerResult, RefinedRequest


def _task(task_id: str = "task-1", *, read_set=None, write_intent=None, mcp=None) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=f"Task {task_id}",
        instruction="Read README.md",
        skill_id="project_explorer",
        model="worker-model",
        mcp=list(mcp if mcp is not None else ["project_filesystem_readonly"]),
        parallel_group=2,
        depends_on=["previous-task"],
        read_set=list(read_set or ["README.md"]),
        write_intent=list(write_intent or []),
    )


def _patch_validator_catalogs(monkeypatch):
    from planning import plan_validator

    monkeypatch.setattr(
        plan_validator,
        "load_skill_catalog",
        lambda: {
            "skills": [
                {
                    "id": "project_explorer",
                    "assignable": True,
                    "allowed_mcp": ["project_filesystem_readonly", "code_locator", "workspace_edit"],
                }
            ]
        },
    )
    monkeypatch.setattr(
        plan_validator,
        "load_mcp_catalog",
        lambda: {
            "mcp_servers": [
                {"id": "project_filesystem_readonly", "implemented": True, "allowed_for_skills": ["project_explorer"]},
                {"id": "code_locator", "implemented": True, "allowed_for_skills": ["project_explorer"]},
                {"id": "workspace_edit", "implemented": True, "allowed_for_skills": ["project_explorer"]},
            ]
        },
    )
    monkeypatch.setattr(
        plan_validator,
        "load_model_catalog",
        lambda: {"models": [{"id": "worker-model", "configured": True, "supports_tools": True}]},
    )
    monkeypatch.setattr(plan_validator, "model_runtime_available", lambda model: True)


def test_normalizer_downgrades_single_task_multi_agent_and_clears_synthesis(monkeypatch):
    from planning.plan_normalizer import normalize_plan_for_execution
    from planning.plan_validator import validate_plan

    _patch_validator_catalogs(monkeypatch)
    plan = PlannerResult(
        route_type="multi_agent",
        reason="planner over split",
        refined_request="Read README",
        tasks=[_task()],
        needs_synthesis=True,
        synthesis_instruction="merge worker outputs",
    )

    normalized, notes = normalize_plan_for_execution(plan)
    validation = validate_plan(normalized)

    assert normalized is not plan
    assert normalized.route_type == "single_agent"
    assert normalized.needs_synthesis is False
    assert normalized.synthesis_instruction == ""
    assert len(normalized.tasks) == 1
    assert normalized.tasks[0] is not plan.tasks[0]
    assert normalized.tasks[0].depends_on == []
    assert normalized.tasks[0].parallel_group == 1
    assert notes
    assert validation.valid is True
    assert validation.errors == []


def test_normalizer_leaves_readonly_same_file_multi_agent_unmerged():
    from planning.plan_normalizer import normalize_plan_for_execution

    plan = PlannerResult(
        route_type="multi_agent",
        reason="readonly split",
        refined_request="Read README",
        tasks=[_task("read-a"), _task("read-b")],
        needs_synthesis=True,
        synthesis_instruction="merge readonly outputs",
    )

    normalized, notes = normalize_plan_for_execution(plan)

    assert normalized is plan
    assert normalized.route_type == "multi_agent"
    assert len(normalized.tasks) == 2
    assert any("single_agent" in note for note in notes)


def test_normalizer_leaves_real_multi_agent_plan_unchanged():
    from planning.plan_normalizer import normalize_plan_for_execution

    plan = PlannerResult(
        route_type="multi_agent",
        reason="multi file edit",
        refined_request="Edit two files",
        tasks=[
            _task("edit-a", read_set=["runtime/a.py"], write_intent=["runtime/a.py"], mcp=["project_filesystem_readonly", "workspace_edit"]),
            _task("edit-b", read_set=["runtime/b.py"], write_intent=["runtime/b.py"], mcp=["project_filesystem_readonly", "workspace_edit"]),
        ],
        needs_synthesis=True,
        synthesis_instruction="merge edits",
    )

    normalized, notes = normalize_plan_for_execution(plan)

    assert normalized is plan
    assert notes == []
    assert normalized.route_type == "multi_agent"


def test_validate_plan_has_no_route_side_effect(monkeypatch):
    from planning.plan_validator import validate_plan

    _patch_validator_catalogs(monkeypatch)
    plan = PlannerResult(
        route_type="multi_agent",
        reason="single task multi",
        refined_request="Read README",
        tasks=[_task()],
        needs_synthesis=True,
        synthesis_instruction="merge",
    )

    validate_plan(plan)

    assert plan.route_type == "multi_agent"
    assert plan.needs_synthesis is True
    assert plan.synthesis_instruction == "merge"


def test_pipeline_gate_treats_suggest_verification_commands_as_readonly():
    from runtime.execution.pipeline import apply_pipeline_gate

    plan = PlannerResult(
        route_type="multi_agent",
        reason="项目体检",
        refined_request="项目体检，不要修改任何文件，不要提交 git，不要安装依赖，最后给出下一步建议验证命令。",
        tasks=[
            _task(
                "gui",
                read_set=["lucode/gui"],
                mcp=["project_filesystem_readonly", "code_locator"],
            ),
            _task(
                "runtime",
                read_set=["runtime"],
                mcp=["project_filesystem_readonly", "code_locator"],
            ),
        ],
        needs_synthesis=True,
        synthesis_instruction="合并体检结论，并列出下一步建议验证命令。",
    )
    for task in plan.tasks:
        task.title = "项目体检和建议验证命令"
        task.instruction = "阅读相关文件，输出体检结论，并给出下一步建议验证命令。"

    decision = apply_pipeline_gate(plan, plan.refined_request)

    assert decision.should_verify is False
    assert decision.test_intent is False
    assert all("command_runner" not in task.mcp for task in plan.tasks)


def test_pipeline_gate_keeps_command_runner_for_real_code_verification():
    from runtime.execution.pipeline import apply_pipeline_gate

    task = _task(
        "fix",
        write_intent=["runtime/foo.py"],
        mcp=["project_filesystem_readonly", "code_locator", "workspace_edit"],
    )
    task.skill_id = "code_engineer"
    task.title = "修复 runtime/foo.py 并运行 pytest 验证"
    task.instruction = "修改 runtime/foo.py 后运行 pytest tests/test_foo.py 验证。"
    plan = PlannerResult(
        route_type="single_agent",
        reason="代码修复",
        refined_request="请修复 runtime/foo.py 并运行 pytest tests/test_foo.py 验证。",
        tasks=[task],
        needs_synthesis=False,
    )

    decision = apply_pipeline_gate(plan, plan.refined_request)

    assert decision.should_verify is True
    assert decision.test_intent is True
    assert "command_runner" in task.mcp


def test_dynamic_attempt_emits_plan_normalized_for_single_task_multi_agent(monkeypatch, tmp_path):
    from runtime.config.settings import RuntimeSettings
    from runtime.execution import dynamic
    from runtime.events import ExecutionEventBus

    task = _task()
    plan = PlannerResult(
        route_type="multi_agent",
        reason="single task multi",
        refined_request="Read README",
        tasks=[task],
        needs_synthesis=True,
        synthesis_instruction="merge",
    )
    refined = RefinedRequest(raw_user_input="Read README", refined_request="Read README")
    event_bus = ExecutionEventBus()
    captured = {}

    async def fake_preview_plan(*args, **kwargs):
        del args, kwargs
        return refined, plan

    async def fake_run_single_agent(refined_request, normalized_plan, *args, **kwargs):
        del refined_request, args, kwargs
        captured["plan"] = normalized_plan
        return "single done", SimpleNamespace(passed=True)

    monkeypatch.setattr(dynamic, "preview_plan", fake_preview_plan)
    monkeypatch.setattr(dynamic, "_run_single_agent", fake_run_single_agent)
    monkeypatch.setattr(dynamic, "validate_plan", lambda plan, privacy_policy=None: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr(dynamic, "review_plan", lambda plan: SimpleNamespace(approved=True, findings=[]))
    monkeypatch.setattr(dynamic, "_apply_executor_model_defaults", lambda plan, settings, model_registry: None)

    class FakeModelRegistry:
        def first_configured(self, model_ids):
            return list(model_ids)[0]

        def get_model(self, model_id):
            return object()

        def get_model_info(self, model_id):
            return {"display_name": model_id}

    settings = RuntimeSettings(
        execution_mode="full",
        query_refiner_enabled=False,
        orchestrator_model_priority=["planner-model"],
        final_synthesizer_model_priority=["synth-model"],
        executor_model_priority=["worker-model"],
    )

    output, audit = asyncio.run(
        dynamic._execute_dynamic_attempt(
            "Read README",
            tmp_path,
            FakeModelRegistry(),
            mcp_manager=object(),
            hooks=None,
            run_agent=None,
            show_plan=False,
            settings=settings,
            privacy_policy=SimpleNamespace(mode="local_first"),
            flywheel=object(),
            attempt=1,
            event_bus=event_bus,
        )
    )

    events = event_bus.snapshot()
    normalized_events = [event for event in events if event.event_type == "PlanNormalized"]
    planning_events = [event for event in events if event.event_type == "PlanningCompleted"]

    assert output == "single done"
    assert getattr(audit, "passed", False) is True
    assert captured["plan"].route_type == "single_agent"
    assert len(normalized_events) == 1
    assert normalized_events[0].payload["route_type"] == "single_agent"
    assert planning_events[-1].payload["route_type"] == "single_agent"


def test_dynamic_attempt_passes_memory_pack_to_preview_plan(monkeypatch, tmp_path):
    from runtime.config.settings import RuntimeSettings
    from runtime.execution import dynamic
    from runtime.events import ExecutionEventBus
    from planning.planner_schema import PlannerResult, RefinedRequest
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    refined = RefinedRequest(raw_user_input="Read README", refined_request="Read README")
    plan = PlannerResult(route_type="direct_answer", reason="memory wired", refined_request="Read README")
    memory_pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-1",
                kind="tool_hint",
                summary="Use pytest for this project",
                confidence=0.8,
                injection_policy="auto",
            )
        ]
    )
    captured = {}

    class FakeResolver:
        def __init__(self, project_root, *, flywheel=None):
            captured["resolver_project_root"] = project_root
            captured["resolver_flywheel"] = flywheel

        def resolve_for_planner(self, request_text):
            captured["resolver_request_text"] = request_text
            return memory_pack

    async def fake_preview_plan(*args, **kwargs):
        del args
        captured["memory_pack"] = kwargs.get("memory_pack")
        return refined, plan

    async def fake_run_direct_answer(*args, **kwargs):
        del args, kwargs
        return "direct done"

    monkeypatch.setattr(dynamic, "MemoryResolver", FakeResolver)
    monkeypatch.setattr(dynamic, "preview_plan", fake_preview_plan)
    monkeypatch.setattr(dynamic, "_run_direct_answer", fake_run_direct_answer)
    monkeypatch.setattr(dynamic, "validate_plan", lambda plan, privacy_policy=None: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr(dynamic, "review_plan", lambda plan: SimpleNamespace(approved=True, findings=[]))
    monkeypatch.setattr(dynamic, "_apply_executor_model_defaults", lambda plan, settings, model_registry: None)

    class FakeModelRegistry:
        def first_configured(self, model_ids):
            return list(model_ids)[0]

        def get_model(self, model_id):
            return object()

        def get_model_info(self, model_id):
            return {"display_name": model_id}

    settings = RuntimeSettings(
        execution_mode="full",
        query_refiner_enabled=False,
        orchestrator_model_priority=["planner-model"],
        final_synthesizer_model_priority=["synth-model"],
        executor_model_priority=["worker-model"],
    )

    output, audit = asyncio.run(
        dynamic._execute_dynamic_attempt(
            "Read README",
            tmp_path,
            FakeModelRegistry(),
            mcp_manager=object(),
            hooks=None,
            run_agent=None,
            show_plan=False,
            settings=settings,
            privacy_policy=SimpleNamespace(mode="local_first"),
            flywheel=object(),
            attempt=1,
            event_bus=ExecutionEventBus(),
        )
    )

    assert output == "direct done"
    assert audit is None
    assert captured["memory_pack"] is memory_pack
    assert captured["resolver_project_root"] == tmp_path
    assert captured["resolver_flywheel"] is not None
    assert captured["resolver_request_text"] == "Read README"
