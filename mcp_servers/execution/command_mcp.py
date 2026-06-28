import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
    from runtime.terminal import CommandGateway, CommandRequest, CommandStatus
except ModuleNotFoundError:
    from operation_log import append_operation_log
    from runtime.terminal import CommandGateway, CommandRequest, CommandStatus


mcp = FastMCP("command_runner", log_level="ERROR")

def _project_root() -> Path:
    return Path(os.environ["COMMAND_RUNNER_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["COMMAND_RUNNER_QUARANTINE_DIR"]).resolve()


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _log_operation(command: str, reason: str, returncode: int, *, status: str = "success", error: str = "") -> None:
    append_operation_log(
        _operation_log(),
        tool="command_runner.run_command",
        action="run_command",
        reason=reason,
        status=status,
        params_summary={"command": command, "returncode": returncode},
        approval_required=True,
        approval_note="MCP server requires approval for command execution.",
        result_summary=f"returncode={returncode}",
        error=error,
    )


@mcp.tool(
    name="run_command",
    description=(
        "Run a local project command without shell expansion. Dangerous commands are denied before execution. "
        "Requires user approval."
    ),
)
def run_command(command: str, reason: str, timeout_seconds: int = 60) -> str:
    gateway = CommandGateway(workspace_root=_project_root())
    result = gateway.run(
        CommandRequest(
            command=command,
            reason=reason,
            timeout_seconds=timeout_seconds,
            source="user",
            approval_granted=True,
        )
    )
    status = "success" if result.status is CommandStatus.SUCCESS else "failed"
    _log_operation(
        command,
        reason,
        result.returncode,
        status=status,
        error=result.stderr if result.returncode else "",
    )
    if result.status is CommandStatus.DENIED:
        raise ValueError(result.stderr)
    return json.dumps(
        {
            "command": command,
            "reason": reason,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
