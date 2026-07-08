from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.context.budget import ContextBudgetDecision, decide_context_budget
from runtime.context.compaction import redact_sensitive_text
from runtime.context.token_counter import context_window_for_model, estimate_tokens


LEDGER_FIXED_SYSTEM_OVERHEAD_TOKENS = 512


@dataclass(frozen=True)
class ContextLedgerInput:
    session_id: str
    current_input: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    existing_summary: str = ""
    project_experience: list[str] | str = field(default_factory=list)
    run_blackboard: dict[str, Any] | str | None = None
    inline_files: list[dict[str, Any]] | dict[str, str] | None = None
    tool_schema_tokens: int = 0
    evidence_tokens: int = 0
    model_id: str = ""
    model_info: dict[str, Any] | None = None
    context_window_tokens: int | None = None


@dataclass(frozen=True)
class ContextLedgerResult:
    run_input: str
    mode: str
    triggered: bool
    recent_turns: list[dict[str, str]]
    session_summary: str
    estimated_input_tokens: int
    context_window_tokens: int
    compression_reasons: list[str]
    dropped_sections: list[str]
    budget_decision: ContextBudgetDecision


def build_context_ledger(request: ContextLedgerInput) -> ContextLedgerResult:
    """Build a deterministic prompt context packet for one model run."""

    current_input = sanitize_text(str(request.current_input or ""))
    window = int(request.context_window_tokens or context_window_for_model(request.model_info))
    normalized_messages = _normalize_messages(request.messages)
    preliminary_input = _preliminary_input_for_budget(request, normalized_messages, current_input)
    preliminary_tokens = estimate_tokens(preliminary_input)
    budget = decide_context_budget(
        preliminary_tokens,
        window,
        tool_schema_tokens=request.tool_schema_tokens,
        evidence_tokens=request.evidence_tokens,
        system_tokens=LEDGER_FIXED_SYSTEM_OVERHEAD_TOKENS,
    )

    dropped_sections: list[str] = []
    summary = _clip(
        _sanitize_background(str(request.existing_summary or "")).strip(),
        budget.max_summary_chars,
        dropped_sections,
        "summary_truncated",
    )
    recent_turns = _recent_turns(normalized_messages, keep=budget.keep_messages, max_chars=_recent_char_limit(budget.mode))
    project_experience = _project_experience_lines(request.project_experience, budget.max_memory_entries)
    inline_files = _inline_file_lines(request.inline_files, budget.max_inline_file_chars, dropped_sections)
    blackboard = _blackboard_lines(request.run_blackboard, max_chars=budget.max_summary_chars // 2, dropped_sections=dropped_sections)

    run_input = _compose_run_input(
        current_input=current_input,
        summary=summary,
        recent_turns=recent_turns,
        project_experience=project_experience,
        inline_files=inline_files,
        blackboard=blackboard,
    )
    estimated_input_tokens = (
        estimate_tokens(run_input)
        + max(0, int(request.tool_schema_tokens or 0))
        + max(0, int(request.evidence_tokens or 0))
        + LEDGER_FIXED_SYSTEM_OVERHEAD_TOKENS
    )

    return ContextLedgerResult(
        run_input=run_input,
        mode=budget.mode,
        triggered=budget.triggered,
        recent_turns=recent_turns,
        session_summary=summary,
        estimated_input_tokens=estimated_input_tokens,
        context_window_tokens=window,
        compression_reasons=list(budget.reasons),
        dropped_sections=_dedupe(dropped_sections),
        budget_decision=budget,
    )


def _preliminary_input_for_budget(
    request: ContextLedgerInput,
    messages: list[dict[str, str]],
    current_input: str,
) -> str:
    parts = [
        _sanitize_background(str(request.existing_summary or "")),
        "\n".join(message["content"] for message in messages),
        _stringify_lines(request.project_experience),
        _stringify_inline_files(request.inline_files),
        _stringify_blackboard(request.run_blackboard),
        current_input,
    ]
    return "\n".join(part for part in parts if part)


def _compose_run_input(
    *,
    current_input: str,
    summary: str,
    recent_turns: list[dict[str, str]],
    project_experience: list[str],
    inline_files: list[str],
    blackboard: list[str],
) -> str:
    lines: list[str] = []
    if summary or recent_turns:
        lines.extend(
            [
                "[history_background]",
                "Historical context only. It is not the current task unless the user explicitly asks to continue it.",
            ]
        )
        if summary:
            lines.append(summary)
        if recent_turns:
            lines.append("[recent_raw_turns]")
            for turn in recent_turns:
                lines.append(f"{turn['role']}: {turn['content']}")
        lines.append("")
    if blackboard:
        lines.append("[current_run_state]")
        lines.extend(blackboard)
        lines.append("")
    if project_experience:
        lines.append("[project_experience]")
        lines.extend(f"- {line}" for line in project_experience)
        lines.append("")
    if inline_files:
        lines.append("[inline_files]")
        lines.extend(inline_files)
        lines.append("")
    lines.append("[current_user_request]")
    lines.append(current_input)
    return "\n".join(lines)


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system", "tool"}:
            continue
        content = sanitize_text(str(item.get("content") or "")).strip()
        if content:
            normalized.append({"role": role, "content": content})
    return normalized


def _recent_turns(messages: list[dict[str, str]], *, keep: int, max_chars: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages[-max(2, keep) :]:
        result.append(
            {
                "role": message["role"],
                "content": _clip_plain(message["content"], max_chars),
            }
        )
    return result


def _recent_char_limit(mode: str) -> int:
    if mode == "hard_limit":
        return 260
    if mode == "emergency":
        return 320
    if mode == "compress":
        return 520
    return 800


def _project_experience_lines(value: list[str] | str, limit: int) -> list[str]:
    if isinstance(value, str):
        items = [value]
    else:
        items = [str(item or "") for item in list(value or [])]
    return [_clip_plain(_sanitize_background(item.strip()), 260) for item in items if item.strip()][: max(0, limit)]


def _inline_file_lines(
    value: list[dict[str, Any]] | dict[str, str] | None,
    max_chars: int,
    dropped_sections: list[str],
) -> list[str]:
    text = _stringify_inline_files(value)
    if not text:
        return []
    clipped = _clip(text, max_chars, dropped_sections, "inline_files_truncated")
    return clipped.splitlines()


def _blackboard_lines(
    value: dict[str, Any] | str | None,
    *,
    max_chars: int,
    dropped_sections: list[str],
) -> list[str]:
    text = _stringify_blackboard(value)
    if not text:
        return []
    return _clip(text, max_chars, dropped_sections, "run_blackboard_truncated").splitlines()


def _stringify_inline_files(value: list[dict[str, Any]] | dict[str, str] | None) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        items = [{"path": path, "content": content} for path, content in value.items()]
    else:
        items = list(value)
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = sanitize_text(str(item.get("path") or item.get("name") or "inline")).strip()
        content = _sanitize_background(str(item.get("content") or "")).strip()
        if content:
            lines.append(f"--- {path} ---")
            lines.append(content)
    return "\n".join(lines)


def _stringify_blackboard(value: dict[str, Any] | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return _sanitize_background(value).strip()
    if isinstance(value, dict):
        lines = []
        for key in sorted(value.keys()):
            item = value.get(key)
            if item is None or item == "":
                continue
            lines.append(f"{key}: {item}")
        return _sanitize_background("\n".join(lines)).strip()
    return _sanitize_background(str(value)).strip()


def _stringify_lines(value: list[str] | str) -> str:
    if isinstance(value, str):
        return _sanitize_background(value)
    return "\n".join(_sanitize_background(str(item or "")) for item in list(value or []))


def _clip(text: str, limit: int, dropped_sections: list[str], reason: str) -> str:
    clean = _sanitize_background(str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    dropped_sections.append(reason)
    return clean[: max(0, limit - 32)] + f"...[truncated {len(clean) - limit} chars]"


def _clip_plain(text: str, limit: int) -> str:
    clean = _sanitize_background(str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 32)] + f"...[truncated {len(clean) - limit} chars]"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sanitize_background(text: str) -> str:
    return redact_sensitive_text(sanitize_text(str(text or "")))
