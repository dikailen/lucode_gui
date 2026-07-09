from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def test_preview_plan_injects_skill_candidates_and_adoption_contract(monkeypatch, tmp_path):
    from planning import planner

    captured = {}

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "adopt skill candidate",
                        "refined_request": "Fix Electron UI.",
                        "tasks": [
                            {
                                "id": "task_ui",
                                "title": "Fix UI",
                                "instruction": "Fix desktop/src/App.tsx.",
                                "skill_id": "code_engineer",
                                "model": "worker-model",
                            }
                        ],
                        "needs_synthesis": False,
                        "skill_interface": {
                            "version": 1,
                            "candidate_skill_ids": ["electron_ui_refactor"],
                            "adopted_skill_ids": ["electron_ui_refactor"],
                            "task_bindings": {"task_ui": ["electron_ui_refactor"]},
                            "reasons": {"electron_ui_refactor": "UI candidate matches desktop/src."},
                        },
                    }
                )
            )

    class FakeSkillResolver:
        def resolve_for_planner(self, query, *, paths=None, explicit_skill_ids=None, limit=None):
            captured["resolver_query"] = query
            captured["resolver_paths"] = paths
            captured["explicit_skill_ids"] = explicit_skill_ids
            captured["resolver_limit"] = limit
            return SimpleNamespace(candidate_skill_ids=("electron_ui_refactor",), candidates=("candidate",))

        def render_for_planner(self, pack):
            captured["pack"] = pack
            return (
                "Available Skill candidates (metadata only):\n"
                "1. [electron_ui_refactor] Electron UI score=0.91\n"
                "   summary: Electron React UI layout work."
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    _, plan = asyncio.run(
        planner.preview_plan(
            "Fix desktop/src/App.tsx UI layout",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            allow_project_scout=False,
            skill_resolver=FakeSkillResolver(),
        )
    )

    assert "Available Skill candidates" in captured["prompt"]
    assert "electron_ui_refactor" in captured["prompt"]
    assert "skill_interface" in captured["prompt"]
    assert "candidate_skill_ids" in captured["prompt"]
    assert "adopted_skill_ids" in captured["prompt"]
    assert "task_bindings" in captured["prompt"]
    assert "rejected_skill_ids" in captured["prompt"]
    assert "SKILL.md" not in captured["prompt"]
    assert "Fix desktop/src/App.tsx UI layout" in captured["resolver_query"]
    assert "desktop/src/App.tsx" in captured["resolver_paths"]
    assert plan.tasks[0].skill_id == "code_engineer"
    assert plan.tasks[0].bound_skill_ids == ["electron_ui_refactor"]


def test_preview_plan_continues_when_skill_resolver_fails(monkeypatch, tmp_path):
    from planning import planner

    captured = {}

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "direct_answer",
                        "reason": "simple request",
                        "refined_request": "hello",
                        "direct_answer_instruction": "Answer briefly.",
                        "tasks": [],
                    }
                )
            )

    class FailingSkillResolver:
        def resolve_for_planner(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("resolver unavailable")

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    _, plan = asyncio.run(
        planner.preview_plan(
            "hello",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            allow_project_scout=False,
            skill_resolver=FailingSkillResolver(),
        )
    )

    assert plan.route_type == "direct_answer"
    assert "Available Skill candidates" not in captured["prompt"]
    assert "skill_interface" not in captured["prompt"]
