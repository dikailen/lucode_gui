from __future__ import annotations

from planning.planner import _store_memory_resolver_interface
from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.failure_memory import _record_flywheel_safely
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.task_runner import _emit_task_memory_provided
from runtime.memory.flywheel import FlywheelStore
from runtime.memory.resolver import MemoryPack, MemoryPackEntry


def _entry(kind: str, summary: str, *, confidence: float = 0.9, scope: list[str] | None = None) -> dict:
    return {
        "kind": kind,
        "summary": summary,
        "tags": ["memory"],
        "source": "distiller",
        "metadata": {
            "fingerprint": f"{kind}:{summary}",
            "confidence": confidence,
            "scope": scope or ["runtime/memory/resolver.py"],
            "action": kind,
            "status": "active",
            "injection_policy": "auto" if kind != "failure_lesson" else "planner_candidate",
        },
    }


def test_planner_memory_adoption_flushes_usage_only_at_run_recording(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    auto = flywheel.upsert_distilled_entry(
        _entry("verification_command", "Run memory resolver tests after resolver edits.")
    )
    failure = flywheel.upsert_distilled_entry(
        _entry("failure_lesson", "Avoid rebinding unrelated memory lessons.", confidence=0.7)
    )
    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id=auto["id"],
                kind="verification_command",
                summary=auto["summary"],
                confidence=0.9,
                scope=("runtime/memory/resolver.py",),
                injection_policy="auto",
            )
        ],
        failure_lesson_candidates=[
            MemoryPackEntry(
                id=failure["id"],
                kind="failure_lesson",
                summary=failure["summary"],
                confidence=0.7,
                scope=("runtime/memory/resolver.py",),
                injection_policy="planner_candidate",
            )
        ],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="test",
        refined_request="Update memory resolver.",
        tasks=[
            PlannedTask(
                id="worker-memory",
                title="Memory resolver",
                instruction="Update memory resolver.",
                skill_id="code_engineer",
                model="gpt",
            )
        ],
        memory_interface={
            "memory_resolver": {
                "adopted_entry_ids": [failure["id"]],
                "task_bindings": {"worker-memory": [failure["id"]]},
                "adoption_reasons": {failure["id"]: "Relevant previous failure."},
            }
        },
    )

    _store_memory_resolver_interface(plan, pack)
    before_recording = {entry["id"]: entry for entry in flywheel.load_entries()}
    run_state = PipelineRunState.create("Update memory resolver.", plan, tmp_path, memory_pack=pack)
    _record_flywheel_safely(flywheel, run_state)
    after_recording = {entry["id"]: entry for entry in flywheel.load_entries()}

    assert "usage_count" not in before_recording[auto["id"]]["metadata"]
    assert "usage_count" not in before_recording[failure["id"]]["metadata"]
    assert after_recording[auto["id"]]["metadata"]["usage_count"] == 1
    assert "used_by_planner_provided" in after_recording[auto["id"]]["metadata"]["maintenance_reasons"]
    assert after_recording[failure["id"]]["metadata"]["usage_count"] == 1
    assert "used_by_planner_adopted" in after_recording[failure["id"]]["metadata"]["maintenance_reasons"]


def test_worker_memory_context_flushes_usage_at_run_recording(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    auto = flywheel.upsert_distilled_entry(
        _entry("tool_hint", "Use resolver tests when editing runtime/memory/resolver.py.")
    )
    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id=auto["id"],
                kind="tool_hint",
                summary=auto["summary"],
                confidence=0.9,
                scope=("runtime/memory/resolver.py",),
                injection_policy="auto",
            )
        ],
    )
    task = PlannedTask(
        id="worker-memory",
        title="Memory resolver",
        instruction="Update memory resolver.",
        skill_id="code_engineer",
        model="gpt",
        read_set=["runtime/memory/resolver.py"],
        write_intent=["runtime/memory/resolver.py"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="test",
        refined_request="Update memory resolver.",
        tasks=[task],
    )
    run_state = PipelineRunState.create("Update memory resolver.", plan, tmp_path, memory_pack=pack)

    task_pack = pack.for_task(task)
    _emit_task_memory_provided(run_state, task, task_pack)
    _record_flywheel_safely(flywheel, run_state)
    loaded = {entry["id"]: entry for entry in flywheel.load_entries()}

    assert loaded[auto["id"]]["metadata"]["usage_count"] == 1
    assert "used_by_worker" in loaded[auto["id"]]["metadata"]["maintenance_reasons"]
