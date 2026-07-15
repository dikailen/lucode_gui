from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.journal import RunJournal
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionResult
from runtime.server.run_manager import RunConflictError, RuntimeRunManager
from planning import planner


TOKEN = "p5-natural-continuation-token"
NOW = "2026-07-14T10:00:00.000Z"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed_interrupted_run(tmp_path, *, session_id: str, run_id: str = "run_interrupted") -> RunJournal:
    journal = RunJournal(tmp_path)
    journal.create_run(run_id=run_id, session_id=session_id, status="running", created_at=NOW)
    journal.mark_unleased_running_runs_interrupted(now=NOW)
    journal.write_checkpoint(
        run_id=run_id,
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous readonly task",
            "tasks": [],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility={
            "snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "planner_schema": "planner_result.v1",
            "execution_mode": "auto",
            "route_type": "single_agent",
            "task_fingerprints": {},
            "tool_registry_hash": "",
            "skill_versions": [],
            "mcp_manifest_hashes": [],
        },
    )
    return journal


def test_ordinary_run_request_receives_server_generated_recovery_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("P5 natural continuation")["session_id"]
    journal = _seed_interrupted_run(tmp_path, session_id=session_id)
    captured = {}

    async def runner(request):
        captured["request"] = request
        return RunExecutionResult(
            final_output="continued safely",
            metadata={"recovery_outcome": "superseded"},
        )

    client = TestClient(
        create_app(
            workspace_root=tmp_path,
            runtime_token=TOKEN,
            run_executor=runner,
        )
    )
    response = client.post(
        "/api/runs",
        headers=_headers(),
        json={"session_id": session_id, "input": "继续"},
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        while websocket.receive_json()["type"] not in {"run.completed", "run.failed", "run.cancelled"}:
            pass

    request = captured["request"]
    assert request.routing_input == "继续"
    assert request.user_input == "继续"
    assert request.recovery_envelope["source_run_id"] == "run_interrupted"
    assert request.recovery_envelope["checkpoint_kind"] == "plan.accepted"
    assert "resume" not in response.json()
    assert journal.recovery_run("run_interrupted")["status"] == "abandoned"


def test_blocked_recovery_outcome_preserves_the_source_run_as_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("P5 blocked recovery")["session_id"]
    journal = _seed_interrupted_run(tmp_path, session_id=session_id)

    async def runner(request):
        return RunExecutionResult(
            final_output="cannot replay unknown side effect",
            metadata={"recovery_outcome": "blocked"},
        )

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner))
    response = client.post(
        "/api/runs",
        headers=_headers(),
        json={"session_id": session_id, "input": "继续"},
    )
    run_id = response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        while websocket.receive_json()["type"] not in {"run.completed", "run.failed", "run.cancelled"}:
            pass

    source = journal.recovery_run("run_interrupted")
    assert source["status"] == "running"
    assert source["recovery_state"] == "blocked"


def test_missing_recovery_outcome_releases_the_source_for_a_later_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("P5 conservative release")["session_id"]
    journal = _seed_interrupted_run(tmp_path, session_id=session_id)

    async def runner(request):
        return RunExecutionResult(final_output="executor returned no recovery decision")

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner))
    response = client.post(
        "/api/runs",
        headers=_headers(),
        json={"session_id": session_id, "input": "继续"},
    )
    run_id = response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        while websocket.receive_json()["type"] not in {"run.completed", "run.failed", "run.cancelled"}:
            pass

    source = journal.recovery_run("run_interrupted")
    assert source["status"] == "running"
    assert source["recovery_state"] == "interrupted"


