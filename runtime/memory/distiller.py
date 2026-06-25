from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from mcp_servers.core.operation_log import _redact_text
from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text


ACCEPTED_KINDS = {"project_fact", "verification_command", "failure_lesson", "path_mapping", "tool_hint"}


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    summary: str
    action: str = ""
    scope: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    source: str = "distiller"
    evidence: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistillationDecision:
    candidate: MemoryCandidate
    accepted: bool
    status: str
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    entry: dict[str, Any] | None = None
    fingerprint: str = ""
    injection_policy: str = "none"
    scope: tuple[str, ...] = field(default_factory=tuple)
    decision_reasons: tuple[str, ...] = field(default_factory=tuple)


def distill_run_experience(run_state: Any, audit: Any) -> list[DistillationDecision]:
    """Return conservative, rule-generated memory decisions for one run.

    Stage 4 starts deliberately narrow: a candidate needs concrete scope and
    evidence before it can become a long-term flywheel entry.
    """

    candidates = _collect_candidates(run_state, audit)
    if not candidates:
        candidates = [_natural_language_candidate(run_state, audit)]
    return [evaluate_memory_candidate(candidate, audit) for candidate in candidates]


def evaluate_memory_candidate(candidate: MemoryCandidate, audit: Any) -> DistillationDecision:
    return _decide_candidate(candidate, audit)


def _collect_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for extractor in _candidate_extractors():
        candidates.extend(extractor(run_state, audit))
    return candidates


def _candidate_extractors():
    return (
        _verification_command_candidates,
        _path_mapping_candidates,
        _tool_hint_candidates,
        _project_fact_candidates,
        _failure_lesson_candidates,
    )


def _verification_command_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    if not bool(getattr(audit, "passed", False)):
        return []
    candidates: list[MemoryCandidate] = []
    for task in list(getattr(run_state, "tasks", []) or []):
        verification = str(getattr(task, "verification", "") or "")
        for report in _parse_configured_verification_commands(verification):
            if report.get("returncode") != 0:
                continue
            command = str(report.get("command") or "").strip()
            if not command:
                continue
            scope = _scope_for_task(task, audit)
            evidence = [f"command={command}", "returncode=0"]
            stdout = str(report.get("stdout") or "").strip()
            if stdout:
                evidence.append(f"stdout={stdout}")
            candidates.append(
                MemoryCandidate(
                    kind="verification_command",
                    summary=f"Successful project verification command: {command}",
                    action=command,
                    scope=scope,
                    tags=tuple(_tags_for_scope(scope, extra=["verification", "command"])),
                    evidence=tuple(evidence),
                    metadata={
                        "command": command,
                        "returncode": 0,
                        "injection_policy": "auto",
                        "decision_reasons": ["audit_passed", "configured_verification_returncode_0"],
                    },
                )
            )
    return candidates


def _path_mapping_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    if not bool(getattr(audit, "passed", False)):
        return []
    candidates: list[MemoryCandidate] = []
    for task in list(getattr(run_state, "tasks", []) or []):
        if str(getattr(task, "status", "") or "").strip().lower() != "completed":
            continue
        path = _primary_path_for_task(task, audit)
        if not path:
            continue
        task_intent = _task_intent(task)
        action = f"{task_intent} -> {path}"
        evidence = _path_mapping_evidence(task, audit, path)
        candidates.append(
            MemoryCandidate(
                kind="path_mapping",
                summary=f"Task intent maps to project path: {action}",
                action=action,
                scope=(path,),
                tags=tuple(_tags_for_scope((path,), extra=["path", "mapping"])),
                evidence=evidence,
                metadata={
                    "path": path,
                    "task_id": str(getattr(task, "id", "") or ""),
                    "task_intent": task_intent,
                    "injection_policy": "auto",
                    "decision_reasons": [
                        "audit_passed",
                        "task_completed",
                        "path_scope_present",
                        "path_evidence_present",
                    ],
                },
            )
        )
    return candidates


def _tool_hint_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    if not bool(getattr(audit, "passed", False)):
        return []
    candidates: list[MemoryCandidate] = []
    candidates.extend(_verification_tool_hint_candidates(run_state, audit))
    candidates.extend(_event_tool_hint_candidates(run_state, audit))
    candidates.extend(_worker_report_tool_hint_candidates(run_state, audit))
    return candidates


