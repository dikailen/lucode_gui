from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.run_context import RunContextStore
from runtime.agent.supervisor import WorkerReport
from runtime.agents.factory import AgentFactory
from runtime.execution.pipeline import PipelineRunState


class _FakeRegistry:
    def get_model(self, model_id):
        return f"model:{model_id}"

    def get_model_info(self, model_id):
        del model_id
        return {"supports_tools": True}


class _FakeMcpManager:
    pass


class _FakeReadonlyServer:
    name = "run_workspace_readonly"


def _team_plan() -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent",
        reason="team supervisor final answer",
        refined_request="修复认证逻辑并汇总结果",
        tasks=[],
        needs_synthesis=False,
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
            }
        },
    )


def _summary_plan() -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent",
        reason="summary helper",
        refined_request="summarize worker outputs",
        tasks=[
            PlannedTask(
                id="worker-a",
                title="Worker A",
                instruction="write partial result",
                skill_id="code_engineer",
                model="m1",
                mcp=[],
            )
        ],
        needs_synthesis=True,
        synthesis_instruction="merge worker outputs",
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": True, "reason": "explicit synthesis"},
            }
        },
    )


def test_create_supervisor_agent_loads_full_supervisor_skill():
    factory = AgentFactory(_FakeRegistry(), _FakeMcpManager())
    server = _FakeReadonlyServer()

    agent = factory.create_supervisor_agent("supervisor-model", [server])

    assert agent.name == "full_supervisor_agent"
    assert agent.model == "model:supervisor-model"
    assert agent.mcp_servers == [server]
    assert "主管" in agent.instructions
    assert "Full Supervisor Contract" in agent.instructions


def test_orchestrator_planner_skill_documents_memory_adoption_contract():
    from pathlib import Path

    text = Path("skills/orchestrator-planner/SKILL.md").read_text(encoding="utf-8")

    assert "memory_resolver" in text
    assert "adopted_entry_ids" in text
    assert "task_bindings" in text
    assert "adoption_reasons" in text
    assert "execution_contract" in text


