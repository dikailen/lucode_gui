from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from runtime.memory.flywheel import FlywheelStore


BLOCKED_STATUSES = {"conflicted", "expired", "rejected"}


@dataclass(frozen=True)
class MemoryMaintenancePolicy:
    expire_after_days: int = 90
    decay_after_days: int = 30
    decay_amount: float = 0.05
    min_confidence: float = 0.50
    protected_kinds: tuple[str, ...] = field(default_factory=lambda: ("pipeline_summary", "failure_case"))


def record_memory_usage(
    store: FlywheelStore,
    entry_ids: Iterable[str],
    *,
    source: str = "resolver",
    now: datetime | None = None,
) -> list[str]:
    timestamp = _timestamp(now)
    ids = _clean_id_set(entry_ids)
    if not ids:
        return []
    entries = store.load_entries()
    changed: list[str] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "").strip()
        if entry_id not in ids:
            continue
        metadata = _metadata(entry)
        if _status(metadata) in BLOCKED_STATUSES:
            continue
        metadata["usage_count"] = int(metadata.get("usage_count") or 0) + 1
        metadata["last_used_at"] = timestamp
        _append_reason(metadata, f"used_by_{_clean_reason_token(source)}")
        entry["metadata"] = FlywheelStore._redact_metadata(metadata)
        changed.append(entry_id)
    if changed:
        store._write_entries(entries)
    return changed


def decay_stale_entries(
    store: FlywheelStore,
    *,
    policy: MemoryMaintenancePolicy | None = None,
    now: datetime | None = None,
) -> list[str]:
    active_policy = policy or MemoryMaintenancePolicy()
    current = now or datetime.now()
    entries = store.load_entries()
    changed: list[str] = []
    for entry in entries:
        metadata = _metadata(entry)
        if _skip_maintenance(entry, metadata, active_policy):
            continue
        if _recently_used_or_seen(metadata, active_policy.decay_after_days, current):
            continue
        confidence = _float_value(metadata.get("confidence"), default=0.0)
        new_confidence = round(max(0.0, confidence - active_policy.decay_amount), 2)
        if new_confidence == confidence:
            continue
        metadata["confidence"] = new_confidence
        metadata["last_decayed_at"] = _timestamp(current)
        _append_reason(metadata, "stale_confidence_decay")
        entry["metadata"] = FlywheelStore._redact_metadata(metadata)
        entry_id = str(entry.get("id") or "").strip()
        if entry_id:
            changed.append(entry_id)
    if changed:
        store._write_entries(entries)
    return changed


def expire_stale_entries(
    store: FlywheelStore,
    *,
    policy: MemoryMaintenancePolicy | None = None,
    now: datetime | None = None,
) -> list[str]:
    active_policy = policy or MemoryMaintenancePolicy()
    current = now or datetime.now()
    entries = store.load_entries()
    changed: list[str] = []
    for entry in entries:
        metadata = _metadata(entry)
        if _skip_maintenance(entry, metadata, active_policy):
            continue
        confidence = _float_value(metadata.get("confidence"), default=0.0)
        old_enough = not _recently_used_or_seen(metadata, active_policy.expire_after_days, current)
        too_weak = confidence < active_policy.min_confidence
        if not old_enough and not too_weak:
            continue
        metadata["status"] = "expired"
        _append_reason(metadata, "expired_by_memory_maintenance")
        if old_enough:
            _append_reason(metadata, "expired_by_ttl")
        if too_weak:
            _append_reason(metadata, "expired_by_low_confidence")
        entry["metadata"] = FlywheelStore._redact_metadata(metadata)
        entry_id = str(entry.get("id") or "").strip()
        if entry_id:
            changed.append(entry_id)
    if changed:
        store._write_entries(entries)
    return changed


def _skip_maintenance(entry: dict, metadata: dict, policy: MemoryMaintenancePolicy) -> bool:
    kind = str(entry.get("kind") or "").strip()
    if kind in set(policy.protected_kinds):
        return True
    return _status(metadata) in BLOCKED_STATUSES


def _recently_used_or_seen(metadata: dict, days: int, now: datetime) -> bool:
    reference = _parse_time(metadata.get("last_used_at")) or _parse_time(metadata.get("last_seen")) or _parse_time(metadata.get("created_at"))
    if reference is None:
        return False
    return reference >= now - timedelta(days=max(0, int(days or 0)))


def _metadata(entry: dict) -> dict:
    raw = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return dict(raw)


def _status(metadata: dict) -> str:
    return str(metadata.get("status") or "active").strip().lower()


def _clean_id_set(values: Iterable[str]) -> set[str]:
    return {str(value or "").strip() for value in values if str(value or "").strip()}


def _append_reason(metadata: dict, reason: str) -> None:
    values = [str(item) for item in list(metadata.get("maintenance_reasons") or []) if str(item).strip()]
    if reason and reason not in values:
        values.append(reason)
    metadata["maintenance_reasons"] = values


def _clean_reason_token(value: str) -> str:
    text = "".join(char.lower() if char.isalnum() else "_" for char in str(value or "resolver"))
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_") or "resolver"


def _timestamp(value: datetime | None) -> str:
    return (value or datetime.now()).isoformat(timespec="seconds")


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _float_value(value, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
