from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from runtime.context.ledger import ContextLedgerInput, ContextLedgerResult, build_context_ledger
from runtime.context.tool_dehydration import ToolDehydratedResult, dehydrate_tool_result


CONTEXT_LEDGER_ENV = "LUCODE_CONTEXT_LEDGER"
VALID_MODES = {"off", "observe", "enforce"}


@dataclass(frozen=True)
class ContextMiddlewareResult:
    run_input: str
    routing_input: str
    mode: str
    observed: bool
    applied: bool
    ledger_result: ContextLedgerResult | None = None
    tool_summaries: list[ToolDehydratedResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextCompressionMiddleware:
    """Prepare conversation context and optional long-context compression.

    Recent conversation is always model context when it exists. The mode only
    controls whether the ledger may apply its long-context compression policy.
    Routing always uses the original current user input.
    """

    def __init__(self, *, history: Any | None = None, mode: str | None = None) -> None:
        self.history = history
        self.mode = _normalize_mode(mode if mode is not None else os.environ.get(CONTEXT_LEDGER_ENV), default="observe")

    def prepare_run_input(
        self,
        *,
        session_id: str,
        user_input: str,
        model_info: dict[str, Any] | None = None,
        tool_schema_tokens: int = 0,
        evidence_tokens: int = 0,
        tool_results: list[dict[str, Any]] | None = None,
        current_input_persisted: bool = False,
        inline_files: list[dict[str, Any]] | None = None,
    ) -> ContextMiddlewareResult:
        current_input = str(user_input or "")
        routing_input = current_input
        current_turn_input = _current_turn_input(
            current_input,
            inline_files=inline_files or [],
            model_info=model_info or {},
        )
        attachment_metadata = {
            "count": len(inline_files or []),
            "applied_to_model_input": bool(inline_files),
        }
        if self.mode == "off":
            return ContextMiddlewareResult(
                run_input=current_turn_input,
                routing_input=routing_input,
                mode="off",
                observed=False,
                applied=False,
                metadata={
                    "context_ledger": {"mode": "off"},
                    "history_context": {"applied": False, "recent_turn_count": 0},
                    "tool_dehydration": {"count": 0, "items": []},
                    "attachment_context": attachment_metadata,
                },
            )

        try:
            messages = self._load_messages(session_id)
            if current_input_persisted:
                messages = _without_persisted_current_input(messages, current_input)
            summary = self._load_context_summary(session_id)
            ledger = build_context_ledger(
                ContextLedgerInput(
                    session_id=str(session_id or ""),
                    current_input=current_input,
                    messages=messages,
                    existing_summary=summary,
                    model_info=model_info or {},
                    inline_files=inline_files or [],
                    tool_schema_tokens=tool_schema_tokens,
                    evidence_tokens=evidence_tokens,
                )
            )
        except Exception as exc:
            tool_summaries = self._dehydrate_tool_results(tool_results or [])
            return ContextMiddlewareResult(
                run_input=current_turn_input,
                routing_input=routing_input,
                mode=self.mode,
                observed=False,
                applied=False,
                ledger_result=None,
                tool_summaries=tool_summaries,
                metadata={
                    "context_ledger": {
                        "mode": self.mode,
                        "error": str(exc),
                    },
                    "history_context": {"applied": False, "recent_turn_count": 0},
                    "tool_dehydration": _tool_dehydration_metadata(tool_summaries),
                    "attachment_context": attachment_metadata,
                },
            )

        tool_summaries = self._dehydrate_tool_results(tool_results or [])
        enforce_eligible = bool(ledger.triggered)
        compression_applied = self.mode == "enforce" and enforce_eligible
        history_applied = bool(ledger.recent_turns or ledger.session_summary)
        return ContextMiddlewareResult(
            run_input=ledger.run_input if history_applied or compression_applied else current_turn_input,
            routing_input=routing_input,
            mode=self.mode,
            observed=True,
            applied=compression_applied,
            ledger_result=ledger,
            tool_summaries=tool_summaries,
            metadata={
                "context_ledger": _ledger_metadata(
                    ledger,
                    requested_mode=self.mode,
                    applied=compression_applied,
                    enforce_eligible=enforce_eligible,
                ),
                "history_context": {
                    "applied": history_applied,
                    "recent_turn_count": len(ledger.recent_turns),
                },
                "tool_dehydration": _tool_dehydration_metadata(tool_summaries),
                "attachment_context": attachment_metadata,
            },
        )

    def _load_messages(self, session_id: str) -> list[dict[str, str]]:
        if self.history is None:
            return []
        loader = getattr(self.history, "load_messages", None)
        if callable(loader):
            return _message_list(loader(session_id, limit=80))
        recent_loader = getattr(self.history, "load_recent_turns", None)
        if callable(recent_loader):
            return _message_list(recent_loader(session_id, max_messages=12))
        return []

    def _load_context_summary(self, session_id: str) -> str:
        if self.history is None:
            return ""
        loader = getattr(self.history, "load_context_summary", None)
        if not callable(loader):
            return ""
        return str(loader(session_id, max_chars=2400) or "")

    def _dehydrate_tool_results(self, tool_results: list[dict[str, Any]]) -> list[ToolDehydratedResult]:
        summaries: list[ToolDehydratedResult] = []
        for item in list(tool_results or []):
            if not isinstance(item, dict):
                continue
            summaries.append(
                dehydrate_tool_result(
                    tool=str(item.get("tool") or item.get("tool_name") or ""),
                    action=str(item.get("action") or item.get("command") or ""),
                    raw_result=item.get("raw_result", item.get("result")),
                    evidence_ref=str(item.get("evidence_ref") or ""),
                    raw_artifact_ref=str(item.get("raw_artifact_ref") or ""),
                )
            )
        return summaries


def _current_turn_input(
    current_input: str,
    *,
    inline_files: list[dict[str, Any]],
    model_info: dict[str, Any],
) -> str:
    if not inline_files:
        return current_input
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="current_turn",
            current_input=current_input,
            inline_files=inline_files,
            model_info=model_info,
        )
    )
    return result.run_input


