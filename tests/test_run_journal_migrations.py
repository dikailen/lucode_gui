from __future__ import annotations

import sqlite3

from runtime.recovery.migrations import RUN_RECOVERY_SCHEMA_VERSION, initialize_run_recovery_schema
from runtime.storage.sqlite_store import initialize_sqlite_store


def test_run_recovery_schema_initializes_empty_database_without_creating_context_tables(tmp_path):
    result = initialize_run_recovery_schema(tmp_path)

    assert result.db_path == tmp_path / ".lucode" / "lucode.db"
    assert result.schema_version == RUN_RECOVERY_SCHEMA_VERSION
    assert result.created is True

    with sqlite3.connect(result.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'").fetchall()
        }
        version = connection.execute(
            "select value from run_recovery_meta where key = 'schema_version'"
        ).fetchone()[0]

    assert {"agent_runs", "run_events", "run_checkpoints", "run_tasks", "tool_invocations", "run_approvals"} <= tables
    assert "sessions" not in tables
    assert version == RUN_RECOVERY_SCHEMA_VERSION


def test_run_recovery_schema_is_idempotent_and_does_not_change_context_schema_version(tmp_path):
    context = initialize_sqlite_store(tmp_path)

    first = initialize_run_recovery_schema(tmp_path)
    second = initialize_run_recovery_schema(tmp_path)

    assert first.db_path == second.db_path == context.db_path
    assert first.created is False
    assert second.created is False

    with sqlite3.connect(context.db_path) as connection:
        context_version = connection.execute(
            "select value from schema_meta where key = 'schema_version'"
        ).fetchone()[0]
        recovery_version = connection.execute(
            "select value from run_recovery_meta where key = 'schema_version'"
        ).fetchone()[0]

    assert context_version == "context_store.v1"
    assert recovery_version == RUN_RECOVERY_SCHEMA_VERSION


def test_dropping_recovery_tables_does_not_damage_existing_session_rows(tmp_path):
    context = initialize_sqlite_store(tmp_path)
    initialize_run_recovery_schema(tmp_path)

    with sqlite3.connect(context.db_path) as connection:
        connection.execute(
            "insert into sessions(session_id, title, created_at, updated_at) values (?, ?, ?, ?)",
            ("session-1", "Existing session", "2026-07-13T00:00:00Z", "2026-07-13T00:00:00Z"),
        )
        connection.execute("drop table run_events")
        connection.execute("drop table run_checkpoints")
        connection.execute("drop table run_tasks")
        connection.execute("drop table tool_invocations")
        connection.execute("drop table run_approvals")
        connection.execute("drop table agent_runs")
        connection.execute("drop table run_recovery_meta")

    with sqlite3.connect(context.db_path) as connection:
        session = connection.execute("select title from sessions where session_id = ?", ("session-1",)).fetchone()

    assert session == ("Existing session",)
