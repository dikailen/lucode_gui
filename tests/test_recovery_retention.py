from __future__ import annotations

from runtime.recovery.journal import RunJournal
from runtime.storage.sqlite_store import connect


def _set_timestamp(connection, table: str, column: str, value: str, *, run_id: str) -> None:
    connection.execute(f"update {table} set {column} = ? where run_id = ?", (value, run_id))


def test_retention_prunes_only_expired_terminal_event_payloads_and_preserves_recovery_records(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id="run-old",
        session_id="session-1",
        status="completed",
        created_at="2026-01-01T00:00:00.000Z",
    )
    journal.append_event(
        run_id="run-old",
        session_id="session-1",
        event_type="final.ready",
        payload={"detail": "expired terminal detail"},
    )
    checkpoint = journal.write_checkpoint(
        run_id="run-old",
        kind="final.ready",
        state={
            "accepted_evidence": {
                "mode": "enforce",
                "claims": [],
                "evidence": [{"ref_id": "evidence:keep"}],
                "blocked_claims": [],
            }
        },
        compatibility={"schema": "v1"},
    )
    journal.create_run(
        run_id="run-active",
        session_id="session-1",
        status="running",
        created_at="2026-01-01T00:00:00.000Z",
    )
    journal.append_event(
        run_id="run-active",
        session_id="session-1",
        event_type="task.progress",
        payload={"detail": "must remain"},
    )
    with connect(tmp_path) as connection:
        _set_timestamp(connection, "agent_runs", "updated_at", "2026-01-01T00:00:00.000Z", run_id="run-old")
        _set_timestamp(connection, "run_events", "created_at", "2026-01-01T00:00:00.000Z", run_id="run-old")

    preview = journal.prune_terminal_event_payloads(
        before="2026-06-15T00:00:00.000Z",
        dry_run=True,
    )

    assert preview == {"run_ids": ("run-old",), "event_count": 1, "dry_run": True}
    assert journal.events_for_run("run-old")[0].payload == {"detail": "expired terminal detail"}

    applied = journal.prune_terminal_event_payloads(
        before="2026-06-15T00:00:00.000Z",
        dry_run=False,
    )

    assert applied == {"run_ids": ("run-old",), "event_count": 1, "dry_run": False}
    assert journal.events_for_run("run-old")[0].payload == {"retention": "payload_pruned.v1"}
    assert journal.events_for_run("run-active")[0].payload == {"detail": "must remain"}
    assert journal.load_checkpoint(checkpoint.checkpoint_id).state["accepted_evidence"]["evidence"] == [
        {"ref_id": "evidence:keep"}
    ]
