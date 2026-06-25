from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp_servers.core.operation_log import _redact_text
from runtime.execution.pipeline import PipelineRunState


class FlywheelStore:
    """Small local experience store for project lessons and pipeline summaries."""

    def __init__(self, project_root: Path, cache_dir: Path | None = None):
        self.project_root = project_root.resolve()
        self.cache_dir = (cache_dir or self.project_root / ".agent_cache").resolve()
        self.path = self.cache_dir / "flywheel_memory.jsonl"

    def append_entry(
        self,
        *,
        kind: str,
        summary: str,
        tags: list[str] | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": self._new_id(),
            "time": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(self.project_root),
            "kind": kind,
            "source": source,
            "summary": _redact_text(summary),
            "tags": sorted(set(tags or [])),
            "metadata": self._redact_metadata(metadata or {}),
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


    def upsert_distilled_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Write a distilled memory entry, deduping by metadata.fingerprint."""

        clean_entry = self._normalize_distilled_entry(entry)
        metadata = clean_entry.get("metadata") if isinstance(clean_entry.get("metadata"), dict) else {}
        fingerprint = str(metadata.get("fingerprint") or "").strip()
        if not fingerprint:
            return self.append_entry(
                kind=str(clean_entry.get("kind") or ""),
                summary=str(clean_entry.get("summary") or ""),
                tags=list(clean_entry.get("tags") or []),
                source=str(clean_entry.get("source") or "distiller"),
                metadata=metadata,
            )

        entries = self.load_entries()
        existing_index = next(
            (
                index
                for index, existing in enumerate(entries)
                if isinstance(existing.get("metadata"), dict)
                and str(existing["metadata"].get("fingerprint") or "") == fingerprint
            ),
            None,
        )
        if existing_index is None:
            clean_entry = self._mark_overlapping_conflicts(entries, clean_entry)
            entries.append(clean_entry)
            self._write_entries(entries)
            return clean_entry

        existing = entries[existing_index]
        merged = self._merge_distilled_entries(existing, clean_entry)
        entries[existing_index] = merged
        self._write_entries(entries)
        return merged

    def mark_entry_status(self, entry_id: str, status: str, *, reason: str = "") -> dict[str, Any] | None:
        """Mark an existing memory entry status without changing its identity."""

        clean_entry_id = str(entry_id or "").strip()
        clean_status = str(status or "").strip().lower()
        if not clean_entry_id or not clean_status:
            return None
        entries = self.load_entries()
        for index, entry in enumerate(entries):
            if str(entry.get("id") or "") != clean_entry_id:
                continue
            updated = dict(entry)
            metadata = dict(updated.get("metadata") or {})
            metadata["status"] = clean_status
            if reason:
                metadata["decision_reasons"] = _merge_string_lists(
                    metadata.get("decision_reasons") or [],
                    [str(reason)],
                )
            updated["metadata"] = self._redact_metadata(metadata)
            entries[index] = updated
            self._write_entries(entries)
            return updated
        return None

    def _normalize_distilled_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        now = datetime.now().isoformat(timespec="seconds")
        created_at = str(metadata.get("created_at") or now)
        normalized_metadata = self._redact_metadata(
            {
                **metadata,
                "created_at": created_at,
                "last_seen": str(metadata.get("last_seen") or now),
                "status": str(metadata.get("status") or "active"),
                "evidence_count": int(metadata.get("evidence_count") or 1),
                "decision_reasons": _clean_string_list(metadata.get("decision_reasons") or []),
            }
        )
        return {
            "id": str(entry.get("id") or self._new_id()),
            "time": str(entry.get("time") or now),
            "project_root": str(self.project_root),
            "kind": str(entry.get("kind") or ""),
            "source": str(entry.get("source") or "distiller"),
            "summary": _redact_text(str(entry.get("summary") or "")),
            "tags": sorted(set(str(item) for item in list(entry.get("tags") or []) if str(item).strip())),
            "metadata": normalized_metadata,
        }

    def _merge_distilled_entries(self, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        existing_metadata = dict(existing.get("metadata") or {})
        incoming_metadata = dict(incoming.get("metadata") or {})
        existing_count = int(existing_metadata.get("evidence_count") or 1)
        incoming_count = int(incoming_metadata.get("evidence_count") or 1)
        existing_confidence = _float_value(existing_metadata.get("confidence"), default=0.0)
        incoming_confidence = _float_value(incoming_metadata.get("confidence"), default=0.0)
        existing_base_confidence = _float_value(existing_metadata.get("base_confidence"), default=existing_confidence)
        incoming_base_confidence = _float_value(incoming_metadata.get("base_confidence"), default=incoming_confidence)
        conflict = self._distilled_entries_conflict(existing, incoming)
        merged_count = existing_count + incoming_count

        merged_metadata = {
            **existing_metadata,
            "last_seen": incoming_metadata.get("last_seen") or datetime.now().isoformat(timespec="seconds"),
            "evidence_count": merged_count,
            "decision_reasons": _merge_string_lists(
                existing_metadata.get("decision_reasons") or [],
                incoming_metadata.get("decision_reasons") or [],
            ),
        }
        if conflict:
            merged_metadata["status"] = "conflicted"
            merged_metadata["confidence"] = round(max(0.0, min(existing_confidence, incoming_confidence) - 0.20), 2)
            reasons = list(merged_metadata.get("decision_reasons") or [])
            if "conflicting_evidence" not in reasons:
                reasons.append("conflicting_evidence")
            merged_metadata["decision_reasons"] = reasons
        else:
            base_confidence = max(existing_base_confidence, incoming_base_confidence)
            merged_metadata["status"] = incoming_metadata.get("status") or existing_metadata.get("status") or "active"
            merged_metadata["base_confidence"] = round(base_confidence, 2)
            merged_metadata["confidence"] = _confidence_with_repeated_evidence(base_confidence, merged_count)

        merged = dict(existing)
        merged["summary"] = incoming.get("summary") or existing.get("summary") or ""
        merged["tags"] = sorted(set(list(existing.get("tags") or []) + list(incoming.get("tags") or [])))
        merged["metadata"] = self._redact_metadata(merged_metadata)
        return merged

    def _mark_overlapping_conflicts(self, entries: list[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any]:
        for index, existing in enumerate(entries):
            if not self._distilled_entries_conflict(existing, incoming):
                continue
            if not _entries_scope_action_overlap(existing, incoming):
                continue
            entries[index] = self._mark_conflicted_entry(existing, counterpart=incoming)
            incoming = self._mark_conflicted_entry(incoming, counterpart=existing)
        return incoming

    def _mark_conflicted_entry(self, entry: dict[str, Any], *, counterpart: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(entry.get("metadata") or {})
        current_confidence = _float_value(metadata.get("confidence"), default=0.0)
        counterpart_metadata = counterpart.get("metadata") if isinstance(counterpart.get("metadata"), dict) else {}
        counterpart_confidence = _float_value(counterpart_metadata.get("confidence"), default=current_confidence)
        reasons = _merge_string_lists(
            metadata.get("decision_reasons") or [],
            ["conflicting_evidence", "scope_action_overlap"],
        )
        metadata["status"] = "conflicted"
        metadata["confidence"] = round(max(0.0, min(current_confidence, counterpart_confidence) - 0.20), 2)
        metadata["decision_reasons"] = reasons
        updated = dict(entry)
        updated["metadata"] = self._redact_metadata(metadata)
        return updated

    def _distilled_entries_conflict(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        existing_kind = str(existing.get("kind") or "")
        incoming_kind = str(incoming.get("kind") or "")
        if existing_kind == incoming_kind:
            return False
        success_kinds = {"verification_command", "project_fact", "tool_hint", "path_mapping"}
        failure_kinds = {"failure_lesson", "failure_case"}
        return (existing_kind in success_kinds and incoming_kind in failure_kinds) or (
            existing_kind in failure_kinds and incoming_kind in success_kinds
        )

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_pipeline_state(self, state: PipelineRunState) -> dict[str, Any]:
        completed = sum(1 for task in state.tasks if task.status == "completed")
        failed = sum(1 for task in state.tasks if task.status == "failed")
        skills = sorted({task.skill_id for task in state.tasks if task.skill_id})
        mcp_ids = sorted({mcp_id for task in state.tasks for mcp_id in task.mcp})
        summary = (
            f"Pipeline {state.route_type}: {completed} completed, {failed} failed. "
            f"Reason: {state.reason}. Skills: {', '.join(skills) or 'none'}."
        )
        return self.append_entry(
            kind="pipeline_summary",
            source="pipeline",
            summary=summary,
            tags=[state.route_type, *skills, *mcp_ids],
            metadata={
                "route_type": state.route_type,
                "task_count": len(state.tasks),
                "completed": completed,
                "failed": failed,
            },
        )

    def record_failure_case(
        self,
        *,
        user_request: str,
        attempt_count: int,
        models_used: list[str],
        files_touched: list[str],
        failure_reasons: list[str],
        rollback_status: str,
        lesson: str,
    ) -> dict[str, Any]:
        summary = (
            f"Failure after {attempt_count} attempts. "
            f"Rollback: {rollback_status}. Lesson: {lesson}"
        )
        return self.append_entry(
            kind="failure_case",
            source="repair_loop",
            summary=summary,
            tags=["failure", "repair_loop", rollback_status],
            metadata={
                "user_request": user_request,
                "attempt_count": attempt_count,
                "models_used": models_used,
                "files_touched": files_touched,
                "failure_reasons": failure_reasons,
                "rollback_status": rollback_status,
                "lesson": lesson,
            },
        )

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = [item for item in query.lower().split() if item]
        entries = self.load_entries()
        scored = []
        for entry in entries:
            haystack = " ".join(
                [
                    str(entry.get("kind", "")),
                    str(entry.get("summary", "")),
                    " ".join(entry.get("tags", [])),
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: (item[0], item[1].get("time", "")), reverse=True)
        return [entry for _, entry in scored[: max(1, int(limit or 5))]]

    def load_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _new_id() -> str:
        return datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid4().hex[:8]

    @staticmethod
    def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for key, value in metadata.items():
            if isinstance(value, str):
                redacted[key] = _redact_text(value)
            elif isinstance(value, dict):
                redacted[key] = FlywheelStore._redact_metadata(value)
            elif isinstance(value, list):
                redacted[key] = [_redact_text(item) if isinstance(item, str) else item for item in value]
            else:
                redacted[key] = value
        return redacted

def _clean_string_list(values: Any) -> list[str]:
    result: list[str] = []
    for value in list(values or []) if isinstance(values, list) else []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _merge_string_lists(left: Any, right: Any) -> list[str]:
    result = _clean_string_list(left)
    for item in _clean_string_list(right):
        if item not in result:
            result.append(item)
    return result


def _float_value(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_with_repeated_evidence(base_confidence: float, evidence_count: int) -> float:
    repeat_bonus = min(0.15, max(0, int(evidence_count or 1) - 1) * 0.05)
    return round(min(0.95, max(0.0, float(base_confidence)) + repeat_bonus), 2)


def _entries_scope_action_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_metadata = left.get("metadata") if isinstance(left.get("metadata"), dict) else {}
    right_metadata = right.get("metadata") if isinstance(right.get("metadata"), dict) else {}
    left_scopes = _normalized_terms(left_metadata.get("scope"))
    right_scopes = _normalized_terms(right_metadata.get("scope"))
    scope_overlap = any(
        _terms_overlap(left_scope, right_scope)
        for left_scope in left_scopes
        for right_scope in right_scopes
    )
    left_action = _normalize_term(left_metadata.get("action"))
    right_action = _normalize_term(right_metadata.get("action"))
    action_overlap = _terms_overlap(left_action, right_action) if left_action and right_action else False
    return scope_overlap and (action_overlap or not left_action or not right_action)


def _normalized_terms(values: Any) -> list[str]:
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values or []) if isinstance(values, (list, tuple, set)) else []
    result: list[str] = []
    for item in items:
        term = _normalize_term(item)
        if term and term not in result:
            result.append(term)
    return result


def _normalize_term(value: Any) -> str:
    text = _redact_text(str(value or "")).replace("\\", "/").strip().lower()
    text = text.strip("`'\"()[]{}<>，。；;:：")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/")


def _terms_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    if "/" in left or "/" in right or "." in left or "." in right:
        return left.startswith(right + "/") or right.startswith(left + "/") or left in right or right in left
    return left in right.split() or right in left.split()
