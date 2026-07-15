from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_seed_inline_files_records_attachment_evidence_for_workers_and_placement(tmp_path):
    from runtime.compute.context_sources import strongest_context_sensitivity
    from runtime.execution.run_context import RunContextStore, seed_inline_files

    workspace = tmp_path / "workspace"
    stored = workspace / ".lucode" / "attachments" / "session_1" / "run_1" / "notes.txt"
    stored.parent.mkdir(parents=True)
    stored.write_text("full file body", encoding="utf-8")
    context = RunContextStore(workspace)

    recorded = seed_inline_files(
        context,
        workspace,
        [
            {
                "path": ".lucode/attachments/session_1/run_1/notes.txt",
                "content": "bounded attachment excerpt",
            }
        ],
    )

    assert recorded == 1
    rendered = context.render_for_task("worker_1")
    assert ".lucode/attachments/session_1/run_1/notes.txt" in rendered
    assert "bounded attachment excerpt" in rendered
    sensitivity, reasons = strongest_context_sensitivity(context.source_labels())
    assert sensitivity == "project_private"
    assert "source:file_snapshot" in reasons


def test_seed_inline_files_ignores_paths_outside_workspace(tmp_path):
    from runtime.execution.run_context import RunContextStore, seed_inline_files

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not enter run context", encoding="utf-8")
    context = RunContextStore(workspace)

    recorded = seed_inline_files(
        context,
        workspace,
        [{"path": str(outside), "content": "must not enter run context"}],
    )

    assert recorded == 0
    assert context.render_for_task() == ""


def test_dynamic_single_agent_run_keeps_raw_routing_input_and_shares_attachment_excerpt(tmp_path, monkeypatch):
    from planning.planner_schema import PlannedTask, PlannerResult, RefinedRequest
    from runtime.config.settings import RuntimeSettings
    from runtime.execution import dynamic

    stored = tmp_path / ".lucode" / "attachments" / "session_1" / "run_1" / "notes.txt"
    stored.parent.mkdir(parents=True)
    stored.write_text("full attachment", encoding="utf-8")
    task = PlannedTask(
        id="task_1",
        title="Summarize attachment",
        instruction="Summarize the user attachment.",
        skill_id="project_explorer",
        model="worker-model",
        mcp=[],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="attachment summary",
        refined_request="Summarize the attached file.",
        tasks=[task],
    )
    refined = RefinedRequest(
        raw_user_input="Summarize the attached file.",
        refined_request="Summarize the attached file.",
    )
    captured = {}

    async def fake_preview_plan(raw_input, *args, **kwargs):
        captured["planner_context"] = raw_input
        captured["routing_input"] = kwargs.get("routing_input")
        return refined, plan

    async def fake_run_single_agent(refined_request, normalized_plan, *args, **kwargs):
        del refined_request, normalized_plan, kwargs
        run_state = args[4]
        captured["worker_context"] = run_state.run_context.render_for_task("task_1")
        return "done", SimpleNamespace(passed=True)

    monkeypatch.setattr(dynamic, "preview_plan", fake_preview_plan)
    monkeypatch.setattr(dynamic, "_run_single_agent", fake_run_single_agent)
    monkeypatch.setattr(dynamic, "validate_plan", lambda plan, privacy_policy=None: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr(dynamic, "review_plan", lambda plan: SimpleNamespace(approved=True, findings=[]))
    monkeypatch.setattr(dynamic, "_resolve_planner_memory_pack", lambda *args, **kwargs: None)

    class FakeModelRegistry:
        def first_configured(self, model_ids):
            return list(model_ids)[0]

        def get_model(self, model_id):
            return object()

        def get_model_info(self, model_id):
            return {"id": model_id, "display_name": model_id, "configured": True}

    settings = RuntimeSettings(
        execution_mode="full",
        query_refiner_enabled=False,
        orchestrator_model_priority=["planner-model"],
        final_synthesizer_model_priority=["synth-model"],
        executor_model_priority=["worker-model"],
    )

    output, audit = asyncio.run(
        dynamic._execute_dynamic_attempt(
            "[inline_files]\n--- notes.txt ---\nbrowser_submit_form\n\n[current_user_request]\nSummarize the attached file.",
            tmp_path,
            FakeModelRegistry(),
            mcp_manager=object(),
            hooks=None,
            run_agent=None,
            show_plan=False,
            settings=settings,
            privacy_policy=SimpleNamespace(mode="local_first"),
            flywheel=object(),
            attempt=1,
            routing_input="Summarize the attached file.",
            inline_files=[
                {
                    "path": ".lucode/attachments/session_1/run_1/notes.txt",
                    "content": "bounded attachment excerpt",
                }
            ],
        )
    )

    assert output == "done"
    assert audit.passed is True
    assert captured["routing_input"] == "Summarize the attached file."
    assert "browser_submit_form" in captured["planner_context"]
    assert "bounded attachment excerpt" in captured["worker_context"]
