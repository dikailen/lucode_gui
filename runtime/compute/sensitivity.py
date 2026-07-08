from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from runtime.compute.context_sources import ContextSourceLabel, strongest_context_sensitivity


SENSITIVITY_LEVELS = {"public", "project_private", "secret", "local_only"}
LOCAL_ONLY_MCPS = {"desktop_browser", "command_runner", "terminal", "terminal_shell", "shell"}
PROJECT_PRIVATE_MCPS = {"project_filesystem_readonly", "code_locator", "workspace_edit", "safe_backup", "git_tools"}


@dataclass(frozen=True)
class SensitivityDecision:
    sensitivity: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sensitivity": self.sensitivity,
            "reasons": list(self.reasons),
        }


def classify_task_sensitivity(
    task,
    context_text: str = "",
    *,
    context_labels: Iterable[ContextSourceLabel] | None = None,
) -> SensitivityDecision:
    text = _task_text(task, context_text).lower()
    mcps = {str(item or "").strip() for item in list(getattr(task, "mcp", []) or []) if str(item or "").strip()}
    read_set = _string_list(getattr(task, "read_set", []) or [])
    write_intent = _string_list(getattr(task, "write_intent", []) or [])
    reasons: list[str] = []
    label_sensitivity, label_reasons = strongest_context_sensitivity(context_labels)
    reasons.extend(label_reasons)

    if label_sensitivity == "local_only":
        return SensitivityDecision("local_only", _dedupe(reasons))
    if label_sensitivity == "secret":
        return SensitivityDecision("secret", _dedupe(reasons))

    if _contains_secret(text) or any(_contains_secret(item.lower()) for item in [*read_set, *write_intent]):
        reasons.append("secret_marker")
        return SensitivityDecision("secret", _dedupe(reasons))

    if mcps & LOCAL_ONLY_MCPS:
        reasons.append("local_runtime_surface")
        return SensitivityDecision("local_only", _dedupe(reasons))
    if _mentions_browser_dom_or_terminal(text):
        reasons.append("local_runtime_state")
        return SensitivityDecision("local_only", _dedupe(reasons))

    if read_set or write_intent:
        reasons.append("declared_project_resource")
        return SensitivityDecision("project_private", _dedupe(reasons))
    if mcps & PROJECT_PRIVATE_MCPS:
        reasons.append("project_mcp")
        return SensitivityDecision("project_private", _dedupe(reasons))
    if _looks_like_project_path(text):
        reasons.append("path_like_text")
        return SensitivityDecision("project_private", _dedupe(reasons))
    if _mentions_private_project_context(text):
        reasons.append("project_context")
        return SensitivityDecision("project_private", _dedupe(reasons))
    if label_sensitivity == "project_private":
        return SensitivityDecision("project_private", _dedupe(reasons))

    return SensitivityDecision("public", ["no_private_markers"])


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


def _contains_secret(text: str) -> bool:
    if re.search(r"\b(api[_-]?key|secret|password|passwd|credential|private[_-]?key|auth\.json)\b", text):
        return True
    if re.search(r"\b(token|bearer)\s*[:=]\s*\S+", text):
        return True
    if re.search(r"\bsk-[a-z0-9_-]{6,}\b", text, flags=re.IGNORECASE):
        return True
    return ".env" in text


def _mentions_browser_dom_or_terminal(text: str) -> bool:
    return bool(
        re.search(r"\b(browser\s+dom|logged-in|logged in|terminal output|shell output|command output)\b", text)
        or "dom:" in text
    )


def _looks_like_project_path(text: str) -> bool:
    if re.search(r"[a-z]:[\\/][^\s]+", text, flags=re.IGNORECASE):
        return True
    if re.search(r"(^|\s)(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+", text):
        return True
    return bool(re.search(r"\b[\w.-]+\.(py|ts|tsx|js|jsx|json|toml|yaml|yml|md|env|txt)\b", text))


def _mentions_private_project_context(text: str) -> bool:
    markers = {
        "project file",
        "workspace file",
        "source code",
        "codebase",
        "repository",
        "repo",
        "runtime/",
        "desktop/src",
        "src/",
    }
    return any(marker in text for marker in markers)


def _string_list(values) -> list[str]:
    result: list[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text:
            result.append(text)
    return result


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result
