from __future__ import annotations

import re


def sanitize_planning_text(text: str, *, limit: int = 1200) -> str:
    value = str(text or "")
    value = _redact_dom(value)
    value = _redact_secrets(value)
    value = _redact_paths(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[:limit] + f"...[truncated {len(value) - limit} chars]"
    return value


def _redact_dom(value: str) -> str:
    value = re.sub(r"DOM\s*:\s*<[^>]+>.*", "DOM: [dom]", value, flags=re.IGNORECASE)
    return re.sub(r"<(?:input|form|button|select|textarea|html|body|div)\b[^>]*>", "[dom]", value, flags=re.IGNORECASE)


def _redact_secrets(value: str) -> str:
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}\b", "[secret]", value)
    value = re.sub(r"\b(token|api[_-]?key|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[secret]", value, flags=re.IGNORECASE)
    return value


def _redact_paths(value: str) -> str:
    value = re.sub(r"[A-Za-z]:[\\/][^\s,;]+", "[path]", value)
    value = re.sub(r"(?<!://)(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+", "[path]", value)
    value = re.sub(r"\b[\w.-]+\.(?:py|ts|tsx|js|jsx|json|toml|yaml|yml|md|env|txt)\b", "[path]", value)
    return value
