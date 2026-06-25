from __future__ import annotations

import json

from runtime.history.store import HistoryFacade, HistoryStore
from runtime.memory.flywheel import FlywheelStore


def test_flywheel_store_roundtrips_entries_and_redacts_sensitive_text(tmp_path):
    store = FlywheelStore(tmp_path)

    entry = store.append_entry(
        kind="tool_hint",
        summary="Use token=sk-secret123456 when calling the fake service",
        tags=["gui", "pytest", "gui"],
        source="unit_test",
        metadata={
            "confidence": 0.9,
            "scope": ["tests/test_gui.py"],
            "api_key": "sk-secret123456",
            "nested": {"token": "sk-secret123456"},
        },
    )

    loaded = store.load_entries()

    assert len(loaded) == 1
    assert loaded[0]["id"] == entry["id"]
    assert loaded[0]["tags"] == ["gui", "pytest"]
    assert "sk-secret123456" not in json.dumps(loaded[0], ensure_ascii=False)
    assert "[REDACTED_SECRET]" in json.dumps(loaded[0], ensure_ascii=False)


def test_history_store_writes_redacted_run_context_summary_sidecar(tmp_path):
    store = HistoryStore(tmp_path)

    store.append_event(
        "session-1",
        {
            "type": "message",
            "role": "assistant",
            "content": "done",
            "metadata": {"run_context_summary": "Read auth.py with api_key=sk-secret123456"},
        },
    )

    summary = HistoryFacade(tmp_path, history_store=store).load_context_summary("session-1")

    assert "Read auth.py" in summary
    assert "sk-secret123456" not in summary
    assert "api_key=[redacted]" in summary


def test_flywheel_store_upserts_distilled_entry_by_fingerprint(tmp_path):
    store = FlywheelStore(tmp_path)
    entry = {
        "kind": "verification_command",
        "summary": "Run GUI theme pytest",
        "tags": ["verification", "gui"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "verification_command:gui-theme",
            "confidence": 0.85,
            "scope": ["lucode/gui/theme.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_passed"],
            "action": "python -m pytest tests/test_gui_minimal_theme.py -q",
        },
    }

    first = store.upsert_distilled_entry(entry)
    second = store.upsert_distilled_entry(entry)
    loaded = store.load_entries()

    assert first["id"] == second["id"]
    assert len(loaded) == 1
    assert loaded[0]["metadata"]["evidence_count"] == 2
    assert loaded[0]["metadata"]["fingerprint"] == "verification_command:gui-theme"
    assert loaded[0]["metadata"]["status"] == "active"


def test_flywheel_store_increases_confidence_for_repeated_project_fact_evidence(tmp_path):
    store = FlywheelStore(tmp_path)
    entry = {
        "kind": "project_fact",
        "summary": "Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
        "tags": ["memory", "resolver"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "project_fact:memory-resolver",
            "confidence": 0.65,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["worker_report_role_statement"],
            "action": "module_role:runtime/memory/resolver.py",
        },
    }

    first = store.upsert_distilled_entry(entry)
    second = store.upsert_distilled_entry(entry)
    third = store.upsert_distilled_entry(entry)
    loaded = store.load_entries()

    assert first["metadata"]["confidence"] == 0.65
    assert second["metadata"]["confidence"] == 0.70
    assert third["metadata"]["confidence"] == 0.75
    assert len(loaded) == 1
    assert loaded[0]["metadata"]["evidence_count"] == 3
    assert loaded[0]["metadata"]["confidence"] == 0.75


def test_flywheel_store_does_not_boost_conflicted_repeated_evidence(tmp_path):
    store = FlywheelStore(tmp_path)
    success = {
        "kind": "project_fact",
        "summary": "Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
        "tags": ["memory", "resolver"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "project_fact:memory-resolver",
            "confidence": 0.65,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["worker_report_role_statement"],
            "action": "module_role:runtime/memory/resolver.py",
        },
    }
    failure = {
        "kind": "failure_lesson",
        "summary": "Memory resolver project fact caused a failed repair.",
        "tags": ["failure", "memory"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "project_fact:memory-resolver",
            "confidence": 0.60,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_failed"],
            "action": "module_role:runtime/memory/resolver.py",
        },
    }

    store.upsert_distilled_entry(success)
    updated = store.upsert_distilled_entry(failure)

    assert updated["metadata"]["status"] == "conflicted"
    assert updated["metadata"]["confidence"] == 0.40
    assert updated["metadata"]["evidence_count"] == 2


