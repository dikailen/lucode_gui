from __future__ import annotations

from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.journal import RunJournal
from runtime.server.run_manager import RuntimeRunManager
from runtime.server.execution_bridge import RunExecutionResult


def test_checkpointed_final_answer_is_appended_to_history_at_most_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed_manager = RuntimeRunManager(tmp_path)
    session_id = seed_manager.create_session("P5 final idempotency")["session_id"]
    run_id = "run_final_ready"
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id=run_id,
        session_id=session_id,
        status="running",
        created_at="2026-07-14T10:00:00.000Z",
    )
    journal.write_checkpoint(
        run_id=run_id,
        kind="final.ready",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "direct_answer",
            "reason": "final ready",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
            "final_output": "durable final answer",
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )
    executor_calls = []

    async def runner(request):
        executor_calls.append(request)
        return RunExecutionResult(final_output="unexpected")

    manager = RuntimeRunManager(tmp_path, run_executor=runner)
    RuntimeRunManager(tmp_path, run_executor=runner)

    assistant_messages = [
        message
        for message in manager.load_session_messages(session_id)["messages"]
        if message["role"] == "assistant" and message["content"] == "durable final answer"
    ]
    assert [message["content"] for message in assistant_messages] == ["durable final answer"]
    assert journal.recovery_run(run_id)["status"] == "completed"
    assert executor_calls == []


def test_runtime_restart_does_not_duplicate_final_already_present_in_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed_manager = RuntimeRunManager(tmp_path)
    session_id = seed_manager.create_session("P5 final already written")["session_id"]
    run_id = "run_final_written"
    seed_manager._history_facade.history_store.append_message(
        session_id,
        "assistant",
        "durable final answer",
        metadata={"run_id": run_id},
    )
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id=run_id,
        session_id=session_id,
        status="running",
        created_at="2026-07-14T10:00:00.000Z",
    )
    journal.write_checkpoint(
        run_id=run_id,
        kind="final.ready",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "direct_answer",
            "reason": "final ready",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
            "final_output": "durable final answer",
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )

    manager = RuntimeRunManager(tmp_path)

    assistant_messages = [
        message
        for message in manager.load_session_messages(session_id)["messages"]
        if message["role"] == "assistant" and message["content"] == "durable final answer"
    ]
    assert len(assistant_messages) == 1
    assert journal.recovery_run(run_id)["status"] == "completed"


def test_runtime_repairs_completed_run_when_final_checkpoint_exists_but_history_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed_manager = RuntimeRunManager(tmp_path)
    session_id = seed_manager.create_session("P5 completed before history")["session_id"]
    run_id = "run_completed_without_history"
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id=run_id,
        session_id=session_id,
        status="completed",
        created_at="2026-07-14T10:00:00.000Z",
    )
    journal.write_checkpoint(
        run_id=run_id,
        kind="final.ready",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "direct_answer",
            "reason": "final ready",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
            "final_output": "repaired completed final",
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )

    manager = RuntimeRunManager(tmp_path)

    assert [
        item["content"]
        for item in manager.load_session_messages(session_id)["messages"]
        if item["role"] == "assistant"
    ] == ["repaired completed final"]


def test_runtime_never_revives_an_abandoned_run_from_a_stale_final_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed_manager = RuntimeRunManager(tmp_path)
    session_id = seed_manager.create_session("P5 abandoned final")["session_id"]
    run_id = "run_abandoned_with_final"
    journal = RunJournal(tmp_path)
    journal.create_run(
        run_id=run_id,
        session_id=session_id,
        status="abandoned",
        created_at="2026-07-14T10:00:00.000Z",
    )
    journal.write_checkpoint(
        run_id=run_id,
        kind="final.ready",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "direct_answer",
            "reason": "stale final",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
            "final_output": "must not be restored",
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )

    manager = RuntimeRunManager(tmp_path)

    assert [
        item["content"]
        for item in manager.load_session_messages(session_id)["messages"]
        if item["role"] == "assistant"
    ] == []
    assert journal.recovery_run(run_id)["status"] == "abandoned"
