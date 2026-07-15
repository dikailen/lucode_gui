from __future__ import annotations

from runtime.consistency.timeline import reliability_flags_from_env
from runtime.evidence.extractor import extract_claims_from_worker_report, evidence_refs_from_worker_report
from runtime.evidence.schema import Claim, EvidenceGateResult, EvidenceRef, GateVerdict


MATERIAL_EVIDENCE_KINDS = {"file_snapshot", "file_write", "tool_output", "browser_summary", "command_output", "test_result", "timeline_event"}
HIGH_RISK_CLAIM_TYPES = {"code_change", "command_result", "browser_fact", "file_fact"}
HIGH_RISK_LEVELS = {"high", "critical"}


def evaluate_claims(claims: list[Claim], *, available_evidence: list[EvidenceRef]) -> list[GateVerdict]:
    available_by_id = {item.ref_id: item for item in list(available_evidence or []) if item.ref_id}
    verdicts: list[GateVerdict] = []
    for claim in list(claims or []):
        reasons: list[str] = []
        claim_refs = [str(item or "").strip() for item in list(getattr(claim, "evidence_refs", []) or []) if str(item or "").strip()]
        unknown_refs = [ref for ref in claim_refs if ref not in available_by_id]
        if unknown_refs:
            reasons.extend(f"unknown_evidence_ref:{ref}" for ref in unknown_refs)
            verdicts.append(
                GateVerdict(
                    claim_id=claim.claim_id,
                    status="rejected",
                    reasons=reasons,
                    required_rework="Remove forged evidence refs and rerun the concrete tool or file read.",
                )
            )
            continue
        material_refs = [
            ref for ref in claim_refs if str(getattr(available_by_id.get(ref), "kind", "") or "") in MATERIAL_EVIDENCE_KINDS
        ]
        if _requires_material_evidence(claim) and not material_refs:
            reasons.append("missing_evidence")
            verdicts.append(
                GateVerdict(
                    claim_id=claim.claim_id,
                    status="needs_recheck",
                    reasons=reasons,
                    required_rework="Attach runtime-owned file, tool, browser, command, or timeline evidence.",
                )
            )
            continue
        verdicts.append(GateVerdict(claim_id=claim.claim_id, status="accepted", reasons=["evidence_verified"]))
    return verdicts


def run_evidence_gate_for_reports(
    reports: list,
    *,
    run_state=None,
    mode: str | None = None,
) -> EvidenceGateResult:
    gate_mode = _normalize_gate_mode(mode or reliability_flags_from_env().evidence_gate)
    if gate_mode == "off":
        result = EvidenceGateResult(mode="off")
        _record_gate_result(run_state, result)
        return result

    all_claims: list[Claim] = []
    all_evidence: list[EvidenceRef] = []
    timeline = getattr(run_state, "timeline", None)
    for report in list(reports or []):
        evidence = evidence_refs_from_worker_report(report, timeline=timeline)
        claims = extract_claims_from_worker_report(report, available_evidence=evidence)
        all_evidence.extend(evidence)
        all_claims.extend(claims)
    all_evidence = _dedupe_evidence(all_evidence)
    verdicts = evaluate_claims(all_claims, available_evidence=all_evidence)
    result = EvidenceGateResult(mode=gate_mode, claims=all_claims, evidence=all_evidence, verdicts=verdicts)
    _record_gate_result(run_state, result)
    _emit_gate_events(run_state, result)
    return result


def enforced_gate_verdicts(result: EvidenceGateResult) -> list[tuple[Claim | None, GateVerdict]]:
    mode = _normalize_gate_mode(getattr(result, "mode", "off"))
    if mode not in {"enforce_high_risk", "enforce_all"}:
        return []

    claims_by_id = {str(getattr(claim, "claim_id", "") or ""): claim for claim in list(getattr(result, "claims", []) or [])}
    enforced: list[tuple[Claim | None, GateVerdict]] = []
    for verdict in list(getattr(result, "verdicts", []) or []):
        if str(getattr(verdict, "status", "") or "").strip().lower() == "accepted":
            continue
        claim = claims_by_id.get(str(getattr(verdict, "claim_id", "") or ""))
        if mode == "enforce_all" or _is_high_risk_claim(claim):
            enforced.append((claim, verdict))
    return enforced


