from __future__ import annotations

import sqlite3

import pytest

from runtime.history.store import HistoryFacade, HistoryStore
from runtime.storage.context_store import ContextSQLiteStore
from runtime.storage.search import rebuild_fts_index
from runtime.storage.sqlite_store import connect, initialize_sqlite_store


def test_sqlite_store_initializes_database_with_required_pragmas(tmp_path):
    result = initialize_sqlite_store(tmp_path)

    assert result.db_path == tmp_path / ".lucode" / "lucode.db"
    assert result.schema_version == "context_store.v1"
    assert result.created is True

    with connect(tmp_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        schema_version = connection.execute(
            "select value from schema_meta where key = 'schema_version'"
        ).fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout >= 3000
    assert schema_version == "context_store.v1"


def test_sqlite_connect_context_manager_closes_connection(tmp_path):
    with connect(tmp_path) as connection:
        connection.execute("select 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("select 1").fetchone()


def test_sqlite_store_initialization_is_idempotent(tmp_path):
    first = initialize_sqlite_store(tmp_path)
    second = initialize_sqlite_store(tmp_path)

    assert first.db_path == second.db_path
    assert first.schema_version == second.schema_version == "context_store.v1"
    assert first.created is True
    assert second.created is False


def test_context_sqlite_store_persists_minimal_context_records(tmp_path):
    store = ContextSQLiteStore(tmp_path)

    store.save_session(
        {
            "session_id": "session-1",
            "title": "SQLite session",
            "created_at": "2026-07-08T01:00:00Z",
            "updated_at": "2026-07-08T01:00:00Z",
            "source": "jsonl",
        }
    )
    message_id = store.save_message(
        {
            "session_id": "session-1",
            "role": "user",
            "content": "hello",
            "created_at": "2026-07-08T01:00:01Z",
            "metadata": {"run_id": "run-1"},
            "source": "jsonl",
        }
    )
    store.save_context_summary(
        {
            "session_id": "session-1",
            "summary": "summary text",
            "created_at": "2026-07-08T01:00:02Z",
            "source": "jsonl",
        }
    )
    store.save_context_ledger_result(
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "mode": "normal",
            "applied": False,
            "triggered": False,
            "estimated_input_tokens": 120,
            "context_window_tokens": 8192,
            "metadata": {"recent_turn_count": 1},
            "created_at": "2026-07-08T01:00:03Z",
        }
    )
    tool_id = store.save_tool_dehydrated_result(
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "tool": "browser_get_page_summary",
            "summary": "title and url",
            "evidence_ref": "browser:summary:1",
            "raw_artifact_ref": "artifact:browser:1",
            "metadata": {"omitted_fields": ["dom"]},
            "created_at": "2026-07-08T01:00:04Z",
        }
    )
    evidence_id = store.save_evidence_ref(
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "evidence_ref": "browser:summary:1",
            "artifact_ref": "artifact:browser:1",
            "source_type": "browser",
            "metadata": {"accepted": True},
            "created_at": "2026-07-08T01:00:05Z",
        }
    )

    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        session = connection.execute("select title from sessions where session_id = ?", ("session-1",)).fetchone()
        message = connection.execute("select role, content from messages where message_id = ?", (message_id,)).fetchone()
        summary = connection.execute(
            "select summary from context_summaries where session_id = ?", ("session-1",)
        ).fetchone()
        ledger = connection.execute(
            "select mode, applied, context_window_tokens from context_ledger_results where run_id = ?",
            ("run-1",),
        ).fetchone()
        tool = connection.execute(
            "select tool, evidence_ref from tool_dehydrated_results where result_id = ?",
            (tool_id,),
        ).fetchone()
        evidence = connection.execute(
            "select evidence_ref, artifact_ref from evidence_refs where ref_id = ?",
            (evidence_id,),
        ).fetchone()

    assert session == ("SQLite session",)
    assert message == ("user", "hello")
    assert summary == ("summary text",)
    assert ledger == ("normal", 0, 8192)
    assert tool == ("browser_get_page_summary", "browser:summary:1")
    assert evidence == ("browser:summary:1", "artifact:browser:1")


def test_history_delete_removes_sqlite_session_bundle_and_fts_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    session_id = "session-delete-bundle"
    history = HistoryStore(tmp_path)
    history.append_event(session_id, {"type": "session_metadata", "title": "Delete bundle"})
    history.append_message(session_id, "user", "private question", metadata={"run_id": "run-delete"})
    history.append_message(
        session_id,
        "assistant",
        "private answer",
        metadata={"run_id": "run-delete", "run_context_summary": "private summary"},
    )
    sqlite_store = ContextSQLiteStore(tmp_path)
    sqlite_store.save_context_ledger_result(
        {
            "session_id": session_id,
            "run_id": "run-delete",
            "mode": "normal",
            "created_at": "2026-07-10T01:00:00Z",
        }
    )
    sqlite_store.save_tool_dehydrated_result(
        {
            "session_id": session_id,
            "run_id": "run-delete",
            "tool": "terminal",
            "summary": "private tool result",
            "created_at": "2026-07-10T01:00:01Z",
        }
    )
    sqlite_store.save_evidence_ref(
        {
            "session_id": session_id,
            "run_id": "run-delete",
            "evidence_ref": "evidence:delete",
            "created_at": "2026-07-10T01:00:02Z",
        }
    )
    assert rebuild_fts_index(tmp_path) is True

    result = HistoryFacade(tmp_path).delete(session_id)

    assert result.deleted is True
    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        for table in (
            "sessions",
            "messages",
            "context_summaries",
            "context_ledger_results",
            "tool_dehydrated_results",
            "evidence_refs",
            "sessions_fts",
            "messages_fts",
            "summaries_fts",
        ):
            count = connection.execute(
                f"select count(*) from {table} where session_id = ?",
                (session_id,),
            ).fetchone()[0]
            assert count == 0, table