def _verification_tool_hint_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for task in list(getattr(run_state, "tasks", []) or []):
        if not _task_completed(task):
            continue
        verification = str(getattr(task, "verification", "") or "")
        for report in _parse_configured_verification_commands(verification):
            if report.get("returncode") != 0:
                continue
            raw_command = str(report.get("command") or "").strip()
            if not raw_command:
                continue
            scope = _tool_hint_scope_for_command(task, audit, raw_command)
            if not scope:
                continue
            command = _clean_text(raw_command)
            tool = _command_tool_name(raw_command)
            task_intent = _task_intent(task)
            evidence = [f"command={command}", "returncode=0", f"tool={tool}"]
            stdout = _clean_text(str(report.get("stdout") or "")).strip()
            if stdout:
                evidence.append(f"stdout={stdout}")
            candidates.append(
                MemoryCandidate(
                    kind="tool_hint",
                    summary=f"Use {tool} verification command for {task_intent}: {command}",
                    action=command,
                    scope=scope,
                    tags=tuple(_tags_for_scope(scope, extra=["tool", tool, "verification", "command"])),
                    evidence=tuple(evidence),
                    metadata={
                        "command": command,
                        "returncode": 0,
                        "tool": tool,
                        "action": command,
                        "source": "verification_command",
                        "task_id": str(getattr(task, "id", "") or ""),
                        "task_intent": task_intent,
                        "injection_policy": "auto",
                        "decision_reasons": [
                            "audit_passed",
                            "task_completed",
                            "verification_returncode_0",
                            "scoped_command",
                        ],
                    },
                )
            )
    return candidates

def _project_fact_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    if not bool(getattr(audit, "passed", False)):
        return []

    candidates = sorted(
        [
        *_worker_report_project_fact_candidates(run_state, audit),
        *_repeated_task_project_fact_candidates(run_state, audit),
        ],
        key=lambda candidate: (
            _score_project_fact_candidate(candidate, audit),
            len(candidate.evidence),
            str(candidate.metadata.get("fact_source") or ""),
        ),
        reverse=True,
    )
    result: list[MemoryCandidate] = []
    seen_paths: set[str] = set()
    for candidate in candidates:
        path = candidate.scope[0] if candidate.scope else ""
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        result.append(candidate)
    return result


def _repeated_task_project_fact_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    by_path: dict[str, dict[str, Any]] = {}
    for task in list(getattr(run_state, "tasks", []) or []):
        if not _task_completed(task):
            continue
        task_id = str(getattr(task, "id", "") or "").strip()
        title = _task_intent(task)
        for source_name, raw_paths in (
            ("read_set", list(getattr(task, "read_set", []) or [])),
            ("write_intent", list(getattr(task, "write_intent", []) or [])),
        ):
            for path in _clean_tuple(raw_paths):
                if not _is_safe_project_fact_path(path):
                    continue
                data = by_path.setdefault(path, {"task_ids": set(), "titles": [], "evidence": []})
                if task_id:
                    data["task_ids"].add(task_id)
                if title:
                    data["titles"].append(title)
                data["evidence"].append(f"task:{task_id or 'unknown'}:{source_name}={path}")

    audit_paths = set(_clean_tuple(getattr(audit, "files_touched", []) or []))
    candidates: list[MemoryCandidate] = []
    for path, data in by_path.items():
        task_ids = sorted(data["task_ids"])
        if len(task_ids) < 2:
            continue
        evidence = list(data["evidence"])
        if path in audit_paths:
            evidence.append(f"audit_file={path}")
        candidates.append(
            _project_fact_candidate(
                path=path,
                role=_project_fact_role_for_path(path, data["titles"]),
                fact_source="repeated_task_touch",
                evidence=evidence,
                task_ids=task_ids,
                decision_reasons=[
                    "audit_passed",
                    "task_completed",
                    "file_evidence_present",
                    "repeated_path_touch",
                ],
            )
        )
    return candidates


