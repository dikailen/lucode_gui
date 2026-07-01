from __future__ import annotations

from runtime.agents.factory import AgentFactory
from catalog_system.loader import load_skill_catalog
from skills.loader import load_skill


class _DummyModelRegistry:
    def get_model(self, model_id):
        return f"model::{model_id}"

    def get_model_info(self, model_id):
        return {"supports_tools": True, "model_name": model_id}


class _NamedModelRegistry(_DummyModelRegistry):
    def get_model_info(self, model_id):
        return {
            "supports_tools": True,
            "model_name": "deepseek-v4-flash",
            "display_name": "DeepSeek V4 Flash",
        }


def _instructions(agent) -> str:
    return str(getattr(agent, "instructions", "") or "")


def _skill(skill_id: str) -> dict | None:
    for item in load_skill_catalog().get("skills", []):
        if item.get("id") == skill_id:
            return item
    return None


def test_solo_executor_contract_registered_as_internal_non_assignable():
    item = _skill("solo_executor_contract")

    assert item is not None
    assert item.get("internal") is True
    assert item.get("assignable") is False
    assert item.get("selectable") is False
    assert item.get("planner_visible") is False


def test_solo_agent_prompt_uses_unified_fast_agent_contract_without_claude_branding():
    factory = AgentFactory(_DummyModelRegistry(), mcp_manager=None)

    text = _instructions(factory.create_solo_agent("deepseek"))

    assert "统一 Agent Loop 的快速单 Agent 执行契约" in text
    assert "solo 模式" not in text
    assert "serial/full" not in text
    assert "Claude CLI" not in text
    assert "不要自称系统上下文没有明确提供的 Claude" in text
    assert "不要猜测底层模型品牌" in text


def test_direct_answer_prompt_does_not_expose_removed_execution_modes():
    factory = AgentFactory(_DummyModelRegistry(), mcp_manager=None)

    text = _instructions(
        factory.create_direct_answer_agent("deepseek", "回答你是什么模型", execution_mode="serial")
    )

    assert "当前模式：serial" not in text
    assert "当前模式：full" not in text
    assert "full 模式" not in text
    assert "serial 模式" not in text
    assert "不要自称系统没有明确提供的 Claude" in text
    assert "不要猜测底层模型品牌" in text
    assert "当前配置的模型" in text
    assert "不要自称 Lucode" not in text


def test_direct_answer_prompt_allows_known_configured_model_name():
    factory = AgentFactory(_NamedModelRegistry(), mcp_manager=None)

    text = _instructions(
        factory.create_direct_answer_agent("deepseek", "回答你是什么模型", execution_mode="full")
    )

    assert "DeepSeek V4 Flash" in text
    assert "可以如实回答" in text
    assert "如果当前模型名本身包含这些品牌或模型族" in text
    assert "只能说明你是 Lucode 当前配置" not in text


def test_identity_skill_contract_allows_system_provided_model_name():
    solo_text = load_skill("solo_executor_contract")
    supervisor_text = load_skill("full_supervisor")

    assert "系统上下文明确提供" in solo_text
    assert "系统上下文明确提供" in supervisor_text
    assert "如果当前模型名本身包含这些品牌或模型族" in solo_text
    assert "如果当前模型名本身包含这些品牌或模型族" in supervisor_text
    assert "只能说明：你是 Lucode 当前配置模型驱动的快速单 Agent" not in solo_text
