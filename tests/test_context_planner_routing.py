from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from planning import planner


def test_compressed_browser_history_stays_background_while_raw_greeting_controls_browser_binding(monkeypatch):
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
                        "route_type": "single_agent",
                        "reason": "hallucinated browser task from history",
                        "refined_request": "你好",
                        "tasks": [
                            {
                                "id": "wrong_browser",
                                "title": "Open the embedded browser",
                                "instruction": "Use desktop_browser to open https://example.com",
                                "skill_id": "project_explorer",
                                "model": "model-a",
                                "mcp": ["desktop_browser"],
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_query_refiner", lambda model: "refiner")
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda model, **kwargs: "planner")
    monkeypatch.setattr(planner, "scout_project_context_for_planning", lambda *args, **kwargs: "")
    monkeypatch.setattr(planner, "_render_memory_pack", lambda memory_pack: "")
    monkeypatch.setattr(planner, "_render_skill_candidates_for_planning", lambda *args, **kwargs: "")

    compressed_input = (
        "[history_background]\n"
        "Old task: use the embedded browser to navigate https://example.com.\n\n"
        "[current_user_request]\n你好"
    )
    refined, plan = asyncio.run(
        planner.preview_plan(
            compressed_input,
            refiner_model="model-a",
            planner_model="model-a",
            routing_input="你好",
        )
    )

    assert calls[0] == ("refiner", "你好")
    assert "[history_background]" in calls[1][1]
    assert "Old task: use the embedded browser" in calls[1][1]
    assert refined.raw_user_input == "你好"
    assert plan.tasks[0].mcp == []
    assert "Removed unrequested desktop_browser binding" in plan.tasks[0].risk_notes
