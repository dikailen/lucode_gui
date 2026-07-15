from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from runtime.recovery.checkpoint_codec import (
    PIPELINE_CHECKPOINT_SCHEMA_VERSION,
    PLANNER_SCHEMA_VERSION,
    classify_task_side_effect,
    decode_pipeline_checkpoint,
    task_fingerprint,
)
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.models import canonical_json


@dataclass(frozen=True)
class RecoveryPlan:
    disposition: str
    reason_code: str
    reusable_task_ids: tuple[str, ...] = ()
    executable_task_ids: tuple[str, ...] = ()
    blocked_task_ids: tuple[str, ...] = ()
    reused_outputs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolRecoveryDecision:
    action: str
    reason_code: str
    requires_new_approval: bool = False


def evaluate_tool_recovery(
    invocation: dict[str, Any] | None,
    *,
    arguments_hash: str = "",
) -> ToolRecoveryDecision:
    record = dict(invocation or {}) if isinstance(invocation, dict) else {}
    previous_hash = str(record.get("arguments_hash") or "")
    if arguments_hash and previous_hash and str(arguments_hash) != previous_hash:
        return ToolRecoveryDecision("replan", "arguments_hash_mismatch", requires_new_approval=True)
    status = str(record.get("status") or "unknown").strip().lower()
    side_effect_class = str(record.get("side_effect_class") or "unknown").strip().lower()
    if status == "prepared":
        if side_effect_class == "read_only":
            return ToolRecoveryDecision("retry", "prepared_read_only")
        return ToolRecoveryDecision("replan", "prepared_mutation_requires_new_approval", requires_new_approval=True)
    if status == "dispatched":
        return ToolRecoveryDecision("block", "tool_dispatched_unknown", requires_new_approval=True)
    if status == "unknown":
        return ToolRecoveryDecision("block", "tool_unknown_side_effect", requires_new_approval=True)
    if status == "reconciled":
        return ToolRecoveryDecision("completed", "tool_postcondition_verified")
    if status == "completed":
        if side_effect_class != "read_only":
            return ToolRecoveryDecision("block", "tool_completed_mutation", requires_new_approval=True)
        return ToolRecoveryDecision("completed", "tool_completed")
    if status == "failed":
        if side_effect_class != "read_only":
            return ToolRecoveryDecision("block", "tool_failed_mutation", requires_new_approval=True)
        return ToolRecoveryDecision("replan", "tool_failed_read_only")
    return ToolRecoveryDecision("replan", f"tool_{status or 'unknown'}", requires_new_approval=True)


