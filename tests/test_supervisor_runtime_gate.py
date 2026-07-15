from __future__ import annotations

import asyncio
import json

import pytest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agent.approval import run_with_approval
from runtime.agent.approval_policy import SupervisorApprovalPolicy, SupervisorApprovalRequest
from runtime.agent.supervisor import WorkerReport
from runtime.execution.supervisor_observer import build_supervisor_plan_view
from runtime.execution.supervisor_scheduler import supervisor_execution_batches_for_team


def _write_task(task_id: str, path: str) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=task_id,
        instruction=f"modify {path}",
        skill_id="code_engineer",
        model="worker",
        mcp=["workspace_edit"],
        parallel_group=1,
        write_intent=[path],
    )


def test_supervisor_gate_approves_declared_workspace_write():
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/auth.py", "content": "x = 1", "reason": "implement auth"}),
    )

    assert decision.approve is True
    assert decision.reason == "supervisor_gate_approved"


def test_supervisor_gate_rejects_out_of_scope_workspace_write():
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'", "reason": "expand scope"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "supervisor_gate_requires_agent"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.target_paths == ["runtime/secrets.py"]
    assert decision.fallback_decision is not None
    assert decision.fallback_decision.reject is True
    assert "worker-auth" in decision.fallback_decision.rejection_message


def test_supervisor_gate_can_be_disabled_for_legacy_prompt_path(monkeypatch):
    monkeypatch.setenv("LUCODE_SUPERVISOR_GATE", "0")
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.reason == "write_path_out_of_scope"


