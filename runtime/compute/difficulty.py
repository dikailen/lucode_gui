from __future__ import annotations

import re
from dataclasses import dataclass, field


DIFFICULTY_LEVELS = {"simple", "normal", "complex", "long_context", "high_risk"}


@dataclass(frozen=True)
class DifficultyDecision:
    difficulty: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "difficulty": self.difficulty,
            "reasons": list(self.reasons),
        }


def classify_task_difficulty(task, context_text: str = "") -> DifficultyDecision:
    text = _task_text(task, context_text).lower()
    mcps = {str(item or "").strip() for item in list(getattr(task, "mcp", []) or []) if str(item or "").strip()}
    read_set = _string_list(getattr(task, "read_set", []) or [])
    write_intent = _string_list(getattr(task, "write_intent", []) or [])
    reasons: list[str] = []

    if write_intent or "workspace_edit" in mcps or "command_runner" in mcps:
        reasons.append("mutating_or_command_tool")
        return DifficultyDecision("high_risk", reasons)
    if _mentions_high_risk_browser_action(text):
        reasons.append("browser_mutation")
        return DifficultyDecision("high_risk", reasons)
    if _mentions_destructive_action(text):
        reasons.append("destructive_action")
        return DifficultyDecision("high_risk", reasons)

    if len(text) > 6000 or len(read_set) >= 5:
        reasons.append("large_context")
        return DifficultyDecision("long_context", reasons)

    if _mentions_complex_work(text) or len(_string_list(getattr(task, "depends_on", []) or [])) > 0:
        reasons.append("complex_reasoning")
        return DifficultyDecision("complex", reasons)

    if not mcps and not read_set and len(text) < 600:
        return DifficultyDecision("simple", ["short_no_tools"])
    return DifficultyDecision("normal", ["default"])


def _mentions_high_risk_browser_action(text: str) -> bool:
    return bool(
        "browser_submit_form" in text
        or "browser_set_input_value" in text
        or "browser_click_element" in text
        or re.search(r"\b(click|fill|submit|form|selector)\b", text)
    )


def _mentions_destructive_action(text: str) -> bool:
    return bool(re.search(r"\b(delete|remove|drop|overwrite|publish|deploy|commit|push)\b", text))


def _mentions_complex_work(text: str) -> bool:
    return bool(
        re.search(
            r"\b(implement|refactor|migrate|migration|architecture|design|compare|plan|multi-agent|workflow|integration)\b",
            text,
        )
    )


def _task_text(task, context_text: str = "") -> str:
    parts = [
        context_text,
        getattr(task, "id", ""),
        getattr(task, "title", ""),
        getattr(task, "instruction", ""),
        " ".join(_string_list(getattr(task, "acceptance_criteria", []) or [])),
        " ".join(_string_list(getattr(task, "expected_outputs", []) or [])),
        " ".join(_string_list(getattr(task, "read_set", []) or [])),
        " ".join(_string_list(getattr(task, "write_intent", []) or [])),
    ]
    return "\n".join(str(item or "") for item in parts if str(item or "").strip())


def _string_list(values) -> list[str]:
    result: list[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text:
            result.append(text)
    return result
