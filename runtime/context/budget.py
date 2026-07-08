from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContextBudgetDecision:
    mode: str
    triggered: bool
    keep_messages: int
    max_summary_chars: int
    max_inline_file_chars: int
    max_memory_entries: int
    reserved_tokens: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    utilization: float = 0.0


def decide_context_budget(
    estimated_input_tokens: int,
    context_window_tokens: int,
    *,
    tool_schema_tokens: int = 0,
    evidence_tokens: int = 0,
    system_tokens: int = 0,
) -> ContextBudgetDecision:
    """Choose how aggressively to shrink history before a model call."""

    window = max(1, int(context_window_tokens or 1))
    reserved = {
        "tool_schema_tokens": _non_negative(tool_schema_tokens),
        "evidence_tokens": _non_negative(evidence_tokens),
        "system_tokens": _non_negative(system_tokens),
    }
    total = _non_negative(estimated_input_tokens) + sum(reserved.values())
    utilization = total / window
    mode = _mode_for_utilization(utilization)
    reasons = [_reason_for_mode(mode)]

    tool_pressure = reserved["tool_schema_tokens"] / window
    evidence_pressure = reserved["evidence_tokens"] / window
    if tool_pressure >= 0.25:
        reasons.append("tool_schema_pressure")
    if evidence_pressure >= 0.15:
        reasons.append("evidence_pressure")

    config = _config_for_mode(mode)
    if "tool_schema_pressure" in reasons and mode in {"normal", "warning"}:
        mode = "compress"
        config = _config_for_mode(mode)
        reasons[0] = _reason_for_mode(mode)
    if tool_pressure >= 0.55 and config["keep_messages"] > 4:
        config = {**config, "keep_messages": 4}

    return ContextBudgetDecision(
        mode=mode,
        triggered=mode in {"compress", "emergency", "hard_limit"},
        keep_messages=max(2, int(config["keep_messages"])),
        max_summary_chars=max(200, int(config["max_summary_chars"])),
        max_inline_file_chars=max(200, int(config["max_inline_file_chars"])),
        max_memory_entries=max(0, int(config["max_memory_entries"])),
        reserved_tokens=reserved,
        reasons=_dedupe(reasons),
        utilization=utilization,
    )


def _mode_for_utilization(utilization: float) -> str:
    if utilization >= 0.95:
        return "hard_limit"
    if utilization >= 0.88:
        return "emergency"
    if utilization >= 0.72:
        return "compress"
    if utilization >= 0.60:
        return "warning"
    return "normal"


def _config_for_mode(mode: str) -> dict[str, int]:
    return {
        "normal": {
            "keep_messages": 6,
            "max_summary_chars": 2_400,
            "max_inline_file_chars": 4_000,
            "max_memory_entries": 6,
        },
        "warning": {
            "keep_messages": 6,
            "max_summary_chars": 2_000,
            "max_inline_file_chars": 3_000,
            "max_memory_entries": 5,
        },
        "compress": {
            "keep_messages": 4,
            "max_summary_chars": 1_400,
            "max_inline_file_chars": 2_000,
            "max_memory_entries": 4,
        },
        "emergency": {
            "keep_messages": 2,
            "max_summary_chars": 900,
            "max_inline_file_chars": 1_000,
            "max_memory_entries": 2,
        },
        "hard_limit": {
            "keep_messages": 2,
            "max_summary_chars": 800,
            "max_inline_file_chars": 800,
            "max_memory_entries": 1,
        },
    }[mode]


def _reason_for_mode(mode: str) -> str:
    return {
        "normal": "within_budget",
        "warning": "budget_warning",
        "compress": "budget_compress",
        "emergency": "budget_emergency",
        "hard_limit": "budget_hard_limit",
    }[mode]


def _non_negative(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
