from __future__ import annotations

import os
import sys
from pathlib import Path

from runtime.recovery.journal import RunJournal


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 64
    workspace_root = Path(argv[1]).resolve()
    fault_point = str(argv[2]).strip()
    journal = RunJournal(workspace_root)
    journal.create_run(
        run_id="run-crash",
        session_id="session-crash",
        status="running",
    )
    if fault_point == "after_event":
        journal.append_event(
            run_id="run-crash",
            session_id="session-crash",
            event_type="task.progress",
            payload={"step": "durable before crash"},
        )
    elif fault_point == "after_checkpoint":
        journal.write_checkpoint(
            run_id="run-crash",
            kind="plan.accepted",
            state={"tasks": [{"id": "task-1", "status": "pending"}]},
            compatibility={"schema": "test"},
        )
    elif fault_point == "after_dispatched":
        journal.prepare_tool_invocation(
            invocation_id="invocation-crash",
            run_id="run-crash",
            task_id="task-1",
            attempt=1,
            tool_name="workspace_edit.write_file",
            arguments_hash="test-hash",
            side_effect_class="non_idempotent",
        )
        journal.mark_tool_invocation_dispatched("invocation-crash")
    else:
        return 64
    os._exit(17)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
