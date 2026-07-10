from __future__ import annotations

import sqlite3

from runtime.history.store import HistoryFacade, HistoryStore
from runtime.storage.context_store import ContextSQLiteStore
from runtime.storage.freshness import jsonl_source_fingerprint
from runtime.storage.sqlite_store import connect


def test_rebuild_sqlite_from_jsonl_indexes_old_history_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("LUCODE_CONTEXT_SQLITE", raising=False)
    store = HistoryStore(tmp_path)
    session_id = "old-jsonl-session"

    store.append_event(
        session_id,
        {
            "type": "session_metadata",
            "title": "Old JSONL",
            "timestamp": "2026-07-08T01:00:00Z",
        },
    )
    store.append_message(session_id, "user", "hello from jsonl", metadata={"run_id": "run-1"})
    store.append_message(
        session_id,
        "assistant",
        "answer from jsonl",
        metadata={
            "run_id": "run-1",
            "run_context_summary": "old context summary",
        },
    )

    from runtime.storage.rebuild_index import rebuild_sqlite_from_jsonl

    result = rebuild_sqlite_from_jsonl(tmp_path)

    assert result.sessions == 1
    assert result.messages == 2
    assert result.summaries == 1
    assert result.errors == []

    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        session = connection.execute(
            "select title from sessions where session_id = ?",
            (session_id,),
        ).fetchone()
        messages = connection.execute(
            "select role, content from messages where session_id = ? order by rowid",
            (session_id,),
        ).fetchall()
        summaries = connection.execute(
            "select summary from context_summaries where session_id = ? order by rowid",
            (session_id,),
        ).fetchall()

    assert session == ("Old JSONL",)
    assert messages == [("user", "hello from jsonl"), ("assistant", "answer from jsonl")]
    assert summaries == [("old context summary",)]

    second = rebuild_sqlite_from_jsonl(tmp_path)

    assert second.errors == []
    with sqlite3.connect(tmp_path / ".lucode" / "lucode.db") as connection:
        message_count = connection.execute(
            "select count(*) from messages where session_id = ?",
            (session_id,),
        ).fetchone()[0]
        summary_count = connection.execute(
            "select count(*) from context_summaries where session_id = ?",
            (session_id,),
        ).fetchone()[0]

    assert message_count == 2
    assert summary_count == 1


def test_history_facade_read_through_prefers_sqlite_but_falls_back_to_jsonl(tmp_path, monkeypatch):
    monkeypatch.delenv("LUCODE_CONTEXT_SQLITE", raising=False)
    store = HistoryStore(tmp_path)
    session_id = "read-through-session"

    store.append_event(session_id, {"type": "session_metadata", "title": "Read Through"})
    store.append_message(session_id, "user", "read from jsonl", metadata={"run_id": "run-1"})
    store.append_message(
        session_id,
        "assistant",
        "read from sqlite",
        metadata={
            "run_id": "run-1",
            "run_context_summary": "sqlite summary",
        },
    )

    from runtime.storage.rebuild_index import rebuild_sqlite_from_jsonl

    rebuild_sqlite_from_jsonl(tmp_path)
    with connect(tmp_path) as connection:
        connection.execute(
            "update messages set content = ? where session_id = ? and role = 'assistant'",
            ("read through sqlite cache", session_id),
        )

    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "read_through")
    facade = HistoryFacade(tmp_path)

    items = facade.list_items(limit=5)
    messages = facade.load_messages(session_id)
    summary = facade.load_context_summary(session_id)

    assert [item.session_id for item in items] == [session_id]
    assert items[0].storage_kind == "history_sqlite"
    assert messages[-1]["content"] == "read through sqlite cache"
    assert summary == "sqlite summary"

    class FailingContextSQLiteStore:
        def __init__(self, workspace_root):
            pass

        def list_sessions(self, limit=20):
            raise sqlite3.OperationalError("database is locked")

        def load_messages(self, session_id, limit=None):
            raise sqlite3.OperationalError("database is locked")

        def load_context_summary(self, session_id, max_chars=2400):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("runtime.history.store.ContextSQLiteStore", FailingContextSQLiteStore)
    fallback = HistoryFacade(tmp_path)

    assert [item.session_id for item in fallback.list_items(limit=5)] == [session_id]
    assert fallback.load_messages(session_id)[-1]["content"] == "read from sqlite"
    assert fallback.load_context_summary(session_id) == "sqlite summary"