def test_flywheel_store_marks_fingerprint_conflict(tmp_path):
    store = FlywheelStore(tmp_path)
    success = {
        "kind": "verification_command",
        "summary": "Run GUI theme pytest",
        "tags": ["verification", "gui"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "verification_command:gui-theme",
            "confidence": 0.85,
            "scope": ["lucode/gui/theme.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_passed"],
            "action": "python -m pytest tests/test_gui_minimal_theme.py -q",
        },
    }
    conflict = {
        "kind": "failure_lesson",
        "summary": "GUI theme pytest later failed after rollback",
        "tags": ["failure", "gui"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "verification_command:gui-theme",
            "confidence": 0.6,
            "scope": ["lucode/gui/theme.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_failed", "rollback_happened"],
            "action": "python -m pytest tests/test_gui_minimal_theme.py -q",
        },
    }

    store.upsert_distilled_entry(success)
    updated = store.upsert_distilled_entry(conflict)
    loaded = store.load_entries()

    assert len(loaded) == 1
    assert updated["kind"] == "verification_command"
    assert loaded[0]["metadata"]["status"] == "conflicted"
    assert loaded[0]["metadata"]["confidence"] < 0.85
    assert "conflicting_evidence" in loaded[0]["metadata"]["decision_reasons"]


def test_flywheel_store_marks_scope_action_conflict_across_fingerprints(tmp_path):
    store = FlywheelStore(tmp_path)
    success = {
        "kind": "verification_command",
        "summary": "GUI theme pytest passed",
        "tags": ["verification", "gui"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "verification_command:gui-theme-pass",
            "confidence": 0.86,
            "scope": ["lucode/gui/theme.py", "tests/test_gui_minimal_theme.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_passed", "verification_returncode_0"],
            "action": "python -m pytest tests/test_gui_minimal_theme.py -q",
        },
    }
    failure = {
        "kind": "failure_lesson",
        "summary": "GUI theme pytest failed after rollback",
        "tags": ["failure", "gui"],
        "source": "distiller",
        "metadata": {
            "fingerprint": "failure_lesson:gui-theme-rollback",
            "confidence": 0.64,
            "scope": ["lucode/gui/theme.py", "tests/test_gui_minimal_theme.py"],
            "status": "active",
            "evidence_count": 1,
            "decision_reasons": ["audit_failed", "rollback_happened"],
            "action": "python -m pytest tests/test_gui_minimal_theme.py -q",
        },
    }

    store.upsert_distilled_entry(success)
    updated = store.upsert_distilled_entry(failure)
    loaded = store.load_entries()

    assert len(loaded) == 2
    assert updated["metadata"]["status"] == "conflicted"
    assert all(entry["metadata"]["status"] == "conflicted" for entry in loaded)
    assert all("conflicting_evidence" in entry["metadata"]["decision_reasons"] for entry in loaded)
    assert all("scope_action_overlap" in entry["metadata"]["decision_reasons"] for entry in loaded)


def test_flywheel_store_can_mark_entry_expired_with_reason(tmp_path):
    store = FlywheelStore(tmp_path)
    entry = store.upsert_distilled_entry(
        {
            "kind": "tool_hint",
            "summary": "Use old GUI pytest command",
            "tags": ["gui", "pytest"],
            "source": "distiller",
            "metadata": {
                "fingerprint": "tool_hint:old-gui-command",
                "confidence": 0.8,
                "scope": ["tests/test_gui_old.py"],
                "status": "active",
                "decision_reasons": ["verification_returncode_0"],
                "action": "python -m pytest tests/test_gui_old.py -q",
            },
        }
    )

    updated = store.mark_entry_status(entry["id"], "expired", reason="superseded_by_new_test_path")
    loaded = store.load_entries()

    assert updated["metadata"]["status"] == "expired"
    assert loaded[0]["metadata"]["status"] == "expired"
    assert "superseded_by_new_test_path" in loaded[0]["metadata"]["decision_reasons"]