def recovery_blocked_items_for_tool_invocations(invocations: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    for record in list(invocations or []):
        if not isinstance(record, dict):
            continue
        decision = evaluate_tool_recovery(record)
        task_id = str(record.get("task_id") or "")
        if decision.action != "block":
            continue
        # A dispatched tool without task scope is still an external side effect.
        # Preserve it as a global blocker rather than silently losing it during recovery.
        item = {"task_id": task_id, "reason_code": decision.reason_code}
        if item not in blocked:
            blocked.append(item)
    return blocked


def recovery_compatibility_for_plan(plan, *, execution_mode: str = "auto") -> dict[str, Any]:
    tasks = list(getattr(plan, "tasks", []) or [])
    mcp_ids = sorted(
        {
            str(item)
            for task in tasks
            for item in list(getattr(task, "mcp", []) or [])
            if str(item or "").strip()
        }
    )
    skill_ids = sorted(
        {
            str(item)
            for task in tasks
            for item in [getattr(task, "skill_id", ""), *list(getattr(task, "bound_skill_ids", []) or [])]
            if str(item or "").strip()
        }
    )
    mcp_stamps = [_mcp_manifest_stamp(item) for item in mcp_ids]
    return {
        "snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
        "planner_schema": PLANNER_SCHEMA_VERSION,
        "execution_mode": "auto" if str(execution_mode or "").strip().lower() != "auto" else "auto",
        "route_type": str(getattr(plan, "route_type", "") or ""),
        "task_fingerprints": {
            str(getattr(task, "id", "") or ""): task_fingerprint(task)
            for task in tasks
            if str(getattr(task, "id", "") or "")
        },
        "model_ids": sorted(
            {str(getattr(task, "model", "") or "") for task in tasks if str(getattr(task, "model", "") or "")}
        ),
        "tool_registry_hash": _stable_hash(mcp_stamps),
        "skill_versions": [_skill_version_stamp(item) for item in skill_ids],
        "mcp_manifest_hashes": mcp_stamps,
    }


def evaluate_recovery_plan(
    envelope: RecoveryEnvelope | dict[str, Any] | None,
    plan,
    *,
    planner_disposition: str,
    current_compatibility: dict[str, Any] | None = None,
) -> RecoveryPlan:
    resolved = envelope if isinstance(envelope, RecoveryEnvelope) else RecoveryEnvelope.from_dict(envelope)
    task_ids = tuple(str(getattr(task, "id", "") or "") for task in list(getattr(plan, "tasks", []) or []))
    disposition = str(planner_disposition or "").strip().lower()
    if resolved is None:
        return RecoveryPlan(disposition="ignore_previous", reason_code="no_recovery_envelope", executable_task_ids=task_ids)
    if disposition == "ignore_previous":
        return RecoveryPlan(disposition="ignore_previous", reason_code="planner_ignored_previous", executable_task_ids=task_ids)
    if disposition != "continue_safe":
        return RecoveryPlan(disposition="replan", reason_code="planner_requested_replan", executable_task_ids=task_ids)

    current = dict(current_compatibility or recovery_compatibility_for_plan(plan))
    if not _compatible(resolved.compatibility, current):
        return RecoveryPlan(disposition="replan", reason_code="compatibility_mismatch", executable_task_ids=task_ids)

    checkpoint = decode_pipeline_checkpoint(resolved.checkpoint)
    previous_tasks = {str(item.get("id") or ""): item for item in list(checkpoint.get("tasks") or [])}
    accepted = checkpoint.get("accepted_evidence") if isinstance(checkpoint.get("accepted_evidence"), dict) else {}
    lifecycle_blocked_items = [
        item
        for item in [*list(checkpoint.get("blocked_items") or []), *list(resolved.blocked_items or [])]
        if isinstance(item, dict) and str(item.get("reason_code") or "").startswith("tool_")
    ]
    lifecycle_blocked_task_ids = {str(item.get("task_id") or "") for item in lifecycle_blocked_items}
    lifecycle_unavailable = "" in lifecycle_blocked_task_ids
    reconciled_by_task = validated_reconciled_items(resolved)
    reusable: list[str] = []
    executable: list[str] = []
    blocked: list[str] = []
    outputs: dict[str, str] = {}
    for task in list(getattr(plan, "tasks", []) or []):
        task_id = str(getattr(task, "id", "") or "")
        if lifecycle_unavailable or task_id in lifecycle_blocked_task_ids:
            blocked.append(task_id)
            continue
        reconciled = reconciled_by_task.get(task_id)
        if reconciled is not None and _reconciled_item_matches_task(reconciled, task):
            reusable.append(task_id)
            outputs[task_id] = _reconciled_task_output(reconciled)
            continue
        side_effect_class = classify_task_side_effect(task)
        if side_effect_class not in {"model_only", "read_only"}:
            blocked.append(task_id)
            continue
        previous = previous_tasks.get(task_id) or {}
        recovered_output = _accepted_output_for_task(accepted, task_id)
        if str(previous.get("status") or "") == "completed" and recovered_output:
            reusable.append(task_id)
            outputs[task_id] = recovered_output
        else:
            executable.append(task_id)
    return RecoveryPlan(
        disposition="continue_safe",
        reason_code="safe_resume_selected",
        reusable_task_ids=tuple(reusable),
        executable_task_ids=tuple(executable),
        blocked_task_ids=tuple(blocked),
        reused_outputs=outputs,
    )


def validated_reconciled_items(envelope: RecoveryEnvelope) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not str(getattr(envelope, "source_run_id", "") or "") or not str(getattr(envelope, "checkpoint_id", "") or ""):
        return result
    for source in list(getattr(envelope, "reconciled_items", ()) or ()):
        if not isinstance(source, dict):
            continue
        item = dict(source)
        task_id = str(item.get("task_id") or "")
        observed = item.get("observed") if isinstance(item.get("observed"), dict) else {}
        invocation_id = str(item.get("invocation_id") or "")
        evidence_ref = str(item.get("evidence_ref") or "")
        event_seq = _positive_int(item.get("event_seq"))
        if (
            not task_id
            or not invocation_id
            or not evidence_ref.startswith(f"postcondition:{invocation_id}:")
            or str(item.get("source_run_id") or "") != str(envelope.source_run_id or "")
            or str(item.get("checkpoint_id") or "") != str(envelope.checkpoint_id or "")
            or event_seq <= 0
        ):
            continue
        kind = str(item.get("kind") or "")
        tool_name = str(item.get("tool_name") or "")
        if kind == "workspace_file_sha256.v1":
            observed_path = str(observed.get("path") or "").replace("\\", "/")
            observed_sha256 = str(observed.get("sha256") or "").lower()
            if (
                tool_name not in {"workspace_edit.write_file", "workspace_edit.create_file"}
                or not _is_normalized_workspace_path(observed_path)
                or not _is_sha256(observed_sha256)
            ):
                continue
            item["observed"] = {"path": observed_path, "sha256": observed_sha256}
        elif kind == "browser_navigation_url_sha256.v1":
            tab_id = str(observed.get("tab_id") or "").strip()
            url_sha256 = str(observed.get("url_sha256") or "").lower()
            if (
                tool_name != "desktop_browser.browser_navigate"
                or not _is_stable_tab_id(tab_id)
                or not _is_sha256(url_sha256)
            ):
                continue
            item["observed"] = {"tab_id": tab_id, "url_sha256": url_sha256}
        else:
            continue
        item["event_seq"] = event_seq
        if task_id not in result:
            result[task_id] = item
    return result


def _reconciled_item_matches_task(item: dict[str, Any], task) -> bool:
    task_mcp = {str(value or "") for value in list(getattr(task, "mcp", []) or [])}
    kind = str(item.get("kind") or "")
    tool_name = str(item.get("tool_name") or "")
    if kind == "workspace_file_sha256.v1":
        return tool_name in {"workspace_edit.write_file", "workspace_edit.create_file"} and "workspace_edit" in task_mcp
    if kind == "browser_navigation_url_sha256.v1":
        return tool_name == "desktop_browser.browser_navigate" and "desktop_browser" in task_mcp
    return False


def _reconciled_task_output(item: dict[str, Any]) -> str:
    observed = dict(item.get("observed") or {})
    if str(item.get("kind") or "") == "browser_navigation_url_sha256.v1":
        return (
            "Recovered verified browser navigation: "
            f"tab_id={observed.get('tab_id')} url_sha256={observed.get('url_sha256')} "
            f"evidence_ref={item.get('evidence_ref')}"
        )
    return (
        "Recovered verified postcondition: "
        f"{item.get('kind')} path={observed.get('path')} sha256={observed.get('sha256')} "
        f"evidence_ref={item.get('evidence_ref')}"
    )


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _is_normalized_workspace_path(value: str) -> bool:
    return bool(
        value
        and not value.startswith("/")
        and ":" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _is_stable_tab_id(value: str) -> bool:
    return bool(value and len(value) <= 256 and not any(ord(char) < 32 for char in value))


def _compatible(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = (
        "snapshot_schema",
        "planner_schema",
        "execution_mode",
        "route_type",
        "task_fingerprints",
        "model_ids",
        "tool_registry_hash",
        "skill_versions",
        "mcp_manifest_hashes",
    )
    return all(canonical_json(previous.get(key)) == canonical_json(current.get(key)) for key in keys)


def _accepted_output_for_task(packet: dict[str, Any], task_id: str) -> str:
    evidence = {
        str(item.get("ref_id") or ""): item
        for item in list(packet.get("evidence") or [])
        if isinstance(item, dict) and str(item.get("ref_id") or "")
    }
    lines = []
    used_refs: list[str] = []
    for claim in list(packet.get("claims") or []):
        if not isinstance(claim, dict) or str(claim.get("task_id") or "") != task_id:
            continue
        refs = [str(ref) for ref in list(claim.get("evidence_refs") or []) if str(ref) in evidence]
        if not refs:
            continue
        text = str(claim.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
        for ref in refs:
            if ref not in used_refs:
                used_refs.append(ref)
    if not lines or not used_refs:
        return ""
    lines.append("Accepted evidence refs: " + ", ".join(used_refs))
    return "\n".join(lines)


def _stable_hash(values: list[str]) -> str:
    return hashlib.sha256(json.dumps(list(values), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _skill_version_stamp(skill_id: str) -> str:
    try:
        from skills.loader import load_skill

        body = str(load_skill(skill_id) or "")
    except Exception:
        body = ""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"{skill_id}:{digest}"


def _mcp_manifest_stamp(mcp_id: str) -> str:
    try:
        from runtime.tools.registry import CORE_SERVER_METADATA

        metadata = dict(CORE_SERVER_METADATA.get(str(mcp_id), {}) or {})
    except Exception:
        metadata = {}
    payload = {"id": str(mcp_id or ""), "metadata": metadata}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{mcp_id}:{digest}"
