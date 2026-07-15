from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from runtime.context.budget import decide_context_budget
from runtime.context.token_counter import context_window_for_model, estimate_tokens


WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS = 512
WORKER_CONTEXT_MODE_ENV = "LUCODE_WORKER_CONTEXT_MODE"


@dataclass(frozen=True)
class WorkerContextObservation:
    task_id: str
    model_id: str
    mcp_ids: tuple[str, ...]
    prompt_tokens: int
    tool_schema_tokens: int
    accepted_evidence_tokens: int
    estimated_input_tokens: int
    context_window_tokens: int
    utilization: float
    budget_mode: str
    triggered: bool
    reserved_tokens: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "mcp_ids": list(self.mcp_ids),
            "prompt_tokens": self.prompt_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "accepted_evidence_tokens": self.accepted_evidence_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "context_window_tokens": self.context_window_tokens,
            "utilization": self.utilization,
            "budget_mode": self.budget_mode,
            "triggered": self.triggered,
            "reserved_tokens": dict(self.reserved_tokens),
        }


def build_worker_context_observation(
    *,
    task,
    prompt: str,
    model_info: dict[str, Any] | None,
    mcp_ids: list[str] | tuple[str, ...],
    tool_context: str,
    accepted_evidence: dict[str, Any] | None,
) -> WorkerContextObservation:
    """Measure a worker prompt without retaining its private contents."""

    prompt_tokens = estimate_tokens(prompt)
    tool_schema_tokens = estimate_tokens(tool_context)
    evidence_tokens = estimate_tokens(_accepted_evidence_text(accepted_evidence))
    window = context_window_for_model(model_info)
    decision = decide_context_budget(
        prompt_tokens,
        window,
        tool_schema_tokens=tool_schema_tokens,
        evidence_tokens=evidence_tokens,
        system_tokens=WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS,
    )
    estimated = prompt_tokens + sum(decision.reserved_tokens.values())
    return WorkerContextObservation(
        task_id=str(getattr(task, "id", "") or ""),
        model_id=str(getattr(task, "model", "") or ""),
        mcp_ids=tuple(_dedupe_mcp_ids(mcp_ids)),
        prompt_tokens=prompt_tokens,
        tool_schema_tokens=tool_schema_tokens,
        accepted_evidence_tokens=evidence_tokens,
        estimated_input_tokens=estimated,
        context_window_tokens=window,
        utilization=decision.utilization,
        budget_mode=decision.mode,
        triggered=decision.triggered,
        reserved_tokens=dict(decision.reserved_tokens),
    )


