from __future__ import annotations

from dataclasses import dataclass, field

import asyncio
from types import SimpleNamespace

import pytest

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
            info = self.infos.get(model_id)
            if info and info.get("configured"):
                return model_id
        for model_id, info in self.infos.items():
            if info.get("configured"):
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


def test_enforce_local_first_private_request_selects_local_planner_and_disables_refiner():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "cloud-planner": _model("cloud-planner"),
            "local-planner": _model("local-planner", backend_type="ollama", is_local=True),
        }
    )

    decision = select_planner_model_for_request(
        "Read D:/repo/runtime/auth.py and inspect project credentials",
        registry,
        ["cloud-planner", "local-planner"],
        privacy_mode="local_first",
        mode="enforce",
    )

    assert decision.model_id == "local-planner"
    assert decision.planner_side == "local"
    assert decision.sensitivity in {"project_private", "secret"}
    assert decision.refiner_enabled is False
    assert decision.allow_project_scout is True


def test_planner_selection_off_mode_keeps_legacy_first_configured_behavior():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry({"cloud-planner": _model("cloud-planner")})

    decision = select_planner_model_for_request(
        "Inspect C:/Users/me/project/.env",
        registry,
        ["cloud-planner"],
        privacy_mode="local_first",
        mode="off",
    )

    assert decision.mode == "off"
    assert decision.model_id == "cloud-planner"
    assert decision.planner_input == "Inspect C:/Users/me/project/.env"


def test_enforce_private_request_does_not_trust_https_model_marked_local():
    from runtime.compute.placement_policy import ComputePlacementViolation, select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "fake-local-cloud": _model(
                "fake-local-cloud",
                is_local=True,
                privacy_level="local",
                base_url="https://api.example.com/v1",
            )
        }
    )

    with pytest.raises(ComputePlacementViolation):
        select_planner_model_for_request(
            "Inspect C:/Users/me/project/.env",
            registry,
            ["fake-local-cloud"],
            privacy_mode="local_first",
            mode="enforce",
        )


def test_enforce_loopback_openai_compatible_model_counts_as_local():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "lm-studio": _model(
                "lm-studio",
                backend_type="openai_compatible",
                privacy_level="cloud",
                base_url="http://127.0.0.1:1234/v1",
            )
        }
    )

    decision = select_planner_model_for_request(
        "Inspect C:/Users/me/project/.env",
        registry,
        ["lm-studio"],
        privacy_mode="local_first",
        mode="enforce",
    )

    assert decision.model_id == "lm-studio"
    assert decision.planner_side == "local"


def test_enforce_offline_allows_loopback_openai_compatible_model():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "lm-studio": _model(
                "lm-studio",
                backend_type="openai_compatible",
                privacy_level="cloud",
                base_url="http://localhost:1234/v1",
            )
        }
    )

    decision = select_planner_model_for_request(
        "Inspect C:/Users/me/project/.env",
        registry,
        ["lm-studio"],
        privacy_mode="offline",
        mode="enforce",
    )

    assert decision.model_id == "lm-studio"
    assert decision.planner_side == "local"


def test_enforce_cloud_allowed_mislabeled_cloud_uses_sanitized_planning_packet():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "fake-local-cloud": _model(
                "fake-local-cloud",
                is_local=True,
                privacy_level="local",
                base_url="https://api.example.com/v1",
            )
        }
    )

    decision = select_planner_model_for_request(
        "Plan a migration using D:/repo/private/app.py metadata only.",
        registry,
        ["fake-local-cloud"],
        privacy_mode="cloud_allowed",
        mode="enforce",
    )

    assert decision.planner_side == "cloud"
    assert "D:/repo/private/app.py" not in decision.planner_input
    assert "[path]" in decision.planner_input


def test_enforce_private_request_blocks_cloud_only_planner_with_readable_error():
    from runtime.compute.placement_policy import ComputePlacementViolation, select_planner_model_for_request

    registry = _FakeRegistry({"cloud-planner": _model("cloud-planner")})

    with pytest.raises(ComputePlacementViolation) as exc_info:
        select_planner_model_for_request(
            "Open C:/Users/me/project/.env and inspect API_KEY",
            registry,
            ["cloud-planner"],
            privacy_mode="local_first",
            mode="enforce",
        )

    message = str(exc_info.value).lower()
    assert "local" in message
    assert "private" in message or "sensitive" in message or "secret" in message


def test_enforce_cloud_allowed_public_complex_request_can_use_cloud_planner():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry(
        {
            "cloud-planner": _model("cloud-planner"),
            "local-planner": _model("local-planner", backend_type="ollama", is_local=True),
        }
    )

    decision = select_planner_model_for_request(
        "Compare public REST API migration strategies and design a workflow plan.",
        registry,
        ["cloud-planner", "local-planner"],
        privacy_mode="cloud_allowed",
        mode="enforce",
    )

    assert decision.model_id == "cloud-planner"
    assert decision.sensitivity == "public"
    assert decision.planner_side == "cloud"
    assert decision.allow_project_scout is False


def test_enforce_cloud_planning_uses_sanitized_packet_for_private_context():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry({"cloud-planner": _model("cloud-planner")})

    decision = select_planner_model_for_request(
        "Plan a migration using D:/repo/private/app.py and project/runtime/config.py metadata only.",
        registry,
        ["cloud-planner"],
        privacy_mode="cloud_allowed",
        mode="enforce",
    )

    assert decision.planner_side == "cloud"
    assert decision.planner_input != decision.original_request
    assert "D:/repo/private/app.py" not in decision.planner_input
    assert decision.allow_project_scout is False


