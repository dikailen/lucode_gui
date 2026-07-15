from __future__ import annotations

import base64
import hashlib
from typing import Any

from runtime.context.compaction import redact_sensitive_text
from runtime.recovery.models import canonical_json
from runtime.recovery.sanitizer import sanitize_payload


PIPELINE_CHECKPOINT_SCHEMA_VERSION = "pipeline_checkpoint.v1"
PLANNER_SCHEMA_VERSION = "planner_result.v1"
MAX_FINAL_OUTPUT_CHARS = 20000

_SAFE_TASK_MCP_IDS = {
    "code_locator",
    "context7_docs",
    "grep_code_search",
    "project_filesystem_readonly",
    "skills_filesystem_readonly",
    "web_fetch",
    "web_search",
}
_TASK_FIELDS = (
    "id",
    "title",
    "skill_id",
    "model",
    "mcp",
    "depends_on",
    "read_set",
    "write_intent",
    "status",
    "side_effect_class",
)


class UnsupportedCheckpointSchema(ValueError):
    pass


def encode_pipeline_checkpoint(run_state, *, final_output: str = "") -> dict[str, Any]:
    tasks = [_encode_task(record) for record in list(getattr(run_state, "tasks", []) or [])]
    payload: dict[str, Any] = {
        "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
        "route_type": _text(getattr(run_state, "route_type", "")),
        "reason": _text(getattr(run_state, "reason", "")),
        "tasks": tasks,
        "accepted_evidence": _accepted_evidence_packet(getattr(run_state, "accepted_evidence", {}) or {}),
        "blocked_items": [],
    }
    if str(final_output or ""):
        payload["final_output"] = _final_output_text(final_output)
    return payload


def decode_pipeline_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    schema_version = _text(source.get("schema_version"))
    if schema_version != PIPELINE_CHECKPOINT_SCHEMA_VERSION:
        raise UnsupportedCheckpointSchema(
            f"unsupported pipeline checkpoint schema: {schema_version or 'missing'}"
        )
    decoded: dict[str, Any] = {
        "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
        "route_type": _text(source.get("route_type")),
        "reason": _text(source.get("reason")),
        "tasks": [_decode_task(item) for item in _mapping_list(source.get("tasks"))],
        "accepted_evidence": _accepted_evidence_packet(source.get("accepted_evidence") or {}),
        "blocked_items": _blocked_items(source.get("blocked_items")),
    }
    if "final_output" in source:
        decoded["final_output"] = _final_output_text(source.get("final_output"))
    return decoded


def sanitize_checkpoint_payload(value: dict[str, Any]) -> dict[str, Any]:
    source = dict(value or {}) if isinstance(value, dict) else {}
    final_output = source.pop("final_output", None)
    sanitized = sanitize_payload(source)
    result = dict(sanitized) if isinstance(sanitized, dict) else {}
    if final_output is not None:
        result["final_output"] = _final_output_text(final_output)
    return result


def classify_task_side_effect(task) -> str:
    write_intent = _strings(getattr(task, "write_intent", []) if not isinstance(task, dict) else task.get("write_intent"))
    if write_intent:
        return "non_idempotent"
    mcp_ids = _strings(getattr(task, "mcp", []) if not isinstance(task, dict) else task.get("mcp"))
    if not mcp_ids:
        return "model_only"
    if all(item in _SAFE_TASK_MCP_IDS for item in mcp_ids):
        return "read_only"
    return "unknown"


