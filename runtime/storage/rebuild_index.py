from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text
from runtime.history.store import HistoryStore
from runtime.storage.context_store import ContextSQLiteStore
from runtime.storage.freshness import jsonl_source_fingerprint


@dataclass
class RebuildResult:
    workspace_root: Path
    sessions: int = 0
    messages: int = 0
    summaries: int = 0
    skipped_events: int = 0
    errors: list[str] = field(default_factory=list)


def rebuild_sqlite_from_jsonl(workspace_root: Path | str) -> RebuildResult:
    """Rebuild the auxiliary SQLite index from authoritative JSONL history."""

    root = Path(workspace_root).resolve()
    result = RebuildResult(workspace_root=root)
    history_store = HistoryStore(root)
    sqlite_store = ContextSQLiteStore(root)
    sessions_dir = history_store.sessions_dir
    if not sessions_dir.is_dir():
        return result

    for path in sorted(sessions_dir.glob("*.jsonl")):
        try:
            _rebuild_session(history_store, sqlite_store, path, result)
        except Exception as exc:
            result.errors.append(f"{path.name}: {exc}")
    return result


def _rebuild_session(
    history_store: HistoryStore,
    sqlite_store: ContextSQLiteStore,
    path: Path,
    result: RebuildResult,
) -> None:
    session_id = path.stem
    events = history_store.load_events(session_id)
    summary = history_store._summarize(path)
    if summary is None and not events:
        return
    title = summary.title if summary is not None else ""
    created_at = summary.created_at if summary is not None else ""
    updated_at = summary.updated_at if summary is not None else ""
    for event in events:
        if event.get("type") == "session_metadata":
            title = sanitize_text(str(event.get("title") or title)).strip()
            created_at = created_at or str(event.get("timestamp") or "")
            updated_at = str(event.get("timestamp") or updated_at or "")

    sqlite_store.save_session(
        {
            "session_id": session_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at or created_at,
            "source": "jsonl_rebuild",
            "source_fingerprint": "",
            "metadata": {"jsonl_path": str(path)},
        }
    )
    result.sessions += 1

    message_index = 0
    for event in events:
        if event.get("type") != "message":
            result.skipped_events += 1
            continue
        message_index += 1
        timestamp = str(event.get("timestamp") or updated_at or created_at or "")
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        sqlite_store.save_message(
            {
                "message_id": f"jsonl:{session_id}:message:{message_index:06d}",
                "session_id": session_id,
                "role": str(event.get("role") or ""),
                "content": str(event.get("content") or ""),
                "created_at": timestamp,
                "metadata": metadata,
                "source": "jsonl_rebuild",
            }
        )
        result.messages += 1
        context_summary = redact_sensitive_text(
            sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
        )
        if context_summary:
            sqlite_store.save_context_summary(
                {
                    "summary_id": f"jsonl:{session_id}:summary:{message_index:06d}",
                    "session_id": session_id,
                    "summary": context_summary,
                    "created_at": timestamp,
                    "source": "jsonl_rebuild",
                    "metadata": {"message_index": message_index},
                }
            )
            result.summaries += 1

    sqlite_store.update_session_source_fingerprint(session_id, jsonl_source_fingerprint(path))