def test_supervisor_finalizer_calls_agent_with_reports_and_blackboard(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    captured = {}

    class FakeFactory:
        def create_supervisor_agent(self, model_id, readonly_servers):
            captured["model_id"] = model_id
            captured["servers"] = readonly_servers
            return SimpleNamespace(name="full_supervisor_agent")

    class FakeServer:
        async def __aenter__(self):
            return _FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_create_readonly_server(run_dir, server_name):
        captured["run_dir"] = run_dir
        captured["server_name"] = server_name
        return FakeServer()

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return SimpleNamespace(final_output="主管 Agent 收口结果")

    monkeypatch.setattr(runner, "create_readonly_filesystem_server", fake_create_readonly_server)
    state = PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full")
    state.run_context.put_insight("auth.py 已经由 worker-a 读取", source="worker-a")

    output = asyncio.run(
        runner._finalize_with_supervisor_agent(
            refined_request="修复认证逻辑",
            plan=_team_plan(),
            run_dir=tmp_path,
            run_state=state,
            mode="full",
            model_id="supervisor-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            worker_outputs=[("worker-a", "Auth", "done")],
            worker_reports=[WorkerReport(task_id="worker-a", status="completed", summary="done")],
            lead_review_findings=[],
            lead_rework_limits=[],
        )
    )

    assert output == "主管 Agent 收口结果"
    assert captured["model_id"] == "supervisor-model"
    assert captured["servers"][0].name == "run_workspace_readonly"
    assert captured["server_name"] == "run_workspace_readonly"
    assert captured["agent"].name == "full_supervisor_agent"
    assert "WorkerReport" in captured["prompt"]
    assert "共享黑板" in captured["prompt"]
    assert "auth.py 已经由 worker-a 读取" in captured["prompt"]
    assert captured["kwargs"]["stream_output"] is True


def test_supervisor_finalizer_streams_answer_delta(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    captured = {}

    class FakeFactory:
        def create_supervisor_agent(self, model_id, readonly_servers):
            del model_id, readonly_servers
            return SimpleNamespace(name="full_supervisor_agent")

    class FakeServer:
        async def __aenter__(self):
            return _FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_create_readonly_server(run_dir, server_name):
        del run_dir, server_name
        return FakeServer()

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        del agent, prompt, hooks
        captured["kwargs"] = kwargs
        kwargs["on_delta"]("supervisor streaming")
        return SimpleNamespace(final_output="supervisor final")

    monkeypatch.setattr(runner, "create_readonly_filesystem_server", fake_create_readonly_server)
    state = PipelineRunState.create("summarize", _team_plan(), project_root=tmp_path, mode="full")

    output = asyncio.run(
        runner._finalize_with_supervisor_agent(
            refined_request="summarize",
            plan=_team_plan(),
            run_dir=tmp_path,
            run_state=state,
            mode="full",
            model_id="supervisor-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            worker_outputs=[],
            worker_reports=[],
            lead_review_findings=[],
            lead_rework_limits=[],
        )
    )

    events = [event.to_dict() for event in state.event_bus.snapshot()]
    delta = [event for event in events if event["event_type"] == "AgentMessageDelta"]

    assert output == "supervisor final"
    assert captured["kwargs"]["stream_output"] is True
    assert len(delta) == 1
    assert delta[0]["agent"] == "full_supervisor_agent"
    assert "task_id" not in delta[0] or not delta[0]["task_id"]
    assert delta[0]["payload"]["text"] == "supervisor streaming"


def test_summary_helper_streams_final_synthesizer_delta(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    captured = {}

    class FakeFactory:
        def create_synthesizer_agent(self, model_id, run_workspace_server):
            captured["model_id"] = model_id
            captured["server"] = run_workspace_server
            return SimpleNamespace(name="final_synthesizer_agent")

    class FakeServer:
        async def __aenter__(self):
            return _FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_create_readonly_server(run_dir, server_name):
        captured["run_dir"] = run_dir
        captured["server_name"] = server_name
        return FakeServer()

    async def fake_run_planned_task(*args, **kwargs):
        del args, kwargs
        return ("Worker A", "worker output")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        del agent, prompt, hooks
        captured["kwargs"] = kwargs
        kwargs["on_delta"]("synthesizer streaming")
        return SimpleNamespace(final_output="synthesizer final")

    monkeypatch.setattr(runner, "create_readonly_filesystem_server", fake_create_readonly_server)
    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    state = PipelineRunState.create("summarize", _summary_plan(), project_root=tmp_path, mode="serial")

    output = asyncio.run(
        runner._run_multi_agent(
            "summarize",
            _summary_plan(),
            tmp_path,
            "summary-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            execution_mode="serial",
            show_progress=False,
        )
    )

    events = [event.to_dict() for event in state.event_bus.snapshot()]
    delta = [event for event in events if event["event_type"] == "AgentMessageDelta"]

    assert output == "synthesizer final"
    assert captured["model_id"] == "summary-model"
    assert captured["server"].name == "run_workspace_readonly"
    assert captured["server_name"] == "run_workspace_readonly"
    assert captured["kwargs"]["stream_output"] is True
    assert len(delta) == 1
    assert delta[0]["agent"] == "final_synthesizer_agent"
    assert "task_id" not in delta[0] or not delta[0]["task_id"]
    assert delta[0]["payload"]["text"] == "synthesizer streaming"


def test_full_team_finalization_prefers_supervisor_agent(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    async def fake_supervisor_finalize(**kwargs):
        assert kwargs["model_id"] == "supervisor-model"
        return "主管 Agent 最终答案"

    def fail_template(*args, **kwargs):
        del args, kwargs
        raise AssertionError("template fallback should not run when supervisor agent succeeds")

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_supervisor_finalize)
    monkeypatch.setattr(runner, "_render_lead_supervisor_output", fail_template)

    output = asyncio.run(
        runner._run_multi_agent(
            "修复认证逻辑",
            _team_plan(),
            tmp_path,
            "supervisor-model",
            factory=SimpleNamespace(),
            hooks=None,
            run_agent=None,
            run_state=PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
        )
    )

    assert output == "主管 Agent 最终答案"


def test_full_team_finalization_falls_back_to_template_when_agent_unavailable(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    async def fake_supervisor_finalize(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_supervisor_finalize)

    output = asyncio.run(
        runner._run_multi_agent(
            "修复认证逻辑",
            _team_plan(),
            tmp_path,
            "supervisor-model",
            factory=SimpleNamespace(),
            hooks=None,
            run_agent=None,
            run_state=PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
        )
    )

    assert output.startswith("主管最终汇报")


def test_planning_supervisor_scout_reads_key_files_into_blackboard(tmp_path):
    from planning.planner import scout_project_context_for_planning

    (tmp_path / "README.md").write_text("# Demo\nProject overview\n", encoding="utf-8")
    package = tmp_path / "package.json"
    package.write_text('{"scripts":{"test":"pytest"}}\n', encoding="utf-8")
    (tmp_path / "notes.log").write_text("ignore me\n", encoding="utf-8")
    store = RunContextStore(tmp_path)

    context = scout_project_context_for_planning(
        "检查当前项目结构和测试入口",
        project_root=tmp_path,
        run_context=store,
        max_files=2,
    )

    rendered = store.render_for_task("worker")
    assert "规划期主管侦察" in context
    assert "README.md" in context
    assert "package.json" in context
    assert "Project overview" in rendered
    assert "pytest" in rendered
    assert "notes.log" not in rendered


def test_preview_plan_includes_planning_scout_context_in_planner_prompt(monkeypatch, tmp_path):
    from planning import planner

    (tmp_path / "README.md").write_text("# Demo\nPlanner scout target\n", encoding="utf-8")
    store = RunContextStore(tmp_path)
    captured = {}

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "scout context available",
                        "tasks": [],
                        "needs_synthesis": False,
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    refined, plan = asyncio.run(
        planner.preview_plan(
            "分析当前项目",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            run_context=store,
        )
    )

    assert refined.refined_request == "分析当前项目"
    assert plan.route_type == "single_agent"
    assert "规划期主管侦察" in captured["prompt"]
    assert "README.md" in captured["prompt"]
    assert "Planner scout target" in store.render_for_task("worker")


def test_preview_plan_includes_memory_pack_in_planner_prompt(monkeypatch, tmp_path):
    from planning import planner
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    captured = {}
    memory_pack = MemoryPack(
        session_summary="上一轮已经确认 GUI theme 的快速测试入口。",
        entries=[
            MemoryPackEntry(
                id="mem-1",
                kind="verification_command",
                summary="修改 GUI theme 后运行 python -m pytest tests/test_gui_minimal_theme.py -q",
                confidence=0.88,
                source="distiller",
                tags=("gui", "theme", "pytest"),
                scope=("lucode/gui/theme.py",),
                injection_policy="auto",
            )
        ],
    )

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "memory available",
                        "tasks": [],
                        "needs_synthesis": False,
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    refined, plan = asyncio.run(
        planner.preview_plan(
            "继续修改 GUI theme",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            memory_pack=memory_pack,
        )
    )

    assert refined.refined_request == "继续修改 GUI theme"
    assert plan.route_type == "single_agent"
    assert "项目经验" in captured["prompt"]
    assert "背景，不是本轮新任务" in captured["prompt"]
    assert "python -m pytest tests/test_gui_minimal_theme.py -q" in captured["prompt"]


def test_preview_plan_tells_planner_how_to_adopt_failure_lesson_candidates(monkeypatch, tmp_path):
    from planning import planner
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    captured = {}
    memory_pack = MemoryPack(
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid parallel writes to lucode/gui/theme.py.",
                confidence=0.68,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ]
    )

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "memory adoption prompt",
                        "tasks": [],
                        "needs_synthesis": False,
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    asyncio.run(
        planner.preview_plan(
            "继续修改 GUI theme",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            memory_pack=memory_pack,
        )
    )

    assert "adopted_entry_ids" in captured["prompt"]
    assert "task_bindings" in captured["prompt"]
    assert "adoption_reasons" in captured["prompt"]
    assert "mem-failure" in captured["prompt"]


def test_preview_plan_records_memory_resolver_interface_without_overwriting_contract(monkeypatch, tmp_path):
    from planning import planner
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    memory_pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-auto",
                kind="verification_command",
                summary="Run pytest after GUI theme changes",
                confidence=0.86,
                injection_policy="auto",
            )
        ],
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-candidate",
                kind="failure_lesson",
                summary="Avoid conflicting writes",
                confidence=0.9,
                injection_policy="planner_candidate",
            )
        ],
        ignored_entry_ids=["mem-ignored"],
    )

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, prompt, hooks
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "memory available",
                        "tasks": [],
                        "needs_synthesis": False,
                        "memory_interface": {
                            "execution_contract": {
                                "readonly_hard_constraint": False,
                                "supervisor_route": "single",
                            }
                        },
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    _, plan = asyncio.run(
        planner.preview_plan(
            "继续修改 GUI theme",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            memory_pack=memory_pack,
        )
    )

    assert plan.memory_interface["execution_contract"]["supervisor_route"] == "single"
    memory = plan.memory_interface["memory_resolver"]
    assert memory["provided_entry_ids"] == ["mem-auto"]
    assert memory["candidate_entry_ids"] == ["mem-candidate"]
    assert memory["ignored_entry_ids"] == ["mem-ignored"]
    assert memory["decision_reasons"]["mem-candidate"] == "planner_candidate_only"

