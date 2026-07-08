from __future__ import annotations

from runtime.evidence.schema import Claim, EvidenceRef


def evidence_refs_from_worker_report(report, *, timeline=None) -> list[EvidenceRef]:
    """Build runtime-owned evidence refs from structured report fields.

    Free-form WorkerReport.evidence_refs are not trusted as available evidence.
    They may cite these refs, but the refs must be backed by files, tool calls,
    run context artifacts, or timeline events.
    """

    task_id = str(getattr(report, "task_id", "") or "")
    evidence: list[EvidenceRef] = []
    for path in _string_list(getattr(report, "files_read", [])):
        evidence.append(EvidenceRef(ref_id=f"read:{path}", kind="file_snapshot", source=task_id))
    for path in _string_list(getattr(report, "files_written", [])):
        evidence.append(EvidenceRef(ref_id=f"write:{path}", kind="file_write", source=task_id))
    for call in list(getattr(report, "tool_calls", []) or []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or call.get("tool_name") or "").strip()
        if not tool:
            continue
        action = str(call.get("action") or call.get("command") or "").strip()
        excerpt = ".".join(item for item in [tool, action] if item)
        evidence.append(EvidenceRef(ref_id=f"tool:{tool}", kind=_tool_evidence_kind(tool, action), source=task_id, excerpt=excerpt))
    evidence.extend(_timeline_evidence_for_task(timeline, task_id))
    return _dedupe_evidence(evidence)


def extract_claims_from_worker_report(report, *, available_evidence: list[EvidenceRef] | None = None) -> list[Claim]:
    task_id = str(getattr(report, "task_id", "") or "")
    available_ref_ids = {item.ref_id for item in list(available_evidence or []) if item.ref_id}
    material_refs = _material_claim_refs(report, available_ref_ids)
    claims: list[Claim] = []
    summary = _preview(getattr(report, "summary", ""))
    if summary:
        claims.append(
            Claim(
                claim_id=f"claim:{task_id or 'unknown'}:summary",
                task_id=task_id,
                text=summary,
                claim_type="observation",
                confidence=0.5,
                evidence_refs=list(material_refs),
                risk_level=_risk_for_report(report),
            )
        )
    files_written = _string_list(getattr(report, "files_written", []))
    if files_written:
        refs = [f"write:{path}" for path in files_written if f"write:{path}" in available_ref_ids]
        refs.extend(ref for ref in material_refs if ref.startswith("tool:"))
        claims.append(
            Claim(
                claim_id=f"claim:{task_id or 'unknown'}:code_change",
                task_id=task_id,
                text="Files written: " + ", ".join(files_written),
                claim_type="code_change",
                confidence=0.8,
                evidence_refs=_unique_strings(refs),
                risk_level="high",
            )
        )
    for index, artifact in enumerate(_string_list(getattr(report, "artifacts", [])), start=1):
        claims.append(
            Claim(
                claim_id=f"claim:{task_id or 'unknown'}:artifact:{index}",
                task_id=task_id,
                text=_preview(artifact),
                claim_type=_claim_type_for_artifact(artifact),
                confidence=0.45,
                evidence_refs=list(material_refs),
                risk_level=_risk_for_artifact(artifact),
            )
        )
    return claims


def _timeline_evidence_for_task(timeline, task_id: str) -> list[EvidenceRef]:
    if timeline is None:
        return []
    evidence: list[EvidenceRef] = []
    try:
        events = timeline.snapshot()
    except Exception:
        return []
    for event in events:
        if task_id and str(getattr(event, "task_id", "") or "") not in {"", task_id}:
            continue
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type not in {"ToolInvoked", "FastPathUsed", "FileSnapshotRecorded", "ToolOutputRecorded"}:
            continue
        event_seq = int(getattr(event, "event_seq", 0) or 0)
        for ref in _string_list(getattr(event, "resource_refs", []) or []):
            evidence.append(
                EvidenceRef(
                    ref_id=ref,
                    kind="timeline_event",
                    source=task_id,
                    event_seq=event_seq,
                    excerpt=str(getattr(event, "message", "") or ""),
                )
            )
    return evidence


def _material_claim_refs(report, available_ref_ids: set[str]) -> list[str]:
    refs = []
    for ref in _string_list(getattr(report, "evidence_refs", [])):
        if ref.startswith("task:"):
            continue
        if ref in available_ref_ids:
            refs.append(ref)
    for path in _string_list(getattr(report, "files_read", [])):
        ref = f"read:{path}"
        if ref in available_ref_ids:
            refs.append(ref)
    for path in _string_list(getattr(report, "files_written", [])):
        ref = f"write:{path}"
        if ref in available_ref_ids:
            refs.append(ref)
    for call in list(getattr(report, "tool_calls", []) or []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or call.get("tool_name") or "").strip()
        ref = f"tool:{tool}" if tool else ""
        if ref and ref in available_ref_ids:
            refs.append(ref)
    return _unique_strings(refs)


def _risk_for_report(report) -> str:
    if _string_list(getattr(report, "files_written", [])):
        return "high"
    if list(getattr(report, "tool_calls", []) or []):
        return "normal"
    return "normal"


def _tool_evidence_kind(tool: str, action: str) -> str:
    text = f"{tool}.{action}".lower()
    if "browser_get_page_summary" in text or ("desktop_browser" in text and "summary" in text):
        return "browser_summary"
    if "command_runner" in text or "run_command" in text:
        return "command_output"
    if "pytest" in text or "test_result" in text:
        return "test_result"
    return "tool_output"


def _claim_type_for_artifact(value: str) -> str:
    lowered = str(value or "").lower()
    if "claimed_changes" in lowered or "files_written" in lowered:
        return "code_change"
    if "verification" in lowered:
        return "command_result"
    return "observation"


def _risk_for_artifact(value: str) -> str:
    return "high" if _claim_type_for_artifact(value) == "code_change" else "normal"


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen = set()
    for item in items:
        if not item.ref_id or item.ref_id in seen:
            continue
        seen.add(item.ref_id)
        result.append(item)
    return result


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    result = []
    for item in list(value or []):
        text = str(item or "").strip().replace("\\", "/")
        if text:
            result.append(text)
    return _unique_strings(result)


def _unique_strings(values) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _preview(value, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
