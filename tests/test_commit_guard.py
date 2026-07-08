from __future__ import annotations

import hashlib

from runtime.consistency.commit_guard import (
    assert_expected_file_sha256,
    expected_sha256_map_for_paths,
    validate_expected_file_sha256,
)
from runtime.consistency.timeline import RunTimeline


def _sha256_file(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_commit_guard_accepts_matching_expected_sha256(tmp_path):
    target = tmp_path / "runtime" / "auth.py"
    target.parent.mkdir()
    target.write_text("TOKEN = 'old'\n", encoding="utf-8")

    decision = validate_expected_file_sha256(
        tmp_path,
        target,
        expected_sha256=_sha256_file(target),
        require_for_existing=True,
    )

    assert decision.allowed is True
    assert decision.status == "accepted"
    assert decision.resource_id == "runtime/auth.py"
    assert decision.current_sha256 == _sha256_file(target)


def test_commit_guard_rejects_stale_expected_sha256(tmp_path):
    target = tmp_path / "runtime" / "auth.py"
    target.parent.mkdir()
    target.write_text("TOKEN = 'new'\n", encoding="utf-8")
    current = _sha256_file(target)

    decision = validate_expected_file_sha256(
        tmp_path,
        target,
        expected_sha256="0" * 64,
        require_for_existing=True,
    )

    assert decision.allowed is False
    assert decision.status == "rejected"
    assert decision.reason == "sha256_mismatch"
    assert decision.current_sha256 == current


def test_commit_guard_requires_sha_for_existing_file_when_strict(tmp_path):
    target = tmp_path / "runtime" / "auth.py"
    target.parent.mkdir()
    target.write_text("TOKEN = 'old'\n", encoding="utf-8")

    decision = validate_expected_file_sha256(
        tmp_path,
        target,
        expected_sha256="",
        require_for_existing=True,
        strict=True,
    )

    assert decision.allowed is False
    assert decision.reason == "missing_expected_sha256"


def test_commit_guard_rejects_target_outside_project_root(tmp_path):
    outside = tmp_path.parent / "outside-auth.py"

    decision = validate_expected_file_sha256(
        tmp_path,
        outside,
        expected_sha256="",
        require_for_existing=False,
    )

    assert decision.allowed is False
    assert decision.reason == "outside_project_root"


def test_commit_guard_can_record_timeline_decision(tmp_path):
    target = tmp_path / "runtime" / "auth.py"
    target.parent.mkdir()
    target.write_text("TOKEN = 'old'\n", encoding="utf-8")
    timeline = RunTimeline.create(project_root=tmp_path, user_request="edit auth")
    decision = assert_expected_file_sha256(
        tmp_path,
        target,
        expected_sha256=_sha256_file(target),
        require_for_existing=True,
    )

    event = timeline.record_commit_guard_decision(decision, task_id="worker-a")

    assert event.event_type == "CommitGuardChecked"
    assert event.resource_refs == ("file:runtime/auth.py",)
    assert event.payload["status"] == "accepted"


def test_expected_sha256_map_for_paths_uses_existing_files_only(tmp_path):
    existing = tmp_path / "runtime" / "auth.py"
    missing = tmp_path / "runtime" / "missing.py"
    existing.parent.mkdir()
    existing.write_text("TOKEN = 'old'\n", encoding="utf-8")

    mapping = expected_sha256_map_for_paths(tmp_path, ["runtime/auth.py", "runtime/missing.py"])

    assert mapping == {"runtime/auth.py": _sha256_file(existing)}
    assert not missing.exists()


def test_workspace_edit_rejects_external_change_before_write(monkeypatch, tmp_path):
    from mcp_servers.mutation import workspace_edit_mcp

    quarantine = tmp_path / ".agent_quarantine"
    target = tmp_path / "runtime" / "auth.py"
    target.parent.mkdir()
    target.write_text("TOKEN = 'old'\n", encoding="utf-8")
    expected = _sha256_file(target)
    target.write_text("TOKEN = 'changed outside task'\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_EDIT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_EDIT_QUARANTINE_DIR", str(quarantine))
    monkeypatch.setenv("WORKSPACE_EDIT_STRICT_SHA256", "1")

    try:
        workspace_edit_mcp.write_file(
            "runtime/auth.py",
            "TOKEN = 'worker write'\n",
            "test stale guard",
            expected_sha256=expected,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("stale expected_sha256 should reject the write")

    assert "expected_sha256 mismatch" in message
    assert target.read_text(encoding="utf-8") == "TOKEN = 'changed outside task'\n"
