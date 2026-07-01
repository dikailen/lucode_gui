from __future__ import annotations

import sys

from runtime.terminal import CommandStatus, TerminalSession


def test_terminal_session_runs_command_and_records_history_and_events(tmp_path):
    events = []
    session = TerminalSession(workspace_root=tmp_path, event_handler=events.append)

    result = session.run(f"{sys.executable} --version", reason="check python")

    assert result.status is CommandStatus.SUCCESS
    assert session.last_result is result
    assert session.current_cwd == tmp_path
    assert len(session.history) == 1
    assert session.history[0].command == f"{sys.executable} --version"
    assert session.history[0].status is CommandStatus.SUCCESS
    assert [event.type for event in events] == ["command_started", "stdout_chunk", "command_finished"]


def test_terminal_session_supports_cwd_clear_and_rerun(tmp_path):
    events = []
    (tmp_path / "pkg").mkdir()
    session = TerminalSession(workspace_root=tmp_path, event_handler=events.append)

    session.set_cwd("pkg")
    first = session.run(f"{sys.executable} --version", reason="first")
    rerun = session.rerun()

    assert first.status is CommandStatus.SUCCESS
    assert rerun.status is CommandStatus.SUCCESS
    assert first.cwd == tmp_path / "pkg"
    assert rerun.cwd == tmp_path / "pkg"
    assert len(session.history) == 2
    assert session.history[1].command == session.history[0].command

    session.clear()

    assert session.transcript == []


def test_terminal_session_can_cancel_running_command(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    session = TerminalSession(workspace_root=tmp_path)

    command_id = session.start(
        f"{sys.executable} sleep.py",
        reason="cancel smoke",
        timeout_seconds=30,
        approval_granted=True,
    )

    assert command_id
    assert session.cancel() is True
    result = session.wait(timeout_seconds=5)

    assert result is not None
    assert result.status is CommandStatus.CANCELLED
    assert session.is_running is False
