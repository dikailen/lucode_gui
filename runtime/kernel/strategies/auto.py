from __future__ import annotations

from dataclasses import replace

from runtime.config.execution_mode import normalize_execution_mode
from runtime.kernel.strategies.base import ExecutionContext


class AutoStrategy:
    mode_name = "auto"

    async def execute(self, context: ExecutionContext) -> str:
        from runtime.execution import execute_dynamic_request

        settings = replace(
            context.settings,
            execution_mode=normalize_execution_mode(context.settings.execution_mode),
        )
        kwargs = {
            "run_agent": context.run_agent,
            "settings": settings,
            "show_plan": context.request.show_plan,
            "display_input": context.request.routing_input or context.request.user_input,
            "routing_input": context.request.routing_input or context.request.user_input,
            "inline_files": getattr(context.request, "inline_files", ()),
            "recovery_envelope": getattr(context.request, "recovery_envelope", {}),
            "checkpoint_sink": getattr(context.request, "checkpoint_sink", None),
            "tool_lifecycle_sink": getattr(context.request, "tool_lifecycle_sink", None),
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
