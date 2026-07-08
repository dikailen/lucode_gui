from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


TIMELINE_MODES = {"off", "observe", "enforce"}
EVIDENCE_GATE_MODES = {"off", "observe", "warn", "enforce_high_risk", "enforce_all"}
COMPUTE_PLACEMENT_MODES = {"off", "observe", "enforce"}


@dataclass(frozen=True)
class ReliabilityFlags:
    timeline_ledger: str = "observe"
    evidence_gate: str = "warn"
    compute_placement: str = "observe"

    def to_dict(self) -> dict[str, str]:
        return {
            "timeline_ledger": self.timeline_ledger,
            "evidence_gate": self.evidence_gate,
            "compute_placement": self.compute_placement,
        }


def timeline_mode_from_env(value: str | None = None) -> str:
    raw = str(value if value is not None else os.environ.get("LUCODE_TIMELINE_LEDGER") or "observe")
    mode = raw.strip().lower()
    return mode if mode in TIMELINE_MODES else "observe"


def reliability_flags_from_env() -> ReliabilityFlags:
    return ReliabilityFlags(
        timeline_ledger=timeline_mode_from_env(),
        evidence_gate=_env_mode("LUCODE_EVIDENCE_GATE", EVIDENCE_GATE_MODES, default="warn"),
        compute_placement=_env_mode("LUCODE_COMPUTE_PLACEMENT", COMPUTE_PLACEMENT_MODES, default="observe"),
    )


@dataclass(frozen=True)
class TimelineEvent:
    event_seq: int
    event_type: str
    task_id: str = ""
    status: str = ""
    message: str = ""
    resource_refs: tuple[str, ...] = field(default_factory=tuple)
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_seq": self.event_seq,
            "event_type": self.event_type,
            "task_id": self.task_id,
            "status": self.status,
            "message": self.message,
            "resource_refs": list(self.resource_refs),
            "payload": dict(self.payload),
            "time": self.timestamp,
        }


@dataclass(frozen=True)
class TaskSnapshot:
    snapshot_id: str
    task_id: str
    created_at_event_seq: int
    read_set: tuple[str, ...] = field(default_factory=tuple)
    write_intent: tuple[str, ...] = field(default_factory=tuple)
    mcp: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "task_id": self.task_id,
            "created_at_event_seq": self.created_at_event_seq,
            "read_set": list(self.read_set),
            "write_intent": list(self.write_intent),
            "mcp": list(self.mcp),
        }


