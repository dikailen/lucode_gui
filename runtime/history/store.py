from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.common.conversation import append_recent_turn
from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text
from runtime.history.model import HistoryDeleteResult, HistoryItem, HistoryPreview
from runtime.sessions.store import SessionStore, SessionSummary
from runtime.storage.context_store import ContextSQLiteStore
from runtime.storage.freshness import jsonl_source_fingerprint, source_snapshot_matches_jsonl
from runtime.storage.sqlite_store import database_path


HISTORY_SCHEMA_VERSION = 1


class HistoryStore(SessionStore):
    """Canonical JSONL history store under .lucode/history."""

    def __init__(self, workspace_root: Path, max_message_chars: int = 20000):
        self.workspace_root = Path(workspace_root).resolve()
        self.history_dir = self.workspace_root / ".lucode" / "history"
        self.index_path = self.history_dir / "index.jsonl"
        self.contexts_dir = self.history_dir / "contexts"
        self.exports_dir = self.history_dir / "exports"
        self._context_sqlite_store: ContextSQLiteStore | None = None
        super().__init__(
            self.workspace_root,
            max_message_chars=max_message_chars,
            sessions_dir=self.history_dir / "sessions",
        )

    def append_event(self, session_id: str, event: dict[str, Any]) -> None:
        super().append_event(session_id, event)
        self._append_context_entry(session_id, event)
        self._append_index_entry(session_id)
        self._append_sqlite_entry(session_id, event)

    def _append_context_entry(self, session_id: str, event: dict[str, Any]) -> None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        summary = sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
        if event.get("type") != "message" or str(event.get("role") or "").lower() != "assistant" or not summary:
            return
        payload = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "type": "run_context_summary",
            "session_id": str(session_id),
            "timestamp": _now_iso(),
            "summary": redact_sensitive_text(summary),
        }
        try:
            self.contexts_dir.mkdir(parents=True, exist_ok=True)
            with self._context_path_for(session_id).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Context sidecars are an auxiliary cache; the canonical session JSONL stays authoritative.
            return

    def _context_path_for(self, session_id: str) -> Path:
        safe_id = self._validate_session_id(session_id)
        return self.contexts_dir / f"{safe_id}.context.jsonl"

    def _append_index_entry(self, session_id: str) -> None:
        try:
            summary = self._summarize(self._path_for(session_id))
            if summary is None:
                return
            self.history_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "type": "session_index",
                "session_id": summary.session_id,
                "path": str(summary.path.relative_to(self.history_dir)),
                "created_at": summary.created_at,
                "updated_at": summary.updated_at,
                "message_count": summary.message_count,
                "title": summary.title,
                "last_user": summary.last_user,
                "last_assistant": summary.last_assistant,
                "indexed_at": _now_iso(),
            }
            with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Index is only a fast lookup/cache layer; session JSONL remains the source of truth.
            return

    def _append_sqlite_entry(self, session_id: str, event: dict[str, Any]) -> None:
        store = self._sqlite_store()
        if store is None:
            return
        try:
            payload = dict(event or {})
            event_type = str(payload.get("type") or "").strip()
            timestamp = str(payload.get("timestamp") or "").strip() or _now_iso()
            source_fingerprint = jsonl_source_fingerprint(self._path_for(session_id))
            if event_type == "session_metadata":
                store.save_session(
                    {
                        "session_id": session_id,
                        "title": str(payload.get("title") or ""),
                        "created_at": timestamp,
                        "updated_at": timestamp,
                        "source": "jsonl",
                        "source_fingerprint": source_fingerprint,
                    }
                )
            elif event_type == "message":
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                run_id = str(metadata.get("run_id") or "")
                store.save_message(
                    {
                        "session_id": session_id,
                        "role": str(payload.get("role") or ""),
                        "content": str(payload.get("content") or ""),
                        "created_at": timestamp,
                        "metadata": metadata,
                        "source": "jsonl",
                    }
                )
                self._append_sqlite_context_metadata(
                    store,
                    session_id=session_id,
                    run_id=run_id,
                    metadata=metadata,
                    created_at=timestamp,
                )
            store.update_session_source_fingerprint(session_id, source_fingerprint)
        except Exception:
            # SQLite is an auxiliary cache/index in Phase 3; JSONL stays authoritative.
            return

    def _append_sqlite_context_metadata(
        self,
        store: ContextSQLiteStore,
        *,
        session_id: str,
        run_id: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        summary = sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
        if summary:
            store.save_context_summary(
                {
                    "session_id": session_id,
                    "summary": redact_sensitive_text(summary),
                    "created_at": created_at,
                    "source": "jsonl",
                    "metadata": {"run_id": run_id} if run_id else {},
                }
            )
        ledger = metadata.get("context_ledger") if isinstance(metadata.get("context_ledger"), dict) else {}
        if ledger:
            store.save_context_ledger_result(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "mode": str(ledger.get("mode") or ""),
                    "applied": bool(ledger.get("applied")),
                    "triggered": bool(ledger.get("triggered")),
                    "estimated_input_tokens": ledger.get("estimated_input_tokens"),
                    "context_window_tokens": ledger.get("context_window_tokens"),
                    "created_at": created_at,
                    "metadata": {
                        key: value
                        for key, value in ledger.items()
                        if key
                        not in {
                            "mode",
                            "applied",
                            "triggered",
                            "estimated_input_tokens",
                            "context_window_tokens",
                        }
                    },
                }
            )
        dehydration = metadata.get("tool_dehydration") if isinstance(metadata.get("tool_dehydration"), dict) else {}
        items = dehydration.get("items") if isinstance(dehydration.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence_ref = str(item.get("evidence_ref") or "")
            artifact_ref = str(item.get("raw_artifact_ref") or "")
            store.save_tool_dehydrated_result(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "tool": str(item.get("tool") or item.get("tool_name") or ""),
                    "summary": str(item.get("summary") or ""),
                    "evidence_ref": evidence_ref,
                    "raw_artifact_ref": artifact_ref,
                    "created_at": created_at,
                    "metadata": item,
                }
            )
            if evidence_ref:
                store.save_evidence_ref(
                    {
                        "session_id": session_id,
                        "run_id": run_id,
                        "evidence_ref": evidence_ref,
                        "artifact_ref": artifact_ref,
                        "source_type": "tool",
                        "created_at": created_at,
                        "metadata": item,
                    }
                )

    def _sqlite_store(self) -> ContextSQLiteStore | None:
        if not _sqlite_dual_write_enabled():
            return None
        if self._context_sqlite_store is not None:
            return self._context_sqlite_store
        try:
            self._context_sqlite_store = ContextSQLiteStore(self.workspace_root)
        except Exception:
            return None
        return self._context_sqlite_store


