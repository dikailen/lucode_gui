from __future__ import annotations

import base64
import hashlib
from typing import Any


MAX_TEXT_CHARS = 800
MAX_COLLECTION_ITEMS = 40
MAX_DEPTH = 6

_SENSITIVE_MARKERS = ("api_key", "apikey", "authorization", "password", "secret", "token", "cookie", "credential")
_DOM_MARKERS = ("dom", "html", "page_source")
_TERMINAL_MARKERS = ("stdout", "stderr", "terminal_output", "command_output")


def sanitize_payload(value: Any) -> Any:
    return _sanitize(value, key="", depth=0)


def _sanitize(value: Any, *, key: str, depth: int) -> Any:
    normalized_key = _normalized_key(key)
    if _contains_marker(normalized_key, _SENSITIVE_MARKERS):
        return "[redacted]"
    if _contains_marker(normalized_key, _DOM_MARKERS):
        return _omitted("dom", value)
    if _contains_marker(normalized_key, _TERMINAL_MARKERS):
        return _omitted("terminal_output", value)
    if depth >= MAX_DEPTH:
        return _omitted("depth", value)
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in list(value.items())[:MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, key=key, depth=depth + 1) for item in list(value)[:MAX_COLLECTION_ITEMS]]
    if isinstance(value, str):
        if _looks_like_base64(value):
            return _omitted("base64", value)
        if _looks_like_dom(value):
            return _omitted("dom", value)
        if len(value) > MAX_TEXT_CHARS:
            return _omitted("truncated_text", value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _omitted("unsupported", repr(value))


def _normalized_key(value: str) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum() or character == "_")


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker.replace("_", "") in value.replace("_", "") for marker in markers)


def _looks_like_dom(value: str) -> bool:
    return value.lstrip().lower().startswith(("<!doctype html", "<html", "<body"))


def _looks_like_base64(value: str) -> bool:
    compact = value.strip()
    if ";base64," in compact.lower():
        return True
    if len(compact) < 512 or len(compact) % 4:
        return False
    try:
        base64.b64decode(compact, validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    return True


def _omitted(kind: str, value: Any) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"[omitted:{kind}:sha256:{digest}]"
