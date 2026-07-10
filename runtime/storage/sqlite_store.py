from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = "context_store.v1"
DB_FILENAME = "lucode.db"


class ClosingSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class SQLiteInitResult:
    db_path: Path
    schema_version: str
    created: bool


def database_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).resolve() / ".lucode" / DB_FILENAME


def connect(workspace_root: Path | str) -> sqlite3.Connection:
    db_path = database_path(workspace_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, factory=ClosingSQLiteConnection)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=3000")
    return connection


def initialize_sqlite_store(workspace_root: Path | str) -> SQLiteInitResult:
    db_path = database_path(workspace_root)
    created = not db_path.exists()
    schema = _schema_text()
    with connect(workspace_root) as connection:
        connection.executescript(schema)
        _ensure_compatible_schema(connection)
        row = connection.execute("select value from schema_meta where key = 'schema_version'").fetchone()
    return SQLiteInitResult(
        db_path=db_path,
        schema_version=str(row[0] if row else SCHEMA_VERSION),
        created=created,
    )


def _schema_text() -> str:
    return (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")


def _ensure_compatible_schema(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("pragma table_info(sessions)").fetchall()}
    if "source_fingerprint" not in columns:
        connection.execute(
            "alter table sessions add column source_fingerprint text not null default ''"
        )
