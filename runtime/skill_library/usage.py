from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_USAGE_SCHEMA_VERSION = "skill_usage.v1"
DEFAULT_USAGE_RELATIVE_PATH = Path(".lucode") / "skills" / "usage.jsonl"


@dataclass(frozen=True)
class SkillUsageRecord:
    schema_version: str
    timestamp: str
    skill_id: str
    query: str
    task_id: str
    result: str
    misfire: bool = False
    files_touched: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    reason: str = ""


class SkillUsageTracker:
    """Append-only Skill usage feedback writer.

    This is intentionally observe-only: it records feedback for later indexing
    or tuning, but it does not mutate Skill metadata or affect current ranking.
    """

    def __init__(self, workspace_root: str | Path, usage_path: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root)
        self.usage_path = Path(usage_path) if usage_path else self.workspace_root / DEFAULT_USAGE_RELATIVE_PATH

    def record(
        self,
        *,
        skill_id: str,
        query: str,
        task_id: str = "",
        result: str,
        misfire: bool = False,
        files_touched: list[str] | tuple[str, ...] | None = None,
        verification: list[str] | tuple[str, ...] | str | None = None,
        reason: str = "",
    ) -> SkillUsageRecord | None:
        clean_skill_id = _clean_text(skill_id, limit=160)
        clean_result = _clean_text(result, limit=80)
        if not clean_skill_id or not clean_result:
            return None

        record = SkillUsageRecord(
            schema_version=SKILL_USAGE_SCHEMA_VERSION,
            timestamp=_utc_timestamp(),
            skill_id=clean_skill_id,
            query=_clean_text(query, limit=2000),
            task_id=_clean_text(task_id, limit=160),
            result=clean_result,
            misfire=bool(misfire),
            files_touched=_clean_list(files_touched, limit=300),
            verification=_clean_list(verification, limit=500),
            reason=_clean_text(reason, limit=1000),
        )
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return record

    def load_records(self) -> list[dict[str, Any]]:
        return load_usage_records(self.usage_path)


def load_usage_records(path: str | Path) -> list[dict[str, Any]]:
    usage_path = Path(path)
    if usage_path.is_dir():
        direct_usage_path = usage_path / "usage.jsonl"
        usage_path = direct_usage_path if direct_usage_path.exists() else usage_path / DEFAULT_USAGE_RELATIVE_PATH
    if not usage_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def load_usage_summary(path: str | Path) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for record in load_usage_records(path):
        skill_id = _normalize_skill_id(record.get("skill_id"))
        if not skill_id:
            continue
        item = summary.setdefault(
            skill_id,
            {
                "used_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "misfire_count": 0,
                "rejected_by_planner_count": 0,
                "last_used_at": "",
                "last_rejected_at": "",
            },
        )
        result = _clean_text(record.get("result"), limit=80)
        timestamp = _clean_text(record.get("timestamp"), limit=80)
        if result == "success":
            item["used_count"] += 1
            item["success_count"] += 1
            item["last_used_at"] = _latest_timestamp(str(item.get("last_used_at") or ""), timestamp)
        elif result == "failure":
            item["used_count"] += 1
            item["failure_count"] += 1
            item["last_used_at"] = _latest_timestamp(str(item.get("last_used_at") or ""), timestamp)
        elif result == "rejected_by_planner":
            item["rejected_by_planner_count"] += 1
            item["last_rejected_at"] = _latest_timestamp(str(item.get("last_rejected_at") or ""), timestamp)
        elif result:
            item["used_count"] += 1
            item["last_used_at"] = _latest_timestamp(str(item.get("last_used_at") or ""), timestamp)
        if bool(record.get("misfire")):
            item["misfire_count"] += 1
    return summary


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)].rstrip() + " [truncated]"


def _clean_list(value: list[str] | tuple[str, ...] | str | None, *, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = [line.strip() for line in value.splitlines() if line.strip()] or [value]
    else:
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = _clean_text(item, limit=limit)
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
    return cleaned


def _normalize_skill_id(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


def _latest_timestamp(current: str, candidate: str) -> str:
    if not candidate:
        return current
    if not current:
        return candidate
    return max(current, candidate)
