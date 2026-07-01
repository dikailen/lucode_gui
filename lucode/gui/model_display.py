from __future__ import annotations

import re


def display_model_name(model_id: str, label: str = "") -> str:
    """Return the user-facing model name without provider prefixes or internal ids."""

    model_id_text = str(model_id or "").strip()
    label_text = str(label or "").strip()
    label_tail = label_text.split()[-1].strip() if label_text else ""
    if _looks_like_public_model_name(label_tail):
        return label_tail
    if _looks_like_internal_model_id(model_id_text):
        return _public_name_from_internal_id(model_id_text)
    return model_id_text or label_tail or label_text


def compact_model_name(value: str, limit: int = 32) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _looks_like_public_model_name(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() == "model":
        return False
    if text.lower().endswith("_model"):
        return False
    return any(mark in text for mark in ("-", ".", "/", ":")) or any(char.isdigit() for char in text)


def _looks_like_internal_model_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and ("_" in text or text.endswith("_model"))


def _public_name_from_internal_id(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"_model$", "", text)
    text = re.sub(r"_+", "-", text)
    text = re.sub(r"(?<=\d)-(?=\d)", ".", text)
    return text
