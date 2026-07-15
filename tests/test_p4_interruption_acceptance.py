from __future__ import annotations

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.journal import RunJournal
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionRequest, RunExecutionResult
from runtime.server.run_manager import RuntimeRunManager


TOKEN = "p4-interruption-acceptance-token"
NOW = "2026-07-14T10:00:00.000Z"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_p4_runtime_restart_classifies_interruption_without_calling_the_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")
    seed_manager = RuntimeRunManager(tmp_path)
    session_id = seed_manager.create_session("P4 interruption acceptance")["session_id"]
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id="run_interrupted",
        session_id=session_id,
        status="running",
        created_at=NOW,
    )
    journal.record_pending_approval(
        approval_id="approval_before_restart",
        run_id="run_interrupted",
        action_digest="digest_before_restart",
        requested_at=NOW,
    )
    executor_calls: list[RunExecutionRequest] = []

    async def runner(request: RunExecutionRequest) -> RunExecutionResult:
        executor_calls.append(request)
        return RunExecutionResult(final_output="unexpected execution")

    client = TestClient(
        create_app(
            workspace_root=tmp_path,
            runtime_token=TOKEN,
            run_executor=runner,
        )
    )

    recovery_response = client.get("/api/runs/recovery", headers=_auth_headers())
    health_response = client.get("/api/health", headers=_auth_headers())

    assert recovery_response.status_code == 200
    assert recovery_response.json()["runs"] == [
        {
            "run_id": "run_interrupted",
            "session_id": session_id,
            "action": "replan",
            "reason_code": "interrupted_runtime",
            "requires_user_action": True,
        }
    ]
    assert health_response.status_code == 200
    assert health_response.json()["run_recovery"] == {
        "mode": "reconnect",
        "journal_enabled": True,
        "degraded": False,
        "degraded_run_count": 0,
    }
    assert journal.recovery_run("run_interrupted")["recovery_state"] == "interrupted"
    assert journal.recovery_run("run_interrupted")["status"] == "running"
    assert journal.approval_status("approval_before_restart") == "expired"
    assert executor_calls == []


def test_p4_interruption_classification_is_idempotent(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id="run_once",
        session_id="session_once",
        status="running",
        created_at=NOW,
    )
    coordinator = RecoveryCoordinator(journal, now=lambda: NOW)

    first_scan = coordinator.scan_startup()
    second_scan = coordinator.scan_startup()

    assert [item.run_id for item in first_scan] == ["run_once"]
    assert second_scan == []
    assert journal.recovery_run("run_once")["recovery_state"] == "interrupted"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "abandoned"])
def test_p4_startup_scan_never_reclassifies_terminal_runs(tmp_path, status):
    journal = RunJournal(tmp_path)
    run_id = f"run_{status}"
    journal.create_run(
        run_id=run_id,
        session_id=f"session_{status}",
        status=status,
        created_at=NOW,
    )

    decisions = RecoveryCoordinator(journal, now=lambda: NOW).scan_startup()

    assert decisions == []
    assert journal.recovery_run(run_id)["status"] == status
    assert journal.recovery_run(run_id)["recovery_state"] == "none"


def test_p4_observe_mode_records_runs_but_does_not_classify_restart_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id="run_observe",
        session_id="session_observe",
        status="running",
        created_at=NOW,
    )

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token=TOKEN))
    response = client.get("/api/runs/recovery", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["runs"] == []
    assert journal.recovery_run("run_observe")["recovery_state"] == "none"