def test_format_plan_preview_renders_memory_resolver_metadata():
    from planning.planner import format_plan_preview
    from planning.planner_schema import PlannerResult, RefinedRequest

    refined = RefinedRequest(raw_user_input="改 GUI", refined_request="改 GUI")
    plan = PlannerResult(
        route_type="single_agent",
        reason="memory resolver metadata",
        refined_request="改 GUI",
        memory_interface={
            "memory_resolver": {
                "provided_entry_ids": ["mem-auto"],
                "candidate_entry_ids": ["mem-candidate"],
                "ignored_entry_ids": ["mem-ignored"],
                "adopted_entry_ids": ["mem-candidate"],
                "task_bindings": {"worker-gui": ["mem-candidate"]},
                "adoption_reasons": {"mem-candidate": "avoid repeating GUI rollback"},
            }
        },
    )

    rendered = format_plan_preview(refined, plan)

    assert "记忆解析器" in rendered
    assert "mem-auto" in rendered
    assert "mem-candidate" in rendered
    assert "mem-ignored" in rendered
    assert "worker-gui" in rendered
    assert "avoid repeating GUI rollback" in rendered

def test_task_prompt_includes_task_memory_pack_without_mixing_workspace_context():
    from runtime.execution.task_runner import _task_prompt
    from runtime.memory.resolver import TaskMemoryPack, MemoryPackEntry

    task_pack = TaskMemoryPack(
        task_id="worker-gui",
        entries=[
            MemoryPackEntry(
                id="mem-gui",
                kind="tool_hint",
                summary="Run GUI theme pytest after changing theme.py.",
                confidence=0.9,
                scope=("lucode/gui/theme.py",),
                injection_policy="auto",
            )
        ],
    )

    prompt = _task_prompt(
        "Update GUI theme",
        "Change theme.py",
        workspace_context="工作区上下文",
        shared_context="共享黑板",
        task_memory_pack=task_pack,
    )

    assert "任务相关项目经验" in prompt
    assert "背景，不是本轮新任务" in prompt
    assert "mem-gui" in prompt
    assert "Run GUI theme pytest after changing theme.py." in prompt
    assert prompt.index("共享黑板") < prompt.index("任务相关项目经验") < prompt.index("工作区上下文")
    assert "Change theme.py" in prompt

