from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CorruptCheckpointError(ValueError):
    pass


class CorruptRunEventError(ValueError):
    pass


@dataclass(frozen=True)
class RecoverySchemaInitResult:
    db_path: Path
    schema_version: str
    created: bool


@dataclass(frozen=True)
class RunJournalEvent:
    run_id: str
    session_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class RunCheckpoint:
    checkpoint_id: str
    run_id: str
    kind: str
    event_seq: int
    state: dict[str, Any]
    compatibility: dict[str, Any]
    checksum: str
    created_at: str


@dataclass(frozen=True)
class RecoveryDecision:
    run_id: str
    session_id: str
    action: str
    reason_code: str
    requires_user_action: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "action": self.action,
            "reason_code": self.reason_code,
            "requires_user_action": self.requires_user_action,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checkpoint_checksum(*, kind: str, state: dict[str, Any], compatibility: dict[str, Any]) -> str:
    payload = canonical_json(
        {
            "kind": str(kind or ""),
            "state": state,
            "compatibility": compatibility,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
