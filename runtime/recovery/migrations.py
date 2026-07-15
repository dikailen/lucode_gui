from __future__ import annotations

from pathlib import Path

from runtime.recovery.models import RecoverySchemaInitResult, utc_now_iso
from runtime.storage.sqlite_store import database_path, connect


RUN_RECOVERY_SCHEMA_VERSION = "run_recovery.v2"


def initialize_run_recovery_schema(workspace_root: Path | str) -> RecoverySchemaInitResult:
    db_path = database_path(workspace_root)
    created = not db_path.exists()
    schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    with connect(workspace_root) as connection:
        connection.executescript(schema)
        _ensure_agent_run_columns(connection)
        _ensure_retention_index(connection)
        connection.execute(
            """
            insert into run_recovery_meta(key, value, updated_at)
            values ('schema_version', ?, ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (RUN_RECOVERY_SCHEMA_VERSION, utc_now_iso()),
        )
    return RecoverySchemaInitResult(
        db_path=db_path,
        schema_version=RUN_RECOVERY_SCHEMA_VERSION,
        created=created,
    )


def _ensure_agent_run_columns(connection) -> None:
    columns = {str(row[1]) for row in connection.execute("pragma table_info(agent_runs)").fetchall()}
    if "recovery_state" not in columns:
        connection.execute("alter table agent_runs add column recovery_state text not null default 'none'")
    if "interrupted_at" not in columns:
        connection.execute("alter table agent_runs add column interrupted_at text not null default ''")


def _ensure_retention_index(connection) -> None:
    connection.execute(
        """
        create index if not exists idx_agent_runs_terminal_updated
        on agent_runs(status, updated_at asc, run_id asc)
        """
    )
