from __future__ import annotations

from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.journal import RunJournal
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionResult
from runtime.server.run_manager import RuntimeRunManager
from runtime.storage.sqlite_store import connect

pytest = __import__("pytest")
pytest.importorskip("starlette")
from starlette.testclient import TestClient


NOW = "2026-07-14T10:00:00.000Z"


def test_startup_scan_marks_unleased_running_run_interrupted_without_changing_business_status(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id="run_interrupted",
        session_id="session_1",
        status="running",
        created_at="2026-07-14T09:00:00.000Z",
    )

    decisions = RecoveryCoordinator(journal, now=lambda: NOW).scan_startup()

    assert [decision.to_dict() for decision in decisions] == [
        {
            "run_id": "run_interrupted",
            "session_id": "session_1",
            "action": "replan",
            "reason_code": "interrupted_runtime",
            "requires_user_action": True,
        }
    ]
    assert journal.recovery_run("run_interrupted") == {
        "run_id": "run_interrupted",
        "session_id": "session_1",
        "status": "running",
        "recovery_state": "interrupted",
        "interrupted_at": NOW,
    }


def test_startup_scan_does_not_mark_run_interrupted_while_another_valid_lease_is_held(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_leased", session_id="session_1", status="running", created_at=NOW)
    assert journal.acquire_recovery_lease(
        run_id="run_leased",
        owner_id="runtime_other",
        now=NOW,
        expires_at="2026-07-14T10:05:00.000Z",
    )

    decisions = RecoveryCoordinator(journal, now=lambda: NOW).scan_startup()

    assert decisions == []
    assert journal.recovery_run("run_leased")["recovery_state"] == "none"


def test_runtime_reconnect_startup_exposes_interrupted_run_as_a_read_only_recovery_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_restart", session_id="session_1", status="running", created_at=NOW)

    app = create_app(workspace_root=tmp_path, runtime_token="test-token")
    client = TestClient(app)
    response = client.get("/api/runs/recovery", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["runs"] == [
        {
            "run_id": "run_restart",
            "session_id": "session_1",
            "action": "replan",
            "reason_code": "interrupted_runtime",
            "requires_user_action": True,
        }
    ]
    assert journal.recovery_run("run_restart")["status"] == "running"


def test_startup_scan_expires_pending_approval_without_reusing_its_decision(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_approval", session_id="session_1", status="running", created_at=NOW)
    journal.record_pending_approval(
        approval_id="approval_old",
        run_id="run_approval",
        action_digest="digest_1",
        requested_at=NOW,
    )

    RecoveryCoordinator(journal, now=lambda: NOW).scan_startup()

    assert journal.approval_status("approval_old") == "expired"


def test_runtime_approval_request_is_durably_expired_by_a_subsequent_runtime_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")

    async def approval_runner(request):
        await request.approval_session.request_tool_approval("Approve this test action?", tool_name="test.write")
        return RunExecutionResult(final_output="unexpected approval resolution")

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token="test-token", run_executor=approval_runner))
    session_id = client.post(
        "/api/sessions",
        headers={"Authorization": "Bearer test-token"},
        json={"title": "approval restart"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers={"Authorization": "Bearer test-token"},
        json={"session_id": session_id, "input": "wait for approval"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token=test-token") as websocket:
        websocket.receive_json()
        approval_event = websocket.receive_json()

    approval_id = approval_event["payload"]["approval_id"]
    assert RunJournal(tmp_path).approval_status(approval_id) == "requested"

    RuntimeRunManager(tmp_path)

    assert RunJournal(tmp_path).approval_status(approval_id) == "expired"
    client.post(f"/api/runs/{run_id}/stop", headers={"Authorization": "Bearer test-token"})


def test_runtime_approval_event_persists_its_tool_invocation_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")

    async def approval_runner(request):
        await request.approval_session.request_tool_approval(
            "Approve this test action?",
            tool_name="workspace_edit.write_file",
            arguments='{"path":"runtime/a.py","content":"x"}',
            invocation_id="invocation_write_file",
        )
        return RunExecutionResult(final_output="unexpected approval resolution")

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token="test-token", run_executor=approval_runner))
    session_id = client.post(
        "/api/sessions",
        headers={"Authorization": "Bearer test-token"},
        json={"title": "approval invocation journal"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers={"Authorization": "Bearer test-token"},
        json={"session_id": session_id, "input": "wait for approval"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token=test-token") as websocket:
        websocket.receive_json()
        approval_event = websocket.receive_json()

    approval_id = approval_event["payload"]["approval_id"]
    assert approval_event["payload"]["invocation_id"] == "invocation_write_file"
    with connect(tmp_path) as connection:
        row = connection.execute(
            "select run_id, invocation_id, status from run_approvals where approval_id = ?",
            (approval_id,),
        ).fetchone()
    assert tuple(row) == (run_id, "invocation_write_file", "requested")
    client.post(f"/api/runs/{run_id}/stop", headers={"Authorization": "Bearer test-token"})


def test_resume_safe_runtime_passes_a_tool_lifecycle_sink_to_the_execution_request(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    captured = {}

    async def runner(request):
        captured["request"] = request
        return RunExecutionResult(final_output="done")

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token="test-token", run_executor=runner))
    session_id = client.post(
        "/api/sessions",
        headers={"Authorization": "Bearer test-token"},
        json={"title": "lifecycle sink runtime"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers={"Authorization": "Bearer test-token"},
        json={"session_id": session_id, "input": "finish safely"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token=test-token") as websocket:
        events = [websocket.receive_json(), websocket.receive_json()]

    assert events[-1]["type"] == "run.completed"
    sink = captured["request"].tool_lifecycle_sink
    assert sink is not None
    assert sink.run_id == run_id


def test_recovery_abandon_marks_interrupted_run_without_executing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_abandon", session_id="session_1", status="running", created_at=NOW)
    client = TestClient(create_app(workspace_root=tmp_path, runtime_token="test-token"))

    response = client.post("/api/runs/run_abandon/abandon", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {"abandoned": True, "run_id": "run_abandon"}
    assert journal.recovery_run("run_abandon")["status"] == "abandoned"
