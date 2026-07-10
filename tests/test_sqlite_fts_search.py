from __future__ import annotations

import runtime.storage.search as search_module
from runtime.history.store import HistoryFacade, HistoryStore
from runtime.storage.search import rebuild_fts_index, search_history


def test_search_history_matches_session_title_messages_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    store = HistoryStore(tmp_path)
    session_id = "fts-search-session"

    store.append_event(
        session_id,
        {
            "type": "session_metadata",
            "title": "Invoice audit workflow",
            "timestamp": "2026-07-09T01:00:00Z",
        },
    )
    store.append_message(
        session_id,
        "user",
        "How do I reconcile the refund ledger?",
        metadata={"run_id": "run-1"},
    )
    store.append_message(
        session_id,
        "assistant",
        "Compare payments against settled invoice rows.",
        metadata={
            "run_id": "run-1",
            "run_context_summary": "Finance context summary keeps vendor balances and refund risk notes.",
        },
    )

    assert rebuild_fts_index(tmp_path) is True

    title_results = search_history(tmp_path, "audit workflow", limit=5)
    user_results = search_history(tmp_path, "refund ledger", limit=5)
    assistant_results = search_history(tmp_path, "settled invoice", limit=5)
    summary_results = search_history(tmp_path, "vendor balances", limit=5)

    assert [(item.session_id, item.source) for item in title_results] == [
        (session_id, "session_title")
    ]
    assert (session_id, "message") in [(item.session_id, item.source) for item in user_results]
    assert any("refund ledger" in item.snippet for item in user_results)
    assert (session_id, "message") in [(item.session_id, item.source) for item in assistant_results]
    assert (session_id, "summary") in [(item.session_id, item.source) for item in summary_results]


def test_search_history_returns_empty_for_blank_missing_or_uninitialized_database(tmp_path):
    assert search_history(tmp_path, "") == []
    assert search_history(tmp_path, "missing") == []


def test_search_history_filters_deleted_jsonl_sessions_even_if_sqlite_rows_remain(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    store = HistoryStore(tmp_path)
    session_id = "deleted-search-session"

    store.append_event(
        session_id,
        {
            "type": "session_metadata",
            "title": "Temporary deletion target",
            "timestamp": "2026-07-09T02:00:00Z",
        },
    )
    store.append_message(session_id, "user", "unique deletion search marker")

    assert search_history(tmp_path, "deletion marker", limit=5)

    HistoryFacade(tmp_path).delete(session_id)

    assert search_history(tmp_path, "deletion marker", limit=5) == []


def test_search_history_filters_stale_sqlite_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    store = HistoryStore(tmp_path)
    session_id = "stale-search-session"

    store.append_event(
        session_id,
        {
            "type": "session_metadata",
            "title": "Stale search target",
            "timestamp": "2026-07-09T03:00:00Z",
        },
    )
    store.append_message(session_id, "user", "old sqlite searchable marker")

    monkeypatch.delenv("LUCODE_CONTEXT_SQLITE", raising=False)
    store.append_message(session_id, "assistant", "jsonl only message that makes sqlite stale")

    assert search_history(tmp_path, "old sqlite searchable", limit=5) == []


def test_search_history_uses_fts_triggers_without_rebuilding_on_each_query(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    store = HistoryStore(tmp_path)
    session_id = "fts-incremental-session"
    store.append_event(session_id, {"type": "session_metadata", "title": "Incremental FTS"})
    store.append_message(session_id, "user", "initial searchable marker")

    assert search_history(tmp_path, "initial marker", limit=5)

    def fail_rebuild(_connection):
        raise AssertionError("search must not rebuild populated FTS tables")

    monkeypatch.setattr(search_module, "_rebuild_fts_tables", fail_rebuild)
    store.append_message(session_id, "assistant", "new trigger indexed marker")

    results = search_history(tmp_path, "trigger indexed", limit=5)

    assert [item.session_id for item in results] == [session_id]
