from __future__ import annotations

from datetime import datetime, timedelta

from runtime.memory.flywheel import FlywheelStore
from runtime.memory.maintenance import (
    MemoryMaintenancePolicy,
    decay_stale_entries,
    expire_stale_entries,
    record_memory_usage,
)


def _entry(kind: str, *, confidence: float = 0.8, days_old: int = 0, status: str = "active") -> dict:
    timestamp = (datetime(2026, 1, 1, 12, 0, 0) - timedelta(days=days_old)).isoformat(timespec="seconds")
    return {
        "kind": kind,
        "summary": f"{kind} summary for runtime/memory/resolver.py",
        "tags": ["memory"],
        "source": "distiller",
        "metadata": {
            "fingerprint": f"{kind}:resolver",
            "confidence": confidence,
            "scope": ["runtime/memory/resolver.py"],
            "status": status,
            "created_at": timestamp,
            "last_seen": timestamp,
            "decision_reasons": ["audit_passed"],
            "action": "module_role:runtime/memory/resolver.py",
        },
    }


def test_record_memory_usage_updates_last_used_and_usage_count(tmp_path):
    store = FlywheelStore(tmp_path)
    first = store.upsert_distilled_entry(_entry("tool_hint"))
    now = datetime(2026, 1, 2, 10, 30, 0)

    updated = record_memory_usage(store, [first["id"]], source="planner", now=now)
    loaded = store.load_entries()

    assert updated == [first["id"]]
    assert loaded[0]["metadata"]["usage_count"] == 1
    assert loaded[0]["metadata"]["last_used_at"] == now.isoformat(timespec="seconds")
    assert "used_by_planner" in loaded[0]["metadata"]["maintenance_reasons"]


def test_decay_stale_entries_lowers_confidence_after_unused_window(tmp_path):
    store = FlywheelStore(tmp_path)
    first = store.upsert_distilled_entry(_entry("project_fact", confidence=0.8, days_old=45))
    now = datetime(2026, 1, 1, 12, 0, 0)

    changed = decay_stale_entries(
        store,
        policy=MemoryMaintenancePolicy(decay_after_days=30, decay_amount=0.05),
        now=now,
    )
    loaded = store.load_entries()

    assert changed == [first["id"]]
    assert loaded[0]["metadata"]["confidence"] == 0.75
    assert loaded[0]["metadata"]["last_decayed_at"] == now.isoformat(timespec="seconds")
    assert "stale_confidence_decay" in loaded[0]["metadata"]["maintenance_reasons"]


def test_expire_stale_entries_marks_low_confidence_or_old_entries_expired(tmp_path):
    store = FlywheelStore(tmp_path)
    old = store.upsert_distilled_entry(_entry("path_mapping", confidence=0.7, days_old=120))
    low = store.upsert_distilled_entry(_entry("tool_hint", confidence=0.45, days_old=5))
    now = datetime(2026, 1, 1, 12, 0, 0)

    expired = expire_stale_entries(
        store,
        policy=MemoryMaintenancePolicy(expire_after_days=90, min_confidence=0.5),
        now=now,
    )
    loaded = store.load_entries()

    assert set(expired) == {old["id"], low["id"]}
    assert [entry["metadata"]["status"] for entry in loaded] == ["expired", "expired"]
    assert all("expired_by_memory_maintenance" in entry["metadata"]["maintenance_reasons"] for entry in loaded)


def test_memory_maintenance_skips_blocked_and_protected_kinds(tmp_path):
    store = FlywheelStore(tmp_path)
    pipeline = store.upsert_distilled_entry(_entry("pipeline_summary", confidence=0.2, days_old=365))
    conflicted = store.upsert_distilled_entry(_entry("tool_hint", confidence=0.9, days_old=365, status="conflicted"))
    now = datetime(2026, 1, 1, 12, 0, 0)

    changed = decay_stale_entries(store, now=now)
    expired = expire_stale_entries(store, now=now)
    loaded = store.load_entries()

    assert changed == []
    assert expired == []
    assert [entry["id"] for entry in loaded] == [pipeline["id"], conflicted["id"]]
    assert loaded[0]["metadata"]["status"] == "active"
    assert loaded[1]["metadata"]["status"] == "conflicted"