@dataclass
class RunTimeline:
    run_id: str
    mode: str = "observe"
    project_root: str = ""
    user_request: str = ""
    current_event_seq: int = 0
    events: list[TimelineEvent] = field(default_factory=list)
    task_snapshots: dict[str, TaskSnapshot] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        project_root: Path | str | None = None,
        user_request: str = "",
        mode: str | None = None,
    ) -> "RunTimeline":
        normalized_mode = timeline_mode_from_env(mode)
        root = ""
        if project_root is not None:
            try:
                root = str(Path(project_root).resolve())
            except OSError:
                root = str(project_root)
        return cls(
            run_id=uuid.uuid4().hex,
            mode=normalized_mode,
            project_root=root,
            user_request=str(user_request or ""),
        )

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def record(
        self,
        event_type: str,
        *,
        task_id: str = "",
        status: str = "",
        message: str = "",
        resource_refs: list[str] | tuple[str, ...] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TimelineEvent | None:
        if not self.enabled:
            return None
        self.current_event_seq += 1
        event = TimelineEvent(
            event_seq=self.current_event_seq,
            event_type=str(event_type or ""),
            task_id=str(task_id or ""),
            status=str(status or ""),
            message=str(message or ""),
            resource_refs=tuple(_clean_strings(resource_refs or [])),
            payload=dict(payload or {}),
        )
        self.events.append(event)
        return event

    def record_task_started(self, task) -> TimelineEvent | None:
        task_id = _task_id(task)
        event = self.record(
            "TaskStarted",
            task_id=task_id,
            status="running",
            message=str(getattr(task, "title", "") or task_id),
            resource_refs=[
                *[f"read:{item}" for item in _clean_strings(getattr(task, "read_set", []) or [])],
                *[f"write:{item}" for item in _clean_strings(getattr(task, "write_intent", []) or [])],
                *[f"mcp:{item}" for item in _clean_strings(getattr(task, "mcp", []) or [])],
            ],
            payload={
                "title": str(getattr(task, "title", "") or ""),
                "skill_id": str(getattr(task, "skill_id", "") or ""),
                "model": str(getattr(task, "model", "") or ""),
            },
        )
        if event is not None and task_id and task_id not in self.task_snapshots:
            self.task_snapshots[task_id] = TaskSnapshot(
                snapshot_id=f"{self.run_id}:{task_id}:{event.event_seq}",
                task_id=task_id,
                created_at_event_seq=event.event_seq,
                read_set=tuple(_clean_strings(getattr(task, "read_set", []) or [])),
                write_intent=tuple(_clean_strings(getattr(task, "write_intent", []) or [])),
                mcp=tuple(_clean_strings(getattr(task, "mcp", []) or [])),
            )
        return event

    def record_task_completed(self, task, *, output_preview: str = "") -> TimelineEvent | None:
        return self.record(
            "TaskCompleted",
            task_id=_task_id(task),
            status="completed",
            message=str(getattr(task, "title", "") or _task_id(task)),
            payload={"output_preview": _preview(output_preview)},
        )

    def record_task_failed(self, task, error: Exception | str) -> TimelineEvent | None:
        message = str(error or "")
        return self.record(
            "TaskFailed",
            task_id=_task_id(task),
            status="failed",
            message=message,
            payload={"reason": _preview(message, limit=240)},
        )

    def record_fast_path_used(self, task, *, tool: str, action: str) -> TimelineEvent | None:
        tool_text = str(tool or "")
        action_text = str(action or "")
        return self.record(
            "FastPathUsed",
            task_id=_task_id(task),
            status="completed",
            message=" ".join(item for item in [tool_text, action_text] if item),
            resource_refs=[f"tool:{tool_text}"] if tool_text else [],
            payload={"tool": tool_text, "action": action_text},
        )

    def record_commit_guard_decision(self, decision, *, task_id: str = "") -> TimelineEvent | None:
        resource_id = str(getattr(decision, "resource_id", "") or "")
        payload = decision.to_dict() if hasattr(decision, "to_dict") else dict(getattr(decision, "__dict__", {}) or {})
        status = str(payload.get("status") or ("accepted" if getattr(decision, "allowed", False) else "rejected"))
        return self.record(
            "CommitGuardChecked",
            task_id=task_id,
            status=status,
            message=str(payload.get("reason") or status),
            resource_refs=[f"file:{resource_id}"] if resource_id else [],
            payload=payload,
        )

    def snapshot(self) -> list[TimelineEvent]:
        return list(self.events)

    def snapshot_for_task(self, task_id: str) -> TaskSnapshot | None:
        return self.task_snapshots.get(str(task_id or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "project_root": self.project_root,
            "current_event_seq": self.current_event_seq,
            "events": [event.to_dict() for event in self.events],
            "task_snapshots": [snapshot.to_dict() for snapshot in self.task_snapshots.values()],
        }


def _task_id(task) -> str:
    return str(getattr(task, "id", "") or "")


def _clean_strings(values) -> list[str]:
    result: list[str] = []
    for value in list(values or []):
        text = str(value or "").strip().replace("\\", "/")
        if text:
            result.append(text)
    return result


def _preview(value: str, *, limit: int = 500) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _env_mode(name: str, allowed: set[str], *, default: str) -> str:
    raw = str(os.environ.get(name) or default).strip().lower()
    return raw if raw in allowed else default
