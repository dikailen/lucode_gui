from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.compute.context_sources import ContextSourceLabel
from runtime.compute.sanitizer import sanitize_planning_text
from runtime.compute.sensitivity import classify_task_sensitivity
from runtime.compute.placement_policy import (
    ComputePlacementViolation,
    PlannerModelDecision,
    model_info_is_local,
    model_usable_for_compute_placement,
    select_planner_model_for_request,
)
from runtime.config.model_selection import model_usable_for_task
from runtime.consistency.timeline import COMPUTE_PLACEMENT_MODES, reliability_flags_from_env
from runtime.safety.privacy import PrivacyPolicy


@dataclass(frozen=True)
class ExecutorModelDecision:
    task_id: str
    model_id: str
    candidate_index: int
    sensitivity: str
    requires_tools: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "candidate_index": self.candidate_index,
            "sensitivity": self.sensitivity,
            "requires_tools": self.requires_tools,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ComputePlacementGuard:
    model_registry: Any
    privacy_mode: str = "local_first"
    mode: str | None = None
    model_catalog: dict | None = None
    context_labels: tuple[ContextSourceLabel, ...] | list[ContextSourceLabel] = field(default_factory=tuple)

    @property
    def normalized_mode(self) -> str:
        raw = str(
            self.mode if self.mode is not None else reliability_flags_from_env().compute_placement
        ).strip().lower()
        return raw if raw in COMPUTE_PLACEMENT_MODES else "off"

    @property
    def policy(self) -> PrivacyPolicy:
        return PrivacyPolicy(self.privacy_mode)

    def guard_planner_request(
        self,
        request_text: str,
        preferred_model_ids: list[str] | tuple[str, ...] | None,
    ) -> PlannerModelDecision:
        return select_planner_model_for_request(
            request_text,
            self.model_registry,
            preferred_model_ids,
            privacy_mode=self.privacy_mode,
            mode=self.normalized_mode,
            model_catalog=self.model_catalog,
            context_labels=self.context_labels,
        )

    def guard_planning_packet(self, decision: PlannerModelDecision) -> str:
        if str(getattr(decision, "planner_side", "") or "") == "cloud":
            return sanitize_planning_text(getattr(decision, "planner_input", "") or "")
        return str(getattr(decision, "planner_input", "") or "")

    def executor_model_allowed(
        self,
        task,
        model_id: str,
        *,
        requires_tools: bool = False,
    ) -> bool:
        info = self._model_info(model_id)
        if self.normalized_mode != "enforce":
            return model_usable_for_task(info, self.policy, requires_tools=requires_tools)
        if not model_usable_for_compute_placement(info, self.policy, requires_tools=requires_tools):
            return False
        sensitivity = self.task_sensitivity(task)
        if sensitivity == "public":
            return True
        return model_info_is_local(info)

    def select_executor_model(
        self,
        task,
        candidate_model_ids: list[str] | tuple[str, ...],
        *,
        start_index: int = 0,
        requires_tools: bool | None = None,
    ) -> ExecutorModelDecision | None:
        candidates = [str(item or "").strip() for item in list(candidate_model_ids or []) if str(item or "").strip()]
        if not candidates:
            return None
        needs_tools = bool(list(getattr(task, "mcp", []) or [])) if requires_tools is None else bool(requires_tools)
        count = len(candidates)
        for offset in range(count):
            index = (start_index + offset) % count
            model_id = candidates[index]
            if not self.executor_model_allowed(task, model_id, requires_tools=needs_tools):
                continue
            return ExecutorModelDecision(
                task_id=str(getattr(task, "id", "") or ""),
                model_id=model_id,
                candidate_index=index,
                sensitivity=self.task_sensitivity(task),
                requires_tools=needs_tools,
                reasons=["compute_placement_guard"],
            )
        return None

    def task_sensitivity(self, task) -> str:
        try:
            return classify_task_sensitivity(task, context_labels=self.context_labels).sensitivity
        except Exception:
            return "project_private" if getattr(task, "mcp", None) else "public"

    def violation(self, message: str) -> ComputePlacementViolation:
        return ComputePlacementViolation(message)

    def _model_info(self, model_id: str) -> dict:
        getter = getattr(self.model_registry, "get_model_info", None)
        if not callable(getter) or not model_id:
            return {}
        try:
            return dict(getter(model_id) or {})
        except Exception:
            return {}
