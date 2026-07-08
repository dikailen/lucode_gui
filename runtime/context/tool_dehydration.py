from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text


MAX_SUMMARY_CHARS = 1_200
MAX_FIELD_CHARS = 360
LONG_FIELD_NAMES = {
    "content",
    "dom",
    "html",
    "raw",
    "raw_html",
    "raw_json",
    "stdout",
    "stderr",
    "trace",
    "logs",
    "metadata",
}
SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key)", re.IGNORECASE)


@dataclass(frozen=True)
class ToolDehydratedResult:
    summary: str
    key_fields: dict[str, Any] = field(default_factory=dict)
    evidence_ref: str = ""
    raw_artifact_ref: str = ""
    omitted_fields: list[str] = field(default_factory=list)
    redacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def dehydrate_tool_result(
    *,
    tool: str,
    action: str,
    raw_result: Any,
    evidence_ref: str = "",
    raw_artifact_ref: str = "",
) -> ToolDehydratedResult:
    """Compress a tool result for prompt use while keeping audit refs."""

    clean_tool = _clean_text(tool)
    clean_action = _clean_text(action)
    data = raw_result if isinstance(raw_result, dict) else {"result": raw_result}
    omitted = _omitted_fields(data)

    lowered = f"{clean_tool}.{clean_action}".lower()
    if "browser" in lowered:
        key_fields, lines, extra_omitted = _browser_summary(data)
    elif "terminal" in lowered or "command" in lowered or "run_command" in lowered:
        key_fields, lines, extra_omitted = _command_summary(data)
    elif "search" in lowered:
        key_fields, lines, extra_omitted = _search_summary(data)
    elif _looks_like_file_snapshot(clean_tool, clean_action, data):
        key_fields, lines, extra_omitted = _file_snapshot_summary(data)
    elif "mcp" in lowered or _contains_key(data, "jsonrpc"):
        key_fields, lines, extra_omitted = _mcp_summary(data)
    else:
        key_fields, lines, extra_omitted = _generic_summary(data)

    omitted.extend(extra_omitted)
    if not evidence_ref:
        omitted.append("missing_evidence_ref")
    if not raw_artifact_ref:
        omitted.append("missing_raw_artifact_ref")

    raw_summary = "\n".join(line for line in lines if line)
    redacted_summary = _redact(raw_summary)
    summary = _clip(redacted_summary, MAX_SUMMARY_CHARS)
    redacted = redacted_summary != sanitize_text(raw_summary) or _contains_redacted_secret(raw_result)
    return ToolDehydratedResult(
        summary=summary,
        key_fields=key_fields,
        evidence_ref=_clean_text(evidence_ref),
        raw_artifact_ref=_clean_text(raw_artifact_ref),
        omitted_fields=_dedupe(omitted),
        redacted=redacted,
    )


def _browser_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    url = _clean_text(data.get("url"))
    title = _clean_text(data.get("title"))
    text = _preview(data.get("text"), 240)
    elements = _element_summaries(data.get("elements"))
    key_fields = {"url": url, "title": title, "elements": elements}
    lines = ["browser summary", f"url: {url}", f"title: {title}"]
    if text:
        lines.append(f"text: {text}")
    if elements:
        lines.append("elements:")
        lines.extend(f"- {item}" for item in elements[:8])
    return _drop_empty(key_fields), lines, ["browser_dom_omitted"] if data.get("dom") else []


def _command_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    command = _clean_text(data.get("command") or data.get("cmd") or data.get("args"))
    returncode = data.get("returncode", data.get("exit_code"))
    stdout_tail = _tail_text(data.get("stdout"), 20)
    stderr_tail = _tail_text(data.get("stderr"), 12)
    key_fields = {"command": command, "returncode": returncode}
    lines = ["command output", f"command: {command}", f"returncode={returncode}"]
    if stderr_tail:
        lines.append("stderr_tail:")
        lines.append(stderr_tail)
    if stdout_tail:
        lines.append("stdout_tail:")
        lines.append(stdout_tail)
    return _drop_empty(key_fields), lines, ["stdout", "stderr"]


def _mcp_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    status = result.get("status") or result.get("state") or data.get("status")
    models = result.get("models") or result.get("items") or result.get("tools")
    key_fields = {}
    if status:
        key_fields["status"] = status
    if isinstance(models, list):
        key_fields["models"] = [_clean_text(item) for item in models[:20]]
    lines = ["mcp result"]
    if status:
        lines.append(f"status: {status}")
    if isinstance(models, list):
        lines.append("models: " + ", ".join(_clean_text(item) for item in models[:20]))
    if not key_fields:
        key_fields = _small_key_fields(result)
        lines.append(_json_preview(key_fields))
    return key_fields, lines, ["protocol_fields_omitted", "mcp_metadata_omitted"]


def _search_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    raw_results = data.get("results") if isinstance(data.get("results"), list) else []
    results: list[dict[str, str]] = []
    lines = ["search results"]
    for item in raw_results[:5]:
        if not isinstance(item, dict):
            continue
        entry = {
            "title": _clean_text(item.get("title")),
            "url": _clean_text(item.get("url")),
            "snippet": _preview(item.get("snippet") or item.get("summary") or item.get("text"), 220),
        }
        entry = _drop_empty(entry)
        if entry:
            results.append(entry)
            lines.append(f"- {entry.get('title', '')} {entry.get('url', '')}: {entry.get('snippet', '')}".strip())
    return {"results": results}, lines, ["search_raw_payload_omitted"]