def test_enforce_tool_task_requires_tool_capable_execution_model():
    from runtime.compute.placement_policy import ComputePlacementViolation

    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="Read project", tasks=[_task()])
    settings = _FakeSettings(executor_model_priority=["plain-model"], _executor_default="plain-model")
    registry = _FakeRegistry({"plain-model": _model("plain-model", supports_tools=False)})

    with pytest.raises(ComputePlacementViolation) as exc_info:
        dynamic._apply_executor_model_defaults(
            plan,
            settings,
            registry,
            compute_placement_mode="enforce",
        )

    message = str(exc_info.value).lower()
    assert "tool" in message
    assert "model" in message


def test_enforce_private_tool_task_prefers_local_tool_model_over_cloud_tool_model():
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="Read project", tasks=[_task()])
    settings = _FakeSettings(
        executor_model_priority=["cloud-tool-model", "local-tool-model"],
        _executor_default="cloud-tool-model",
    )
    registry = _FakeRegistry(
        {
            "cloud-tool-model": _model("cloud-tool-model", supports_tools=True),
            "local-tool-model": _model("local-tool-model", backend_type="ollama", is_local=True, supports_tools=True),
        }
    )

    dynamic._apply_executor_model_defaults(
        plan,
        settings,
        registry,
        compute_placement_mode="enforce",
    )

    assert plan.tasks[0].model == "local-tool-model"


def test_planner_decision_payload_does_not_persist_raw_sensitive_input():
    from runtime.compute.placement_policy import select_planner_model_for_request

    registry = _FakeRegistry({"local-planner": _model("local-planner", backend_type="ollama", is_local=True)})

    decision = select_planner_model_for_request(
        "Inspect C:/Users/me/project/.env with API_KEY=sk-test-secret",
        registry,
        ["local-planner"],
        privacy_mode="local_first",
        mode="enforce",
    )

    payload = decision.to_dict()
    rendered = str(payload)
    assert "C:/Users/me/project/.env" not in rendered
    assert "sk-test-secret" not in rendered
    assert "[path]" in rendered
    assert "[secret]" in rendered


def test_dynamic_enforce_blocks_private_cloud_only_request_before_planning(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_COMPUTE_PLACEMENT", "enforce")

    async def fail_preview_plan(*args, **kwargs):
        raise AssertionError("private cloud-only request must be blocked before planner execution")

    monkeypatch.setattr(dynamic, "preview_plan", fail_preview_plan)

    settings = dynamic.RuntimeSettings(
        execution_mode="full",
        query_refiner_enabled=False,
        orchestrator_model_priority=["cloud-planner"],
        final_synthesizer_model_priority=["cloud-planner"],
        executor_model_priority=["cloud-planner"],
        privacy_mode="local_first",
    )
    registry = _FakeRegistry({"cloud-planner": _model("cloud-planner")})

    output, audit = asyncio.run(
        dynamic._execute_dynamic_attempt(
            "Inspect C:/Users/me/project/.env",
            tmp_path,
            registry,
            mcp_manager=object(),
            hooks=None,
            run_agent=None,
            show_plan=False,
            settings=settings,
            privacy_policy=SimpleNamespace(mode="local_first"),
            flywheel=object(),
            attempt=1,
        )
    )

    assert audit is None
    assert "Compute placement blocked" in str(output)


def test_dynamic_enforce_cloud_allowed_private_planning_uses_sanitized_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_COMPUTE_PLACEMENT", "enforce")
    captured = {}

    async def fake_preview_plan(raw_user_input, *args, **kwargs):
        captured["raw_user_input"] = raw_user_input
        captured["allow_project_scout"] = kwargs.get("allow_project_scout")
        captured["refiner_enabled"] = kwargs.get("refiner_enabled")
        plan = PlannerResult(route_type="direct_answer", reason="test", refined_request=raw_user_input)
        refined = SimpleNamespace(refined_request=raw_user_input)
        return refined, plan

    async def fake_run_direct_answer(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(dynamic, "preview_plan", fake_preview_plan)
    monkeypatch.setattr(dynamic, "_run_direct_answer", fake_run_direct_answer)
    monkeypatch.setattr(dynamic, "validate_plan", lambda plan, privacy_policy=None: SimpleNamespace(valid=True, errors=[], warnings=[]))
    monkeypatch.setattr(dynamic, "review_plan", lambda plan: SimpleNamespace(approved=True, findings=[]))

    settings = dynamic.RuntimeSettings(
        execution_mode="full",
        query_refiner_enabled=True,
        query_refiner_model_priority=["cloud-planner"],
        orchestrator_model_priority=["cloud-planner"],
        final_synthesizer_model_priority=["cloud-planner"],
        executor_model_priority=["cloud-planner"],
        privacy_mode="cloud_allowed",
    )
    registry = _FakeRegistry({"cloud-planner": _model("cloud-planner")})

    output, audit = asyncio.run(
        dynamic._execute_dynamic_attempt(
            "Plan a migration from D:/repo/private/app.py without reading raw file contents.",
            tmp_path,
            registry,
            mcp_manager=object(),
            hooks=None,
            run_agent=None,
            show_plan=False,
            settings=settings,
            privacy_policy=SimpleNamespace(mode="cloud_allowed"),
            flywheel=object(),
            attempt=1,
        )
    )

    assert str(output) == "ok"
    assert audit is None
    assert "D:/repo/private/app.py" not in captured["raw_user_input"]
    assert "[path]" in captured["raw_user_input"]
    assert captured["allow_project_scout"] is False
    assert captured["refiner_enabled"] is False
