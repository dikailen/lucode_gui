from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.storage.freshness import sqlite_session_matches_jsonl
from runtime.storage.sqlite_store import connect, database_path, initialize_sqlite_store


@dataclass(frozen=True)
class HistorySearchResult:
    session_id: str
    source: str
    snippet: str
    title: str = ""
    role: str = ""
    updated_at: str = ""
    score: float = 0.0


def rebuild_fts_index(workspace_root: Path | str) -> bool:
    """Rebuild optional FTS tables from the auxiliary SQLite context tables."""

    root = Path(workspace_root).resolve()
    initialize_sqlite_store(root)
    with connect(root) as connection:
        if not _ensure_fts_schema(connection):
            return False
        _rebuild_fts_tables(connection)
    return True


def search_history(
    workspace_root: Path | str,
    query: str,
    limit: int | None = 20,
) -> list[HistorySearchResult]:
    """Search indexed history without making SQLite the authoritative history source."""

    root = Path(workspace_root).resolve()
    normalized_query = sanitize_text(str(query or "")).strip()
    if not normalized_query:
        return []
    if not database_path(root).is_file():
        return []

    safe_limit = max(1, int(limit or 20)) if limit is not None else None
    candidate_limit = safe_limit * 4 if safe_limit is not None else None
    try:
        initialize_sqlite_store(root)
        with connect(root) as connection:
            results: list[HistorySearchResult]
            fts_exists = _fts_schema_exists(connection)
            if _ensure_fts_schema(connection):
                if not fts_exists:
                    _rebuild_fts_tables(connection)
                results = _search_fts(connection, normalized_query, limit=candidate_limit)
            else:
                results = []
            if not results:
                results = _search_like(connection, normalized_query, limit=candidate_limit)
            return _filter_current_jsonl_sessions(connection, root, results, limit=safe_limit)
    except (OSError, sqlite3.Error, ValueError):
        return []


def _ensure_fts_schema(connection: sqlite3.Connection) -> bool:
    try:
        connection.executescript(
            """
            create virtual table if not exists sessions_fts using fts5(
              session_id unindexed,
              title,
              tokenize = 'unicode61'
            );

            create virtual table if not exists messages_fts using fts5(
              session_id unindexed,
              role unindexed,
              content,
              tokenize = 'unicode61'
            );

            create virtual table if not exists summaries_fts using fts5(
              session_id unindexed,
              summary,
              tokenize = 'unicode61'
            );

            create trigger if not exists sessions_fts_ai
            after insert on sessions
            begin
              insert into sessions_fts(rowid, session_id, title)
              values (new.rowid, new.session_id, coalesce(new.title, ''));
            end;

            create trigger if not exists sessions_fts_ad
            after delete on sessions
            begin
              delete from sessions_fts where rowid = old.rowid;
            end;

            create trigger if not exists sessions_fts_au
            after update of session_id, title on sessions
            begin
              delete from sessions_fts where rowid = old.rowid;
              insert into sessions_fts(rowid, session_id, title)
              values (new.rowid, new.session_id, coalesce(new.title, ''));
            end;

            create trigger if not exists messages_fts_ai
            after insert on messages
            begin
              insert into messages_fts(rowid, session_id, role, content)
              values (new.rowid, new.session_id, new.role, coalesce(new.content, ''));
            end;

            create trigger if not exists messages_fts_ad
            after delete on messages
            begin
              delete from messages_fts where rowid = old.rowid;
            end;

            create trigger if not exists messages_fts_au
            after update of session_id, role, content on messages
            begin
              delete from messages_fts where rowid = old.rowid;
              insert into messages_fts(rowid, session_id, role, content)
              values (new.rowid, new.session_id, new.role, coalesce(new.content, ''));
            end;

            create trigger if not exists summaries_fts_ai
            after insert on context_summaries
            begin
              insert into summaries_fts(rowid, session_id, summary)
              values (new.rowid, new.session_id, coalesce(new.summary, ''));
            end;

            create trigger if not exists summaries_fts_ad
            after delete on context_summaries
            begin
              delete from summaries_fts where rowid = old.rowid;
            end;

            create trigger if not exists summaries_fts_au
            after update of session_id, summary on context_summaries
            begin
              delete from summaries_fts where rowid = old.rowid;
              insert into summaries_fts(rowid, session_id, summary)
              values (new.rowid, new.session_id, coalesce(new.summary, ''));
            end;
            """
        )
        return True
    except sqlite3.Error:
        return False


