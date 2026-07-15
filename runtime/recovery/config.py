from __future__ import annotations

import os
from dataclasses import dataclass


RUN_RECOVERY_MODES = {"off", "observe", "reconnect", "resume_safe"}
DEFAULT_RUN_RECOVERY_MODE = "off"


def normalize_run_recovery_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in RUN_RECOVERY_MODES else DEFAULT_RUN_RECOVERY_MODE


def run_recovery_mode_from_env(value: str | None = None) -> str:
    raw = os.environ.get("LUCODE_RUN_RECOVERY") if value is None else value
    return normalize_run_recovery_mode(raw)


@dataclass(frozen=True)
class RunRecoverySettings:
    mode: str = DEFAULT_RUN_RECOVERY_MODE

    @property
    def records_journal(self) -> bool:
        return self.mode in {"observe", "reconnect", "resume_safe"}

    @property
    def allows_event_reconnect(self) -> bool:
        return self.mode in {"reconnect", "resume_safe"}

    @property
    def allows_safe_resume(self) -> bool:
        return self.mode == "resume_safe"


def run_recovery_settings_from_env(value: str | None = None) -> RunRecoverySettings:
    return RunRecoverySettings(mode=run_recovery_mode_from_env(value))
