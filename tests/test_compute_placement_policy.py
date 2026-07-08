from __future__ import annotations

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState


def _task(**overrides) -> PlannedTask:
    values = {
        "id": "inspect_private",
        "title": "Inspect private auth file",
        "instruction": "Read runtime/auth.py and summarize the auth flow.",
        "skill_id": "code_engineer",
        "model": "worker-model",
        "mcp": ["project_filesystem_readonly"],
        "read_set": ["runtime/auth.py"],
    }
    values.update(overrides)
    return PlannedTask(**values)


def _plan(task: PlannedTask) -> PlannerResult:
    return PlannerResult(
        route_type="single_agent",
        reason="test",
        refined_request=task.instruction,
        tasks=[task],
    )


def test_sensitivity_classifier_marks_secret_and_local_surfaces():
    from runtime.compute.sensitivity import classify_task_sensitivity

    secret = _task(
        id="secret",
        instruction="Check .env and API_KEY before calling the provider.",
        read_set=[".env"],
    )
    browser = _task(
        id="browser",
        instruction="Read the browser DOM from the logged-in dashboard.",
        mcp=["desktop_browser"],
    )

    assert classify_task_sensitivity(secret).sensitivity == "secret"
    assert classify_task_sensitivity(browser).sensitivity == "local_only"


def test_planning_sanitizer_redacts_paths_secrets_and_dom_text():
    from runtime.compute.sanitizer import sanitize_planning_text

    sanitized = sanitize_planning_text(
        "Open C:/Users/me/project/.env, token=sk-test-secret, DOM: <input value='private'>"
    )

    assert "C:/Users/me/project/.env" not in sanitized
    assert "sk-test-secret" not in sanitized
    assert "<input" not in sanitized
    assert "[path]" in sanitized
    assert "[secret]" in sanitized
    assert "[dom]" in sanitized


def test_observe_plan_returns_private_local_decision_without_mutating_task_model():
    from runtime.compute.placement_policy import observe_compute_placement_for_plan

    task = _task(model="cloud-planner")
    plan = _plan(task)

    result = observe_compute_placement_for_plan(
        plan,
        "Read runtime/auth.py",
        privacy_mode="local_first",
        mode="observe",
    )

    assert task.model == "cloud-planner"
    assert result.mode == "observe"
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.task_id == "inspect_private"
    assert decision.sensitivity == "project_private"
    assert decision.planner_side == "local"
    assert decision.executor_side in {"local", "edge"}
    assert "project_private_context" in decision.reasons


def test_public_complex_task_can_recommend_cloud_planning_in_observe_mode():
    from runtime.compute.placement_policy import observe_compute_placement_for_plan

    task = _task(
        id="public_research",
        title="Compare public APIs",
        instruction="Compare public REST API design options and produce a migration plan.",
        skill_id="project_explorer",
        mcp=[],
        read_set=[],
        write_intent=[],
    )

    result = observe_compute_placement_for_plan(
        _plan(task),
        task.instruction,
        privacy_mode="cloud_allowed",
        mode="observe",
    )

    decision = result.decisions[0]
    assert decision.sensitivity == "public"
    assert decision.difficulty in {"complex", "long_context"}
    assert decision.planner_side == "cloud"
    assert decision.sanitized_context


def test_pipeline_state_records_compute_placement_decisions(tmp_path):
    from runtime.compute.placement_policy import observe_compute_placement_for_plan

    task = _task()
    plan = _plan(task)
    state = PipelineRunState.create("Read runtime/auth.py", plan, project_root=tmp_path, mode="auto")
    result = observe_compute_placement_for_plan(plan, "Read runtime/auth.py", privacy_mode="local_first", mode="observe")

    state.record_compute_placement_result(result)
    payload = state.to_dict()["compute_placement"]
    events = state.to_dict()["events"]

    assert payload["mode"] == "observe"
    assert payload["decisions"][0]["task_id"] == "inspect_private"
    assert events[-1]["event_type"] == "ComputePlacementObserved"
    assert events[-1]["payload"]["decisions"][0]["task_id"] == "inspect_private"


def test_dynamic_compute_placement_observe_helper_does_not_change_task_model(tmp_path):
    from runtime.execution.dynamic import _record_compute_placement_observation

    task = _task(model="chosen-before-placement")
    plan = _plan(task)
    state = PipelineRunState.create("Read runtime/auth.py", plan, project_root=tmp_path, mode="auto")

    _record_compute_placement_observation(
        plan,
        "Read runtime/auth.py",
        state,
        privacy_mode="local_first",
        mode="observe",
    )

    assert task.model == "chosen-before-placement"
    assert state.to_dict()["compute_placement"]["decisions"][0]["task_id"] == "inspect_private"