def _worker_report_project_fact_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    del audit
    candidates: list[MemoryCandidate] = []
    tasks_by_id = _tasks_by_id(run_state)
    for report in _worker_reports(run_state):
        if not _status_successful(str(getattr(report, "status", "") or "")):
            continue
        task_id = str(getattr(report, "task_id", "") or "").strip()
        task = tasks_by_id.get(task_id)
        if task_id and task is None:
            continue
        if task is not None and not _task_completed(task):
            continue
        raw_summary = str(getattr(report, "summary", "") or "")
        if _contains_raw_secret(raw_summary):
            continue
        summary = _clean_text(raw_summary)
        if not _looks_like_project_fact_statement(summary):
            continue
        report_paths = _clean_tuple(
            [
                *list(getattr(report, "files_read", []) or []),
                *list(getattr(report, "files_written", []) or []),
            ]
        )
        summary_paths = _paths_from_text(summary)
        paths = _clean_tuple([*summary_paths, *report_paths])
        for path in paths:
            if not _is_safe_project_fact_path(path):
                continue
            if summary_paths and path not in summary_paths:
                continue
            evidence = [
                f"worker_report={task_id or 'unknown'}",
                f"file_evidence={path}",
                f"summary={_one_line(summary, 180)}",
            ]
            if path in report_paths:
                evidence.append(f"worker_report_file={path}")
            candidates.append(
                _project_fact_candidate(
                    path=path,
                    role=_project_fact_role_from_summary(summary, path),
                    fact_source="worker_report_role",
                    evidence=evidence,
                    task_ids=[task_id] if task_id else [],
                    decision_reasons=[
                        "audit_passed",
                        *(["task_completed"] if task is not None else []),
                        "worker_report_role_statement",
                        "file_evidence_present",
                    ],
                )
            )
    return candidates


def _project_fact_candidate(
    *,
    path: str,
    role: str,
    fact_source: str,
    evidence: Iterable[str],
    task_ids: Iterable[str],
    decision_reasons: Iterable[str],
) -> MemoryCandidate:
    clean_path = _normalize_scope(path)
    clean_role = _one_line(role or _project_fact_role_from_path(clean_path), 100)
    summary = f"{clean_role} is implemented in {clean_path}."
    return MemoryCandidate(
        kind="project_fact",
        summary=summary,
        action=f"module_role:{clean_path}",
        scope=(clean_path,),
        tags=tuple(_tags_for_scope((clean_path,), extra=["project", "fact", "module"])),
        evidence=tuple(_clean_text(str(item)) for item in evidence if str(item).strip()),
        metadata={
            "fact_type": "module_role",
            "fact_source": fact_source,
            "path": clean_path,
            "task_ids": [str(item) for item in task_ids if str(item).strip()],
            "injection_policy": "auto",
            "decision_reasons": list(decision_reasons),
        },
    )


def _failure_lesson_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    if bool(getattr(audit, "passed", False)):
        return []
    if not bool(getattr(audit, "rollback_happened", False)):
        return []
    scope = _clean_tuple(getattr(audit, "files_touched", []) or [])
    if not scope:
        for task in list(getattr(run_state, "tasks", []) or []):
            scope = scope + tuple(item for item in _scope_for_task(task, audit) if item not in scope)
    issues = [str(item).strip() for item in list(getattr(audit, "remaining_issues", []) or []) if str(item).strip()]
    errors = [str(item).strip() for item in list(getattr(run_state, "errors", []) or []) if str(item).strip()]
    evidence = tuple(_clean_text(item) for item in [*issues, *errors, str(getattr(audit, "rollback_message", "") or "")] if str(item).strip())
    if not evidence:
        return []
    action = "narrow scope before retrying failed verification"
    summary = _one_line(
        "Failure lesson after final audit failure and rollback: " + "; ".join(evidence[:3]),
        260,
    )
    return [
        MemoryCandidate(
            kind="failure_lesson",
            summary=summary,
            action=action,
            scope=scope,
            tags=tuple(_tags_for_scope(scope, extra=["failure", "repair_loop"])),
            evidence=evidence,
            metadata={
                "injection_policy": "planner_candidate",
                "rollback_happened": True,
                "failure_reasons": list(evidence[:6]),
                "decision_reasons": ["audit_failed", "rollback_happened", "planner_candidate_only"],
            },
        )
    ]


