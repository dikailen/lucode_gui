from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from runtime.history.store import HistoryStore
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionResult


TOKEN = "test-runtime-token"


def _auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _context_runner(request):
    return RunExecutionResult(
        final_output="runtime sqlite answer",
        metadata={
            "context_ledger": {
                "mode": "normal",
                "applied": False,
                "triggered": False,
                "estimated_input_tokens": 55,
                "context_window_tokens": 8192,
            },
            "tool_dehydration": {"count": 0, "items": []},
        },
    )


def test_history_store_dual_writes_jsonl_and_sqlite_context_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    store = HistoryStore(tmp_path)
    session_id = store.start_session("SQLite dual write")

    store.append_event(session_id, {"type": "session_metadata", "title": "SQLite dual write"})
    store.append_message(session_id, "user", "hello", metadata={"run_id": "run-1"})
    store.append_message(
        session_id,
        "assistant",
        "answer",
        metadata={
            "run_id": "run-1",
            "run_context_summary": "Used context ledger",
            "context_ledger": {
                "mode": "normal",
                "applied": False,
                "triggered": False,
                "estimated_input_tokens": 123,
                "context_window_tokens": 8192,
                "recent_turn_count": 1,
            },
            "tool_dehydration": {
                "items": [
                    {
                        "summary": "browser summary",
                        "evidence_ref": "browser:summary:1",
                        "raw_artifact_ref": "artifact:browser:1",
                        "key_fields": {"title": "Example"},
                    }
                ]
            },
        },
    )

    jsonl_events = store.load_events(session_id)
    assert [event["type"] for event in jsonl_events] == ["session_metadata", "message", "message"]

    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        session = connection.execute("select title from sessions where session_id = ?", (session_id,)).fetchone()
        messages = connection.execute(
            "select role, content from messages where session_id = ? order by rowid",
            (session_id,),
        ).fetchall()
        summary = connection.execute(
            "select summary from context_summaries where session_id = ?",
            (session_id,),
        ).fetchone()
        ledger = connection.execute(
            "select mode, applied, context_window_tokens from context_ledger_results where session_id = ?",
            (session_id,),
        ).fetchone()
        tool = connection.execute(
            "select summary, evidence_ref, raw_artifact_ref from tool_dehydrated_results where session_id = ?",
            (session_id,),
        ).fetchone()
        evidence = connection.execute(
            "select evidence_ref, artifact_ref, source_type from evidence_refs where session_id = ?",
            (session_id,),
        ).fetchone()

    assert session == ("SQLite dual write",)
    assert messages == [("user", "hello"), ("assistant", "answer")]
    assert summary == ("Used context ledger",)
    assert ledger == ("normal", 0, 8192)
    assert tool == ("browser summary", "browser:summary:1", "artifact:browser:1")
    assert evidence == ("browser:summary:1", "artifact:browser:1", "tool")


def test_history_store_keeps_jsonl_authoritative_when_sqlite_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")

    class FailingContextSQLiteStore:
        def __init__(self, workspace_root):
            pass

        def save_session(self, session):
            raise sqlite3.OperationalError("database is locked")

        def save_message(self, message):
            raise sqlite3.OperationalError("database is locked")

        def save_context_summary(self, summary):
            raise sqlite3.OperationalError("database is locked")

        def save_context_ledger_result(self, result):
            raise sqlite3.OperationalError("database is locked")

        def save_tool_dehydrated_result(self, result):
            raise sqlite3.OperationalError("database is locked")

        def save_evidence_ref(self, evidence):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("runtime.history.store.ContextSQLiteStore", FailingContextSQLiteStore)
    store = HistoryStore(tmp_path)
    session_id = store.start_session("SQLite failure")

    store.append_event(session_id, {"type": "session_metadata", "title": "SQLite failure"})
    store.append_message(session_id, "user", "still writes jsonl", metadata={"run_id": "run-1"})

    messages = store.load_messages(session_id)

    assert messages == [{"role": "user", "content": "still writes jsonl"}]
    assert (tmp_path / ".lucode" / "history" / "sessions" / f"{session_id}.jsonl").is_file()


def test_runtime_server_dual_write_path_keeps_chat_chain_available(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=_context_runner)
    client = TestClient(app)

    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "Runtime SQLite"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "hello runtime"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(2)]

    messages_response = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers())

    assert events[-1]["type"] == "run.completed"
    assert messages_response.status_code == 200
    assert [item["role"] for item in messages_response.json()["messages"]] == ["user", "assistant"]
    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        messages = connection.execute(
            "select role, content from messages where session_id = ? order by rowid",
            (session_id,),
        ).fetchall()
        ledger = connection.execute(
            "select mode, context_window_tokens from context_ledger_results where session_id = ?",
            (session_id,),
        ).fetchone()

    assert messages == [("user", "hello runtime"), ("assistant", "runtime sqlite answer")]
    assert ledger == ("normal", 8192)