def _file_snapshot_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    path = _clean_text(data.get("path") or data.get("file"))
    sha256 = _clean_text(data.get("sha256") or data.get("hash"))
    excerpt = _tail_text(data.get("excerpt") or data.get("content"), 16)
    key_fields = _drop_empty({"path": path, "sha256": sha256})
    lines = ["file snapshot", f"path: {path}", f"sha256: {sha256}"]
    if excerpt:
        lines.append("excerpt_tail:")
        lines.append(excerpt)
    return key_fields, lines, ["file_content_omitted"]


def _generic_summary(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    key_fields = _small_key_fields(data)
    return key_fields, ["tool result", _json_preview(key_fields)], ["raw_payload_omitted"]


def _small_key_fields(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        clean_key = _clean_text(key)
        if clean_key in LONG_FIELD_NAMES or SECRET_KEY_PATTERN.search(clean_key):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[clean_key] = _preview(value, MAX_FIELD_CHARS)
        elif isinstance(value, list):
            result[clean_key] = [_preview(_strip_secret_fields(item), 120) for item in value[:10]]
        elif isinstance(value, dict):
            compact = {
                _clean_text(inner_key): _preview(_strip_secret_fields(inner_value), 120)
                for inner_key, inner_value in list(value.items())[:8]
                if _clean_text(inner_key) not in LONG_FIELD_NAMES and not SECRET_KEY_PATTERN.search(_clean_text(inner_key))
            }
            if compact:
                result[clean_key] = compact
    return result


def _element_summaries(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    elements: list[str] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        selector = _clean_text(item.get("selector"))
        tag = _clean_text(item.get("tag"))
        text = _preview(item.get("text") or item.get("label") or item.get("name"), 80)
        parts = [part for part in [selector, tag, text] if part]
        if parts:
            elements.append(" | ".join(parts))
    return elements


def _omitted_fields(value: Any, *, prefix: str = "") -> list[str]:
    omitted: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            clean_key = _clean_text(key)
            path = f"{prefix}.{clean_key}" if prefix else clean_key
            if SECRET_KEY_PATTERN.search(clean_key):
                omitted.append(path)
                continue
            if clean_key in LONG_FIELD_NAMES:
                omitted.append(path)
                continue
            if isinstance(item, (dict, list)):
                omitted.extend(_omitted_fields(item, prefix=path))
    elif isinstance(value, list):
        for item in value:
            omitted.extend(_omitted_fields(item, prefix=prefix))
    return omitted


def _looks_like_file_snapshot(tool: str, action: str, data: dict[str, Any]) -> bool:
    text = f"{tool}.{action}".lower()
    return "file" in text or "snapshot" in text or "path" in data and ("content" in data or "sha256" in data)


def _contains_key(value: Any, key: str) -> bool:
    if not isinstance(value, dict):
        return False
    if key in value:
        return True
    return any(_contains_key(item, key) for item in value.values() if isinstance(item, dict))


def _json_preview(value: Any) -> str:
    try:
        return _clip(json.dumps(value, ensure_ascii=False, sort_keys=True), MAX_FIELD_CHARS)
    except TypeError:
        return _preview(value, MAX_FIELD_CHARS)


def _tail_text(value: Any, lines: int) -> str:
    text = _redact(_clean_text(value))
    if not text:
        return ""
    split = text.splitlines()
    return "\n".join(_collapse_repeated_lines(split[-lines:]))


def _collapse_repeated_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        repeat_count = 1
        while index + repeat_count < len(lines) and lines[index + repeat_count] == line:
            repeat_count += 1
        result.append(line)
        if repeat_count > 2:
            result.append(f"...[repeated {repeat_count - 1} lines omitted]")
        elif repeat_count == 2:
            result.append(line)
        index += repeat_count
    return result


def _preview(value: Any, limit: int) -> str:
    return _clip(_redact(_clean_text(value).replace("\n", " ")), limit)


def _clip(text: str, limit: int) -> str:
    clean = sanitize_text(str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 32)] + f"...[truncated {len(clean) - limit} chars]"


def _redact(value: str) -> str:
    return redact_sensitive_text(sanitize_text(str(value or "")))


def _contains_redacted_secret(value: Any) -> bool:
    if _contains_secret_key(value):
        return True
    text = _clean_text(value)
    return _redact(text) != sanitize_text(text)


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key or "")) and not _is_empty_value(item):
                return True
            if _contains_secret_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _strip_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = _clean_text(key)
            if SECRET_KEY_PATTERN.search(clean_key):
                result[clean_key] = "[redacted]"
            else:
                result[clean_key] = _strip_secret_fields(item)
        return result
    if isinstance(value, list):
        return [_strip_secret_fields(item) for item in value]
    return value


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)
    return sanitize_text(str(value))


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not _is_empty_value(item)}


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result