def _natural_language_candidate(run_state: Any, audit: Any) -> MemoryCandidate:
    summary_parts = [
        str(getattr(run_state, "user_request", "") or ""),
        str(getattr(audit, "summary", "") or ""),
    ]
    for task in list(getattr(run_state, "tasks", []) or [])[:3]:
        output = str(getattr(task, "output_preview", "") or "").strip()
        if output:
            summary_parts.append(output)
    summary = _one_line(" ".join(part for part in summary_parts if part), 240)
    return MemoryCandidate(
        kind="natural_language_note",
        summary=summary or "No reusable structured experience was found.",
        source="distiller",
    )


def _decide_candidate(candidate: MemoryCandidate, audit: Any) -> DistillationDecision:
    rejection_reasons: list[str] = []
    if not candidate.scope:
        rejection_reasons.append("missing_scope")
    if not candidate.evidence:
        rejection_reasons.append("missing_evidence")
    if candidate.kind == "natural_language_note":
        rejection_reasons.append("natural_language_only")
    if candidate.kind not in ACCEPTED_KINDS:
        rejection_reasons.append("unsupported_kind")

    confidence = _score_candidate(candidate, audit, rejection_reasons)
    accepted = not rejection_reasons and confidence >= 0.50
    status = "active" if accepted else "rejected"
    reasons = _decision_reasons(candidate, audit, rejection_reasons, accepted=accepted)
    entry = _entry_for_candidate(candidate, confidence, status, reasons) if accepted else None
    fingerprint = _decision_fingerprint(candidate, entry)
    injection_policy = str(candidate.metadata.get("injection_policy") or "none").strip() or "none"
    return DistillationDecision(
        candidate=candidate,
        accepted=accepted,
        status=status,
        confidence=confidence,
        reasons=tuple(reasons),
        entry=entry,
        fingerprint=fingerprint,
        injection_policy=injection_policy,
        scope=tuple(candidate.scope),
        decision_reasons=tuple(reasons),
    )


def _score_candidate(candidate: MemoryCandidate, audit: Any, reasons: Iterable[str]) -> float:
    if candidate.kind == "failure_lesson":
        score = 0.0
        if not bool(getattr(audit, "passed", False)):
            score += 0.35
        if candidate.evidence:
            score += 0.15
        if candidate.scope:
            score += 0.10
        if bool(getattr(audit, "rollback_happened", False)):
            score += 0.05
        return max(0.0, min(0.70, round(score, 2)))
    if candidate.kind == "project_fact":
        return _score_project_fact_candidate(candidate, audit)

    score = 0.0
    if bool(getattr(audit, "passed", False)):
        score += 0.35
    else:
        score -= 0.30
    if candidate.evidence:
        score += 0.25
    if candidate.scope or candidate.action:
        score += 0.15
    if candidate.kind == "verification_command":
        score += 0.10
    if candidate.kind == "path_mapping":
        score += 0.15
    if candidate.kind == "tool_hint":
        score += 0.15
    if "natural_language_only" in set(reasons):
        score -= 0.20
    return max(0.0, min(1.0, round(score, 2)))


def _score_project_fact_candidate(candidate: MemoryCandidate, audit: Any) -> float:
    if not bool(getattr(audit, "passed", False)):
        return 0.0
    if not candidate.scope or not candidate.evidence:
        return 0.0
    fact_source = str(candidate.metadata.get("fact_source") or "").strip()
    if fact_source == "repeated_task_touch":
        return 0.85
    if fact_source == "worker_report_role":
        return 0.65
    return 0.75


def _decision_reasons(
    candidate: MemoryCandidate,
    audit: Any,
    rejection_reasons: Iterable[str],
    *,
    accepted: bool,
) -> list[str]:
    if not accepted:
        return list(rejection_reasons)
    reasons = [str(item) for item in list(candidate.metadata.get("decision_reasons") or []) if str(item).strip()]
    if bool(getattr(audit, "passed", False)):
        _append_unique(reasons, "audit_passed")
    else:
        _append_unique(reasons, "audit_failed")
    if candidate.kind == "verification_command":
        _append_unique(reasons, "configured_verification_returncode_0")
    if candidate.kind == "failure_lesson":
        _append_unique(reasons, "planner_candidate_only")
        if bool(getattr(audit, "rollback_happened", False)):
            _append_unique(reasons, "rollback_happened")
    return reasons