def test_worker_report_marks_provided_memory_context(tmp_path):
    from planning.planner_schema import PlannedTask, PlannerResult
    from runtime.execution.pipeline import PipelineRunState
    from runtime.execution.worker_reporter import build_worker_report, render_worker_report
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update theme.py",
        skill_id="code_engineer",
        model="gpt",
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )
    plan = PlannerResult(route_type="single_agent", reason="memory report", refined_request="Update GUI", tasks=[task])
    memory_pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-gui",
                kind="tool_hint",
                summary="Run GUI pytest after changing theme.py.",
                confidence=0.9,
                scope=("lucode/gui/theme.py",),
                injection_policy="auto",
            )
        ]
    )
    state = PipelineRunState.create("Update GUI", plan, project_root=tmp_path, mode="full", memory_pack=memory_pack)
    task_pack = state.memory_pack.for_task(task)
    state.emit_event(
        "TaskMemoryProvided",
        "task memory provided",
        task_id=task.id,
        agent=task.id,
        status="completed",
        payload={"entry_ids": task_pack.provided_entry_ids},
    )
    state.record_task_result(task, "done")

    report = build_worker_report(task, "done", run_state=state)
    rendered = render_worker_report(report)

    assert "memory_context_provided: mem-gui" in report.artifacts
    assert "memory_context_provided: mem-gui" in rendered

