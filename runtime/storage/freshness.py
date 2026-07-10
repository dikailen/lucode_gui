from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def jsonl_source_fingerprint(path: Path | str) -> str:
    source = Path(path)
    try:
        stat = source.stat()
    except OSError:
        return ""
    return f"stat:v1:{int(stat.st_size)}:{int(stat.st_mtime_ns)}"


def jsonl_message_count(path: Path | str) -> int:
    count = 0
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    event: Any = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "message":
                    count += 1
    except OSError:
        return 0
    return count


def source_snapshot_matches_jsonl(
    path: Path | str,
    *,
    message_count: int,
    source_fingerprint: str,
) -> bool:
    expected = str(source_fingerprint or "").strip()
    source = Path(path)
    if not expected or not source.is_file():
        return False
    actual = jsonl_source_fingerprint(source)
    return bool(actual) and expected == actual


def sqlite_session_matches_jsonl(
    connection: sqlite3.Connection,
    path: Path | str,
    session_id: str,
) -> bool:
    row = connection.execute(
        """
        select
          s.source_fingerprint,
          count(m.message_id)
        from sessions s
        left join messages m on m.session_id = s.session_id
        where s.session_id = ?
        group by s.session_id
        """,
        (str(session_id),),
    ).fetchone()
    if row is None:
        return False
    return source_snapshot_matches_jsonl(
        path,
        message_count=int(row[1] or 0),
        source_fingerprint=str(row[0] or ""),
    )
