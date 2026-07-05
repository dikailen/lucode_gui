from __future__ import annotations

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState


def _browser_task(**overrides) -> PlannedTask:
    data = {
        "id": "browser-fill",
        "title": "页面填表任务",
        "instruction": (
            "使用内置浏览器打开 https://www.w3schools.com/html/tryit.asp?filename=tryhtml_form_submit，"
            "读取页面摘要，然后把第一个文本输入框填写为 LucodeTest，不要提交表单，等待我审批。"
        ),
        "skill_id": "project_explorer",
        "model": "worker-model",
        "mcp": ["desktop_browser"],
        "acceptance_criteria": ["输入框内容为 'LucodeTest' 且表单未提交"],
        "expected_outputs": ["浏览器页面摘要和实际交互结果"],
    }
    data.update(overrides)
    return PlannedTask(**data)


def test_agent_factory_tells_browser_worker_to_trigger_runtime_approval():
    from runtime.agents.factory import AgentFactory
    from runtime.capabilities.resolver import CapabilityBinding

    class FakeRegistry:
        def get_model_info(self, model_id):
            return {"supports_tools": True, "model_name": model_id}

    class FakeResolver:
        def resolve_task(self, task):
            return CapabilityBinding(
                task_id=task.id,
                mcp=("desktop_browser",),
                source="test",
                reasons=("desktop_browser_interaction_detected",),
            )

    factory = AgentFactory(FakeRegistry(), mcp_manager=None, capability_resolver=FakeResolver())

    instructions = factory._task_instructions(_browser_task(), capability_binding=FakeResolver().resolve_task(_browser_task()))

    assert "browser_set_input_value" in instructions
    assert "不要在最终回答里用自然语言询问审批" in instructions
    assert "runtime 审批" in instructions
    assert "browser_get_page_summary" in instructions


def test_auditor_fails_browser_action_that_only_asks_for_approval(tmp_path):
    from runtime.safety.auditor import audit_execution

    task = _browser_task()
    plan = PlannerResult(
        route_type="single_agent",
        reason="desktop browser",
        refined_request=task.instruction,
        tasks=[task],
    )
    state = PipelineRunState.create(task.instruction, plan, project_root=tmp_path, mode="auto")
    output = (
        "页面已打开并读取摘要。\n\n"
        "下一步计划：我将尝试定位第一个文本输入框，然后填写为 LucodeTest，不会提交表单。\n"
        "请审批是否继续执行填写操作？"
    )
    state.record_task_result(task, output)

    audit = audit_execution(plan, state, output)

    assert audit.passed is False
    assert any("desktop_browser" in issue or "浏览器" in issue for issue in audit.remaining_issues)


def test_auditor_keeps_readonly_browser_summary_as_soft_semantic_warning(tmp_path):
    from runtime.safety.auditor import audit_execution

    task = _browser_task(
        title="页面摘要任务",
        instruction="使用内置浏览器打开 https://example.com 并读取页面摘要。",
        acceptance_criteria=["返回页面标题和摘要"],
        expected_outputs=["页面摘要文本"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="desktop browser summary",
        refined_request=task.instruction,
        tasks=[task],
    )
    state = PipelineRunState.create(task.instruction, plan, project_root=tmp_path, mode="auto")
    output = "已打开 https://example.com，标题 Example Domain。"
    state.record_task_result(task, output)

    audit = audit_execution(plan, state, output)

    assert audit.passed is True
    assert not audit.remaining_issues