def _accepted_evidence_text(packet: dict[str, Any] | None) -> str:
    if not isinstance(packet, dict) or not packet:
        return ""
    try:
        return json.dumps(packet, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def _dedupe_mcp_ids(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def worker_context_mode() -> str:
    value = str(os.environ.get(WORKER_CONTEXT_MODE_ENV) or "observe").strip().lower()
    return value if value in {"off", "observe", "enforce"} else "observe"


def render_worker_prompt(
    *,
    refined_request: str,
    task_instruction: str,
    dependency_context: str = "",
    workspace_context: str = "",
    shared_context: str = "",
    memory_context: str = "",
) -> str:
    parts = ["优化后的用户请求：\n" + str(refined_request or "")]
    if dependency_context.strip():
        parts.append("前序任务输出：\n" + dependency_context.strip())
    if shared_context.strip():
        parts.append(shared_context.strip())
    if memory_context.strip():
        parts.append(memory_context.strip())
    if workspace_context.strip():
        parts.append(workspace_context.strip())
    parts.append("你的具体任务：\n" + str(task_instruction or ""))
    return "\n\n".join(parts)


def enforce_worker_prompt(
    *,
    observation: WorkerContextObservation,
    refined_request: str,
    task_instruction: str,
    dependency_context: str = "",
    workspace_context: str = "",
    shared_context: str = "",
    memory_context: str = "",
) -> tuple[str, bool, list[str]]:
    """Render a bounded worker prompt while preserving current task text."""

    if worker_context_mode() != "enforce" or not observation.triggered:
        return render_worker_prompt(
            refined_request=refined_request,
            task_instruction=task_instruction,
            dependency_context=dependency_context,
            workspace_context=workspace_context,
            shared_context=shared_context,
            memory_context=memory_context,
        ), False, []
    decision = decide_context_budget(
        observation.prompt_tokens,
        observation.context_window_tokens,
        tool_schema_tokens=observation.tool_schema_tokens,
        evidence_tokens=observation.accepted_evidence_tokens,
        system_tokens=WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS,
    )
    dropped: list[str] = []
    sections = {
        "dependency_context": _clip(
            dependency_context, decision.max_summary_chars, "dependency_context", dropped
        ),
        "shared_context": _clip(shared_context, decision.max_summary_chars, "shared_context", dropped),
        "memory_context": _clip(memory_context, decision.max_summary_chars // 2, "memory_context", dropped),
        "workspace_context": _clip(
            workspace_context, decision.max_inline_file_chars, "workspace_context", dropped
        ),
    }
    sections = _fit_background_sections(
        sections,
        refined_request=refined_request,
        task_instruction=task_instruction,
        prompt_budget_tokens=_remaining_prompt_budget(observation),
        dropped=dropped,
    )
    return render_worker_prompt(
        refined_request=refined_request,
        task_instruction=task_instruction,
        dependency_context=sections["dependency_context"],
        workspace_context=sections["workspace_context"],
        shared_context=sections["shared_context"],
        memory_context=sections["memory_context"],
    ), True, dropped


def _clip(value: str, limit: int, name: str, dropped: list[str], *, record_drop: bool = True) -> str:
    text = str(value or "").strip()
    if limit <= 0:
        if record_drop and text and name not in dropped:
            dropped.append(name)
        return ""
    if len(text) <= limit:
        return text
    if record_drop and name not in dropped:
        dropped.append(name)
    return text[: max(0, limit - 32)].rstrip() + "\n...[context truncated]"


def _remaining_prompt_budget(observation: WorkerContextObservation) -> int:
    return max(
        0,
        observation.context_window_tokens
        - observation.tool_schema_tokens
        - observation.accepted_evidence_tokens
        - WORKER_FIXED_SYSTEM_OVERHEAD_TOKENS,
    )


def _fit_background_sections(
    sections: dict[str, str],
    *,
    refined_request: str,
    task_instruction: str,
    prompt_budget_tokens: int,
    dropped: list[str],
) -> dict[str, str]:
    """Fit optional worker background into one shared token budget.

    The current request and task instruction are deliberately excluded from this
    reduction path. If they alone exceed a model's remaining budget, the caller
    still receives them intact and records the pressure through the observation.
    """

    result = dict(sections)
    for name in ("workspace_context", "memory_context", "shared_context", "dependency_context"):
        if estimate_tokens(_render_with_sections(refined_request, task_instruction, result)) <= prompt_budget_tokens:
            break
        value = result[name]
        if not value:
            continue
        low, high, best = 0, len(value), 0
        while low <= high:
            midpoint = (low + high) // 2
            candidate = dict(result)
            candidate[name] = _clip(value, midpoint, name, dropped, record_drop=False)
            if estimate_tokens(_render_with_sections(refined_request, task_instruction, candidate)) <= prompt_budget_tokens:
                best = midpoint
                low = midpoint + 1
            else:
                high = midpoint - 1
        result[name] = _clip(value, best, name, dropped)
    return result


def _render_with_sections(refined_request: str, task_instruction: str, sections: dict[str, str]) -> str:
    return render_worker_prompt(
        refined_request=refined_request,
        task_instruction=task_instruction,
        dependency_context=sections.get("dependency_context", ""),
        workspace_context=sections.get("workspace_context", ""),
        shared_context=sections.get("shared_context", ""),
        memory_context=sections.get("memory_context", ""),
    )