def test_run_planned_task_injects_only_task_relevant_memory(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from planning.planner_schema import PlannedTask, PlannerResult
    from runtime.execution.pipeline import PipelineRunState
    from runtime.execution.task_runner import _run_planned_task
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update theme.py",
        skill_id="code_engineer",
        model="gpt",
        mcp=["workspace_edit"],
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )
    plan = PlannerResult(route_type="single_agent", reason="memory worker", refined_request="Update GUI", tasks=[task])
    memory_pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-gui",
                kind="tool_hint",
                summary="Run GUI pytest after changing theme.py.",
                confidence=0.9,
                scope=("lucode/gui/theme.py",),
                injection_policy="auto",
            ),
            MemoryPackEntry(
                id="mem-docs",
                kind="project_fact",
                summary="Docs live under docs/.",
                confidence=0.9,
                scope=("docs/",),
                injection_policy="auto",
            ),
        ]
    )
    state = PipelineRunState.create("Update GUI", plan, project_root=tmp_path, mode="full", memory_pack=memory_pack)
    captured = {}

    class FakeFactory:
        async def create_task_agent(self, task, execution_mode=""):
            del task, execution_mode
            return SimpleNamespace(name="worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        del agent, hooks, kwargs
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="done")

    title, output = asyncio.run(
        _run_planned_task(
            "Update GUI",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    events = [event.to_dict() for event in state.event_bus.snapshot()]
    memory_events = [event for event in events if event["event_type"] == "TaskMemoryProvided"]

    assert title == "GUI theme"
    assert output.startswith("done")
    assert "mem-gui" in captured["prompt"]
    assert "Run GUI pytest after changing theme.py." in captured["prompt"]
    assert "mem-docs" not in captured["prompt"]
    assert memory_events[-1]["payload"]["entry_ids"] == ["mem-gui"]

def test_preview_plan_merges_planner_failure_lesson_adoption(monkeypatch, tmp_path):
    from planning import planner
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    memory_pack = MemoryPack(
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid parallel writes to lucode/gui/theme.py.",
                confidence=0.82,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ]
    )

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, prompt, hooks
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "adopt failure lesson",
                        "tasks": [
                            {
                                "id": "worker-gui",
                                "title": "GUI theme",
                                "instruction": "Update theme.py",
                                "skill_id": "code_engineer",
                                "model": "gpt",
                                "read_set": ["lucode/gui/theme.py"],
                                "write_intent": ["lucode/gui/theme.py"],
                            }
                        ],
                        "needs_synthesis": False,
                        "memory_interface": {
                            "execution_contract": {"supervisor_route": "single"},
                            "memory_resolver": {
                                "adopted_entry_ids": ["mem-failure"],
                                "task_bindings": {"worker-gui": ["mem-failure"]},
                                "adoption_reasons": {"mem-failure": "avoid repeating rollback conflict"},
                            },
                        },
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    _, plan = asyncio.run(
        planner.preview_plan(
            "继续修改 GUI theme",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            memory_pack=memory_pack,
        )
    )

    assert plan.memory_interface["execution_contract"]["supervisor_route"] == "single"
    memory = plan.memory_interface["memory_resolver"]
    assert memory["candidate_entry_ids"] == ["mem-failure"]
    assert memory["adopted_entry_ids"] == ["mem-failure"]
    assert memory["task_bindings"] == {"worker-gui": ["mem-failure"]}
    assert memory["adoption_reasons"] == {"mem-failure": "avoid repeating rollback conflict"}
    assert memory_pack.to_memory_interface()["adopted_entry_ids"] == ["mem-failure"]


def test_run_planned_task_injects_planner_bound_failure_lesson(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from planning.planner_schema import PlannedTask, PlannerResult
    from runtime.execution.pipeline import PipelineRunState
    from runtime.execution.task_runner import _run_planned_task
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update theme.py",
        skill_id="code_engineer",
        model="gpt",
        mcp=["workspace_edit"],
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )
    plan = PlannerResult(route_type="single_agent", reason="memory worker", refined_request="Update GUI", tasks=[task])
    memory_pack = MemoryPack(
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid parallel writes to lucode/gui/theme.py after rollback.",
                confidence=0.82,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ]
    )
    memory_pack.apply_memory_interface(
        {
            "adopted_entry_ids": ["mem-failure"],
            "task_bindings": {"worker-gui": ["mem-failure"]},
            "adoption_reasons": {"mem-failure": "planner bound rollback lesson"},
        }
    )
    state = PipelineRunState.create("Update GUI", plan, project_root=tmp_path, mode="full", memory_pack=memory_pack)
    captured = {}

    class FakeFactory:
        async def create_task_agent(self, task, execution_mode=""):
            del task, execution_mode
            return SimpleNamespace(name="worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        del agent, hooks, kwargs
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="done")

    title, output = asyncio.run(
        _run_planned_task(
            "Update GUI",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    events = [event.to_dict() for event in state.event_bus.snapshot()]
    memory_events = [event for event in events if event["event_type"] == "TaskMemoryProvided"]

    assert title == "GUI theme"
    assert output.startswith("done")
    assert "失败教训" in captured["prompt"]
    assert "mem-failure" in captured["prompt"]
    assert "Avoid parallel writes" in captured["prompt"]
    assert memory_events[-1]["payload"]["entry_ids"] == ["mem-failure"]
