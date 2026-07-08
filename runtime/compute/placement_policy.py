from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

from runtime.compute.context_sources import ContextSourceLabel
from runtime.compute.difficulty import classify_task_difficulty
from runtime.compute.sanitizer import sanitize_planning_text
from runtime.compute.sensitivity import classify_task_sensitivity
from runtime.consistency.timeline import COMPUTE_PLACEMENT_MODES, reliability_flags_from_env
from runtime.config.model_selection import model_runtime_available
from runtime.safety.privacy import PrivacyPolicy, is_local_backend


class ComputePlacementViolation(RuntimeError):
    """Raised when enforce mode cannot satisfy privacy/placement constraints."""


@dataclass(frozen=True)
class PlacementDecision:
    task_id: str
    sensitivity: str
    difficulty: str
    planner_side: str
    executor_side: str
    allowed_model_ids: list[str] = field(default_factory=list)
    sanitized_context: str = ""
    blocked_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sensitivity": self.sensitivity,
            "difficulty": self.difficulty,
            "planner_side": self.planner_side,
            "executor_side": self.executor_side,
            "allowed_model_ids": list(self.allowed_model_ids),
            "sanitized_context": self.sanitized_context,
            "blocked_reasons": list(self.blocked_reasons),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ComputePlacementResult:
    mode: str
    decisions: list[PlacementDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(frozen=True)
class PlannerModelDecision:
    mode: str
    sensitivity: str
    difficulty: str
    planner_side: str
    model_id: str
    original_request: str
    planner_input: str
    sanitized_context: str
    refiner_enabled: bool
    allow_project_scout: bool
    blocked_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sensitivity": self.sensitivity,
            "difficulty": self.difficulty,
            "planner_side": self.planner_side,
            "model_id": self.model_id,
            "planner_input": sanitize_planning_text(self.planner_input),
            "sanitized_context": self.sanitized_context,
            "refiner_enabled": self.refiner_enabled,
            "allow_project_scout": self.allow_project_scout,
            "blocked_reasons": list(self.blocked_reasons),
            "reasons": list(self.reasons),
        }


def observe_compute_placement_for_plan(
    plan,
    refined_request: str = "",
    *,
    privacy_mode: str = "local_first",
    mode: str | None = None,
    model_catalog: dict | None = None,
) -> ComputePlacementResult:
    placement_mode = _normalize_mode(mode)
    if placement_mode == "off":
        return ComputePlacementResult(mode="off")

    policy = PrivacyPolicy(privacy_mode)
    decisions = [
        decide_task_placement(
            task,
            refined_request,
            privacy_policy=policy,
            model_catalog=model_catalog,
        )
        for task in list(getattr(plan, "tasks", []) or [])
    ]
    return ComputePlacementResult(mode=placement_mode, decisions=decisions)


def select_planner_model_for_request(
    request_text: str,
    model_registry,
    preferred_model_ids: list[str] | tuple[str, ...] | None,
    *,
    privacy_mode: str = "local_first",
    mode: str | None = None,
    model_catalog: dict | None = None,
    context_labels: Iterable[ContextSourceLabel] | None = None,
) -> PlannerModelDecision:
    placement_mode = _normalize_mode(mode)
    original_request = str(request_text or "")
    policy = PrivacyPolicy(privacy_mode)
    synthetic_task = _synthetic_planning_task(original_request)
    sensitivity = classify_task_sensitivity(
        synthetic_task,
        original_request,
        context_labels=context_labels,
    )
    difficulty = classify_task_difficulty(synthetic_task, original_request)
    if placement_mode != "enforce":
        model_id = _legacy_first_configured_model(model_registry, list(preferred_model_ids or []))
        info = _safe_model_info(model_registry, model_id)
        return PlannerModelDecision(
            mode=placement_mode,
            sensitivity=sensitivity.sensitivity,
            difficulty=difficulty.difficulty,
            planner_side="local" if model_info_is_local(info) else "cloud",
            model_id=model_id,
            original_request=original_request,
            planner_input=original_request,
            sanitized_context=sanitize_planning_text(original_request),
            refiner_enabled=True,
            allow_project_scout=True,
            reasons=_dedupe([*sensitivity.reasons, *difficulty.reasons, "compute_placement_not_enforced"]),
        )
    planner_side, side_reasons = _planner_side_for_request(
        sensitivity.sensitivity,
        difficulty.difficulty,
        policy.mode,
    )
    candidates = _planner_model_candidates(
        model_registry,
        list(preferred_model_ids or []),
        policy,
        model_catalog=model_catalog,
    )
    selected_info = _select_planner_candidate(candidates, desired_side=planner_side)
    if selected_info is None:
        reason = (
            "local planner required for private or sensitive request"
            if planner_side == "local"
            else "no configured planner model satisfies compute placement"
        )
        raise ComputePlacementViolation(
            "Compute placement blocked: "
            f"{reason}; privacy={policy.mode}; sensitivity={sensitivity.sensitivity}; "
            f"difficulty={difficulty.difficulty}."
        )

    selected_side = "local" if model_info_is_local(selected_info) else "cloud"
    selected_id = str(selected_info.get("id") or "")
    sanitized = sanitize_planning_text(original_request)
    planner_input = sanitized if selected_side == "cloud" else original_request
    return PlannerModelDecision(
        mode=placement_mode,
        sensitivity=sensitivity.sensitivity,
        difficulty=difficulty.difficulty,
        planner_side=selected_side,
        model_id=selected_id,
        original_request=original_request,
        planner_input=planner_input,
        sanitized_context=sanitized,
        refiner_enabled=sensitivity.sensitivity == "public",
        allow_project_scout=selected_side == "local",
        reasons=_dedupe([*sensitivity.reasons, *difficulty.reasons, *side_reasons]),
    )


