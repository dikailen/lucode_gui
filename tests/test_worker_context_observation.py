from __future__ import annotations

from types import SimpleNamespace

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState


def test_worker_context_observation_uses_actual_tool_and_accepted_evidence_budgets_without_raw_content():
    from runtime.context.worker_observation import build_worker_context_observation

    task = SimpleNamespace(id="browser-task", model="model-a")
    prompt = "current request " + ("history " * 250)
    tool_context = "desktop_browser rules " + ("tool schema " * 280)
    accepted_evidence = {
        "mode": "enforce_high_risk",
        "claims": [{"claim_id": "c1", "text": "accepted fact", "evidence_refs": ["e1"]}],
        "evidence": [{"ref_id": "e1", "excerpt": "safe excerpt"}],
    }

    observation = build_worker_context_observation(
        task=task,
        prompt=prompt,
        model_info={"context_window_tokens": 1_000},
        mcp_ids=["desktop_browser"],
        tool_context=tool_context,
        accepted_evidence=accepted_evidence,
    )

    assert observation.task_id == "browser-task"
    assert observation.mcp_ids == ("desktop_browser",)
    assert observation.tool_schema_tokens > 0
    assert observation.accepted_evidence_tokens > 0
    assert observation.triggered is True
    serialized = str(observation.to_dict())
    assert prompt not in serialized
    assert tool_context not in serialized
    assert "accepted fact" not in serialized


def test_pipeline_records_worker_context_observation_as_a_safe_runtime_event(tmp_path):
    from runtime.context.worker_observation import build_worker_context_observation

    task = PlannedTask(
        id="worker-1",
        title="Browser task",
        instruction="Read a page summary.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    state = PipelineRunState.create(
        "read page",
        PlannerResult(route_type="single_agent", reason="test", refined_request="read page", tasks=[task]),
        project_root=tmp_path,
    )
    observation = build_worker_context_observation(
        task=task,
        prompt="private prompt text",
        model_info={"context_window_tokens": 8_000},
        mcp_ids=["desktop_browser"],
        tool_context="private tool text",
        accepted_evidence={"claims": [{"text": "private accepted evidence"}]},
    )

    state.record_worker_context_observation(observation)

    snapshot = state.to_dict()
    event = [item for item in snapshot["events"] if item["event_type"] == "WorkerContextObserved"][0]
    assert snapshot["worker_context_observations"][0]["task_id"] == "worker-1"
    assert event["payload"]["mcp_ids"] == ["desktop_browser"]
    assert "private prompt text" not in str(snapshot)
    assert "private tool text" not in str(snapshot)
    assert "private accepted evidence" not in str(snapshot)