def _fts_schema_exists(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "select name from sqlite_master where type = 'table' and name in (?, ?, ?)",
        ("sessions_fts", "messages_fts", "summaries_fts"),
    ).fetchall()
    return {str(row[0]) for row in rows} == {"sessions_fts", "messages_fts", "summaries_fts"}


def _rebuild_fts_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        delete from sessions_fts;
        insert into sessions_fts(rowid, session_id, title)
        select rowid, session_id, coalesce(title, '') from sessions;

        delete from messages_fts;
        insert into messages_fts(rowid, session_id, role, content)
        select rowid, session_id, role, coalesce(content, '') from messages;

        delete from summaries_fts;
        insert into summaries_fts(rowid, session_id, summary)
        select rowid, session_id, coalesce(summary, '') from context_summaries;
        """
    )


def _search_fts(connection: sqlite3.Connection, query: str, *, limit: int | None) -> list[HistorySearchResult]:
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    rows: list[HistorySearchResult] = []
    rows.extend(_search_session_titles_fts(connection, fts_query, limit=limit))
    rows.extend(_search_messages_fts(connection, fts_query, limit=limit))
    rows.extend(_search_summaries_fts(connection, fts_query, limit=limit))
    rows.sort(key=lambda item: (-item.score, _source_priority(item.source), item.updated_at), reverse=False)
    return rows if limit is None else rows[:limit]


def _search_session_titles_fts(
    connection: sqlite3.Connection, fts_query: str, *, limit: int | None
) -> list[HistorySearchResult]:
    rows = connection.execute(
        """
        select
          s.session_id,
          s.title,
          s.updated_at,
          snippet(sessions_fts, 1, '', '', '...', 18) as snippet,
          bm25(sessions_fts) as rank
        from sessions_fts
        join sessions s on s.rowid = sessions_fts.rowid
        where sessions_fts match ?
        order by rank
        limit ?
        """,
        (fts_query, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="session_title",
            title=str(title or ""),
            snippet=_short(snippet or title),
            updated_at=str(updated_at or ""),
            score=-float(rank or 0.0),
        )
        for session_id, title, updated_at, snippet, rank in rows
    ]


def _search_messages_fts(
    connection: sqlite3.Connection, fts_query: str, *, limit: int | None
) -> list[HistorySearchResult]:
    rows = connection.execute(
        """
        select
          m.session_id,
          s.title,
          m.role,
          s.updated_at,
          snippet(messages_fts, 2, '', '', '...', 24) as snippet,
          bm25(messages_fts) as rank
        from messages_fts
        join messages m on m.rowid = messages_fts.rowid
        left join sessions s on s.session_id = m.session_id
        where messages_fts match ?
        order by rank
        limit ?
        """,
        (fts_query, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="message",
            title=str(title or ""),
            role=str(role or ""),
            snippet=_short(snippet),
            updated_at=str(updated_at or ""),
            score=-float(rank or 0.0),
        )
        for session_id, title, role, updated_at, snippet, rank in rows
    ]


def _search_summaries_fts(
    connection: sqlite3.Connection, fts_query: str, *, limit: int | None
) -> list[HistorySearchResult]:
    rows = connection.execute(
        """
        select
          c.session_id,
          s.title,
          s.updated_at,
          snippet(summaries_fts, 1, '', '', '...', 24) as snippet,
          bm25(summaries_fts) as rank
        from summaries_fts
        join context_summaries c on c.rowid = summaries_fts.rowid
        left join sessions s on s.session_id = c.session_id
        where summaries_fts match ?
        order by rank
        limit ?
        """,
        (fts_query, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="summary",
            title=str(title or ""),
            snippet=_short(snippet),
            updated_at=str(updated_at or ""),
            score=-float(rank or 0.0),
        )
        for session_id, title, updated_at, snippet, rank in rows
    ]


def _search_like(connection: sqlite3.Connection, query: str, *, limit: int | None) -> list[HistorySearchResult]:
    terms = _plain_terms(query)
    if not terms:
        return []
    rows: list[HistorySearchResult] = []
    rows.extend(_search_session_titles_like(connection, terms, limit=limit))
    rows.extend(_search_messages_like(connection, terms, limit=limit))
    rows.extend(_search_summaries_like(connection, terms, limit=limit))
    return rows if limit is None else rows[:limit]


def _search_session_titles_like(
    connection: sqlite3.Connection, terms: list[str], *, limit: int | None
) -> list[HistorySearchResult]:
    where, params = _like_where("s.title", terms)
    rows = connection.execute(
        f"""
        select s.session_id, s.title, s.updated_at
        from sessions s
        where {where}
        order by s.updated_at desc
        limit ?
        """,
        (*params, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="session_title",
            title=str(title or ""),
            snippet=_short(title),
            updated_at=str(updated_at or ""),
        )
        for session_id, title, updated_at in rows
    ]


def _search_messages_like(
    connection: sqlite3.Connection, terms: list[str], *, limit: int | None
) -> list[HistorySearchResult]:
    where, params = _like_where("m.content", terms)
    rows = connection.execute(
        f"""
        select m.session_id, s.title, m.role, s.updated_at, m.content
        from messages m
        left join sessions s on s.session_id = m.session_id
        where {where}
        order by m.created_at desc, m.rowid desc
        limit ?
        """,
        (*params, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="message",
            title=str(title or ""),
            role=str(role or ""),
            snippet=_short(content),
            updated_at=str(updated_at or ""),
        )
        for session_id, title, role, updated_at, content in rows
    ]


def _search_summaries_like(
    connection: sqlite3.Connection, terms: list[str], *, limit: int | None
) -> list[HistorySearchResult]:
    where, params = _like_where("c.summary", terms)
    rows = connection.execute(
        f"""
        select c.session_id, s.title, s.updated_at, c.summary
        from context_summaries c
        left join sessions s on s.session_id = c.session_id
        where {where}
        order by c.created_at desc, c.rowid desc
        limit ?
        """,
        (*params, _sqlite_limit(limit)),
    ).fetchall()
    return [
        HistorySearchResult(
            session_id=str(session_id),
            source="summary",
            title=str(title or ""),
            snippet=_short(summary),
            updated_at=str(updated_at or ""),
        )
        for session_id, title, updated_at, summary in rows
    ]


def _filter_current_jsonl_sessions(
    connection: sqlite3.Connection,
    workspace_root: Path,
    results: list[HistorySearchResult],
    *,
    limit: int | None,
) -> list[HistorySearchResult]:
    filtered: list[HistorySearchResult] = []
    current_cache: dict[str, bool] = {}
    for result in results:
        if result.session_id not in current_cache:
            current_cache[result.session_id] = _sqlite_session_matches_jsonl(
                connection,
                workspace_root,
                result.session_id,
            )
        if current_cache[result.session_id]:
            filtered.append(result)
        if limit is not None and len(filtered) >= limit:
            break
    return filtered


def _sqlite_session_matches_jsonl(
    connection: sqlite3.Connection,
    workspace_root: Path,
    session_id: str,
) -> bool:
    path = workspace_root / ".lucode" / "history" / "sessions" / f"{session_id}.jsonl"
    if not path.is_file():
        return False
    return sqlite_session_matches_jsonl(connection, path, session_id)


def _fts_query(query: str) -> str:
    terms = _plain_terms(query)
    return " AND ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)


def _plain_terms(query: str) -> list[str]:
    normalized = sanitize_text(str(query or "")).casefold()
    return [match.group(0) for match in re.finditer(r"[\w\u4e00-\u9fff]+", normalized, flags=re.UNICODE)]


def _like_where(column: str, terms: list[str]) -> tuple[str, list[str]]:
    clauses = [f"lower({column}) like ? escape '\\'" for _ in terms]
    params = [f"%{_escape_like(term)}%" for term in terms]
    return " and ".join(clauses), params


def _escape_like(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sqlite_limit(limit: int | None) -> int:
    return -1 if limit is None else max(1, int(limit))


def _short(value: Any, limit: int = 220) -> str:
    text = sanitize_text(str(value or "")).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _source_priority(source: str) -> int:
    return {"session_title": 0, "message": 1, "summary": 2}.get(str(source or ""), 9)