def decide_task_placement(
    task,
    refined_request: str = "",
    *,
    privacy_policy: PrivacyPolicy | None = None,
    model_catalog: dict | None = None,
    context_labels: Iterable[ContextSourceLabel] | None = None,
) -> PlacementDecision:
    policy = privacy_policy or PrivacyPolicy()
    sensitivity = classify_task_sensitivity(
        task,
        refined_request,
        context_labels=context_labels,
    )
    difficulty = classify_task_difficulty(task, refined_request)
    reasons = [*sensitivity.reasons, *difficulty.reasons]
    planner_side, executor_side, placement_reasons = _placement_sides(
        sensitivity.sensitivity,
        difficulty.difficulty,
        policy.mode,
        has_tools=bool(list(getattr(task, "mcp", []) or [])),
    )
    reasons.extend(placement_reasons)
    context = _task_context(task, refined_request)
    return PlacementDecision(
        task_id=str(getattr(task, "id", "") or ""),
        sensitivity=sensitivity.sensitivity,
        difficulty=difficulty.difficulty,
        planner_side=planner_side,
        executor_side=executor_side,
        allowed_model_ids=_allowed_model_ids(task, policy, model_catalog),
        sanitized_context=sanitize_planning_text(context),
        blocked_reasons=[],
        reasons=_dedupe(reasons),
    )


def model_info_is_local(model_info: dict | None) -> bool:
    if not model_info:
        return False
    backend_type = str(model_info.get("backend_type") or "")
    base_url = str(model_info.get("base_url") or model_info.get("base_url_value") or "").strip()
    if base_url:
        return _base_url_is_local(base_url)
    if is_local_backend(backend_type):
        return True
    if bool(model_info.get("is_local")) and is_local_backend(backend_type):
        return True
    privacy_level = str(model_info.get("privacy_level") or "").strip().lower()
    if privacy_level in {"local", "local_native", "edge"} and is_local_backend(backend_type):
        return True
    return False


def model_usable_for_compute_placement(
    model_info: dict | None,
    policy: PrivacyPolicy | None = None,
    *,
    requires_tools: bool = False,
) -> bool:
    if not model_info:
        return False
    if not model_info.get("configured"):
        return False
    policy = policy or PrivacyPolicy()
    if policy.mode == "offline":
        if not model_info_is_local(model_info):
            return False
    elif not policy.model_allowed(model_info):
        return False
    if not model_runtime_available(model_info):
        return False
    if requires_tools and model_info.get("supports_tools") is False:
        return False
    return True


