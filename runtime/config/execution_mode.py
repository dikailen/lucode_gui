from __future__ import annotations

import re
from dataclasses import dataclass


CANONICAL_EXECUTION_MODES = {"auto"}
LEGACY_EXECUTION_MODES = {"solo", "serial", "full"}
EXECUTION_MODES = CANONICAL_EXECUTION_MODES | LEGACY_EXECUTION_MODES
DEFAULT_EXECUTION_MODE = "auto"


@dataclass(frozen=True)
class ExecutionModePolicy:
    canonical_mode: str = "auto"
    legacy_mode: str = ""
    fast_single_agent: bool = False
    parallel_enabled: bool = True
    supervisor_enabled: bool = True


def normalize_execution_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in EXECUTION_MODES:
        return mode
    return DEFAULT_EXECUTION_MODE


def execution_mode_policy(value: str) -> ExecutionModePolicy:
    mode = normalize_execution_mode(value)
    if mode == "solo":
        return ExecutionModePolicy(
            canonical_mode="auto",
            legacy_mode="solo",
            fast_single_agent=True,
            parallel_enabled=False,
            supervisor_enabled=False,
        )
    if mode == "serial":
        return ExecutionModePolicy(
            canonical_mode="auto",
            legacy_mode="serial",
            fast_single_agent=False,
            parallel_enabled=False,
            supervisor_enabled=True,
        )
    if mode == "full":
        return ExecutionModePolicy(
            canonical_mode="auto",
            legacy_mode="full",
            fast_single_agent=False,
            parallel_enabled=True,
            supervisor_enabled=True,
        )
    return ExecutionModePolicy(
        canonical_mode="auto",
        legacy_mode="",
        fast_single_agent=False,
        parallel_enabled=True,
        supervisor_enabled=True,
    )


def canonical_execution_mode(value: str) -> str:
    return execution_mode_policy(value).canonical_mode


def effective_runtime_mode(value: str) -> str:
    policy = execution_mode_policy(value)
    return policy.legacy_mode or "full"


def explicit_execution_mode_for_input(user_input: str) -> str:
    """Return a per-turn compatibility mode only when the user explicitly asks for one."""

    text = str(user_input or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    mode_word = "\u6a21\u5f0f"
    use_words = "\u7528|\u4f7f\u7528|\u5207\u5230|\u5207\u6362\u5230|\u4ee5|\u6309"
    for mode in ("full", "serial", "solo", "auto"):
        if re.search(rf"(^|\s|/){mode}\s*({mode_word}|mode)(\b|$)", normalized):
            return mode
        if re.search(rf"({use_words}|run with|use)\s*{mode}\b", normalized):
            return mode
        if re.search(rf"\b{mode}\s+(mode|{mode_word})\b", normalized):
            return mode
    return ""


def should_use_solo_mode(user_input: str, execution_mode: str) -> bool:
    del user_input, execution_mode
    return False


def runtime_route_for_input(user_input: str, execution_mode: str) -> str:
    del user_input, execution_mode
    return "dynamic"


def execution_mode_label_zh(mode: str) -> str:
    labels = {
        "auto": "\u81ea\u52a8\u6267\u884c",
        "solo": "\u81ea\u52a8\u6267\u884c\uff08solo \u517c\u5bb9\uff09",
        "serial": "\u81ea\u52a8\u6267\u884c\uff08serial \u517c\u5bb9\uff09",
        "full": "\u81ea\u52a8\u6267\u884c\uff08full \u517c\u5bb9\uff09",
    }
    return labels.get(normalize_execution_mode(mode), labels[DEFAULT_EXECUTION_MODE])
