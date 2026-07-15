from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from planning.planner_schema import PlannedTask
from runtime.evidence.schema import Claim, EvidenceGateResult, EvidenceRef, GateVerdict
from runtime.execution.pipeline import PipelineRunState, TaskRunRecord
from runtime.recovery.checkpoint_codec import (
    PIPELINE_CHECKPOINT_SCHEMA_VERSION,
    UnsupportedCheckpointSchema,
    decode_pipeline_checkpoint,
    encode_pipeline_checkpoint,
)
from runtime.recovery.journal import RunJournal


def _run_state() -> PipelineRunState:
    state = PipelineRunState(
        user_request="inspect the project",
        route_type="multi_agent",
        reason="two independent readonly checks",
        tasks=[
            TaskRunRecord(
                id="task_a",
                title="Inspect configuration",
                skill_id="project_explorer",
                model="model-a",
                mcp=["project_filesystem_readonly"],
                read_set=["pyproject.toml"],
                status="completed",
                output_preview="worker prose must not be persisted",
                verification="pytest output must not be persisted",
            )
        ],
    )
    state.accepted_evidence = {
        "mode": "enforce_all",
        "claims": [
            {
                "claim_id": "claim:task_a:summary",
                "task_id": "task_a",
                "text": "Configuration was inspected.",
                "evidence_refs": ["read:pyproject.toml"],
            }
        ],
        "evidence": [
            {
                "ref_id": "read:pyproject.toml",
                "kind": "file_snapshot",
                "source": "task_a",
                "excerpt": "project metadata",
            }
        ],
        "blocked_claims": [],
    }
    return state


def test_pipeline_checkpoint_round_trip_uses_an_explicit_allowlist():
    encoded = encode_pipeline_checkpoint(_run_state())
    decoded = decode_pipeline_checkpoint(encoded)

    assert decoded == encoded
    assert decoded["schema_version"] == PIPELINE_CHECKPOINT_SCHEMA_VERSION
    assert decoded["tasks"] == [
        {
            "id": "task_a",
            "title": "Inspect configuration",
            "skill_id": "project_explorer",
            "model": "model-a",
            "mcp": ["project_filesystem_readonly"],
            "depends_on": [],
            "read_set": ["pyproject.toml"],
            "write_intent": [],
            "status": "completed",
            "side_effect_class": "read_only",
        }
    ]
    serialized = json.dumps(encoded, ensure_ascii=False)
    assert "worker prose must not be persisted" not in serialized
    assert "pytest output must not be persisted" not in serialized


def test_pipeline_checkpoint_never_serializes_raw_tool_or_approval_payloads():
    state = _run_state()
    state.worker_reports = [
        {
            "dom": "<html>secret page</html>",
            "stdout": "terminal-secret",
            "base64": "A" * 1024,
            "approval_token": "approval-secret",
        }
    ]

    encoded = encode_pipeline_checkpoint(state)
    serialized = json.dumps(encoded, ensure_ascii=False)

    assert "secret page" not in serialized
    assert "terminal-secret" not in serialized
    assert "approval-secret" not in serialized
    assert "A" * 128 not in serialized


def test_pipeline_checkpoint_keeps_evidence_integrity_metadata_without_excerpt():
    state = _run_state()
    state.accepted_evidence["evidence"][0].update(
        {
            "event_seq": 17,
            "excerpt": "raw tool result must not be checkpointed",
            "sha256": "abc123",
        }
    )

    encoded = encode_pipeline_checkpoint(state)

    assert encoded["accepted_evidence"]["evidence"] == [
        {
            "ref_id": "read:pyproject.toml",
            "kind": "file_snapshot",
            "source": "task_a",
            "event_seq": 17,
            "sha256": "abc123",
        }
    ]
    assert "raw tool result must not be checkpointed" not in json.dumps(encoded, ensure_ascii=False)


def test_pipeline_checkpoint_redacts_secrets_from_accepted_claims_and_final_output():
    state = _run_state()
    state.accepted_evidence["claims"][0]["text"] = "token=checkpoint-secret"

    encoded = encode_pipeline_checkpoint(
        state,
        final_output="api_key=final-secret",
    )
    serialized = json.dumps(encoded, ensure_ascii=False)

    assert "checkpoint-secret" not in serialized
    assert "final-secret" not in serialized
    assert "[redacted]" in serialized


def test_pipeline_checkpoint_rejects_unknown_schema_versions():
    payload = encode_pipeline_checkpoint(_run_state())
    payload["schema_version"] = "pipeline_checkpoint.v999"

    with pytest.raises(UnsupportedCheckpointSchema, match="pipeline_checkpoint.v999"):
        decode_pipeline_checkpoint(payload)


def test_pipeline_checkpoint_sink_runs_at_plan_task_evidence_and_final_boundaries():
    checkpoints: list[tuple[str, dict, dict]] = []
    state = _run_state()
    state.checkpoint_sink = lambda kind, payload, compatibility: checkpoints.append(
        (kind, payload, compatibility)
    )
    state.checkpoint_compatibility = {"planner_schema": "planner_result.v1"}
    task = PlannedTask(
        id="task_a",
        title="Inspect configuration",
        instruction="Read pyproject.toml",
        skill_id="project_explorer",
        model="model-a",
        mcp=["project_filesystem_readonly"],
        read_set=["pyproject.toml"],
    )

    state.record_checkpoint("plan.accepted")
    state.record_task_result(task, "worker prose")
    state.record_evidence_gate_result(
        EvidenceGateResult(
            mode="enforce_all",
            claims=[
                Claim(
                    claim_id="claim:task_a:summary",
                    task_id="task_a",
                    text="Configuration was inspected.",
                    evidence_refs=["read:pyproject.toml"],
                )
            ],
            evidence=[
                EvidenceRef(
                    ref_id="read:pyproject.toml",
                    kind="file_snapshot",
                    source="task_a",
                )
            ],
            verdicts=[
                GateVerdict(
                    claim_id="claim:task_a:summary",
                    status="accepted",
                    reasons=["evidence_verified"],
                )
            ],
        )
    )
    state.record_final_ready("safe final answer")

    assert [item[0] for item in checkpoints] == [
        "plan.accepted",
        "task.terminal",
        "evidence.accepted",
        "final.ready",
    ]
    assert checkpoints[-1][1]["final_output"] == "safe final answer"
    assert checkpoints[-1][2] == {"planner_schema": "planner_result.v1"}


def test_pipeline_checkpoint_failure_is_observed_without_failing_the_run():
    state = _run_state()

    def fail_checkpoint(*_args):
        raise OSError("disk full")

    state.checkpoint_sink = fail_checkpoint

    assert state.record_checkpoint("plan.accepted") is None
    assert any(event.event_type == "RecoveryCheckpointFailed" for event in state.event_bus.snapshot())


def test_final_checkpoint_preserves_a_normal_long_answer_for_history_repair(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_long_final", session_id="session_1", status="running")
    state = _run_state()
    final_output = "final line\n" * 600

    checkpoint = journal.write_checkpoint(
        run_id="run_long_final",
        kind="final.ready",
        state=encode_pipeline_checkpoint(state, final_output=final_output),
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )
    loaded = journal.load_checkpoint(checkpoint.checkpoint_id)

    assert decode_pipeline_checkpoint(loaded.state)["final_output"] == final_output
