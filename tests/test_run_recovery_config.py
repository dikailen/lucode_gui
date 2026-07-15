from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from runtime.recovery.config import (
    DEFAULT_RUN_RECOVERY_MODE,
    RUN_RECOVERY_MODES,
    normalize_run_recovery_mode,
    run_recovery_mode_from_env,
    run_recovery_settings_from_env,
)


def test_run_recovery_defaults_to_off_without_environment_override(monkeypatch):
    monkeypatch.delenv("LUCODE_RUN_RECOVERY", raising=False)

    assert DEFAULT_RUN_RECOVERY_MODE == "off"
    assert run_recovery_mode_from_env() == "off"
    assert run_recovery_settings_from_env().mode == "off"
    assert run_recovery_settings_from_env().records_journal is False
    assert run_recovery_settings_from_env().allows_event_reconnect is False
    assert run_recovery_settings_from_env().allows_safe_resume is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("off", "off"),
        (" OBSERVE ", "observe"),
        ("reconnect", "reconnect"),
        ("resume_safe", "resume_safe"),
        ("resume-all", "off"),
        ("on", "off"),
        ("", "off"),
        (None, "off"),
    ],
)
def test_run_recovery_mode_accepts_only_the_declared_values(raw, expected):
    assert normalize_run_recovery_mode(raw) == expected


def test_run_recovery_settings_promote_capabilities_without_enabling_execution_replay(monkeypatch):
    assert RUN_RECOVERY_MODES == {"off", "observe", "reconnect", "resume_safe"}

    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "observe")
    observe = run_recovery_settings_from_env()
    assert observe.records_journal is True
    assert observe.allows_event_reconnect is False
    assert observe.allows_safe_resume is False

    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "reconnect")
    reconnect = run_recovery_settings_from_env()
    assert reconnect.records_journal is True
    assert reconnect.allows_event_reconnect is True
    assert reconnect.allows_safe_resume is False

    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    resume_safe = run_recovery_settings_from_env()
    assert resume_safe.records_journal is True
    assert resume_safe.allows_event_reconnect is True
    assert resume_safe.allows_safe_resume is True


def test_importing_recovery_configuration_does_not_import_the_journal_layer():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import runtime.recovery.config; "
            "assert 'runtime.recovery.journal' not in sys.modules; "
            "assert 'runtime.storage.sqlite_store' not in sys.modules",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
