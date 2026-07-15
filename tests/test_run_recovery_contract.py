from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from planning import planner
from runtime.config.execution_mode import (
    canonical_execution_mode,
    execution_mode_policy,
    runtime_route_for_input,
)


def test_orchestrator_skill_declares_the_recovery_disposition_contract():
    text = (Path(__file__).parents[1] / "skills" / "orchestrator-planner" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "recovery_interface" in text
    assert "continue_safe" in text
    assert "ignore_previous" in text
    assert "恢复背景不能单独触发工具" in text


@pytest.mark.parametrize("mode", ("auto", "solo", "serial", "full"))
def test_recovery_foundation_keeps_all_execution_mode_entries_on_dynamic_auto_contract(mode):
    policy = execution_mode_policy(mode)

    assert canonical_execution_mode(mode) == "auto"
    assert policy.canonical_mode == "auto"
    assert runtime_route_for_input("inspect the workspace", mode) == "dynamic"


def test_recovery_contract_keeps_historical_browser_text_out_of_greeting_routing(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del hooks
            calls.append((agent, prompt))
            if agent == "refiner":
                return SimpleNamespace(
                    final_output=json.dumps(
                        {
                            "raw_user_input": "hello",
                            "refined_request": "hello",
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
                        "reason": "historical browser text leaked into planning",
                        "refined_request": "hello",
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
        "Old task: use desktop_browser to navigate https://example.com and submit a form.\n\n"
        "[current_user_request]\nhello"
    )
    refined, plan = asyncio.run(
        planner.preview_plan(
            compressed_input,
            refiner_model="model-a",
            planner_model="model-a",
            routing_input="hello",
        )
    )

    assert calls[0] == ("refiner", "hello")
    assert "desktop_browser" in calls[1][1]
    assert refined.raw_user_input == "hello"
    assert plan.tasks[0].mcp == []
    assert "Removed unrequested desktop_browser binding" in plan.tasks[0].risk_notes