def test_planned_worker_observes_real_tool_context_without_rewriting_agent_prompt(monkeypatch, tmp_path):
    import asyncio

    from runtime.execution.task_runner import _run_planned_task

    task = PlannedTask(
        id="browser-worker",
        title="Read browser summary",
        instruction="Read the current page summary.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="read page", tasks=[task])
    state = PipelineRunState.create("read page", plan, project_root=tmp_path)
    state.accepted_evidence = {"mode": "enforce_high_risk", "claims": [{"text": "accepted fact"}]}
    captured: dict[str, object] = {}

    class FakeFactory:
        def worker_context_budget(self, task, execution_mode=""):
            captured["budget_task"] = task.id
            return {
                "mcp_ids": ["desktop_browser"],
                "tool_context": "browser_navigate browser_get_page_summary rules",
                "model_info": {"context_window_tokens": 8_000},
            }

        async def create_task_agent(self, task, execution_mode=""):
            return SimpleNamespace(name="browser-worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="done")

    title, output = asyncio.run(
        _run_planned_task(
            "read page",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    assert title == "Read browser summary"
    assert output.startswith("done")
    assert captured["budget_task"] == "browser-worker"
    assert "read page" in captured["prompt"]
    assert task.instruction in captured["prompt"]
    assert "browser_navigate browser_get_page_summary rules" not in captured["prompt"]
    assert "accepted fact" not in captured["prompt"]
    assert state.worker_context_observations[0].mcp_ids == ("desktop_browser",)
    assert state.worker_context_observations[0].accepted_evidence_tokens > 0


def test_agent_factory_worker_context_budget_uses_resolved_task_mcp_instructions():
    from runtime.agents.factory import AgentFactory

    class ModelRegistry:
        def get_model_info(self, model_id):
            return {"id": model_id, "context_window_tokens": 16_000, "supports_tools": True}

    task = PlannedTask(
        id="command-worker",
        title="Run a verification command",
        instruction="Run the requested test command.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["command_runner"],
    )

    budget = AgentFactory(ModelRegistry(), mcp_manager=object()).worker_context_budget(task)

    assert budget["mcp_ids"] == ["command_runner"]
    assert "run_command" in budget["tool_context"]
    assert budget["model_info"]["context_window_tokens"] == 16_000


def test_single_agent_worker_records_context_observation(monkeypatch, tmp_path):
    import asyncio

    from runtime.execution.single_agent_runner import _run_single_agent
    from runtime.memory.flywheel import FlywheelStore

    task = PlannedTask(
        id="single-browser-worker",
        title="Read page",
        instruction="Read the current page summary.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="read page", tasks=[task])
    state = PipelineRunState.create("read page", plan, project_root=tmp_path)
    seen_modes: list[str] = []

    class FakeFactory:
        def worker_context_budget(self, task, execution_mode=""):
            seen_modes.append(execution_mode)
            return {
                "mcp_ids": ["desktop_browser"],
                "tool_context": "browser_get_page_summary rules",
                "model_info": {"context_window_tokens": 8_000},
            }

        async def create_task_agent(self, task, execution_mode=""):
            return SimpleNamespace(name="single-browser-worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        return SimpleNamespace(final_output="page read")

    _output, _audit = asyncio.run(
        _run_single_agent(
            "read page",
            plan,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            flywheel=FlywheelStore(tmp_path),
            execution_mode="serial",
            show_plan=False,
            attempt=1,
        )
    )

    assert state.worker_context_observations[0].task_id == "single-browser-worker"
    assert state.worker_context_observations[0].mcp_ids == ("desktop_browser",)
    assert seen_modes == ["serial"]


def test_worker_context_observation_respects_context_ledger_off(monkeypatch, tmp_path):
    import asyncio

    from runtime.execution.task_runner import _run_planned_task

    monkeypatch.setenv("LUCODE_CONTEXT_LEDGER", "off")
    task = PlannedTask(
        id="off-worker",
        title="Read page",
        instruction="Read the current page summary.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="read page", tasks=[task])
    state = PipelineRunState.create("read page", plan, project_root=tmp_path)

    class FakeFactory:
        def worker_context_budget(self, task, execution_mode=""):
            return {"mcp_ids": ["desktop_browser"], "tool_context": "rules", "model_info": {}}

        async def create_task_agent(self, task, execution_mode=""):
            return SimpleNamespace(name="off-worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        return SimpleNamespace(final_output="done")

    asyncio.run(
        _run_planned_task(
            "read page",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    assert state.worker_context_observations == []


def test_worker_context_mode_off_skips_observation_even_when_ledger_is_enabled(monkeypatch, tmp_path):
    import asyncio

    from runtime.execution.task_runner import _run_planned_task

    monkeypatch.setenv("LUCODE_CONTEXT_LEDGER", "observe")
    monkeypatch.setenv("LUCODE_WORKER_CONTEXT_MODE", "off")
    task = PlannedTask(
        id="worker-mode-off",
        title="Read page",
        instruction="Read the current page summary.",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="read page", tasks=[task])
    state = PipelineRunState.create("read page", plan, project_root=tmp_path)

    class FakeFactory:
        def worker_context_budget(self, task, execution_mode=""):
            return {"mcp_ids": ["desktop_browser"], "tool_context": "rules", "model_info": {}}

        async def create_task_agent(self, task, execution_mode=""):
            return SimpleNamespace(name="worker-mode-off")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        return SimpleNamespace(final_output="done")

    asyncio.run(
        _run_planned_task(
            "read page",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    assert state.worker_context_observations == []


def test_worker_enforce_keeps_normal_prompt_unchanged(monkeypatch):
    from runtime.context.worker_observation import (
        build_worker_context_observation,
        enforce_worker_prompt,
        render_worker_prompt,
    )

    monkeypatch.setenv("LUCODE_WORKER_CONTEXT_MODE", "enforce")
    task = SimpleNamespace(id="normal-worker", model="model-a")
    kwargs = {
        "refined_request": "CURRENT REQUEST",
        "task_instruction": "CURRENT TASK",
        "dependency_context": "dependency context",
        "workspace_context": "workspace context",
        "shared_context": "shared context",
        "memory_context": "memory context",
    }
    observation = build_worker_context_observation(
        task=task,
        prompt=render_worker_prompt(**kwargs),
        model_info={"context_window_tokens": 128_000},
        mcp_ids=[],
        tool_context="",
        accepted_evidence={},
    )

    prompt, applied, dropped = enforce_worker_prompt(observation=observation, **kwargs)

    assert observation.triggered is False
    assert prompt == render_worker_prompt(**kwargs)
    assert applied is False
    assert dropped == []


def test_worker_enforce_truncates_only_background_and_preserves_current_task(monkeypatch):
    from runtime.context.worker_observation import (
        build_worker_context_observation,
        enforce_worker_prompt,
    )

    monkeypatch.setenv("LUCODE_WORKER_CONTEXT_MODE", "enforce")
    task = SimpleNamespace(id="worker", model="model-a")
    background = "workspace-private-detail " * 900
    observation = build_worker_context_observation(
        task=task,
        prompt="current request\n" + background + "\ncurrent task",
        model_info={"context_window_tokens": 1_000},
        mcp_ids=["command_runner"],
        tool_context="tool schema " * 200,
        accepted_evidence={},
    )

    prompt, applied, dropped = enforce_worker_prompt(
        observation=observation,
        refined_request="CURRENT REQUEST MUST REMAIN",
        task_instruction="CURRENT TASK MUST REMAIN",
        dependency_context=background,
        workspace_context=background,
        shared_context=background,
        memory_context=background,
    )

    assert applied is True
    assert "CURRENT REQUEST MUST REMAIN" in prompt
    assert "CURRENT TASK MUST REMAIN" in prompt
    assert "workspace_context" in dropped
    assert len(prompt) < len(background) * 4


def test_worker_enforce_keeps_the_compacted_prompt_within_the_remaining_budget(monkeypatch):
    from runtime.context.token_counter import estimate_tokens
    from runtime.context.worker_observation import (
        WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS,
        build_worker_context_observation,
        enforce_worker_prompt,
    )

    monkeypatch.setenv("LUCODE_WORKER_CONTEXT_MODE", "enforce")
    task = SimpleNamespace(id="bounded-worker", model="model-a")
    background = "background detail " * 2_000
    observation = build_worker_context_observation(
        task=task,
        prompt="CURRENT REQUEST\n" + background * 4 + "\nCURRENT TASK",
        model_info={"context_window_tokens": 4_000},
        mcp_ids=["command_runner"],
        tool_context="tool schema " * 1_000,
        accepted_evidence={},
    )

    prompt, applied, dropped = enforce_worker_prompt(
        observation=observation,
        refined_request="CURRENT REQUEST",
        task_instruction="CURRENT TASK",
        dependency_context=background,
        workspace_context=background,
        shared_context=background,
        memory_context=background,
    )

    remaining_prompt_budget = (
        observation.context_window_tokens
        - observation.tool_schema_tokens
        - observation.accepted_evidence_tokens
        - WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS
    )
    assert applied is True
    assert dropped
    assert estimate_tokens(prompt) <= remaining_prompt_budget


def test_planned_worker_enforce_emits_a_safe_audit_event(monkeypatch, tmp_path):
    import asyncio

    from runtime.context.token_counter import estimate_tokens
    from runtime.execution import task_runner
    from runtime.execution.task_runner import _run_planned_task

    monkeypatch.setenv("LUCODE_WORKER_CONTEXT_MODE", "enforce")
    background = "private workspace detail " * 2_000
    monkeypatch.setattr(task_runner, "_latest_workspace_context", lambda _root, _task: background)
    task = PlannedTask(
        id="enforced-browser-worker",
        title="Read page",
        instruction="CURRENT TASK MUST REMAIN",
        skill_id="project_explorer",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="CURRENT REQUEST MUST REMAIN", tasks=[task])
    state = PipelineRunState.create("read page", plan, project_root=tmp_path)
    captured: dict[str, str] = {}

    class FakeFactory:
        def worker_context_budget(self, task, execution_mode=""):
            return {
                "mcp_ids": ["desktop_browser"],
                "tool_context": "tool schema " * 1_000,
                "model_info": {"context_window_tokens": 4_000},
            }

        async def create_task_agent(self, task, execution_mode=""):
            return SimpleNamespace(name="enforced-browser-worker")

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(final_output="done")

    asyncio.run(
        _run_planned_task(
            "CURRENT REQUEST MUST REMAIN",
            task,
            tmp_path,
            FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=state,
            show_status=False,
        )
    )

    assert "CURRENT REQUEST MUST REMAIN" in captured["prompt"]
    assert "CURRENT TASK MUST REMAIN" in captured["prompt"]
    assert estimate_tokens(captured["prompt"]) <= 4_000 - 3_000 - 512
    events = state.to_dict()["events"]
    enforced = [event for event in events if event["event_type"] == "WorkerContextEnforced"]
    assert enforced[0]["payload"] == {
        "budget_mode": "hard_limit",
        "dropped_sections": ["workspace_context"],
    }
    assert background not in str(enforced[0])
