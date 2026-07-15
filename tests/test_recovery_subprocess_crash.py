from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

import pytest

from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.journal import RunJournal


_WORKER = Path(__file__).with_name("recovery_fault_worker.py")


@pytest.mark.parametrize("fault_point", ["after_event", "after_checkpoint", "after_dispatched"])
def test_hard_exit_after_a_durable_recovery_write_leaves_a_recoverable_journal(tmp_path, fault_point):
    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    existing_python_path = str(environment.get("PYTHONPATH") or "")
    environment["PYTHONPATH"] = str(project_root) + (os.pathsep + existing_python_path if existing_python_path else "")
    result = subprocess.run(
        [sys.executable, str(_WORKER), str(tmp_path), fault_point],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )

    assert result.returncode == 17, result.stderr
    journal = RunJournal(tmp_path)
    if fault_point == "after_event":
        assert [event.event_type for event in journal.events_for_run("run-crash")] == ["task.progress"]
        assert [item.run_id for item in RecoveryCoordinator(journal).scan_startup()] == ["run-crash"]
    elif fault_point == "after_checkpoint":
        checkpoint = journal.latest_checkpoint_for_run("run-crash")
        assert checkpoint is not None
        assert checkpoint.kind == "plan.accepted"
        assert [item.run_id for item in RecoveryCoordinator(journal).scan_startup()] == ["run-crash"]
    else:
        RecoveryCoordinator(journal).scan_startup()
        invocation = journal.tool_invocation("invocation-crash")
        assert invocation is not None
        assert invocation["status"] == "unknown"
