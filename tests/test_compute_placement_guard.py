from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import runtime.execution.dynamic as dynamic
from planning.planner_schema import PlannedTask, PlannerResult


@dataclass
class _FakeRegistry:
    infos: dict[str, dict]

    def get_model_info(self, model_id: str) -> dict:
        return dict(self.infos[model_id])

    def get_model(self, model_id: str):
        return object()

    def first_configured(self, preferred, privacy_mode: str = "local_first") -> str:
        for model_id in list(preferred or []):
            if model_id in self.infos:
                return model_id
        raise ValueError("No configured models")


@dataclass
class _FakeSettings:
    executor_model_priority: list[str] = field(default_factory=list)
    allowed_worker_models: list[str] = field(default_factory=list)
    privacy_mode: str = "local_first"
    _executor_default: str = ""

    def worker_model_pool(self, model_registry=None) -> list[str]:
        return [item for item in self.allowed_worker_models if item]

    def select_model_id(self, model_registry, role: str) -> str:
        return self._executor_default


def _model(
    model_id: str,
    *,
    backend_type: str = "openai_compatible",
    is_local: bool = False,
    privacy_level: str | None = None,
    base_url: str = "",
    supports_tools: bool = True,
) -> dict:
    return {
        "id": model_id,
        "configured": True,
        "backend_type": backend_type,
        "is_local": is_local,
        "privacy_level": privacy_level if privacy_level is not None else ("local" if is_local else "cloud"),
        "base_url": base_url,
        "supports_tools": supports_tools,
        "probe": {"status": "ok"},
    }


def _task(**overrides) -> PlannedTask:
    values = {
        "id": "inspect_private",
        "title": "Inspect private file",
        "instruction": "Read runtime/auth.py and summarize it.",
        "skill_id": "code_engineer",
        "model": "",
        "mcp": ["project_filesystem_readonly"],
        "read_set": ["runtime/auth.py"],
    }
    values.update(overrides)
    return PlannedTask(**values)


def test_compute_placement_guard_covers_planner_executor_and_planning_packet():
    from runtime.compute.placement_guard import ComputePlacementGuard

    registry = _FakeRegistry(
        {
            "cloud": _model("cloud", supports_tools=True),
            "local": _model("local", backend_type="ollama", is_local=True, supports_tools=True),
        }
    )
    guard = ComputePlacementGuard(model_registry=registry, privacy_mode="local_first", mode="enforce")

    planner = guard.guard_planner_request(
        "Inspect C:/Users/me/project/.env",
        ["cloud", "local"],
    )
    executor = guard.select_executor_model(
        _task(),
        ["cloud", "local"],
    )

    assert planner.model_id == "local"
    assert planner.planner_side == "local"
    assert executor.model_id == "local"
    assert executor.candidate_index == 1
    assert guard.guard_planning_packet(planner) == planner.planner_input


def test_compute_placement_guard_sanitizes_cloud_planning_packet():
    from runtime.compute.placement_guard import ComputePlacementGuard

    registry = _FakeRegistry({"cloud": _model("cloud")})
    guard = ComputePlacementGuard(model_registry=registry, privacy_mode="cloud_allowed", mode="enforce")

    planner = guard.guard_planner_request(
        "Plan from D:/repo/private/app.py metadata only.",
        ["cloud"],
    )

    packet = guard.guard_planning_packet(planner)
    assert planner.planner_side == "cloud"
    assert "D:/repo/private/app.py" not in packet
    assert "[path]" in packet


def test_dynamic_executor_backfill_uses_compute_placement_guard(monkeypatch):
    calls: list[str] = []

    class FakeGuard:
        def __init__(self, *args, **kwargs):
            pass

        def executor_model_allowed(self, task, model_id: str, *, requires_tools: bool = False) -> bool:
            calls.append(model_id)
            return model_id == "allowed"

        def select_executor_model(self, task, candidate_model_ids, *, start_index: int = 0, requires_tools=None):
            for index, model_id in enumerate(list(candidate_model_ids or [])):
                if self.executor_model_allowed(task, model_id, requires_tools=bool(requires_tools)):
                    return SimpleNamespace(model_id=model_id, candidate_index=index)
            return None

    monkeypatch.setattr(dynamic, "ComputePlacementGuard", FakeGuard, raising=False)
    registry = _FakeRegistry(
        {
            "blocked": _model("blocked", backend_type="ollama", is_local=True, supports_tools=True),
            "allowed": _model("allowed", backend_type="ollama", is_local=True, supports_tools=True),
        }
    )
    settings = _FakeSettings(executor_model_priority=["blocked", "allowed"], _executor_default="blocked")
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="Read project", tasks=[_task()])

    dynamic._apply_executor_model_defaults(
        plan,
        settings,
        registry,
        compute_placement_mode="enforce",
    )

    assert plan.tasks[0].model == "allowed"
    assert calls == ["blocked", "allowed"]
