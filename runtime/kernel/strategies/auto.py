from __future__ import annotations

from dataclasses import replace

from runtime.config.execution_mode import effective_runtime_mode
from runtime.kernel.strategies.base import ExecutionContext


class AutoStrategy:
    mode_name = "auto"

    async def execute(self, context: ExecutionContext) -> str:
        from runtime.execution import execute_dynamic_request

        settings = replace(context.settings, execution_mode=effective_runtime_mode(context.settings.execution_mode))
        kwargs = {
            "run_agent": context.run_agent,
            "settings": settings,
            "show_plan": context.request.show_plan,
            "display_input": context.request.routing_input or context.request.user_input,
        }
        if getattr(context, "output_controller", None) is not None:
            kwargs["output_controller"] = context.output_controller
        if getattr(context, "event_bus", None) is not None:
            kwargs["event_bus"] = context.event_bus
        return await execute_dynamic_request(
            context.request.user_input,
            context.request.workspace_root,
            context.model_registry,
            context.mcp_manager,
            context.hooks,
            **kwargs,
        )
