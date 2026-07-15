from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


RUNTIME_SERVER_SCHEMA_VERSION = "runtime_server.v1"
MODEL_LIST_SCHEMA_VERSION = "models.v1"
SESSION_SCHEMA_VERSION = "session.v1"
SESSION_MESSAGES_SCHEMA_VERSION = "messages.v1"
RUN_SCHEMA_VERSION = "run.v1"
RUN_LIST_SCHEMA_VERSION = "runs.v1"
RUN_EVENT_SCHEMA_VERSION = "run_event.v1"
TERMINAL_SCHEMA_VERSION = "terminal.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ServerSession:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    display_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "title": self.title,
            "display_title": self.display_title or shorten_title(self.title),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ServerRun:
    run_id: str
    session_id: str
    user_input: str
    status: str
    created_at: str
    updated_at: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    inline_files: list[dict[str, str]] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.attachments:
            payload["attachments"] = [dict(item) for item in self.attachments]
        return payload


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    session_id: str
    seq: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_EVENT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "seq": self.seq,
            "type": self.type,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }


def shorten_title(title: str, limit: int = 36) -> str:
    text = str(title or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."
