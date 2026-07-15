from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.recovery.journal import RunJournal
from runtime.recovery.models import CorruptCheckpointError, CorruptRunEventError
from runtime.storage.sqlite_store import connect


def _journal_with_run(tmp_path) -> RunJournal:
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-1", session_id="session-1", status="running")
    return journal


def test_run_journal_assigns_contiguous_event_sequences_under_concurrent_writers(tmp_path):
    journal = _journal_with_run(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(
            executor.map(
                lambda index: journal.append_event(
                    run_id="run-1",
                    session_id="session-1",
                    event_type="task.progress",
                    payload={"index": index},
                ),
                range(24),
            )
        )

    assert sorted(event.seq for event in events) == list(range(1, 25))
    assert [event.seq for event in journal.events_for_run("run-1")] == list(range(1, 25))


def test_run_journal_lease_allows_only_one_recovery_owner(tmp_path):
    journal = _journal_with_run(tmp_path)

    assert journal.acquire_recovery_lease(
        run_id="run-1",
        owner_id="runtime-a",
        now="2026-07-13T00:00:00Z",
        expires_at="2026-07-13T00:05:00Z",
    )
    assert not journal.acquire_recovery_lease(
        run_id="run-1",
        owner_id="runtime-b",
        now="2026-07-13T00:00:01Z",
        expires_at="2026-07-13T00:05:01Z",
    )
    assert journal.release_recovery_lease(run_id="run-1", owner_id="runtime-a")
    assert journal.acquire_recovery_lease(
        run_id="run-1",
        owner_id="runtime-b",
        now="2026-07-13T00:00:02Z",
        expires_at="2026-07-13T00:05:02Z",
    )


def test_run_journal_rejects_checkpoint_with_modified_state_payload(tmp_path):
    journal = _journal_with_run(tmp_path)
    checkpoint = journal.write_checkpoint(
        run_id="run-1",
        kind="plan.accepted",
        state={"tasks": [{"id": "task-1", "status": "completed"}]},
        compatibility={"planner_schema": "v1"},
    )

    loaded = journal.load_checkpoint(checkpoint.checkpoint_id)
    assert loaded.state == {"tasks": [{"id": "task-1", "status": "completed"}]}

    with connect(tmp_path) as connection:
        connection.execute(
            "update run_checkpoints set state_json = ? where checkpoint_id = ?",
            ('{"tasks":[{"id":"task-1","status":"tampered"}]}', checkpoint.checkpoint_id),
        )

    with pytest.raises(CorruptCheckpointError, match="checksum"):
        journal.load_checkpoint(checkpoint.checkpoint_id)


def test_run_journal_rejects_event_with_modified_payload(tmp_path):
    journal = _journal_with_run(tmp_path)
    journal.append_event(
        run_id="run-1",
        session_id="session-1",
        event_type="task.progress",
        payload={"status": "running"},
    )

    with connect(tmp_path) as connection:
        connection.execute(
            "update run_events set payload_json = ? where run_id = ? and seq = ?",
            ('{"status":"tampered"}', "run-1", 1),
        )

    with pytest.raises(CorruptRunEventError, match="checksum"):
        journal.events_for_run("run-1")


def test_run_journal_rejects_a_valid_checksum_event_sequence_gap(tmp_path):
    journal = _journal_with_run(tmp_path)
    journal.append_event(
        run_id="run-1",
        session_id="session-1",
        event_type="task.progress",
        payload={"step": 1},
    )
    payload_json = '{"step":3}'
    with connect(tmp_path) as connection:
        connection.execute(
            """
            insert into run_events(event_id, run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-gap",
                "run-1",
                "session-1",
                3,
                "task.progress",
                payload_json,
                hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                "2026-07-15T00:00:00.000Z",
            ),
        )

    with pytest.raises(CorruptRunEventError, match="sequence gap"):
        journal.events_for_run("run-1")


def test_run_journal_database_rejects_duplicate_event_sequence_for_one_run(tmp_path):
    journal = _journal_with_run(tmp_path)
    event = journal.append_event(
        run_id="run-1",
        session_id="session-1",
        event_type="task.progress",
        payload={"step": 1},
    )

    with connect(tmp_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(
                """
                insert into run_events(event_id, run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "event-duplicate-seq",
                    "run-1",
                    "session-1",
                    event.seq,
                    "task.progress",
                    "{}",
                    hashlib.sha256(b"{}").hexdigest(),
                    "2026-07-15T00:00:00.000Z",
                ),
            )


def test_latest_checkpoint_uses_durable_insert_order_when_timestamps_collide(tmp_path, monkeypatch):
    journal = _journal_with_run(tmp_path)
    ids = iter(["z" * 32, "a" * 32])
    monkeypatch.setattr(
        "runtime.recovery.journal.uuid.uuid4",
        lambda: type("FakeUUID", (), {"hex": next(ids)})(),
    )
    monkeypatch.setattr("runtime.recovery.journal.utc_now_iso", lambda: "2026-07-14T10:00:00.000Z")
    journal.write_checkpoint(
        run_id="run-1",
        kind="plan.accepted",
        state={"step": "plan"},
        compatibility={"schema": "v1"},
    )
    final = journal.write_checkpoint(
        run_id="run-1",
        kind="final.ready",
        state={"step": "final"},
        compatibility={"schema": "v1"},
    )

    latest = journal.latest_checkpoint_for_run("run-1")

    assert latest is not None
    assert latest.checkpoint_id == final.checkpoint_id
    assert latest.kind == "final.ready"