def _decision_fingerprint(candidate: MemoryCandidate, entry: dict[str, Any] | None) -> str:
    if isinstance(entry, dict):
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        value = str(metadata.get("fingerprint") or "").strip()
        if value:
            return value
    return _fingerprint(candidate.kind, candidate.scope, candidate.action or candidate.summary)


def _entry_for_candidate(
    candidate: MemoryCandidate,
    confidence: float,
    status: str,
    reasons: Iterable[str],
) -> dict[str, Any]:
    timestamp = datetime.now().isoformat(timespec="seconds")
    fingerprint = _fingerprint(candidate.kind, candidate.scope, candidate.action or candidate.summary)
    return {
        "kind": candidate.kind,
        "summary": _clean_text(candidate.summary),
        "tags": sorted(set(candidate.tags)),
        "source": candidate.source,
        "metadata": {
            **dict(candidate.metadata),
            "confidence": confidence,
            "scope": list(candidate.scope),
            "status": status,
            "fingerprint": fingerprint,
            "evidence_count": max(1, len(candidate.evidence)),
            "evidence": [_clean_text(item) for item in candidate.evidence],
            "decision_reasons": list(reasons),
            "action": _clean_text(candidate.action or candidate.summary),
            "created_at": timestamp,
            "last_seen": timestamp,
        },
    }


def _parse_configured_verification_commands(text: str) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- command=") or line.startswith("command="):
            if current:
                reports.append(current)
            current = {"command": line.split("command=", 1)[1].strip()}
            continue
        if current is None:
            continue
        if line.startswith("returncode="):
            current["returncode"] = _int_value(line.split("=", 1)[1].strip(), default=None)
        elif line.startswith("stdout="):
            current["stdout"] = line.split("=", 1)[1].strip()
        elif line.startswith("stderr="):
            current["stderr"] = line.split("=", 1)[1].strip()
    if current:
        reports.append(current)
    return reports



def _event_tool_hint_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    tasks_by_id = _tasks_by_id(run_state)
    for event in _run_events(run_state):
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type not in {"FastPathUsed", "ToolInvoked"}:
            continue
        payload = _dict_value(getattr(event, "payload", {}) or {})
        if not _tool_call_successful(payload, status=str(getattr(event, "status", "") or "")):
            continue
        task_id = str(getattr(event, "task_id", "") or "")
        task = tasks_by_id.get(task_id)
        if task_id and task is None:
            continue
        if task is not None and not _task_completed(task):
            continue
        tool = _tool_call_tool(payload, event=event, event_type=event_type)
        action = _tool_call_action(payload, event_type=event_type)
        if not tool or not action:
            continue
        scope = _tool_call_scope(task, audit, payload)
        if not scope:
            continue
        source = "fast_path_event" if event_type == "FastPathUsed" else "tool_event"
        reason = "fast_path_success" if event_type == "FastPathUsed" else "tool_event_success"
        candidates.append(
            _tool_call_candidate(
                task=task,
                audit=audit,
                tool=tool,
                action=action,
                scope=scope,
                source=source,
                success_reason=reason,
                evidence=[f"event_type={event_type}", f"status={str(getattr(event, 'status', '') or '')}"],
            )
        )
    return candidates


def _worker_report_tool_hint_candidates(run_state: Any, audit: Any) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    tasks_by_id = _tasks_by_id(run_state)
    for report in _worker_reports(run_state):
        if not _status_successful(str(getattr(report, "status", "") or "")):
            continue
        task_id = str(getattr(report, "task_id", "") or "")
        task = tasks_by_id.get(task_id)
        if task_id and task is None:
            continue
        if task is not None and not _task_completed(task):
            continue
        for raw_call in list(getattr(report, "tool_calls", []) or []):
            payload = _dict_value(raw_call)
            if not _tool_call_successful(payload, status=str(payload.get("status") or "")):
                continue
            tool = _tool_call_tool(payload, event=None, event_type="WorkerReport")
            action = _tool_call_action(payload, event_type="WorkerReport")
            if not tool or not action:
                continue
            scope = _tool_call_scope(task, audit, payload, report=report)
            if not scope:
                continue
            candidates.append(
                _tool_call_candidate(
                    task=task,
                    audit=audit,
                    tool=tool,
                    action=action,
                    scope=scope,
                    source="worker_report",
                    success_reason="worker_report_tool_success",
                    evidence=[f"worker_report={task_id}", f"status={str(payload.get('status') or '')}"],
                )
            )
    return candidates