def test_only_one_runtime_can_claim_the_same_interrupted_run_for_an_ordinary_message(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("P5 lease race")["session_id"]
    _seed_interrupted_run(tmp_path, session_id=session_id)

    release = asyncio.Event()

    async def slow_runner(request):
        await release.wait()
        return RunExecutionResult(final_output="done")

    async def scenario():
        first = RuntimeRunManager(tmp_path, run_executor=slow_runner)
        second = RuntimeRunManager(tmp_path, run_executor=slow_runner)
        first_run = first.start_run(session_id=session_id, user_input="继续")
        with pytest.raises(RunConflictError, match="recovery lease"):
            second.start_run(session_id=session_id, user_input="继续")
        release.set()
        await first._run_tasks[first_run["run_id"]]

    asyncio.run(scenario())


def test_start_failure_releases_the_recovery_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", "resume_safe")
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session("P5 failed start")["session_id"]
    journal = _seed_interrupted_run(tmp_path, session_id=session_id)
    manager = RuntimeRunManager(tmp_path)
    monkeypatch.setattr(
        manager._history_facade.history_store,
        "append_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("history unavailable")),
    )

    with pytest.raises(OSError, match="history unavailable"):
        manager.start_run(session_id=session_id, user_input="继续")

    assert journal.recovery_run("run_interrupted")["recovery_state"] == "interrupted"


@pytest.mark.parametrize("mode", ["off", "observe", "reconnect"])
def test_non_resume_modes_never_attach_recovery_envelope(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("LUCODE_RUN_RECOVERY", mode)
    seed = RuntimeRunManager(tmp_path)
    session_id = seed.create_session(f"P5 {mode}")["session_id"]
    _seed_interrupted_run(tmp_path, session_id=session_id)
    captured = {}

    async def runner(request):
        captured["request"] = request
        return RunExecutionResult(final_output="normal")

    client = TestClient(create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner))
    response = client.post(
        "/api/runs",
        headers=_headers(),
        json={"session_id": session_id, "input": "你好"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        while websocket.receive_json()["type"] not in {"run.completed", "run.failed", "run.cancelled"}:
            pass

    assert captured["request"].routing_input == "你好"
    assert captured["request"].recovery_envelope == {}


def test_recovery_context_is_planner_background_while_raw_greeting_remains_the_only_route_input(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            calls.append((agent, prompt))
            if agent == "refiner":
                return SimpleNamespace(
                    final_output=json.dumps(
                        {
                            "raw_user_input": "你好",
                            "refined_request": "你好",
                            "explicit_constraints": [],
                            "possible_ambiguities": [],
                            "likely_intent": "greeting",
                        }
                    )
                )
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "direct_answer",
                        "reason": "new greeting is unrelated",
                        "refined_request": "你好",
                        "direct_answer_instruction": "Answer the greeting only.",
                        "tasks": [],
                        "recovery_interface": {"disposition": "ignore_previous"},
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_query_refiner", lambda model: "refiner")
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda model, **kwargs: "planner")
    monkeypatch.setattr(planner, "scout_project_context_for_planning", lambda *args, **kwargs: "")
    monkeypatch.setattr(planner, "_render_memory_pack", lambda memory_pack: "")
    monkeypatch.setattr(planner, "_render_skill_candidates_for_planning", lambda *args, **kwargs: "")
    envelope = RecoveryEnvelope(
        source_run_id="run_browser",
        session_id="session_1",
        checkpoint_id="checkpoint_browser",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "old browser task",
            "tasks": [
                {
                    "id": "browser_task",
                    "title": "Submit old form",
                    "skill_id": "project_explorer",
                    "model": "model-a",
                    "mcp": ["desktop_browser"],
                    "depends_on": [],
                    "read_set": [],
                    "write_intent": [],
                    "status": "running",
                    "side_effect_class": "unknown",
                }
            ],
            "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [{"task_id": "browser_task", "reason_code": "unknown_side_effect"}],
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )

    _refined, plan = asyncio.run(
        planner.preview_plan(
            "你好",
            refiner_model="model-a",
            planner_model="model-a",
            routing_input="你好",
            recovery_envelope=envelope.to_dict(),
        )
    )

    assert calls[0] == ("refiner", "你好")
    assert "## Recovery Context" in calls[1][1]
    assert "Submit old form" in calls[1][1]
    assert plan.route_type == "direct_answer"
    assert plan.tasks == []
    assert plan.recovery_interface == {"disposition": "ignore_previous"}


def test_recovery_planner_background_has_a_fixed_budget_and_omits_final_output():
    tasks = [
        {
            "id": f"task_{index}",
            "title": "Long task title " + ("x" * 500),
            "skill_id": "project_explorer",
            "model": "model-a",
            "mcp": ["project_filesystem_readonly"],
            "depends_on": [],
            "read_set": [],
            "write_intent": [],
            "status": "completed",
            "side_effect_class": "read_only",
        }
        for index in range(40)
    ]
    claims = [
        {
            "claim_id": f"claim:task_{index}:summary",
            "task_id": f"task_{index}",
            "text": "accepted " + ("y" * 700),
            "evidence_refs": [f"read:file_{index}.txt"],
        }
        for index in range(40)
    ]
    evidence = [
        {
            "ref_id": f"read:file_{index}.txt",
            "kind": "file_snapshot",
            "source": f"task_{index}",
            "excerpt": "z" * 700,
        }
        for index in range(40)
    ]
    envelope = RecoveryEnvelope(
        source_run_id="run_large",
        session_id="session_large",
        checkpoint_id="checkpoint_large",
        checkpoint_kind="final.ready",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "multi_agent",
            "reason": "large recovery",
            "tasks": tasks,
            "accepted_evidence": {
                "mode": "enforce_all",
                "claims": claims,
                "evidence": evidence,
                "blocked_claims": [],
            },
            "blocked_items": [],
            "final_output": "must not enter the planner prompt",
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )

    rendered = envelope.render_for_planner()

    assert len(rendered) <= 12000
    assert "must not enter the planner prompt" not in rendered
    assert "run_large" in rendered
    assert "task_0" in rendered


def test_recovery_planner_background_budget_survives_oversized_compatibility_metadata():
    repeated_diagnostic = "long compatibility diagnostic " * 20
    envelope = RecoveryEnvelope(
        source_run_id="run_extreme",
        session_id="session_extreme",
        checkpoint_id="checkpoint_extreme",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "resume a readonly task",
            "tasks": [],
            "accepted_evidence": {
                "mode": "off",
                "claims": [],
                "evidence": [],
                "blocked_claims": [],
            },
            "blocked_items": [],
        },
        compatibility={
            "snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "planner_schema": "planner_result.v1",
            "diagnostics": [repeated_diagnostic for _ in range(40)],
        },
    )

    rendered = envelope.render_for_planner()

    assert len(rendered) <= 12000
    assert "run_extreme" in rendered
    assert "pipeline_checkpoint.v1" in rendered
