from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from catalog_system.model_catalog import ModelRegistry
    from runtime.config.settings import RuntimeSettings


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: "ModelRegistry",
    mcp_manager: Any,
    hooks: Any,
    run_agent: Any,
    show_plan: bool = False,
    settings: "RuntimeSettings | None" = None,
    display_input: str | None = None,
    routing_input: str | None = None,
    output_controller: Any = None,
    event_bus: Any = None,
    inline_files: Any = None,
    recovery_envelope: Any = None,
    checkpoint_sink: Any = None,
    tool_lifecycle_sink: Any = None,
) -> str:
    from runtime.execution.dynamic import execute_dynamic_request as _execute_dynamic_request

    return await _execute_dynamic_request(
        raw_user_input,
        project_root,
        model_registry,
        mcp_manager,
        hooks,
        run_agent=run_agent,
        show_plan=show_plan,
        settings=settings,
        display_input=display_input,
        routing_input=routing_input,
        output_controller=output_controller,
        event_bus=event_bus,
        inline_files=inline_files,
        recovery_envelope=recovery_envelope,
        checkpoint_sink=checkpoint_sink,
        tool_lifecycle_sink=tool_lifecycle_sink,
    )


__all__ = ["execute_dynamic_request"]