def _tool_call_candidate(
    *,
    task: Any,
    audit: Any,
    tool: str,
    action: str,
    scope: tuple[str, ...],
    source: str,
    success_reason: str,
    evidence: Iterable[str] = (),
) -> MemoryCandidate:
    del audit
    clean_tool = _clean_text(tool)
    clean_action = _clean_text(action)
    clean_source = _clean_text(source)
    task_id = str(getattr(task, "id", "") or "") if task is not None else ""
    task_intent = _task_intent(task) if task is not None else "task"
    evidence_items = [
        f"tool={clean_tool}",
        f"action={clean_action}",
        f"source={clean_source}",
        *[_clean_text(str(item)) for item in evidence if str(item).strip()],
    ]
    if task_id:
        evidence_items.append(f"task_id={task_id}")
    return MemoryCandidate(
        kind="tool_hint",
        summary=_one_line(f"Use {clean_tool} tool for {task_intent}: {clean_action}", 240),
        action=clean_action,
        scope=scope,
        tags=tuple(_tags_for_scope(scope, extra=["tool", clean_tool, clean_source])),
        evidence=tuple(evidence_items),
        metadata={
            "tool": clean_tool,
            "action": clean_action,
            "source": clean_source,
            "task_id": task_id,
            "task_intent": task_intent,
            "injection_policy": "auto",
            "decision_reasons": [
                "audit_passed",
                *(["task_completed"] if task is not None else []),
                success_reason,
                "scoped_tool_hint",
            ],
        },
    )


def _run_events(run_state: Any) -> list[Any]:
    bus = getattr(run_state, "event_bus", None)
    if bus is None or not hasattr(bus, "snapshot"):
        return []
    try:
        return list(bus.snapshot())
    except Exception:
        return []


def _worker_reports(run_state: Any) -> list[Any]:
    return list(getattr(run_state, "worker_reports", []) or [])


def _tasks_by_id(run_state: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for task in list(getattr(run_state, "tasks", []) or []):
        task_id = str(getattr(task, "id", "") or "")
        if task_id:
            result[task_id] = task
    return result


def _task_completed(task: Any) -> bool:
    return str(getattr(task, "status", "") or "").strip().lower() == "completed"


def _tool_call_successful(payload: dict[str, Any], *, status: str = "") -> bool:
    values = [
        str(status or "").strip().lower(),
        str(payload.get("status") or "").strip().lower(),
        str(payload.get("outcome") or "").strip().lower(),
        str(payload.get("decision") or "").strip().lower(),
    ]
    blocked = {"pending", "rejected", "denied", "blocked", "failed", "error", "cancelled", "canceled"}
    if any(value in blocked for value in values if value):
        return False
    return any(_status_successful(value) for value in values if value)


def _status_successful(value: str) -> bool:
    return str(value or "").strip().lower() in {
        "approved",
        "success",
        "successful",
        "completed",
        "auto_approved",
        "ok",
        "done",
        "supervisor_auto_approved",
    }


def _tool_call_tool(payload: dict[str, Any], *, event: Any, event_type: str) -> str:
    tool = str(payload.get("tool") or payload.get("tool_name") or "").strip()
    if not tool and event is not None:
        tool = str(getattr(event, "agent", "") or "").strip()
    if not tool and event_type == "FastPathUsed":
        tool = "fast_path"
    return _clean_text(tool)


def _tool_call_action(payload: dict[str, Any], *, event_type: str) -> str:
    arguments = _dict_value(payload.get("arguments_summary") or {})
    command = str(payload.get("command") or arguments.get("command") or "").strip()
    if command:
        return _clean_text(command)
    return _clean_text(str(payload.get("action") or event_type or ""))


def _tool_call_scope(
    task: Any,
    audit: Any,
    payload: dict[str, Any],
    *,
    report: Any | None = None,
) -> tuple[str, ...]:
    values: list[Any] = []
    if task is not None:
        values.extend(_scope_for_task(task, audit))
    else:
        values.extend(list(getattr(audit, "files_touched", []) or []))
    values.extend(_paths_from_tool_payload(payload))
    if report is not None:
        values.extend(list(getattr(report, "files_read", []) or []))
        values.extend(list(getattr(report, "files_written", []) or []))
    return _clean_tuple(value for value in values if _looks_like_project_path(str(value or "")))


def _paths_from_tool_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    paths: list[Any] = []
    for touched in list(payload.get("files_touched") or []):
        if isinstance(touched, dict):
            paths.append(touched.get("path"))
    arguments = _dict_value(payload.get("arguments_summary") or {})
    for key in ("path", "target", "target_path", "file_path"):
        paths.append(arguments.get(key))
    for key in ("paths", "target_paths", "patch_paths"):
        _extend_values(paths, arguments.get(key))
    command = str(payload.get("command") or arguments.get("command") or "").strip()
    if command:
        paths.extend(_paths_from_command(command))
    return _clean_tuple(value for value in paths if _looks_like_project_path(str(value or "")))


def _extend_values(values: list[Any], raw: Any) -> None:
    if isinstance(raw, (list, tuple, set)):
        values.extend(list(raw))
    elif raw:
        values.append(raw)


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}

