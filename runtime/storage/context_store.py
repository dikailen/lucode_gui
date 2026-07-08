from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.sessions.store import SessionSummary
from runtime.storage.sqlite_store import SCHEMA_VERSION, connect, initialize_sqlite_store


class ContextSQLiteStore:
    """Narrow SQLite context store used as an auxiliary history/index layer."""

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()
        initialize_sqlite_store(self.workspace_root)

    def list_sessions(self, limit: int = 20) -> list[SessionSummary]:
        safe_limit = max(1, int(limit or 20))
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select
                  s.session_id,
                  s.title,
                  s.created_at,
                  s.updated_at,
                  count(m.message_id) as message_count
                from sessions s
                left join messages m on m.session_id = s.session_id
                group by s.session_id
                order by s.updated_at desc
                limit ?
                """,
                (safe_limit,),
            ).fetchall()
        summaries: list[SessionSummary] = []
        for session_id, title, created_at, updated_at, message_count in rows:
            messages = self.load_messages(str(session_id))
            last_user = ""
            last_assistant = ""
            for message in messages:
                role = str(message.get("role") or "").lower()
                content = str(message.get("content") or "")
                if role == "user":
                    last_user = content
                elif role == "assistant":
                    last_assistant = content
            summaries.append(
                SessionSummary(
                    session_id=str(session_id),
                    path=self.workspace_root / ".lucode" / "history" / "sessions" / f"{session_id}.jsonl",
                    created_at=str(created_at or ""),
                    updated_at=str(updated_at or created_at or ""),
                    message_count=int(message_count or 0),
                    last_user=_short(last_user, 160),
                    last_assistant=_short(last_assistant, 160),
                    title=str(title or ""),
                )
            )
        return summaries

    def load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit)) if limit is not None else None
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select role, content, metadata_json
                from messages
                where session_id = ?
                order by created_at, rowid
                """,
                (str(session_id),),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for role, content, metadata_json in rows:
            message: dict[str, Any] = {
                "role": str(role or ""),
                "content": str(content or ""),
            }
            metadata = _client_message_metadata(_json_dict(metadata_json))
            if metadata:
                message["metadata"] = metadata
            if message["role"] and message["content"]:
                messages.append(message)
        if safe_limit is not None:
            return messages[-safe_limit:]
        return messages

    def load_context_summary(self, session_id: str, max_chars: int = 2400) -> str:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select summary
                from context_summaries
                where session_id = ?
                order by created_at, rowid
                """,
                (str(session_id),),
            ).fetchall()
        summaries = [sanitize_text(str(row[0] or "")).strip() for row in rows]
        summaries = [summary for summary in summaries if summary]
        if not summaries:
            return ""
        text = "\n\n".join(summaries[-3:])
        limit = max(400, int(max_chars or 2400))
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def save_session(self, session: dict[str, Any]) -> str:
        session_id = _required_text(session, "session_id")
        created_at = _timestamp(session.get("created_at"))
        updated_at = _timestamp(session.get("updated_at") or created_at)
        metadata = _metadata_json(session.get("metadata"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into sessions(session_id, title, created_at, updated_at, source, schema_version, metadata_json)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(session_id) do update set
                  title = excluded.title,
                  updated_at = excluded.updated_at,
                  source = excluded.source,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    session_id,
                    str(session.get("title") or ""),
                    created_at,
                    updated_at,
                    str(session.get("source") or "sqlite"),
                    SCHEMA_VERSION,
                    metadata,
                ),
            )
        return session_id

    def save_message(self, message: dict[str, Any]) -> str:
        session_id = _required_text(message, "session_id")
        message_id = str(message.get("message_id") or _new_id("msg"))
        created_at = _timestamp(message.get("created_at"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert or ignore into sessions(session_id, title, created_at, updated_at, source, schema_version)
                values (?, '', ?, ?, 'sqlite', ?)
                """,
                (session_id, created_at, created_at, SCHEMA_VERSION),
            )
            connection.execute(
                """
                insert into messages(
                  message_id, session_id, role, content, created_at, source, schema_version, metadata_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(message_id) do update set
                  role = excluded.role,
                  content = excluded.content,
                  source = excluded.source,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    message_id,
                    session_id,
                    _required_text(message, "role"),
                    str(message.get("content") or ""),
                    created_at,
                    str(message.get("source") or "sqlite"),
                    SCHEMA_VERSION,
                    _metadata_json(message.get("metadata")),
                ),
            )
            connection.execute(
                "update sessions set updated_at = ? where session_id = ?",
                (created_at, session_id),
            )
        return message_id

    def save_context_summary(self, summary: dict[str, Any]) -> str:
        session_id = _required_text(summary, "session_id")
        summary_id = str(summary.get("summary_id") or _new_id("summary"))
        created_at = _timestamp(summary.get("created_at"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into context_summaries(
                  summary_id, session_id, summary, created_at, source, schema_version, metadata_json
                )
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(summary_id) do update set
                  summary = excluded.summary,
                  source = excluded.source,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    summary_id,
                    session_id,
                    str(summary.get("summary") or ""),
                    created_at,
                    str(summary.get("source") or "sqlite"),
                    SCHEMA_VERSION,
                    _metadata_json(summary.get("metadata")),
                ),
            )
        return summary_id

    def save_context_ledger_result(self, result: dict[str, Any]) -> str:
        session_id = _required_text(result, "session_id")
        ledger_id = str(result.get("ledger_id") or _new_id("ledger"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into context_ledger_results(
                  ledger_id, session_id, run_id, mode, applied, triggered,
                  estimated_input_tokens, context_window_tokens, created_at,
                  schema_version, metadata_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(ledger_id) do update set
                  mode = excluded.mode,
                  applied = excluded.applied,
                  triggered = excluded.triggered,
                  estimated_input_tokens = excluded.estimated_input_tokens,
                  context_window_tokens = excluded.context_window_tokens,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    ledger_id,
                    session_id,
                    str(result.get("run_id") or ""),
                    str(result.get("mode") or ""),
                    1 if result.get("applied") else 0,
                    1 if result.get("triggered") else 0,
                    _int_value(result.get("estimated_input_tokens")),
                    _int_value(result.get("context_window_tokens")),
                    _timestamp(result.get("created_at")),
                    SCHEMA_VERSION,
                    _metadata_json(result.get("metadata")),
                ),
            )
        return ledger_id

    def save_tool_dehydrated_result(self, result: dict[str, Any]) -> str:
        session_id = _required_text(result, "session_id")
        result_id = str(result.get("result_id") or _new_id("tool"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into tool_dehydrated_results(
                  result_id, session_id, run_id, tool, summary, evidence_ref,
                  raw_artifact_ref, created_at, schema_version, metadata_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(result_id) do update set
                  tool = excluded.tool,
                  summary = excluded.summary,
                  evidence_ref = excluded.evidence_ref,
                  raw_artifact_ref = excluded.raw_artifact_ref,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    result_id,
                    session_id,
                    str(result.get("run_id") or ""),
                    str(result.get("tool") or result.get("tool_name") or ""),
                    str(result.get("summary") or ""),
                    str(result.get("evidence_ref") or ""),
                    str(result.get("raw_artifact_ref") or ""),
                    _timestamp(result.get("created_at")),
                    SCHEMA_VERSION,
                    _metadata_json(result.get("metadata")),
                ),
            )
        return result_id

    def save_evidence_ref(self, evidence: dict[str, Any]) -> str:
        session_id = _required_text(evidence, "session_id")
        ref_id = str(evidence.get("ref_id") or _new_id("evidence"))
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into evidence_refs(
                  ref_id, session_id, run_id, evidence_ref, artifact_ref,
                  source_type, created_at, schema_version, metadata_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(ref_id) do update set
                  evidence_ref = excluded.evidence_ref,
                  artifact_ref = excluded.artifact_ref,
                  source_type = excluded.source_type,
                  schema_version = excluded.schema_version,
                  metadata_json = excluded.metadata_json
                """,
                (
                    ref_id,
                    session_id,
                    str(evidence.get("run_id") or ""),
                    _required_text(evidence, "evidence_ref"),
                    str(evidence.get("artifact_ref") or evidence.get("raw_artifact_ref") or ""),
                    str(evidence.get("source_type") or ""),
                    _timestamp(evidence.get("created_at")),
                    SCHEMA_VERSION,
                    _metadata_json(evidence.get("metadata")),
                ),
            )
        return ref_id


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _metadata_json(value: Any) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        value = {"value": value}
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _client_message_metadata(value: Any) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, dict) else {}
    if set(metadata) <= {"run_id"}:
        return {}
    return metadata


def _short(text: str, limit: int = 88) -> str:
    normalized = sanitize_text(str(text or "")).replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + f"...[truncated {len(normalized) - limit} chars]"


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
