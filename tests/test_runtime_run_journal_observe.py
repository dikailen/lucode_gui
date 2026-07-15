from __future__ import annotations

import asyncio
import sqlite3

import pytest

from runtime.recovery.journal import RunJournal
from runtime.server.execution_bridge import RunExecutionResult
from runtime.server.run_manager import RuntimeRunManager


async def _completed_runner(request):
    request.event_bus.emit("PlanningStarted", "planning", mode="auto", status="running")
    return RunExecutionResult(final_output="done")


async def _wait_for_completion(manager: RuntimeRunManager, run_id: str) -> None:
    while run_id in manager._run_tasks:
        await asyncio.sleep(0)


def test_observe_mode_mirrors_run_lifecycle_and_execution_events_to_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = manager.create_session("observe")

    async def scenario():
        run = manager.start_run(session_id=session["session_id"], user_input="hello")
        await _wait_for_completion(manager, run["run_id"])
        return run

    run = asyncio.run(scenario())

    streamed = manager.events.snapshot(run["run_id"])
    journaled = RunJournal(tmp_path).events_for_run(run["run_id"])

    assert [event.type for event in streamed] == [event.event_type for event in journaled]
    assert [event.seq for event in journaled] == list(range(1, len(journaled) + 1))
    assert manager._runs[run["run_id"]].status == "completed"
    assert RunJournal(tmp_path).find_run_by_client_request_id("") is None
    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        status = connection.execute(
            "select status from agent_runs where run_id = ?", (run["run_id"],)
        ).fetchone()[0]
    assert status == "completed"


def test_observe_journal_failure_does_not_fail_the_run(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = manager.create_session("observe degraded")

    class BrokenJournal:
        def create_run(self, **_kwargs):
            raise OSError("disk locked")

        def append_event(self, **_kwargs):
            raise OSError("disk locked")

    manager._run_journal = BrokenJournal()
    async def scenario():
        run = manager.start_run(session_id=session["session_id"], user_input="hello")
        await _wait_for_completion(manager, run["run_id"])
        return run

    run = asyncio.run(scenario())

    assert manager._runs[run["run_id"]].status == "completed"
    assert manager.events.snapshot(run["run_id"])[-1].type == "run.completed"
    assert manager._journal_degraded_runs[run["run_id"]]
    assert manager.health()["run_recovery"]["degraded"] is True


def test_resume_safe_recovery_claim_failure_allows_a_new_model_only_run(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    captured = {}

    async def runner(request):
        captured["recovery_envelope"] = request.recovery_envelope
        return RunExecutionResult(final_output="done")

    manager = RuntimeRunManager(tmp_path, run_executor=runner)

    class BrokenJournal:
        def claim_latest_recovery_run(self, **_kwargs):
            raise OSError("database locked")

        def create_run(self, **_kwargs):
            raise OSError("database locked")

        def append_event(self, **_kwargs):
            raise OSError("database locked")

        def update_run_status(self, **_kwargs):
            raise OSError("database locked")

    manager._run_journal = BrokenJournal()
    session = manager.create_session("resume-safe degraded")

    async def scenario():
        run = manager.start_run(session_id=session["session_id"], user_input="hello")
        await _wait_for_completion(manager, run["run_id"])
        return run

    run = asyncio.run(scenario())

    assert manager._runs[run["run_id"]].status == "completed"
    assert captured["recovery_envelope"] == {}
    assert manager.health()["run_recovery"]["degraded"] is True


def test_client_request_id_reuses_the_same_run_without_duplicate_user_message(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = manager.create_session("idempotent")

    async def scenario():
        first = manager.start_run(
            session_id=session["session_id"],
            user_input="hello",
            client_request_id="request-1",
        )
        second = manager.start_run(
            session_id=session["session_id"],
            user_input="hello",
            client_request_id="request-1",
        )
        await _wait_for_completion(manager, first["run_id"])
        return first, second

    first, second = asyncio.run(scenario())

    assert second["run_id"] == first["run_id"]
    assert len(manager._history_facade.load_messages(session["session_id"])) == 2
    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        stored_request_id = connection.execute(
            "select client_request_id from agent_runs where run_id = ?",
            (first["run_id"],),
        ).fetchone()[0]
    assert stored_request_id == "request-1"


def test_client_request_id_rejects_a_different_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = manager.create_session("idempotent conflict")

    async def scenario():
        manager.start_run(
            session_id=session["session_id"],
            user_input="first",
            client_request_id="request-1",
        )
        with pytest.raises(ValueError, match="client_request_id"):
            manager.start_run(
                session_id=session["session_id"],
                user_input="different",
                client_request_id="request-1",
            )

    asyncio.run(scenario())


def test_client_request_id_survives_manager_restart_without_duplicate_user_message(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    first_manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = first_manager.create_session("restart idempotency")

    async def first_run():
        run = first_manager.start_run(
            session_id=session["session_id"],
            user_input="hello",
            client_request_id="request-restart",
        )
        await _wait_for_completion(first_manager, run["run_id"])
        return run

    first = asyncio.run(first_run())
    second_manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)

    async def retry_after_restart():
        return second_manager.start_run(
            session_id=session["session_id"],
            user_input="hello",
            client_request_id="request-restart",
        )

    second = asyncio.run(retry_after_restart())

    assert second["run_id"] == first["run_id"]
    assert len(second_manager._history_facade.load_messages(session["session_id"])) == 2


def test_client_request_id_rejects_a_different_input_after_manager_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    first_manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)
    session = first_manager.create_session("restart conflict")

    async def first_run():
        run = first_manager.start_run(
            session_id=session["session_id"],
            user_input="first",
            client_request_id="request-restart-conflict",
        )
        await _wait_for_completion(first_manager, run["run_id"])

    asyncio.run(first_run())
    second_manager = RuntimeRunManager(tmp_path, run_executor=_completed_runner)

    async def retry_with_different_input():
        with pytest.raises(ValueError, match="client_request_id"):
            second_manager.start_run(
                session_id=session["session_id"],
                user_input="different",
                client_request_id="request-restart-conflict",
            )

    asyncio.run(retry_with_different_input())
