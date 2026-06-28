from __future__ import annotations

import json
import sys

import pytest

from mcp_servers.execution import command_mcp


def _configure_command_runner(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    quarantine_dir = tmp_path / "quarantine"
    project_root.mkdir()
    quarantine_dir.mkdir()
    monkeypatch.setenv("COMMAND_RUNNER_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("COMMAND_RUNNER_QUARANTINE_DIR", str(quarantine_dir))
    return project_root, quarantine_dir


def _operation_records(quarantine_dir):
    log_path = quarantine_dir / "operations.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_command_mcp_run_command_uses_gateway_success_shape(monkeypatch, tmp_path):
    project_root, quarantine_dir = _configure_command_runner(monkeypatch, tmp_path)

    payload = json.loads(command_mcp.run_command(f"{sys.executable} --version", "check python"))

    assert payload["command"] == f"{sys.executable} --version"
    assert payload["reason"] == "check python"
    assert payload["returncode"] == 0
    assert "Python" in payload["stdout"]
    assert payload["stderr"] == ""
    records = _operation_records(quarantine_dir)
    assert records[-1]["tool"] == "command_runner.run_command"
    assert records[-1]["status"] == "success"
    assert records[-1]["params_summary"]["returncode"] == 0
    assert project_root.exists()


def test_command_mcp_run_command_denies_dangerous_command_and_logs(monkeypatch, tmp_path):
    _project_root, quarantine_dir = _configure_command_runner(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Command denied"):
        command_mcp.run_command("git reset --hard", "dangerous")

    records = _operation_records(quarantine_dir)
    assert records[-1]["status"] == "failed"
    assert records[-1]["params_summary"]["returncode"] == -1
    assert "Command denied" in records[-1]["error"]


def test_command_mcp_run_command_preserves_timeout_payload(monkeypatch, tmp_path):
    project_root, quarantine_dir = _configure_command_runner(monkeypatch, tmp_path)
    (project_root / "test_hang.py").write_text(
        "import time\n\n"
        "def test_hang():\n"
        "    time.sleep(10)\n",
        encoding="utf-8",
    )

    payload = json.loads(command_mcp.run_command(f"{sys.executable} -m pytest test_hang.py -q", "timeout", 1))

    assert payload["returncode"] == 124
    assert "timed out" in payload["stderr"]
    records = _operation_records(quarantine_dir)
    assert records[-1]["status"] == "failed"
    assert records[-1]["params_summary"]["returncode"] == 124


def test_command_mcp_treats_tool_invocation_as_approved_gateway_request(monkeypatch, tmp_path):
    _project_root, quarantine_dir = _configure_command_runner(monkeypatch, tmp_path)

    payload = json.loads(command_mcp.run_command(f'{sys.executable} -c "print(456)"', "approved inline"))

    assert payload["returncode"] == 0
    assert "456" in payload["stdout"]
    records = _operation_records(quarantine_dir)
    assert records[-1]["status"] == "success"
