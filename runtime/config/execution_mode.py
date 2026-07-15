from __future__ import annotations

from dataclasses import dataclass


CANONICAL_EXECUTION_MODES = {"auto"}
EXECUTION_MODES = CANONICAL_EXECUTION_MODES
DEFAULT_EXECUTION_MODE = "auto"


@dataclass(frozen=True)
class ExecutionModePolicy:
    canonical_mode: str = "auto"
    legacy_mode: str = ""
    fast_single_agent: bool = False
    parallel_enabled: bool = True
    supervisor_enabled: bool = True


def normalize_execution_mode(value: str) -> str:
    del value
    return DEFAULT_EXECUTION_MODE


def execution_mode_policy(value: str) -> ExecutionModePolicy:
    del value
    return ExecutionModePolicy()


def canonical_execution_mode(value: str) -> str:
    del value
    return DEFAULT_EXECUTION_MODE


def runtime_route_for_input(user_input: str, execution_mode: str) -> str:
    del user_input, execution_mode
    return "dynamic"


def execution_mode_label_zh(mode: str) -> str:
    del mode
    return "\u81ea\u52a8\u6267\u884c"