class HistoryFacade:
    """Stable history API backed by canonical history plus legacy JSONL sessions."""

    def __init__(
        self,
        workspace_root: Path,
        session_store: SessionStore | None = None,
        history_store: HistoryStore | None = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.legacy_sessions_dir = self.workspace_root / ".lucode" / "sessions"
        self.history_dir = self.workspace_root / ".lucode" / "history"
        self.session_store = session_store or SessionStore(self.workspace_root)
        self.history_store = history_store or HistoryStore(self.workspace_root)
        self._context_sqlite_read_store: ContextSQLiteStore | None = None

    def list_items(self, limit: int = 20) -> list[HistoryItem]:
        return self._list_items_up_to(max(1, int(limit or 20)))

    def list_items_page(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[HistoryItem], str, bool]:
        return _page_history_items(self._list_all_items(), limit=limit, cursor=cursor)

    def _list_items_up_to(self, limit: int) -> list[HistoryItem]:
        items: list[HistoryItem] = []
        seen: set[str] = set()
        for summary in self._sqlite_list_sessions(limit=limit):
            if summary.session_id in seen:
                continue
            if not self._sqlite_summary_is_current(summary):
                continue
            seen.add(summary.session_id)
            items.append(_history_item_from_summary(summary, storage_kind="history_sqlite"))
        for storage_kind, store in (("history", self.history_store), ("legacy_session", self.session_store)):
            for summary in store.list_sessions(limit=limit):
                if summary.session_id in seen:
                    continue
                seen.add(summary.session_id)
                items.append(_history_item_from_summary(summary, storage_kind=storage_kind))
        items.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return items[:limit]

    def _list_all_items(self) -> list[HistoryItem]:
        items: list[HistoryItem] = []
        seen: set[str] = set()
        for storage_kind, store in (("history", self.history_store), ("legacy_session", self.session_store)):
            for summary in store.list_sessions(limit=None):
                if summary.session_id in seen:
                    continue
                seen.add(summary.session_id)
                items.append(_history_item_from_summary(summary, storage_kind=storage_kind))
        items.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return items

    def preview(self, history_id: str) -> HistoryPreview:
        session_id = self.resolve(history_id)
        if not session_id:
            return HistoryPreview(history_id=str(history_id or ""), session_id="")
        store = self._store_for(session_id)
        if store is None:
            return HistoryPreview(history_id=session_id, session_id=session_id)
        first_user = ""
        last_user = ""
        last_assistant = ""
        run_context_summary = ""
        message_count = 0
        updated_at = ""
        for event in store.load_events(session_id):
            timestamp = str(event.get("timestamp") or "")
            if timestamp:
                updated_at = timestamp
            if event.get("type") != "message":
                continue
            role = str(event.get("role") or "").lower()
            content = str(event.get("content") or "")
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            message_count += 1
            if role == "user":
                if not first_user:
                    first_user = content
                last_user = content
            elif role == "assistant":
                last_assistant = content
                context_summary = str(metadata.get("run_context_summary") or "").strip()
                if context_summary:
                    run_context_summary = context_summary
        return HistoryPreview(
            history_id=session_id,
            session_id=session_id,
            first_user=_short(first_user, 180),
            last_user=_short(last_user, 180),
            last_assistant=_short(last_assistant, 220),
            run_context_summary=_short(run_context_summary, 260),
            message_count=message_count,
            updated_at=updated_at,
        )

    def resolve(self, selector: str | None) -> str | None:
        query = str(selector or "last").strip()
        if query.casefold() not in {"", "last", "latest"} and self.contains(query):
            return query
        items = self.list_items(limit=200)
        if not items:
            return None
        if query.lower() in {"", "last", "latest", "最近"}:
            return items[0].session_id
        exact = [item for item in items if item.session_id == query]
        if exact:
            return exact[0].session_id
        matches = [item for item in items if item.session_id.startswith(query)]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"会话前缀不唯一：{query}")
        return matches[0].session_id

    def contains(self, session_id: str) -> bool:
        target = str(session_id or "").strip()
        if not target:
            return False
        return any(
            (store.sessions_dir / f"{target}.jsonl").is_file()
            for store in (self.history_store, self.session_store)
        )

    def delete(self, selector: str) -> HistoryDeleteResult:
        session_id = self.resolve(selector)
        if not session_id:
            raise ValueError(f"没有找到历史会话：{selector}")
        item = next((entry for entry in self.list_items(limit=200) if entry.session_id == session_id), None)
        store = self._store_for(session_id)
        path = (store.sessions_dir if store is not None else self.session_store.sessions_dir) / f"{session_id}.jsonl"
        title = item.title if item is not None else session_id
        if database_path(self.workspace_root).is_file():
            ContextSQLiteStore(self.workspace_root).delete_session(session_id)
        deleted = False
        if path.is_file():
            path.unlink()
            deleted = True
        context_path = self.history_store.contexts_dir / f"{session_id}.context.jsonl"
        if context_path.is_file():
            try:
                context_path.unlink()
            except OSError:
                pass
        return HistoryDeleteResult(
            history_id=session_id,
            session_id=session_id,
            title=title,
            path=path,
            deleted=deleted,
        )

    def search(self, query: str, limit: int | None = 20) -> list[HistoryItem]:
        terms = [term.casefold() for term in sanitize_text(str(query or "")).split() if term.strip()]
        if not terms:
            return self._list_all_items() if limit is None else self.list_items(limit=limit)
        safe_limit = max(1, int(limit or 20)) if limit is not None else None
        matches: list[HistoryItem] = self._fts_search_items(query, limit=safe_limit)
        seen = {item.session_id for item in matches}
        source_items = self._list_all_items() if safe_limit is None else self.list_items(limit=max(200, safe_limit * 4))
        for item in source_items:
            if item.session_id in seen:
                continue
            haystack = self._search_text(item.session_id)
            if all(term in haystack for term in terms):
                matches.append(item)
                seen.add(item.session_id)
            if safe_limit is not None and len(matches) >= safe_limit:
                break
        return matches

    def search_page(
        self,
        query: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[HistoryItem], str, bool]:
        # Search pages share the session list's ordering contract before pagination.
        matches = self.search(query, limit=None)
        matches.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return _page_history_items(
            matches,
            limit=limit,
            cursor=cursor,
            scope=_search_cursor_scope(query),
        )

    def export(self, selector: str, output_path: Path | None = None) -> Path:
        session_id = self.resolve(selector)
        if not session_id:
            raise ValueError(f"没有找到历史会话：{selector}")
        events = self.load_events(session_id)
        preview = self.preview(session_id)
        target = Path(output_path).resolve() if output_path is not None else self.history_store.exports_dir / f"{session_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._render_export_markdown(session_id, preview, events), encoding="utf-8", newline="\n")
        return target

    def load_context_summary(self, history_id: str, max_chars: int = 2400) -> str:
        session_id = self.resolve(history_id)
        if not session_id:
            return ""
        sqlite_summary = self._sqlite_load_context_summary(session_id, max_chars=max_chars)
        if sqlite_summary:
            return sqlite_summary
        summaries: list[str] = []
        context_path = self.history_store.contexts_dir / f"{session_id}.context.jsonl"
        if context_path.is_file():
            for event in _iter_jsonl(context_path):
                summary = sanitize_text(str(event.get("summary") or "")).strip()
                if summary:
                    summaries.append(summary)
        if not summaries:
            for event in self.load_events(session_id):
                metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                summary = sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
                if summary:
                    summaries.append(redact_sensitive_text(summary))
        if not summaries:
            return ""
        text = "\n\n".join(summaries[-3:])
        limit = max(400, int(max_chars or 2400))
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def load_recent_turns(self, history_id: str, max_messages: int = 6) -> list[dict[str, str]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        turns: list[dict[str, str]] = []
        for message in self.load_messages(session_id, limit=max_messages):
            append_recent_turn(
                turns,
                str(message.get("role") or ""),
                str(message.get("content") or ""),
                max_chars=800,
            )
        return turns

    def load_messages(self, history_id: str, limit: int | None = None) -> list[dict[str, str]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        sqlite_messages = self._sqlite_load_messages(session_id, limit=limit)
        if sqlite_messages:
            return sqlite_messages
        store = self._store_for(session_id)
        return store.load_messages(session_id, limit=limit) if store else []

    def load_events(self, history_id: str) -> list[dict[str, Any]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        store = self._store_for(session_id)
        return store.load_events(session_id) if store else []

    def as_session_store(self) -> "HistoryFacadeSessionView":
        return HistoryFacadeSessionView(self)

    def migrate_legacy_session(self, selector: str) -> str | None:
        session_id = self.resolve(selector)
        if not session_id:
            return None
        source = self.session_store.sessions_dir / f"{session_id}.jsonl"
        target = self.history_store.sessions_dir / f"{session_id}.jsonl"
        if target.is_file() or not source.is_file():
            return session_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.history_store._append_index_entry(session_id)
        return session_id

    def _sqlite_list_sessions(self, limit: int | None) -> list[SessionSummary]:
        store = self._sqlite_read_store()
        if store is None:
            return []
        try:
            return store.list_sessions(limit=limit)
        except Exception:
            return []

    def _sqlite_load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        store = self._sqlite_read_store()
        if store is None:
            return []
        try:
            summary = store.get_session(session_id)
            if summary is None or not self._sqlite_summary_is_current(summary):
                return []
            return store.load_messages(session_id, limit=limit)
        except Exception:
            return []

    def _sqlite_load_context_summary(self, session_id: str, max_chars: int) -> str:
        store = self._sqlite_read_store()
        if store is None:
            return ""
        try:
            summary = store.get_session(session_id)
            if summary is None or not self._sqlite_summary_is_current(summary):
                return ""
            return store.load_context_summary(session_id, max_chars=max_chars)
        except Exception:
            return ""

    def _sqlite_read_store(self) -> ContextSQLiteStore | None:
        if not _sqlite_read_through_enabled():
            return None
        if self._context_sqlite_read_store is not None:
            return self._context_sqlite_read_store
        try:
            self._context_sqlite_read_store = ContextSQLiteStore(self.workspace_root)
        except Exception:
            return None
        return self._context_sqlite_read_store

    def _fts_search_items(self, query: str, limit: int | None) -> list[HistoryItem]:
        if not _context_fts_enabled():
            return []
        try:
            from runtime.storage.search import search_history

            results = search_history(
                self.workspace_root,
                query,
                limit=max(limit * 2, limit) if limit is not None else None,
            )
        except Exception:
            return []
        items: list[HistoryItem] = []
        seen: set[str] = set()
        for result in results:
            if result.session_id in seen:
                continue
            summary = self._history_summary_for(result.session_id)
            if summary is None:
                continue
            seen.add(result.session_id)
            items.append(_history_item_from_summary(summary, storage_kind="history_fts"))
            if limit is not None and len(items) >= limit:
                break
        return items

    def _history_jsonl_exists(self, session_id: str) -> bool:
        safe_id = str(session_id or "").strip()
        if not safe_id:
            return False
        return (self.history_store.sessions_dir / f"{safe_id}.jsonl").is_file()

    def _sqlite_summary_is_current(self, sqlite_summary: SessionSummary) -> bool:
        path = self.history_store.sessions_dir / f"{sqlite_summary.session_id}.jsonl"
        return source_snapshot_matches_jsonl(
            path,
            message_count=sqlite_summary.message_count,
            source_fingerprint=sqlite_summary.source_fingerprint,
        )

    def _history_summary_for(self, session_id: str) -> SessionSummary | None:
        safe_id = str(session_id or "").strip()
        if not safe_id:
            return None
        path = self.history_store.sessions_dir / f"{safe_id}.jsonl"
        if not path.is_file():
            return None
        try:
            return self.history_store._summarize(path)
        except Exception:
            return None

    def _store_for(self, session_id: str) -> SessionStore | None:
        safe_id = str(session_id or "").strip()
        if safe_id and (self.history_store.sessions_dir / f"{safe_id}.jsonl").is_file():
            return self.history_store
        if safe_id and (self.session_store.sessions_dir / f"{safe_id}.jsonl").is_file():
            return self.session_store
        return None

    def _search_text(self, session_id: str) -> str:
        parts = [session_id]
        history_summary = self._history_summary_for(session_id)
        if history_summary is not None:
            parts.append(history_summary.title)
        preview = self.preview(session_id)
        parts.extend(
            [
                preview.first_user,
                preview.last_user,
                preview.last_assistant,
                preview.run_context_summary,
            ]
        )
        for event in self.load_events(session_id):
            parts.append(str(event.get("content") or ""))
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            parts.append(str(metadata.get("run_context_summary") or ""))
        parts.append(self.load_context_summary(session_id))
        return sanitize_text("\n".join(parts)).casefold()

    def _render_export_markdown(self, session_id: str, preview: HistoryPreview, events: list[dict[str, Any]]) -> str:
        lines = [
            f"# Lucode History Export: {session_id}",
            "",
            f"- Updated: {preview.updated_at or 'unknown'}",
            f"- Messages: {preview.message_count}",
        ]
        context_summary = self.load_context_summary(session_id)
        if context_summary:
            lines.extend(["", "## Context Summary", "", context_summary])
        lines.extend(["", "## Messages"])
        for event in events:
            if event.get("type") != "message":
                continue
            role = str(event.get("role") or "message").strip() or "message"
            content = sanitize_text(str(event.get("content") or "")).strip()
            if not content:
                continue
            lines.extend(["", f"### {role}", "", content])
        lines.append("")
        return "\n".join(lines)


class HistoryFacadeSessionView:
    """SessionStore-shaped adapter over HistoryFacade for resume compatibility."""

    def __init__(self, facade: HistoryFacade):
        self.facade = facade
        self.sessions_dir = facade.history_store.sessions_dir

    def list_sessions(self, limit: int = 10) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        for item in self.facade.list_items(limit=limit):
            summaries.append(
                SessionSummary(
                    session_id=item.session_id,
                    path=item.path,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    message_count=item.message_count,
                    title=item.title,
                    last_user=item.last_user,
                    last_assistant=item.last_assistant,
                )
            )
        return summaries

    def resolve_session_id(self, selector: str | None = None) -> str | None:
        return self.facade.resolve(selector)

    def load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        return self.facade.load_messages(session_id, limit=limit)

    def load_events(self, session_id: str) -> list[dict[str, Any]]:
        return self.facade.load_events(session_id)

    def load_recent_turns(self, session_id: str, max_messages: int = 6) -> list[dict[str, str]]:
        return self.facade.load_recent_turns(session_id, max_messages=max_messages)

    def load_context_summary(self, session_id: str, max_chars: int = 2400) -> str:
        return self.facade.load_context_summary(session_id, max_chars=max_chars)


def _history_item_from_summary(summary: SessionSummary, *, storage_kind: str) -> HistoryItem:
    title = _short(summary.title or summary.last_user or summary.last_assistant or summary.session_id, 42)
    return HistoryItem(
        history_id=summary.session_id,
        session_id=summary.session_id,
        path=summary.path,
        title=title,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        message_count=summary.message_count,
        last_user=summary.last_user,
        last_assistant=summary.last_assistant,
        storage_kind=storage_kind,
    )


def _summary_for_session(summaries: list[SessionSummary], session_id: str) -> SessionSummary | None:
    target = str(session_id or "")
    return next((summary for summary in summaries if summary.session_id == target), None)


def _page_history_items(
    items: list[HistoryItem],
    *,
    limit: int,
    cursor: str | None,
    scope: str = "",
) -> tuple[list[HistoryItem], str, bool]:
    safe_limit = max(1, int(limit or 20))
    boundary = _decode_page_cursor(cursor, expected_scope=scope)
    candidates = items
    if boundary is not None:
        candidates = [
            item
            for item in items
            if (item.updated_at, item.session_id) < boundary
        ]
    page = candidates[:safe_limit]
    has_more = len(candidates) > safe_limit
    next_cursor = _encode_page_cursor(page[-1], scope=scope) if page and has_more else ""
    return page, next_cursor, has_more


def _encode_page_cursor(item: HistoryItem, *, scope: str = "") -> str:
    payload_data = {
        "version": 2,
        "updated_at": item.updated_at,
        "session_id": item.session_id,
    }
    if scope:
        payload_data["scope"] = scope
    payload = json.dumps(
        payload_data,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_page_cursor(cursor: str | None, *, expected_scope: str = "") -> tuple[str, str] | None:
    text = str(cursor or "").strip()
    if not text:
        return None
    try:
        padded = text + ("=" * (-len(text) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session cursor") from exc
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("invalid session cursor")
    if str(payload.get("scope") or "") != str(expected_scope or ""):
        raise ValueError("session cursor does not match this search query")
    updated_at = str(payload.get("updated_at") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not updated_at or not session_id:
        raise ValueError("invalid session cursor")
    return updated_at, session_id


def _search_cursor_scope(query: str) -> str:
    normalized = " ".join(sanitize_text(str(query or "")).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _decode_offset_page_cursor(cursor: str | None) -> int:
    text = str(cursor or "").strip()
    if not text:
        return 0
    try:
        offset = int(text)
    except ValueError as exc:
        raise ValueError("invalid session cursor") from exc
    if offset < 0:
        raise ValueError("invalid session cursor")
    return offset


def _short(text: Any, limit: int = 88) -> str:
    normalized = _clean_truncation_marker(sanitize_text(str(text or "")).replace("\n", " ").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _clean_truncation_marker(text: str) -> str:
    return re.sub(r"\.\.\.\[truncated \d+ chars\]", "...", str(text or ""))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    yield json.loads(text)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _sqlite_dual_write_enabled() -> bool:
    mode = str(os.environ.get("LUCODE_CONTEXT_SQLITE") or "off").strip().lower()
    return mode in {"dual_write", "read_through", "primary"}


def _sqlite_read_through_enabled() -> bool:
    mode = str(os.environ.get("LUCODE_CONTEXT_SQLITE") or "off").strip().lower()
    return mode in {"read_through", "primary"}


def _context_fts_enabled() -> bool:
    raw = str(os.environ.get("LUCODE_CONTEXT_FTS") or "off").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}