def test_history_facade_read_through_rejects_stale_sqlite_index(tmp_path, monkeypatch):
    monkeypatch.delenv("LUCODE_CONTEXT_SQLITE", raising=False)
    store = HistoryStore(tmp_path)
    session_id = "stale-read-through-session"

    store.append_event(session_id, {"type": "session_metadata", "title": "Stale Read Through"})
    store.append_message(session_id, "user", "first user")
    store.append_message(
        session_id,
        "assistant",
        "first assistant",
        metadata={"run_context_summary": "old sqlite summary"},
    )

    from runtime.storage.rebuild_index import rebuild_sqlite_from_jsonl

    rebuild_sqlite_from_jsonl(tmp_path)
    store.append_message(session_id, "user", "second user")
    store.append_message(
        session_id,
        "assistant",
        "second assistant",
        metadata={"run_context_summary": "new jsonl summary"},
    )

    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "read_through")
    facade = HistoryFacade(tmp_path)

    items = facade.list_items(limit=5)
    messages = facade.load_messages(session_id)
    summary = facade.load_context_summary(session_id)

    assert items[0].session_id == session_id
    assert items[0].storage_kind == "history"
    assert items[0].message_count == 4
    assert [message["content"] for message in messages] == [
        "first user",
        "first assistant",
        "second user",
        "second assistant",
    ]
    assert summary != "old sqlite summary"
    assert "old sqlite summary" in summary
    assert "new jsonl summary" in summary


def test_history_facade_read_through_rejects_same_count_jsonl_content_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("LUCODE_CONTEXT_SQLITE", raising=False)
    store = HistoryStore(tmp_path)
    session_id = "same-count-stale-session"
    store.append_event(session_id, {"type": "session_metadata", "title": "Same Count"})
    store.append_message(session_id, "user", "original user content")
    store.append_message(session_id, "assistant", "original assistant content")

    from runtime.storage.rebuild_index import rebuild_sqlite_from_jsonl

    rebuild_sqlite_from_jsonl(tmp_path)
    path = store._path_for(session_id)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("original assistant content", "changed assistant content"), encoding="utf-8")

    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "read_through")
    facade = HistoryFacade(tmp_path)

    items = facade.list_items(limit=5)
    messages = facade.load_messages(session_id)

    assert items[0].storage_kind == "history"
    assert [message["content"] for message in messages] == [
        "original user content",
        "changed assistant content",
    ]


def test_existing_sqlite_database_adds_source_fingerprint_column(tmp_path):
    db_path = tmp_path / ".lucode" / "lucode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            create table sessions (
              session_id text primary key,
              title text not null default '',
              created_at text not null,
              updated_at text not null,
              source text not null default 'sqlite',
              schema_version text not null default 'context_store.v1',
              metadata_json text not null default '{}'
            )
            """
        )

    ContextSQLiteStore(tmp_path)

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(sessions)").fetchall()}
    assert "source_fingerprint" in columns


def test_jsonl_source_fingerprint_uses_file_metadata_without_reading_full_content(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"message","content":"large history"}\n', encoding="utf-8")

    def fail_open(*_args, **_kwargs):
        raise AssertionError("freshness fingerprint must not read the JSONL body")

    monkeypatch.setattr(type(path), "open", fail_open)

    fingerprint = jsonl_source_fingerprint(path)

    assert fingerprint.startswith("stat:v1:")