def test_high_risk_preflight_is_disabled_when_evidence_gate_is_not_enforcing(monkeypatch):
    monkeypatch.delenv("LUCODE_EVIDENCE_GATE", raising=False)
    task = PlannedTask(
        id="browser",
        title="Browser action",
        instruction="Click a browser button.",
        skill_id="project_explorer",
        model="worker",
        mcp=["desktop_browser"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "desktop_browser.browser_click_element",
        json.dumps({"url": "https://example.com", "selector": "#submit"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is False
    assert decision.reason == "tool_not_in_supervisor_policy"


def test_high_risk_preflight_sends_browser_action_to_supervisor(monkeypatch):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    task = PlannedTask(
        id="browser",
        title="Browser action",
        instruction="Click a browser button after approval.",
        skill_id="project_explorer",
        model="worker",
        mcp=["desktop_browser"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "desktop_browser.browser_submit_form",
        json.dumps({"url": "https://example.com/form", "selector": "form#signup"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert isinstance(decision.supervisor_request, SupervisorApprovalRequest)
    assert decision.supervisor_request.operation == "browser_submit_form"
    assert decision.supervisor_request.target_paths == ["https://example.com/form", "form#signup"]
    assert decision.fallback_decision is not None
    assert decision.fallback_decision.reject is True


def test_high_risk_preflight_sends_safe_delete_to_supervisor(monkeypatch):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    task = PlannedTask(
        id="cleanup",
        title="Cleanup",
        instruction="Remove one generated temp file.",
        skill_id="code_engineer",
        model="worker",
        mcp=["safe_backup"],
        write_intent=["build/tmp.txt"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "safe_backup.safe_delete_file",
        json.dumps({"path": "build/tmp.txt", "reason": "remove generated temp"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.operation == "safe_delete_file"
    assert decision.supervisor_request.target_paths == ["build/tmp.txt"]
    assert decision.fallback_decision is not None
    assert decision.fallback_decision.reject is True


def test_high_risk_preflight_sends_external_mcp_mutation_to_supervisor(monkeypatch):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    task = PlannedTask(
        id="external",
        title="External MCP mutation",
        instruction="Update external private service state.",
        skill_id="project_explorer",
        model="worker",
        mcp=["private_crm_mcp"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "private_crm_mcp.update_record",
        json.dumps({"record_id": "customer-1", "status": "closed"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.operation == "external_mcp_mutation"
    assert decision.supervisor_request.target_paths == ["private_crm_mcp.update_record"]
    assert decision.fallback_decision is not None
    assert decision.fallback_decision.reject is True


def test_high_risk_preflight_uses_workspace_mcp_manifest_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    mcp_dir = tmp_path / ".lucode" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "private_crm.json").write_text(
        json.dumps(
            {
                "id": "private_crm",
                "display_name": "Private CRM",
                "tools": ["sync"],
                "approval_required": True,
                "side_effects": "writes_external_private_service",
                "risk_level": "high",
                "trusted": True,
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="external",
        title="External CRM sync",
        instruction="Sync a private CRM record.",
        skill_id="project_explorer",
        model="worker",
        mcp=["private_crm"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "private_crm.sync",
        json.dumps({"record_id": "customer-1"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.operation == "external_mcp_mutation"
    assert decision.supervisor_request.target_paths == ["private_crm.sync"]


def test_high_risk_preflight_does_not_treat_manifest_readonly_mcp_as_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    mcp_dir = tmp_path / ".lucode" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "readonly_docs.json").write_text(
        json.dumps(
            {
                "id": "readonly_docs",
                "display_name": "Readonly Docs",
                "tools": ["fetch_doc"],
                "approval_required": False,
                "side_effects": "none",
                "risk_level": "low",
                "trusted": True,
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="docs",
        title="Read docs",
        instruction="Fetch documentation.",
        skill_id="project_explorer",
        model="worker",
        mcp=["readonly_docs"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "readonly_docs.fetch_doc",
        json.dumps({"topic": "api"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is False
    assert decision.reason == "tool_not_in_supervisor_policy"


def test_run_with_approval_rejects_manifest_high_risk_external_mcp_without_supervisor(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    mcp_dir = tmp_path / ".lucode" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "private_crm.json").write_text(
        json.dumps(
            {
                "id": "private_crm",
                "display_name": "Private CRM",
                "tools": ["sync"],
                "approval_required": True,
                "side_effects": "writes_external_private_service",
                "risk_level": "high",
                "trusted": True,
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )

    class FakeItem:
        qualified_name = "private_crm.sync"
        name = "sync"
        arguments = json.dumps({"record_id": "customer-1"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    task = PlannedTask(
        id="external",
        title="External CRM sync",
        instruction="Sync a private CRM record.",
        skill_id="project_explorer",
        model="worker",
        mcp=["private_crm"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    result = asyncio.run(run_with_approval("agent", "input", hooks, approval_policy=policy))

    assert result.final_output == "worker adapted"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert "private_crm.sync" in state.rejected[0][1] or "external" in state.rejected[0][1].lower()
    assert hooks.tool_events[-1].status == "supervisor_rejected"


def test_high_risk_preflight_uses_workspace_mcp_servers_json_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "comfyui_graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                        "approval_required": True,
                        "side_effects": "controls_external_private_service",
                        "risk_level": "high",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="comfyui",
        title="ComfyUI graph mutation",
        instruction="Queue a ComfyUI workflow through MCP.",
        skill_id="project_explorer",
        model="worker",
        mcp=["comfyui_graph"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "comfyui_graph.queue",
        json.dumps({"workflow_id": "demo"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.target_paths == ["comfyui_graph.queue"]


def test_high_risk_preflight_normalizes_hyphenated_mcp_manifest_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "comfyui-graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                        "approval_required": True,
                        "side_effects": "controls_external_private_service",
                        "risk_level": "high",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="comfyui",
        title="ComfyUI graph mutation",
        instruction="Queue a ComfyUI workflow through MCP.",
        skill_id="project_explorer",
        model="worker",
        mcp=["comfyui-graph"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "comfyui-graph.queue",
        json.dumps({"workflow_id": "demo"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.target_paths == ["comfyui-graph.queue"]


def test_high_risk_preflight_uses_heuristic_when_mcp_manifest_lacks_risk_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "readonly_docs": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8765/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="docs",
        title="Readonly external docs",
        instruction="Fetch public documentation through MCP.",
        skill_id="project_explorer",
        model="worker",
        mcp=["readonly_docs"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "readonly_docs.fetch",
        json.dumps({"topic": "api"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is False
    assert decision.reason == "tool_not_in_supervisor_policy"


def test_high_risk_preflight_supports_requires_approval_manifest_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "comfyui_graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                        "requires_approval": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="comfyui",
        title="ComfyUI graph mutation",
        instruction="Queue a ComfyUI workflow through MCP.",
        skill_id="project_explorer",
        model="worker",
        mcp=["comfyui_graph"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "comfyui_graph.queue",
        json.dumps({"workflow_id": "demo"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"


def test_high_risk_preflight_treats_non_empty_approval_required_list_as_required(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workflow_bridge": {
                        "transport": "http",
                        "url": "http://127.0.0.1:9000/mcp",
                        "approval_required": ["mcp_write"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="workflow",
        title="Workflow bridge",
        instruction="Call a workflow bridge MCP.",
        skill_id="project_explorer",
        model="worker",
        mcp=["workflow_bridge"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "workflow_bridge.queue",
        json.dumps({"workflow_id": "demo"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"


def test_high_risk_preflight_falls_back_to_http_method_for_external_mcp_without_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCODE_EVIDENCE_GATE", "enforce_high_risk")
    monkeypatch.setenv("LUCODE_WORKSPACE_ROOT", str(tmp_path))
    lucode_dir = tmp_path / ".lucode"
    lucode_dir.mkdir(parents=True)
    (lucode_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "external_docs": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8765/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    task = PlannedTask(
        id="external",
        title="External MCP mutation",
        instruction="Post to an external MCP endpoint.",
        skill_id="project_explorer",
        model="worker",
        mcp=["external_docs"],
    )
    policy = SupervisorApprovalPolicy.from_task(task)

    decision = policy.decide(
        "external_docs.request",
        json.dumps({"method": "POST", "path": "/records"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "high_risk_preflight_requires_supervisor"


def test_supervisor_conflict_view_marks_conflicts_as_serialized():
    tasks = [_write_task("worker-a", "runtime/auth.py"), _write_task("worker-b", "runtime")]
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="edit files", tasks=tasks)

    view = build_supervisor_plan_view(plan, mode="full")
    batches = supervisor_execution_batches_for_team(tasks)

    assert len(batches) == 2
    assert view.conflicts
    assert view.decisions[0].action == "serialize_conflict"
    assert "observation-only" not in " ".join(view.notes).lower()


def test_run_with_approval_rejects_out_of_scope_write_without_executing(monkeypatch):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"
        arguments = json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(run_with_approval("agent", "input", hooks, approval_policy=policy))

    assert result.final_output == "worker adapted"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert "runtime/secrets.py" in state.rejected[0][1]
    assert hooks.tool_events[-1].status == "supervisor_rejected"


def test_run_with_approval_rejects_reused_once_approval_with_reordered_arguments(monkeypatch):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"

        def __init__(self, arguments):
            self.arguments = arguments

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem(json.dumps({"path": "runtime/auth.py", "content": "x"}))]

        def to_state(self):
            return state

    class SecondResult:
        interruptions = [FakeItem(json.dumps({"content": "x", "path": "runtime/auth.py"}))]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted"

    calls = [FirstResult(), SecondResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    class FakeSession:
        async def request_tool_approval(self, *args, **kwargs):
            del args, kwargs
            return "once"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            session=FakeSession(),
            approval_policy=None,
        )
    )

    assert result.final_output == "worker adapted"
    assert len(state.approved) == 1
    assert len(state.rejected) == 1
    assert "同一工具调用" in state.rejected[0][1]
    assert hooks.tool_events[-1].status == "duplicate_rejected"


def test_run_with_approval_allows_out_of_scope_write_when_supervisor_agent_approves(monkeypatch):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"
        arguments = json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker wrote approved file"

    calls = [FirstResult(), FinalResult()]
    captured = {}

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    async def fake_supervisor_decider(request, policy, tool_name, arguments):
        captured["request"] = request
        captured["policy"] = policy
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return "approve", "主管确认该越界写入属于用户目标。"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=fake_supervisor_decider,
        )
    )

    assert result.final_output == "worker wrote approved file"
    assert isinstance(captured["request"], SupervisorApprovalRequest)
    assert captured["request"].target_paths == ["runtime/secrets.py"]
    assert state.approved == [FirstResult.interruptions[0]]
    assert state.rejected == []
    assert hooks.tool_events[-1].status == "supervisor_agent_approved"


def test_run_with_approval_falls_back_when_supervisor_agent_unavailable(monkeypatch):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"
        arguments = json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    async def failing_supervisor_decider(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("supervisor unavailable")

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=failing_supervisor_decider,
        )
    )

    assert result.final_output == "worker adapted"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert "runtime/secrets.py" in state.rejected[0][1]
    assert hooks.tool_events[-1].status == "supervisor_rejected"


@pytest.mark.parametrize(
    ("agent_decision", "reason"),
    [
        ("reject", "主管拒绝该越界写入。"),
        ("serialize", "主管要求先串行化该冲突写入。"),
    ],
)
def test_run_with_approval_rejects_when_supervisor_agent_blocks_or_serializes(monkeypatch, agent_decision, reason):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"
        arguments = json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted after supervisor decision"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    async def fake_supervisor_decider(*args, **kwargs):
        del args, kwargs
        return agent_decision, reason

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = SupervisorApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=fake_supervisor_decider,
        )
    )

    assert result.final_output == "worker adapted after supervisor decision"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert state.rejected[0][1] == reason
    assert hooks.tool_events[-1].status == "supervisor_agent_rejected"


def test_full_multi_agent_passes_supervisor_decider_to_worker(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner
    from runtime.execution.pipeline import PipelineRunState

    task = _write_task("worker-auth", "runtime/auth.py")
    plan = PlannerResult(
        route_type="multi_agent",
        reason="team",
        refined_request="edit auth",
        tasks=[task],
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
            }
        },
    )
    captured = {}

    async def fake_run_planned_task(*args, **kwargs):
        captured["approval_policy"] = kwargs.get("approval_policy")
        captured["execution_mode"] = kwargs.get("execution_mode")
        return "Auth", "done"

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(
        runner,
        "build_worker_report",
        lambda task, output, run_state=None: WorkerReport(task_id=task.id, status="completed", summary=output),
    )
    async def fake_finalize_with_supervisor_agent(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_finalize_with_supervisor_agent)

    class FakeFactory:
        def create_supervisor_agent(self, model_id, readonly_servers):
            del model_id, readonly_servers
            return object()

    async def fake_run_agent(*args, **kwargs):
        del args, kwargs
        return type("Result", (), {"final_output": '{"decision":"approve","reason":"ok"}'})()

    output = asyncio.run(
        runner._run_multi_agent(
            "edit auth",
            plan,
            tmp_path,
            "supervisor-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=PipelineRunState.create("edit auth", plan, project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
            approval_policy_factory=SupervisorApprovalPolicy.from_task,
        )
    )

    assert "主管最终汇报" in output
    assert captured["execution_mode"] == "auto"
    assert captured["approval_policy"] is not None
    assert callable(getattr(captured["approval_policy"], "supervisor_approval_decider", None))


def test_supervisor_approval_prompt_includes_conflict_view():
    from runtime.execution import multi_agent_runner as runner

    tasks = [_write_task("worker-a", "runtime/auth.py"), _write_task("worker-b", "runtime")]
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="edit auth files", tasks=tasks)
    view = build_supervisor_plan_view(plan, mode="full")
    request = SupervisorApprovalRequest(
        task_id="worker-b",
        tool_name="workspace_edit.write_file",
        operation="write",
        target_paths=["runtime/auth.py"],
        reason="conflicting write request",
    )

    prompt = runner._render_supervisor_approval_prompt(
        refined_request="edit auth files",
        plan=plan,
        run_state=None,
        request=request,
        tool_name="workspace_edit.write_file",
        arguments=json.dumps({"path": "runtime/auth.py", "content": "x = 1"}),
        supervisor_view=view,
    )

    assert "## 冲突视图" in prompt
    assert "write_conflict" in prompt
    assert "worker-a" in prompt
    assert "worker-b" in prompt
