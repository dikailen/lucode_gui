from __future__ import annotations

import json

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.journal import RunJournal
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionRequest, RunExecutionResult
from runtime.server.run_manager import RuntimeRunManager
from runtime.storage.sqlite_store import connect


TOKEN = "recovery-full-chain-token"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _assistant_messages(manager: RuntimeRunManager, session_id: str) -> list[dict[str, object]]:
    return [
        event
        for event in manager._history_facade.history_store.load_events(session_id)
        if event.get("role") == "assistant"
    ]


def _checkpoint_state(*, final_output: str = "") -> dict[str, object]:
    state: dict[str, object] = {
        "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
        "route_type": "single_agent",
        "reason": "recovery full-chain test",
        "tasks": [],
        "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
        "blocked_items": [],
    }
    if final_output:
        state["final_output"] = final_output
    return state


def test_restart_repairs_a_final_checkpoint_once_without_reexecuting(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("final checkpoint recovery")["session_id"]
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-final", session_id=session_id, status="running")
    journal.write_checkpoint(
        run_id="run-final",
        kind="final.ready",
        state=_checkpoint_state(final_output="durably recovered answer"),
        compatibility={"schema": "recovery-full-chain-test"},
    )

    first_restart = RuntimeRunManager(tmp_path)
    second_restart = RuntimeRunManager(tmp_path)

    first_messages = _assistant_messages(first_restart, session_id)
    second_messages = _assistant_messages(second_restart, session_id)
    assert [message["content"] for message in first_messages] == ["durably recovered answer"]
    assert [message["content"] for message in second_messages] == ["durably recovered answer"]
    recovered_run = journal.recovery_run("run-final")
    assert recovered_run is not None
    assert recovered_run["session_id"] == session_id
    assert recovered_run["status"] == "completed"
    assert recovered_run["recovery_state"] == "none"
    assert recovered_run["interrupted_at"]


def test_corrupt_checkpoint_degrades_recovery_but_preserves_a_normal_new_request(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("corrupt checkpoint recovery")["session_id"]
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-corrupt-checkpoint", session_id=session_id, status="running")
    checkpoint = journal.write_checkpoint(
        run_id="run-corrupt-checkpoint",
        kind="plan.accepted",
        state=_checkpoint_state(),
        compatibility={"schema": "recovery-full-chain-test"},
    )
    journal.mark_unleased_running_runs_interrupted(now="2026-07-15T00:00:00.000Z")
    with connect(tmp_path) as connection:
        connection.execute(
            "update run_checkpoints set state_json = ? where checkpoint_id = ?",
            (json.dumps({"tasks": ["tampered"]}), checkpoint.checkpoint_id),
        )

    captured: list[RunExecutionRequest] = []

    async def runner(request: RunExecutionRequest) -> RunExecutionResult:
        captured.append(request)
        return RunExecutionResult(final_output="new request remains available")

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    client = TestClient(app)
    response = client.post(
        "/api/runs",
        headers=_headers(),
        json={"session_id": session_id, "input": "start a different task"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        while websocket.receive_json()["type"] not in {"run.completed", "run.failed", "run.cancelled"}:
            pass

    manager = app.state.lucode_run_manager
    assert len(captured) == 1
    assert captured[0].recovery_envelope == {}
    assert "recovery_claim:" + session_id in manager._journal_degraded_runs
    assert "checksum" in manager._journal_degraded_runs["recovery_claim:" + session_id]
    assert journal.recovery_run("run-corrupt-checkpoint")["recovery_state"] == "interrupted"


def test_restart_marks_dispatched_browser_submit_unknown_without_automatic_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("browser submit recovery")["session_id"]
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-browser-submit", session_id=session_id, status="running")
    journal.prepare_tool_invocation(
        invocation_id="submit-before-crash",
        run_id="run-browser-submit",
        task_id="browser-task",
        attempt=1,
        tool_name="desktop_browser.browser_submit_form",
        arguments_hash="submit-form-hash",
        side_effect_class="non_idempotent",
    )
    journal.mark_tool_invocation_dispatched("submit-before-crash")
    executor_calls: list[RunExecutionRequest] = []

    async def runner(request: RunExecutionRequest) -> RunExecutionResult:
        executor_calls.append(request)
        return RunExecutionResult(final_output="must not run during startup")

    restarted = RuntimeRunManager(tmp_path, run_executor=runner)

    assert executor_calls == []
    assert journal.tool_invocation("submit-before-crash")["status"] == "unknown"
    assert restarted.list_recovery_runs()["runs"] == [
        {
            "run_id": "run-browser-submit",
            "session_id": session_id,
            "action": "replan",
            "reason_code": "interrupted_runtime",
            "requires_user_action": True,
        }
    ]