def _primary_path_for_task(task: Any, audit: Any) -> str:
    candidates = _clean_tuple(
        [
            *list(getattr(task, "write_intent", []) or []),
            *list(getattr(task, "read_set", []) or []),
            *list(getattr(audit, "files_touched", []) or []),
        ]
    )
    for path in candidates:
        if _looks_like_project_path(path):
            return path
    return ""


def _path_mapping_evidence(task: Any, audit: Any, path: str) -> tuple[str, ...]:
    evidence: list[str] = ["audit_passed", "task_completed"]
    write_paths = set(_clean_tuple(getattr(task, "write_intent", []) or []))
    read_paths = set(_clean_tuple(getattr(task, "read_set", []) or []))
    audit_paths = set(_clean_tuple(getattr(audit, "files_touched", []) or []))
    if path in write_paths:
        evidence.append(f"write_intent={path}")
    if path in read_paths:
        evidence.append(f"read_set={path}")
    if path in audit_paths:
        evidence.append(f"audit_file={path}")
    return tuple(evidence)


def _task_intent(task: Any) -> str:
    title = _clean_text(str(getattr(task, "title", "") or "")).strip()
    if title:
        return _one_line(title, 80)
    task_id = _clean_text(str(getattr(task, "id", "") or "")).strip()
    if task_id:
        return task_id
    return "task"


def _looks_like_project_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith(".") and text in {".", ".."}:
        return False
    if any(marker in text for marker in ("<redacted", "[redacted")):
        return False
    return "/" in text or "\\" in text or "." in text



def _tool_hint_scope_for_command(task: Any, audit: Any, command: str) -> tuple[str, ...]:
    return _clean_tuple([*_scope_for_task(task, audit), *_paths_from_command(command)])


def _paths_from_command(command: str) -> tuple[str, ...]:
    return _paths_from_text(command)


def _paths_from_text(text: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for match in re.findall(r"[A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.-]+)+|[A-Za-z0-9_.-]+\.py", str(text or "")):
        path = str(match or "").strip().rstrip(".,;:)]}\"'")
        if _looks_like_project_path(path):
            candidates.append(path)
    return _clean_tuple(candidates)


def _command_tool_name(command: str) -> str:
    normalized = re.sub(r"\s+", " ", str(command or "").strip()).lower()
    parts = normalized.split()
    if "pytest" in parts or "-m pytest" in normalized:
        return "pytest"
    if parts:
        first = parts[0].replace("\\", "/").rsplit("/", 1)[-1]
        return first or "command"
    return "command"

def _scope_for_task(task: Any, audit: Any) -> tuple[str, ...]:
    values: list[str] = []
    values.extend(list(getattr(task, "write_intent", []) or []))
    values.extend(list(getattr(task, "read_set", []) or []))
    values.extend(list(getattr(audit, "files_touched", []) or []))
    return _clean_tuple(values)


def _project_fact_role_for_path(path: str, titles: Iterable[str]) -> str:
    for title in titles:
        role = _project_fact_role_from_title(title)
        if role:
            return role
    return _project_fact_role_from_path(path)


def _project_fact_role_from_title(title: str) -> str:
    role = _one_line(str(title or ""), 100)
    role = re.sub(
        r"^(read|inspect|update|modify|implement|fix|improve|wire|test|verify)\s+",
        "",
        role,
        flags=re.IGNORECASE,
    ).strip(" -:.,")
    if len(role.split()) < 2:
        return ""
    return role


def _project_fact_role_from_path(path: str) -> str:
    parts = [part for part in _normalize_scope(path).split("/") if part]
    if not parts:
        return "Project module"
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0]
    if stem in {"__init__", "index"} and len(parts) >= 2:
        stem = parts[-2]
    if len(parts) >= 2 and parts[-2] not in {"runtime", "lucode", "tests", "test"}:
        stem = f"{parts[-2]} {stem}"
    role = re.sub(r"[_-]+", " ", stem).strip()
    return role[:1].upper() + role[1:] if role else "Project module"


