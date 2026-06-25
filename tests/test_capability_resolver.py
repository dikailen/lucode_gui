from __future__ import annotations

import asyncio
from types import SimpleNamespace

from planning.planner_schema import PlannedTask


def _task(**overrides) -> PlannedTask:
    data = {
        "id": "worker-gui",
        "title": "GUI task",
        "instruction": "Update GUI files",
        "skill_id": "code_engineer",
        "model": "worker-model",
        "mcp": ["project_filesystem_readonly", "workspace_edit"],
        "read_set": ["lucode/gui/theme.py"],
        "write_intent": ["lucode/gui/theme.py"],
    }
    data.update(overrides)
    return PlannedTask(**data)


def test_capability_resolver_preserves_existing_task_tool_scope():
    from runtime.capabilities.resolver import CapabilityResolver

    task = _task()

    binding = CapabilityResolver().resolve_task(task)

    assert binding.task_id == "worker-gui"
    assert binding.mcp == ("project_filesystem_readonly", "workspace_edit")
    assert binding.read_set == ("lucode/gui/theme.py",)
    assert binding.write_intent == ("lucode/gui/theme.py",)
    assert binding.resource_locks == ()
    assert binding.source == "planner_task"
    assert "planner_task_mcp_passthrough" in binding.reasons


def test_agent_factory_binds_mcp_servers_from_capability_resolver():
    from runtime.agents.factory import AgentFactory
    from runtime.capabilities.resolver import CapabilityBinding

    class FakeRegistry:
        def get_model_info(self, model_id):
            assert model_id == "worker-model"
            return {"supports_tools": True}

        def get_model(self, model_id):
            return f"model:{model_id}"

    class FakeMcpManager:
        def __init__(self):
            self.requested = None

        async def get_many(self, mcp_ids):
            self.requested = list(mcp_ids)
            return [f"server:{mcp_id}" for mcp_id in mcp_ids]

    class FakeResolver:
        def resolve_task(self, task):
            assert task.id == "worker-gui"
            return CapabilityBinding(
                task_id=task.id,
                mcp=("project_filesystem_readonly", "comfyui_graph_mcp"),
                read_set=tuple(task.read_set),
                write_intent=tuple(task.write_intent),
                resource_locks=("comfyui",),
                source="test_resolver",
                reasons=("registered_capability",),
            )

    manager = FakeMcpManager()
    factory = AgentFactory(FakeRegistry(), manager, capability_resolver=FakeResolver())

    agent = asyncio.run(factory.create_task_agent(_task()))

    assert manager.requested == ["project_filesystem_readonly", "comfyui_graph_mcp"]
    assert agent.mcp_servers == ["server:project_filesystem_readonly", "server:comfyui_graph_mcp"]
    assert agent.name == "dynamic_worker-gui"
    assert "comfyui_graph_mcp" in agent.instructions

def test_scheduler_uses_capability_resolver_resource_locks():
    from runtime.capabilities.resolver import CapabilityBinding
    from runtime.execution.parallel_scheduler import execution_batch_decisions_for_mode

    class FakeResolver:
        def resolve_task(self, task):
            return CapabilityBinding(
                task_id=task.id,
                mcp=("comfyui_graph_mcp",),
                resource_locks=("comfyui",),
                source="test_resolver",
                reasons=("plugin_resource_lock",),
            )

    tasks = [
        _task(id="graph-a", mcp=["project_filesystem_readonly"], read_set=[], write_intent=[]),
        _task(id="graph-b", mcp=["project_filesystem_readonly"], read_set=[], write_intent=[]),
    ]

    decisions = execution_batch_decisions_for_mode(tasks, "auto", capability_resolver=FakeResolver())

    assert [[task.id for task in decision.tasks] for decision in decisions] == [["graph-a"], ["graph-b"]]
    assert decisions[1].reason == "resource_lock"
    assert "comfyui" in " ".join(decisions[1].details)

def test_multi_agent_scheduler_and_factory_share_capability_resolver(monkeypatch, tmp_path):
    from planning.planner_schema import PlannerResult
    from runtime.agent.supervisor import WorkerReport
    from runtime.capabilities.resolver import CapabilityBinding
    from runtime.execution import multi_agent_runner as runner
    from runtime.execution.pipeline import PipelineRunState

    class FakeResolver:
        def resolve_task(self, task):
            return CapabilityBinding(
                task_id=task.id,
                mcp=("project_filesystem_readonly", "comfyui_graph_mcp"),
                resource_locks=("comfyui",),
                source="test_resolver",
                reasons=("plugin_resource_lock",),
            )

    class FakeFactory:
        def __init__(self):
            self.capability_resolver = FakeResolver()
            self.bound_mcps = []

        async def create_task_agent(self, task, execution_mode=""):
            binding = self.capability_resolver.resolve_task(task)
            self.bound_mcps.append(list(binding.mcp))
            return SimpleNamespace(name=f"dynamic_{task.id}", mcp_servers=list(binding.mcp))

        def create_synthesizer_agent(self, model_id, run_workspace_server):
            del model_id, run_workspace_server
            return SimpleNamespace(name="final_synthesizer_agent")

    class FakeReadonlyServer:
        name = "run_workspace_readonly"

    class FakeServerContext:
        async def __aenter__(self):
            return FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    tasks = [
        _task(id="graph-a", mcp=["project_filesystem_readonly"], read_set=[], write_intent=[]),
        _task(id="graph-b", mcp=["project_filesystem_readonly"], read_set=[], write_intent=[]),
    ]
    plan = PlannerResult(
        route_type="multi_agent",
        reason="capability resolver runtime",
        refined_request="operate comfyui graphs",
        tasks=tasks,
        needs_synthesis=True,
        synthesis_instruction="summarize",
    )

    async def fake_run_agent(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(final_output="final")

    async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, *args, **kwargs):
        del refined_request, project_root, hooks, run_agent, args, kwargs
        await factory.create_task_agent(task)
        return task.title, "done"

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(
        runner,
        "build_worker_report",
        lambda task, output, run_state=None: WorkerReport(task_id=task.id, status="completed", summary=output),
    )
    monkeypatch.setattr(
        runner,
        "create_readonly_filesystem_server",
        lambda run_dir, server_name: FakeServerContext(),
    )

    factory = FakeFactory()
    state = PipelineRunState.create("operate comfyui graphs", plan, project_root=tmp_path, mode="auto")

    output = asyncio.run(
        runner._run_multi_agent(
            "operate comfyui graphs",
            plan,
            tmp_path,
            "model",
            factory=factory,
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            execution_mode="auto",
            show_progress=False,
        )
    )

    serialized = [
        event.to_dict()
        for event in state.event_bus.snapshot()
        if event.to_dict()["event_type"] == "ParallelBatchSerialized"
    ]

    assert output == "final"
    assert serialized[-1]["payload"]["reason"] == "resource_lock"
    assert "comfyui" in " ".join(serialized[-1]["payload"]["details"])
    assert factory.bound_mcps == [
        ["project_filesystem_readonly", "comfyui_graph_mcp"],
        ["project_filesystem_readonly", "comfyui_graph_mcp"],
    ]