def accepted_evidence_packet(result: EvidenceGateResult) -> dict:
    """Return a final-answer-safe evidence packet.

    The packet intentionally includes accepted claim text, but blocked claims are
    reduced to ids/status/reasons so final synthesis cannot accidentally use
    rejected or needs_recheck text as facts.
    """

    claims = list(getattr(result, "claims", []) or [])
    evidence = list(getattr(result, "evidence", []) or [])
    verdicts = list(getattr(result, "verdicts", []) or [])
    claims_by_id = {str(getattr(claim, "claim_id", "") or ""): claim for claim in claims}
    evidence_by_id = {str(getattr(item, "ref_id", "") or ""): item for item in evidence}
    accepted_ids = {
        str(getattr(verdict, "claim_id", "") or "")
        for verdict in verdicts
        if str(getattr(verdict, "status", "") or "").strip().lower() == "accepted"
    }
    accepted_claims = [claims_by_id[claim_id] for claim_id in accepted_ids if claim_id in claims_by_id]
    cited_ref_ids: list[str] = []
    for claim in accepted_claims:
        for ref in list(getattr(claim, "evidence_refs", []) or []):
            clean = str(ref or "").strip()
            if clean and clean in evidence_by_id and clean not in cited_ref_ids:
                cited_ref_ids.append(clean)
    blocked_claims = []
    for verdict in verdicts:
        status = str(getattr(verdict, "status", "") or "").strip().lower()
        if status == "accepted":
            continue
        blocked_claims.append(
            {
                "claim_id": str(getattr(verdict, "claim_id", "") or ""),
                "status": status or "unknown",
                "reasons": list(getattr(verdict, "reasons", []) or []),
                "required_rework": str(getattr(verdict, "required_rework", "") or ""),
            }
        )
    return {
        "mode": str(getattr(result, "mode", "") or "off"),
        "claims": [claim.to_dict() for claim in accepted_claims],
        "evidence": [evidence_by_id[ref_id].to_dict() for ref_id in cited_ref_ids],
        "blocked_claims": blocked_claims,
    }


def recovery_reconciliation_evidence_packet(envelope, *, reusable_task_ids) -> dict:
    """Render only durably bound recovery postconditions as final-answer evidence."""

    from runtime.recovery.policy import validated_reconciled_items

    allowed = {str(task_id or "") for task_id in list(reusable_task_ids or []) if str(task_id or "")}
    reconciled_by_task = validated_reconciled_items(envelope)
    claims: list[dict] = []
    evidence: list[dict] = []
    for task_id in sorted(allowed):
        item = reconciled_by_task.get(task_id)
        if item is None:
            continue
        observed = dict(item.get("observed") or {})
        evidence_ref = str(item.get("evidence_ref") or "")
        invocation_id = str(item.get("invocation_id") or "")
        checkpoint_id = str(item.get("checkpoint_id") or "")
        source_run_id = str(item.get("source_run_id") or "")
        event_seq = int(item.get("event_seq") or 0)
        if not (evidence_ref and invocation_id and checkpoint_id and source_run_id and event_seq > 0):
            continue
        kind = str(item.get("kind") or "")
        if kind == "workspace_file_sha256.v1":
            path = str(observed.get("path") or "")
            sha256 = str(observed.get("sha256") or "")
            if not (path and sha256):
                continue
            claims.append(
                {
                    "claim_id": f"recovery-postcondition:{invocation_id}",
                    "task_id": task_id,
                    "text": f"Recovered verified workspace file postcondition: {path}",
                    "claim_type": "code_change",
                    "confidence": 1.0,
                    "evidence_refs": [evidence_ref],
                    "risk_level": "high",
                }
            )
            evidence.append(
                {
                    "ref_id": evidence_ref,
                    "kind": "file_write",
                    "source": "recovery_postcondition",
                    "event_seq": event_seq,
                    "excerpt": path,
                    "sha256": sha256,
                    "invocation_id": invocation_id,
                    "checkpoint_id": checkpoint_id,
                    "run_id": source_run_id,
                }
            )
        elif kind == "browser_navigation_url_sha256.v1":
            tab_id = str(observed.get("tab_id") or "")
            url_sha256 = str(observed.get("url_sha256") or "")
            if not (tab_id and _is_sha256(url_sha256)):
                continue
            claims.append(
                {
                    "claim_id": f"recovery-postcondition:{invocation_id}",
                    "task_id": task_id,
                    "text": f"Recovered verified browser navigation: tab {tab_id}",
                    "claim_type": "browser_fact",
                    "confidence": 1.0,
                    "evidence_refs": [evidence_ref],
                    "risk_level": "high",
                }
            )
            evidence.append(
                {
                    "ref_id": evidence_ref,
                    "kind": "browser_summary",
                    "source": "recovery_postcondition",
                    "event_seq": event_seq,
                    "excerpt": f"tab_id={tab_id}",
                    "url_sha256": url_sha256,
                    "invocation_id": invocation_id,
                    "checkpoint_id": checkpoint_id,
                    "run_id": source_run_id,
                }
            )
    if not claims:
        return {}
    return {
        "mode": "recovery_verified",
        "claims": claims,
        "evidence": evidence,
        "blocked_claims": [],
    }


