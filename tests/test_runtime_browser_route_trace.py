from __future__ import annotations

import json

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from planning.planner_schema import PlannerResult, parse_planner_result
from runtime.capabilities.resolver import CapabilityResolver
from runtime.server.app import create_app
from runtime.server.execution_bridge import RunExecutionResult


TOKEN = "trace-runtime-token"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _set_desktop_browser_bridge(monkeypatch) -> None:
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")


def _client(tmp_path, *, run_executor) -> TestClient:
    app = create_app(
        workspace_root=tmp_path,
        runtime_token=TOKEN,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "local_trace_model",
                    "name": "Local Trace Model",
                    "provider": "local",
                    "configured": True,
                    "backend_type": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "supports_tools": True,
                }
            ]
        },
        run_executor=run_executor,
    )
    return TestClient(app)


def _start_trace_run(client: TestClient, *, title: str, user_input: str) -> tuple[str, str, list[dict]]:
    session_id = client.post("/api/sessions", headers=_auth_headers(), json={"title": title}).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": user_input},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]
    return session_id, run_id, events


def _fallback_text(user_input: str) -> str:
    return f"raw_user_input: {user_input}\nrefined_request: {user_input}"


def _binding_payload(plan: PlannerResult) -> list[dict]:
    resolver = CapabilityResolver()
    return [resolver.resolve_task(task).to_dict() for task in plan.tasks]


def _task_payload(plan: PlannerResult) -> list[dict]:
    return [
        {
            "id": task.id,
            "title": task.title,
            "mcp": list(task.mcp),
            "risk_notes": task.risk_notes,
        }
        for task in plan.tasks
    ]


def test_runtime_normal_inquiry_strips_hallucinated_desktop_browser_binding(tmp_path, monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)

    user_input = "你好，简单介绍一下你能做什么。"

    async def runner(request):
        plan = parse_planner_result(
            json.dumps(
                {
                    "route_type": "single_agent",
                    "reason": "planner hallucinated a desktop browser worker for a greeting",
                    "refined_request": user_input,
                    "tasks": [
                        {
                            "id": "wrong_browser",
                            "title": "Answer greeting",
                            "instruction": "Reply to the greeting. No page interaction is needed.",
                            "skill_id": "project_explorer",
                            "model": "local_trace_model",
                            "mcp": ["desktop_browser"],
                        }
                    ],
                }
            ),
            fallback_user_input=_fallback_text(request.user_input),
        )
        bindings = _binding_payload(plan)
        request.event_bus.emit(
            "PlanningCompleted",
            "normal inquiry route traced",
            agent="orchestrator",
            status="completed",
            payload={
                "trace_marker": "normal_inquiry_route",
                "route_type": plan.route_type,
                "tasks": _task_payload(plan),
                "bindings": bindings,
                "desktop_browser_bound": any("desktop_browser" in item["mcp"] for item in bindings),
            },
        )
        assert plan.route_type == "single_agent"
        assert plan.tasks[0].mcp == []
        assert all("desktop_browser" not in item["mcp"] for item in bindings)
        return RunExecutionResult(
            final_output="普通问询回答完成，没有触发内置浏览器。",
            metadata={"mcp_ids_used": [mcp for item in bindings for mcp in item["mcp"]]},
        )

    client = _client(tmp_path, run_executor=runner)
    session_id, _run_id, events = _start_trace_run(client, title="normal route trace", user_input=user_input)

    assert [event["type"] for event in events] == ["run.started", "planner.completed", "run.completed"]
    planning_payload = events[1]["payload"]
    assert planning_payload["trace_marker"] == "normal_inquiry_route"
    assert planning_payload["desktop_browser_bound"] is False
    assert planning_payload["tasks"][0]["mcp"] == []
    assert "Removed unrequested desktop_browser binding" in planning_payload["tasks"][0]["risk_notes"]
    assert events[-1]["payload"]["mcp_ids_used"] == []

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers()).json()["messages"]
    snapshot_events = messages[1]["metadata"]["run_snapshot"]["events"]
    assert [event["type"] for event in snapshot_events] == ["run.started", "planner.completed", "run.completed"]
    snapshot_planning_payload = snapshot_events[1]["payload"]
    assert snapshot_planning_payload["desktop_browser_bound"] is False
    assert all("desktop_browser" not in item["mcp"] for item in snapshot_planning_payload["bindings"])
    assert all("browser_session" not in item["resource_locks"] for item in snapshot_planning_payload["bindings"])
    assert "desktop_browser_interaction_detected" not in json.dumps(snapshot_events, ensure_ascii=False)


def test_runtime_explicit_browser_inquiry_binds_desktop_browser_precisely(tmp_path, monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)

    user_input = "用内置浏览器打开 https://example.com 并读取页面摘要。"

    async def runner(request):
        plan = parse_planner_result(
            json.dumps(
                {
                    "route_type": "direct_answer",
                    "reason": "planner under-routed an explicit browser request",
                    "refined_request": user_input,
                    "direct_answer_instruction": "Answer directly.",
                }
            ),
            fallback_user_input=_fallback_text(request.user_input),
        )
        bindings = _binding_payload(plan)
        request.event_bus.emit(
            "PlanningCompleted",
            "explicit browser route traced",
            agent="orchestrator",
            status="completed",
            payload={
                "trace_marker": "explicit_browser_route",
                "route_type": plan.route_type,
                "tasks": _task_payload(plan),
                "bindings": bindings,
                "desktop_browser_bound": any("desktop_browser" in item["mcp"] for item in bindings),
            },
        )
        assert plan.route_type == "single_agent"
        assert plan.tasks[0].mcp == ["desktop_browser"]
        assert any("desktop_browser" in item["mcp"] for item in bindings)
        return RunExecutionResult(
            final_output="显式浏览器问询已精确绑定内置浏览器能力。",
            metadata={"mcp_ids_used": [mcp for item in bindings for mcp in item["mcp"]]},
        )

    client = _client(tmp_path, run_executor=runner)
    session_id, _run_id, events = _start_trace_run(client, title="browser route trace", user_input=user_input)

    assert [event["type"] for event in events] == ["run.started", "planner.completed", "run.completed"]
    planning_payload = events[1]["payload"]
    assert planning_payload["trace_marker"] == "explicit_browser_route"
    assert planning_payload["desktop_browser_bound"] is True
    assert planning_payload["route_type"] == "single_agent"
    assert planning_payload["tasks"][0]["id"] == "desktop_browser_task"
    assert planning_payload["tasks"][0]["mcp"] == ["desktop_browser"]
    assert events[-1]["payload"]["mcp_ids_used"] == ["desktop_browser"]

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers()).json()["messages"]
    snapshot_events = messages[1]["metadata"]["run_snapshot"]["events"]
    assert "desktop_browser_task" in json.dumps(snapshot_events, ensure_ascii=False)
