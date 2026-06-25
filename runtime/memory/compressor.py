from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from mcp_servers.core.operation_log import _redact_text
from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text


Compressor = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class CompressionResult:
    summary: str
    accepted: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def compress_distilled_entry(
    entry: dict[str, Any],
    *,
    compressor: Compressor | None = None,
    max_summary_chars: int = 180,
    compressor_name: str = "llm_summary_compressor",
) -> dict[str, Any]:
    updated = _copy_entry(entry)
    metadata = dict(updated.get("metadata") or {})
    if compressor is None:
        return updated

    result = compress_entry_summary(updated, compressor=compressor, max_summary_chars=max_summary_chars)
    if result.accepted:
        metadata.setdefault("original_summary", str(updated.get("summary") or ""))
        metadata["summary_compressed"] = True
        metadata["summary_compressor"] = compressor_name
        _append_reason(metadata, "summary_compressed")
        updated["summary"] = result.summary
    else:
        _extend_reasons(metadata, result.reasons)
    updated["metadata"] = metadata
    return updated


def compress_entry_summary(
    entry: dict[str, Any],
    *,
    compressor: Compressor,
    max_summary_chars: int = 180,
) -> CompressionResult:
    original = _clean_text(str(entry.get("summary") or ""))
    payload = _compression_payload(entry)
    try:
        raw_summary = compressor(payload)
    except Exception:
        return CompressionResult(summary=original, accepted=False, reasons=("compression_failed",))

    clean_summary = _clean_text(str(raw_summary or ""))
    if not clean_summary:
        return CompressionResult(summary=original, accepted=False, reasons=("compression_rejected_empty",))
    if len(clean_summary) > max_summary_chars:
        return CompressionResult(summary=original, accepted=False, reasons=("compression_rejected_too_long",))
    if _looks_sensitive(str(raw_summary)) or _looks_sensitive(clean_summary):
        return CompressionResult(summary=original, accepted=False, reasons=("compression_rejected_sensitive_output",))
    if not _summary_preserves_scope(clean_summary, entry):
        return CompressionResult(summary=original, accepted=False, reasons=("compression_rejected_missing_scope",))
    return CompressionResult(summary=clean_summary, accepted=True, reasons=("summary_compressed",))


def _compression_payload(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), list) else []
    return {
        "kind": str(entry.get("kind") or ""),
        "summary": _clean_text(str(entry.get("summary") or "")),
        "scope": [str(item) for item in list(metadata.get("scope") or []) if str(item).strip()],
        "action": _clean_text(str(metadata.get("action") or "")),
        "evidence": [_clean_text(str(item)) for item in evidence[:5]],
    }


def _summary_preserves_scope(summary: str, entry: dict[str, Any]) -> bool:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    scope = [str(item or "").replace("\\", "/").strip() for item in list(metadata.get("scope") or []) if str(item or "").strip()]
    if not scope:
        return True
    normalized = summary.replace("\\", "/").lower()
    for item in scope:
        path = item.lower().rstrip("/")
        if path and path in normalized:
            return True
    return False


def _copy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    updated = dict(entry)
    updated["tags"] = list(entry.get("tags") or [])
    updated["metadata"] = dict(entry.get("metadata") or {}) if isinstance(entry.get("metadata"), dict) else {}
    return updated


def _append_reason(metadata: dict[str, Any], reason: str) -> None:
    reasons = [str(item) for item in list(metadata.get("compression_reasons") or []) if str(item).strip()]
    if reason and reason not in reasons:
        reasons.append(reason)
    metadata["compression_reasons"] = reasons


def _extend_reasons(metadata: dict[str, Any], reasons: tuple[str, ...]) -> None:
    for reason in reasons:
        _append_reason(metadata, reason)


def _clean_text(text: str) -> str:
    return sanitize_text(_redact_text(redact_sensitive_text(str(text or "")))).strip()


def _looks_sensitive(text: str) -> bool:
    raw = str(text or "")
    redacted = _clean_text(raw)
    if raw != redacted:
        return True
    lowered = raw.lower()
    sensitive_markers = ("sk-", "api_key=", "token=", "secret=", "password=")
    return any(marker in lowered for marker in sensitive_markers)