def _requires_material_evidence(claim: Claim) -> bool:
    if str(getattr(claim, "risk_level", "") or "").lower() in HIGH_RISK_LEVELS:
        return True
    if str(getattr(claim, "claim_type", "") or "").lower() in HIGH_RISK_CLAIM_TYPES:
        return True
    return bool(str(getattr(claim, "text", "") or "").strip())


def _is_high_risk_claim(claim: Claim | None) -> bool:
    if claim is None:
        return False
    risk_level = str(getattr(claim, "risk_level", "") or "").strip().lower()
    claim_type = str(getattr(claim, "claim_type", "") or "").strip().lower()
    return risk_level in HIGH_RISK_LEVELS or claim_type in HIGH_RISK_CLAIM_TYPES


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _record_gate_result(run_state, result: EvidenceGateResult) -> None:
    if run_state is None or not hasattr(run_state, "record_evidence_gate_result"):
        return
    try:
        run_state.record_evidence_gate_result(result)
    except Exception:
        return


def _emit_gate_events(run_state, result: EvidenceGateResult) -> None:
    if run_state is None or not hasattr(run_state, "emit_event"):
        return
    for verdict in list(result.verdicts or []):
        claim = next((item for item in result.claims if item.claim_id == verdict.claim_id), None)
        payload = verdict.to_dict()
        if claim is not None:
            payload["claim"] = claim.to_dict()
        run_state.emit_event(
            "EvidenceGateVerdict",
            f"{verdict.status}: {verdict.claim_id}",
            agent="supervisor",
            task_id=str(getattr(claim, "task_id", "") or ""),
            status=verdict.status,
            payload=payload,
        )
    run_state.emit_event(
        "EvidenceGateCompleted",
        f"Evidence gate {result.mode}: {len(result.verdicts)} verdict(s)",
        agent="supervisor",
        status="warning" if any(item.status != "accepted" for item in result.verdicts) else "completed",
        payload={
            "mode": result.mode,
            "claim_count": len(result.claims),
            "verdict_count": len(result.verdicts),
            "needs_recheck_count": sum(1 for item in result.verdicts if item.status == "needs_recheck"),
            "rejected_count": sum(1 for item in result.verdicts if item.status == "rejected"),
        },
    )


def _normalize_gate_mode(value: str) -> str:
    mode = str(value or "off").strip().lower()
    if mode in {"off", "observe", "warn", "enforce_high_risk", "enforce_all"}:
        return mode
    return "off"


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen = set()
    for item in list(items or []):
        if not item.ref_id or item.ref_id in seen:
            continue
        seen.add(item.ref_id)
        result.append(item)
    return result
