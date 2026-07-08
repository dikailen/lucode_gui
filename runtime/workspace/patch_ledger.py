from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from planning.planner_schema import PlannedTask
from runtime.consistency.commit_guard import expected_sha256_map_for_paths


class PatchProposalLedger:
    """Append-only ledger for planned file edits and task outcomes."""

    def __init__(self, project_root: Path, ledger_dir: Path | None = None):
        self.project_root = project_root.resolve()
        self.ledger_dir = (ledger_dir or self.project_root / ".agent_quarantine").resolve()
        self.ledger_path = self.ledger_dir / "patch_proposals.jsonl"

    def record_proposal(self, task: PlannedTask, note: str = "") -> dict[str, Any]:
        entry = {
            "timestamp": _now(),
            "kind": "patch_proposal",
            "task_id": task.id,
            "title": task.title,
            "skill_id": task.skill_id,
            "model": task.model,
            "mcp": list(task.mcp),
            "read_set": list(task.read_set),
            "write_intent": list(task.write_intent),
            "expected_sha256": self._expected_hashes(task.write_intent),
            "status": "proposed",
            "note": _preview(note),
        }
        self._append(entry)
        return entry

    def record_task_status(self, task_id: str, status: str, output: str = "") -> dict[str, Any]:
        entry = {
            "timestamp": _now(),
            "kind": "task_status",
            "task_id": task_id,
            "status": status,
            "output_preview": _preview(output),
        }
        self._append(entry)
        return entry

    def _expected_hashes(self, paths: list[str]) -> dict[str, str]:
        return expected_sha256_map_for_paths(self.project_root, paths)

    def _append(self, entry: dict[str, Any]) -> None:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _preview(value: str, limit: int = 800) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"
