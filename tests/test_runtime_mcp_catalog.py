from __future__ import annotations

from planning.planner_schema import PlannedTask, PlannerResult


def test_desktop_browser_mcp_is_runtime_scoped_to_bridge_env(monkeypatch):
    from catalog_system.loader import load_mcp_catalog, load_skill_catalog

    monkeypatch.delenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", raising=False)
    monkeypatch.delenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", raising=False)

    static_mcp_ids = {item["id"] for item in load_mcp_catalog().get("mcp_servers", [])}
    static_skills = {item["id"]: item for item in load_skill_catalog().get("skills", [])}

    assert "desktop_browser" not in static_mcp_ids
    assert "desktop_browser" not in (static_skills["project_explorer"].get("allowed_mcp") or [])

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")

    runtime_mcp = {item["id"]: item for item in load_mcp_catalog().get("mcp_servers", [])}
    runtime_skills = {item["id"]: item for item in load_skill_catalog().get("skills", [])}

    assert "desktop_browser" in runtime_mcp
    assert runtime_mcp["desktop_browser"]["implemented"] is True
    assert "browser_navigate" in runtime_mcp["desktop_browser"]["tools"]
    assert "desktop_browser" in (runtime_skills["project_explorer"].get("allowed_mcp") or [])


def test_validate_plan_accepts_desktop_browser_when_bridge_is_available(monkeypatch):
    from planning import plan_validator
    from planning.plan_validator import validate_plan

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    monkeypatch.setattr(
        plan_validator,
        "load_model_catalog",
        lambda: {"models": [{"id": "worker-model", "configured": True, "supports_tools": True}]},
    )
    monkeypatch.setattr(plan_validator, "model_runtime_available", lambda model: True)

    plan = PlannerResult(
        route_type="single_agent",
        reason="forced to embedded desktop browser worker route",
        refined_request="Use the built-in browser to open https://example.com and read the page summary.",
        tasks=[
            PlannedTask(
                id="desktop_browser_task",
                title="Use embedded desktop browser",
                instruction="Use the embedded desktop browser to open https://example.com and read the page summary.",
                skill_id="project_explorer",
                model="worker-model",
                mcp=["desktop_browser"],
                acceptance_criteria=["Return browser page summary."],
                expected_outputs=["Browser page summary."],
            )
        ],
    )

    validation = validate_plan(plan)

    assert validation.valid is True
    assert validation.errors == []