def _project_fact_role_from_summary(summary: str, path: str) -> str:
    clean_summary = _one_line(summary, 180)
    normalized_path = _normalize_scope(path)
    pattern = re.compile(rf"\b(.+?)\s+(?:is\s+)?(?:implemented|defined|handled|located|stored|configured)\s+in\s+{re.escape(normalized_path)}\b", re.IGNORECASE)
    match = pattern.search(clean_summary)
    if match:
        role = match.group(1).strip(" -:.,")
        if role:
            return _one_line(role, 100)
    if normalized_path in clean_summary:
        before_path = clean_summary.split(normalized_path, 1)[0].strip(" -:.,")
        before_path = re.sub(r"\s+(?:is\s+)?(?:implemented|defined|handled|located|stored|configured)\s+in$", "", before_path, flags=re.IGNORECASE)
        if before_path:
            return _one_line(before_path, 100)
    return _project_fact_role_from_path(path)


def _looks_like_project_fact_statement(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not normalized:
        return False
    markers = (
        "implemented in",
        "defined in",
        "handled in",
        "located in",
        "configured in",
        "lives in",
        "responsible for",
        "controls",
        "implements",
    )
    return any(marker in normalized for marker in markers) and bool(_paths_from_text(normalized))


def _is_safe_project_fact_path(path: str) -> bool:
    if not _looks_like_project_path(path):
        return False
    normalized = _normalize_scope(path)
    if any(marker in normalized for marker in ("<redacted", "[redacted")):
        return False
    parts = {part for part in re.split(r"[/\\_.-]+", normalized) if part}
    sensitive_parts = {
        "env",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "password",
        "passwd",
        "token",
        "tokens",
        "private",
        "key",
        "keys",
    }
    return not bool(parts & sensitive_parts)


def _contains_raw_secret(text: str) -> bool:
    raw = str(text or "")
    if re.search(r"\b(?:sk|pk)-[A-Za-z0-9_-]{8,}", raw):
        return True
    return bool(re.search(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*\S{6,}", raw))


def _clean_tuple(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_scope(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _tags_for_scope(scope: Iterable[str], *, extra: Iterable[str] = ()) -> list[str]:
    tags: list[str] = []
    for item in extra:
        _append_unique(tags, str(item).strip())
    for path in scope:
        for part in re.split(r"[/\\_.-]+", str(path or "")):
            token = part.strip().lower()
            if len(token) >= 3 and token not in {"runtime", "tests", "test", "python"}:
                _append_unique(tags, token)
    return tags[:10]


def _fingerprint(kind: str, scope: Iterable[str], action: str) -> str:
    normalized_scope = ",".join(sorted(_normalize_scope(item) for item in scope if str(item or "").strip()))
    normalized_action = _normalize_action(action)
    digest = hashlib.sha256(f"{kind}\n{normalized_scope}\n{normalized_action}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:16]}"


def _normalize_scope(value: Any) -> str:
    text = _clean_text(str(value or "")).replace("\\", "/").strip().lower()
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/")


def _normalize_action(value: Any) -> str:
    text = _clean_text(str(value or "")).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def _append_unique(values: list[str], item: str) -> None:
    value = str(item or "").strip()
    if value and value not in values:
        values.append(value)


def _int_value(value: Any, *, default: int | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(text: str) -> str:
    return sanitize_text(_redact_text(redact_sensitive_text(str(text or "")))).strip()


def _one_line(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", _clean_text(text)).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"...[truncated {len(value) - limit} chars]"