def _ledger_metadata(
    ledger: ContextLedgerResult,
    *,
    requested_mode: str,
    applied: bool,
    enforce_eligible: bool,
) -> dict[str, Any]:
    return {
        "mode": ledger.mode,
        "requested_mode": requested_mode,
        "triggered": ledger.triggered,
        "applied": bool(applied),
        "enforce_eligible": bool(enforce_eligible),
        "apply_reason": (
            "budget_triggered"
            if applied
            else "below_enforce_threshold"
            if requested_mode == "enforce"
            else "observe_only"
        ),
        "estimated_input_tokens": ledger.estimated_input_tokens,
        "context_window_tokens": ledger.context_window_tokens,
        "compression_reasons": list(ledger.compression_reasons),
        "dropped_sections": list(ledger.dropped_sections),
        "recent_turn_count": len(ledger.recent_turns),
        "summary_chars": len(ledger.session_summary),
    }


def _tool_dehydration_metadata(items: list[ToolDehydratedResult]) -> dict[str, Any]:
    return {
        "count": len(items),
        "items": [
            {
                "summary": item.summary,
                "key_fields": dict(item.key_fields),
                "evidence_ref": item.evidence_ref,
                "raw_artifact_ref": item.raw_artifact_ref,
                "omitted_fields": list(item.omitted_fields),
                "redacted": item.redacted,
            }
            for item in items
        ],
    }


def _message_list(value: Any) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role and content:
            messages.append({"role": role, "content": content})
    return messages


def _without_persisted_current_input(
    messages: list[dict[str, str]],
    current_input: str,
) -> list[dict[str, str]]:
    if not messages:
        return messages
    tail = messages[-1]
    if tail.get("role") != "user" or tail.get("content") != str(current_input or "").strip():
        return messages
    return messages[:-1]


def _normalize_mode(value: str | None, *, default: str) -> str:
    mode = str(value or default or "observe").strip().lower()
    return mode if mode in VALID_MODES else default