def task_fingerprint(task) -> str:
    payload = {
        "id": _task_value(task, "id"),
        "title": _task_value(task, "title"),
        "instruction": _task_value(task, "instruction"),
        "skill_id": _task_value(task, "skill_id"),
        "model": _task_value(task, "model"),
        "bound_skill_ids": _strings(_task_raw_value(task, "bound_skill_ids")),
        "mcp": _strings(_task_raw_value(task, "mcp")),
        "depends_on": _strings(_task_raw_value(task, "depends_on")),
        "read_set": _strings(_task_raw_value(task, "read_set")),
        "write_intent": _strings(_task_raw_value(task, "write_intent")),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _encode_task(record) -> dict[str, Any]:
    payload = {
        "id": _text(getattr(record, "id", "")),
        "title": _text(getattr(record, "title", "")),
        "skill_id": _text(getattr(record, "skill_id", "")),
        "model": _text(getattr(record, "model", "")),
        "mcp": _strings(getattr(record, "mcp", [])),
        "depends_on": _strings(getattr(record, "depends_on", [])),
        "read_set": _strings(getattr(record, "read_set", [])),
        "write_intent": _strings(getattr(record, "write_intent", [])),
        "status": _task_status(getattr(record, "status", "")),
        "side_effect_class": classify_task_side_effect(record),
    }
    return {field: payload[field] for field in _TASK_FIELDS}


def _decode_task(item: dict[str, Any]) -> dict[str, Any]:
    source = dict(item or {})
    side_effect_class = _text(source.get("side_effect_class"))
    if side_effect_class not in {"model_only", "read_only", "non_idempotent", "unknown"}:
        side_effect_class = classify_task_side_effect(source)
    payload = {
        "id": _text(source.get("id")),
        "title": _text(source.get("title")),
        "skill_id": _text(source.get("skill_id")),
        "model": _text(source.get("model")),
        "mcp": _strings(source.get("mcp")),
        "depends_on": _strings(source.get("depends_on")),
        "read_set": _strings(source.get("read_set")),
        "write_intent": _strings(source.get("write_intent")),
        "status": _task_status(source.get("status")),
        "side_effect_class": side_effect_class,
    }
    return {field: payload[field] for field in _TASK_FIELDS}


def _accepted_evidence_packet(value: Any) -> dict[str, Any]:
    source = dict(value or {}) if isinstance(value, dict) else {}
    claims = []
    for item in _mapping_list(source.get("claims")):
        claims.append(
            {
                "claim_id": _text(item.get("claim_id")),
                "task_id": _text(item.get("task_id")),
                "text": _text(item.get("text")),
                "claim_type": _text(item.get("claim_type")),
                "confidence": item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else 0.0,
                "evidence_refs": _strings(item.get("evidence_refs")),
                "risk_level": _text(item.get("risk_level")),
            }
        )
    evidence = []
    for item in _mapping_list(source.get("evidence")):
        evidence.append(
            {
                "ref_id": _text(item.get("ref_id")),
                "kind": _text(item.get("kind")),
                "source": _text(item.get("source")),
                "event_seq": int(item.get("event_seq") or 0),
                "sha256": _text(item.get("sha256")),
            }
        )
    blocked_claims = []
    for item in _mapping_list(source.get("blocked_claims")):
        blocked_claims.append(
            {
                "claim_id": _text(item.get("claim_id")),
                "status": _text(item.get("status")),
                "reasons": _strings(item.get("reasons")),
                "required_rework": _text(item.get("required_rework")),
            }
        )
    return {
        "mode": _text(source.get("mode") or "off"),
        "claims": claims,
        "evidence": evidence,
        "blocked_claims": blocked_claims,
    }


def _blocked_items(value: Any) -> list[dict[str, str]]:
    items = []
    for item in _mapping_list(value):
        items.append(
            {
                "task_id": _text(item.get("task_id")),
                "reason_code": _text(item.get("reason_code")),
            }
        )
    return items


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        return []
    result = []
    for item in list(value or []):
        clean = _text(item)
        if clean and clean not in result:
            result.append(clean)
    return result


def _text(value: Any) -> str:
    sanitized = sanitize_payload(redact_sensitive_text(str(value or "")))
    return str(sanitized or "")


def _final_output_text(value: Any) -> str:
    text = redact_sensitive_text(str(value or ""))
    compact = text.strip()
    lowered = compact.lower()
    if lowered.startswith(("<!doctype html", "<html", "<body")):
        return _omitted_text("dom", text)
    if ";base64," in lowered or _is_large_base64(compact):
        return _omitted_text("base64", text)
    if len(text) > MAX_FINAL_OUTPUT_CHARS:
        return text[:MAX_FINAL_OUTPUT_CHARS] + f"...[truncated {len(text) - MAX_FINAL_OUTPUT_CHARS} chars]"
    return text


def _is_large_base64(value: str) -> bool:
    if len(value) < 512 or len(value) % 4:
        return False
    try:
        base64.b64decode(value, validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    return True


def _omitted_text(kind: str, value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"[omitted:{kind}:sha256:{digest}]"


def _task_status(value: Any) -> str:
    status = _text(value).strip().lower()
    return status if status in {"pending", "running", "completed", "failed", "cancelled", "unknown"} else "pending"


def _task_raw_value(task, field: str) -> Any:
    return task.get(field) if isinstance(task, dict) else getattr(task, field, [])


def _task_value(task, field: str) -> str:
    return _text(_task_raw_value(task, field))