def _base_url_is_local(base_url: str) -> bool:
    value = str(base_url or "").strip()
    if not value:
        return False
    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = str(parsed.hostname or "").strip().lower().strip("[]")
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in {"host.docker.internal"}
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _placement_sides(
    sensitivity: str,
    difficulty: str,
    privacy_mode: str,
    *,
    has_tools: bool,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if privacy_mode == "offline":
        return "local", "local", ["privacy_offline"]

    if sensitivity in {"secret", "local_only"}:
        return "local", "local", [f"{sensitivity}_requires_local"]

    if sensitivity == "project_private":
        if privacy_mode == "cloud_allowed":
            executor = "local" if has_tools else "edge"
            return "cloud", executor, ["project_private_context", "cloud_allowed_sanitized_planning"]
        executor = "local" if has_tools else "edge"
        return "local", executor, ["project_private_context"]

    if difficulty in {"complex", "long_context"} and privacy_mode in {"local_first", "cloud_allowed"}:
        executor = "local" if has_tools else "cloud"
        reasons.append("public_complex_can_use_cloud_planner")
        return "cloud", executor, reasons

    executor = "local" if has_tools else ("cloud" if privacy_mode == "cloud_allowed" else "edge")
    return "local", executor, ["default_low_risk"]


def _planner_side_for_request(
    sensitivity: str,
    difficulty: str,
    privacy_mode: str,
) -> tuple[str, list[str]]:
    if privacy_mode == "offline":
        return "local", ["privacy_offline"]
    if sensitivity in {"secret", "local_only"}:
        return "local", [f"{sensitivity}_requires_local_planner"]
    if sensitivity == "project_private":
        if privacy_mode == "cloud_allowed":
            return "cloud", ["project_private_cloud_allowed_sanitized_planning"]
        return "local", ["project_private_requires_local_planner"]
    if difficulty in {"complex", "long_context"} and privacy_mode in {"local_first", "cloud_allowed"}:
        return "cloud", ["public_complex_can_use_cloud_planner"]
    if privacy_mode == "cloud_allowed":
        return "cloud", ["cloud_allowed_public_planning"]
    return "local", ["default_local_planning"]


def _allowed_model_ids(task, policy: PrivacyPolicy, model_catalog: dict | None) -> list[str]:
    if not model_catalog:
        return []
    requires_tools = bool(list(getattr(task, "mcp", []) or []))
    result: list[str] = []
    for item in list(model_catalog.get("models", []) or []):
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or not item.get("configured"):
            continue
        if not policy.model_allowed(item):
            continue
        if requires_tools and item.get("supports_tools") is False:
            continue
        result.append(model_id)
    return result


def _planner_model_candidates(
    model_registry,
    preferred_model_ids: list[str],
    policy: PrivacyPolicy,
    *,
    model_catalog: dict | None = None,
) -> list[dict]:
    model_infos = _model_infos_from_registry_or_catalog(model_registry, model_catalog)
    ordered_ids = _dedupe([*preferred_model_ids, *list(model_infos.keys())])
    candidates: list[dict] = []
    for model_id in ordered_ids:
        info = model_infos.get(model_id)
        if not isinstance(info, dict):
            continue
        if not model_usable_for_compute_placement(info, policy, requires_tools=False):
            continue
        candidates.append(info)
    return candidates


def _model_infos_from_registry_or_catalog(model_registry, model_catalog: dict | None) -> dict[str, dict]:
    registry_infos = getattr(model_registry, "infos", None)
    if isinstance(registry_infos, dict):
        return {
            str(model_id): dict(info)
            for model_id, info in registry_infos.items()
            if str(model_id).strip() and isinstance(info, dict)
        }
    catalog = model_catalog
    if catalog is None:
        try:
            from catalog_system.model_catalog import load_model_catalog

            catalog = load_model_catalog()
        except Exception:
            catalog = {}
    result: dict[str, dict] = {}
    for item in list((catalog or {}).get("models", []) or []):
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            result[model_id] = dict(item)
    return result


def _select_planner_candidate(candidates: list[dict], *, desired_side: str) -> dict | None:
    if desired_side == "local":
        for item in candidates:
            if model_info_is_local(item):
                return item
        return None
    for item in candidates:
        if not model_info_is_local(item):
            return item
    return candidates[0] if candidates else None


def _legacy_first_configured_model(model_registry, preferred_model_ids: list[str]) -> str:
    selector = getattr(model_registry, "first_configured", None)
    if callable(selector):
        return str(selector(preferred_model_ids) or "")
    for model_id in preferred_model_ids:
        if str(model_id or "").strip():
            return str(model_id)
    return ""


def _safe_model_info(model_registry, model_id: str) -> dict:
    getter = getattr(model_registry, "get_model_info", None)
    if not callable(getter) or not model_id:
        return {}
    try:
        return dict(getter(model_id) or {})
    except Exception:
        return {}


def _synthetic_planning_task(request_text: str):
    class _PlanningTask:
        id = "planner_request"
        title = "Planner request"
        skill_id = "orchestrator_planner"
        model = ""
        mcp: list[str] = []
        read_set: list[str] = []
        write_intent: list[str] = []
        depends_on: list[str] = []
        acceptance_criteria: list[str] = []
        expected_outputs: list[str] = []

        def __init__(self, instruction: str):
            self.instruction = instruction

    return _PlanningTask(request_text)


def _task_context(task, refined_request: str) -> str:
    parts = [
        refined_request,
        getattr(task, "title", ""),
        getattr(task, "instruction", ""),
        " ".join(str(item) for item in list(getattr(task, "read_set", []) or [])),
        " ".join(str(item) for item in list(getattr(task, "write_intent", []) or [])),
    ]
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _normalize_mode(mode: str | None = None) -> str:
    raw = str(mode if mode is not None else reliability_flags_from_env().compute_placement).strip().lower()
    return raw if raw in COMPUTE_PLACEMENT_MODES else "off"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result
