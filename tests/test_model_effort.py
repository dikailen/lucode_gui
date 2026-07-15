from __future__ import annotations

import pytest


def test_reasoning_effort_config_is_per_model_and_rejects_unsupported_models(tmp_path):
    from runtime.config.model_effort import selected_reasoning_effort, set_reasoning_effort

    model_info = {
        "id": "openai_gpt_5_2_model",
        "configured": True,
        "supports_reasoning_effort": True,
        "reasoning_effort_levels": ["low", "medium", "high", "xhigh"],
    }
    assert set_reasoning_effort(
        workspace_root=tmp_path,
        model_id="openai_gpt_5_2_model",
        effort="high",
        model_info=model_info,
    ) == "high"
    assert selected_reasoning_effort(workspace_root=tmp_path, model_id="openai_gpt_5_2_model") == "high"

    with pytest.raises(ValueError, match="does not support"):
        set_reasoning_effort(
            workspace_root=tmp_path,
            model_id="deepseek_chat_model",
            effort="high",
            model_info={"id": "deepseek_chat_model", "configured": True},
        )


def test_agent_factory_attaches_reasoning_settings_only_for_explicitly_supported_models(tmp_path):
    from runtime.agents.factory import AgentFactory

    class Registry:
        def get_model_info(self, model_id):
            return {
                "id": model_id,
                "configured": True,
                "supports_reasoning_effort": model_id == "openai_gpt_5_2_model",
                "reasoning_effort_levels": ["low", "medium", "high", "xhigh"],
            }

        def get_model(self, model_id):
            return model_id

    class McpManager:
        async def get_many(self, _ids):
            return []

    from runtime.config.model_effort import set_reasoning_effort

    set_reasoning_effort(
        workspace_root=tmp_path,
        model_id="openai_gpt_5_2_model",
        effort="high",
        model_info=Registry().get_model_info("openai_gpt_5_2_model"),
    )
    factory = AgentFactory(Registry(), McpManager(), workspace_root=tmp_path)

    supported_agent = factory.create_direct_answer_agent("openai_gpt_5_2_model", "answer")
    unsupported_agent = factory.create_direct_answer_agent("deepseek_chat_model", "answer")

    assert getattr(getattr(supported_agent, "model_settings", None), "reasoning", None).effort == "high"
    assert getattr(unsupported_agent, "model_settings", None) is None
