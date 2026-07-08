from __future__ import annotations

import math
import re
from typing import Any

from runtime.common.text_utils import sanitize_text


DEFAULT_CONTEXT_WINDOW_TOKENS = 32_768
CONTEXT_WINDOW_KEYS = (
    "context_window_tokens",
    "context_length",
    "max_context_tokens",
    "max_input_tokens",
)
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """Estimate prompt tokens without a model-specific tokenizer.

    This deliberately overcounts CJK text and moderately undercounts compact
    ASCII strings less often than a pure char/4 heuristic. The value is for
    budgeting decisions, not billing.
    """

    clean = sanitize_text(str(text or ""))
    if not clean:
        return 0

    cjk_count = len(CJK_PATTERN.findall(clean))
    non_cjk = CJK_PATTERN.sub("", clean)
    non_cjk_tokens = math.ceil(len(non_cjk) / 4) if non_cjk else 0
    word_floor = len(re.findall(r"[A-Za-z0-9_]+", non_cjk))
    return max(1, cjk_count + max(non_cjk_tokens, word_floor))


def context_window_for_model(model_info: dict[str, Any] | None, *, default: int = DEFAULT_CONTEXT_WINDOW_TOKENS) -> int:
    """Return the usable context window from provider/model metadata."""

    metadata = model_info or {}
    for key in CONTEXT_WINDOW_KEYS:
        value = metadata.get(key)
        parsed = _parse_positive_int(value)
        if parsed:
            return parsed
    return max(1, int(default or DEFAULT_CONTEXT_WINDOW_TOKENS))


def _parse_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
