from __future__ import annotations

import sys

from runtime.terminal import CommandGateway, CommandRequest, CommandStatus


def test_command_gateway_runs_allowlisted_readonly_command(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)

    result = gateway.run(CommandRequest(command=f"{sys.executable} --version", reason="check python"))

    assert result.status is CommandStatus.SUCCESS
    assert result.returncode == 0
    assert "Python" in result.stdout
    assert result.decision.decision == "allow"
    assert result.cwd == tmp_path
    assert result.duration_ms >= 0


def test_command_gateway_denies_dangerous_command_without_execution(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)

    result = gateway.run(CommandRequest(command="git reset --hard", reason="dangerous"))

    assert result.status is CommandStatus.DENIED
    assert result.returncode == -1
    assert result.decision.decision == "deny"
    assert "command denied" in result.stderr.lower()


def test_command_gateway_rejects_cwd_outside_workspace(tmp_path):
    outside = tmp_path.parent
    gateway = CommandGateway(workspace_root=tmp_path)

    result = gateway.run(CommandRequest(command=f"{sys.executable} --version", cwd=outside, reason="outside"))

    assert result.status is CommandStatus.DENIED
    assert result.returncode == -1
    assert result.decision.decision == "deny"
    assert "cwd" in result.stderr.lower()


def test_command_gateway_times_out_limited_local_command(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)
    (tmp_path / "test_hang.py").write_text(
        "import time\n\n"
        "def test_hang():\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )
    command = f"{sys.executable} -m pytest test_hang.py -q"

    result = gateway.run(CommandRequest(command=command, timeout_seconds=1, reason="timeout smoke"))

    assert result.status is CommandStatus.TIMEOUT
    assert result.returncode == 124
    assert result.decision.decision == "allow_limited"
    assert "timed out" in result.stderr


def test_command_gateway_does_not_execute_when_approval_is_required(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)

    result = gateway.run(
        CommandRequest(command=f"{sys.executable} --version", reason="force approval", require_approval=True)
    )

    assert result.status is CommandStatus.DENIED
    assert result.returncode == -1
    assert result.decision.decision == "ask"
    assert "Command denied" in result.stderr


def test_command_gateway_agent_source_does_not_bypass_permission_ask(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)

    result = gateway.run(CommandRequest(command=f"{sys.executable} --version", source="agent", reason="agent check"))

    assert result.status is CommandStatus.DENIED
    assert result.returncode == -1
    assert result.decision.decision == "ask"
    assert result.decision.permission_decision == "ask"


def test_command_gateway_can_execute_ask_command_after_explicit_approval(tmp_path):
    gateway = CommandGateway(workspace_root=tmp_path)
    command = f'{sys.executable} -c "print(456)"'

    result = gateway.run(CommandRequest(command=command, reason="approved inline", approval_granted=True))

    assert result.status is CommandStatus.SUCCESS
    assert result.returncode == 0
    assert result.decision.decision == "ask"
    assert "456" in result.stdout
