from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from runtime.agent.approval import run_with_approval
from runtime.agent.tool_invocation_guard import ToolInvocationGuard
from runtime.hooks.task_scope import TaskScopedHooks
from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.journal import RunJournal
from runtime.recovery.tool_lifecycle import JournalToolLifecycleSink


class _Interruption:
    qualified_name = "workspace_edit.write_file"
    name = "write_file"
    arguments = '{"path":"runtime/a.py","content":"x"}'


class _State:
    def __init__(self) -> None:
        self.approved = []
        self.rejected = []

    def approve(self, item) -> None:
        self.approved.append(item)

    def reject(self, item, rejection_message="") -> None:
        self.rejected.append((item, rejection_message))


def _journal_and_sink(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    return journal, JournalToolLifecycleSink(journal, run_id="run_1")


def test_approved_invocation_is_durable_before_the_sdk_resumes(tmp_path, monkeypatch):
    journal, sink = _journal_and_sink(tmp_path)
    state = _State()
    captured = {}

    class FirstResult:
        interruptions = [_Interruption()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker completed"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*_args, **_kwargs):
        if len(calls) == 1:
            invocation = journal.tool_invocation(captured["invocation_id"])
            assert invocation is not None
            assert invocation["status"] == "dispatched"
        return calls.pop(0)

    class Session:
        async def request_tool_approval(self, *_args, **kwargs):
            captured["invocation_id"] = kwargs["invocation_id"]
            return "yes"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            session=Session(),
            lifecycle_sink=sink,
            run_id="run_1",
            task_id="task_1",
        )
    )

    assert result.final_output == "worker completed"
    assert len(state.approved) == 1
    assert state.approved[0].qualified_name == "workspace_edit.write_file"


def test_workspace_file_write_prepare_records_a_content_hash_postcondition(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    content = "verified content"
    invocation = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "workspace_edit.write_file",
        '{"target_path":"notes.txt","content":"verified content","reason":"test"}',
    )

    postcondition = journal.tool_postcondition(invocation.invocation_id)

    assert postcondition is not None
    assert postcondition["kind"] == "workspace_file_sha256.v1"
    assert postcondition["expectation"] == {
        "path": "notes.txt",
        "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def test_sdk_tool_start_persists_a_nonapproval_browser_navigation_before_execution(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="browser_task",
        tool_lifecycle_sink=sink,
    )
    context = SimpleNamespace(
        tool_name="desktop_browser.browser_navigate",
        tool_arguments='{"tab_id":"tab_9","url":"https://example.test/reports"}',
    )

    asyncio.run(hooks.on_tool_start(context, object(), SimpleNamespace(name="browser_navigate")))

    invocations = journal.tool_invocations_for_run("run_1")
    assert len(invocations) == 1
    assert invocations[0]["task_id"] == "browser_task"
    assert invocations[0]["tool_name"] == "desktop_browser.browser_navigate"
    assert invocations[0]["status"] == "dispatched"
    postcondition = journal.tool_postcondition(invocations[0]["invocation_id"])
    assert postcondition is not None
    assert postcondition["kind"] == "browser_navigation_url_sha256.v1"


def test_sdk_tool_start_reuses_an_already_dispatched_approved_invocation(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    arguments = '{"target_path":"notes.txt","content":"value","reason":"test"}'
    invocation = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "workspace_edit.write_file",
        arguments,
    )
    sink.mark_dispatched(invocation)
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="task_1",
        tool_lifecycle_sink=sink,
    )
    context = SimpleNamespace(tool_name="workspace_edit.write_file", tool_arguments=arguments)

    asyncio.run(hooks.on_tool_start(context, object(), SimpleNamespace(name="write_file")))

    invocations = journal.tool_invocations_for_run("run_1")
    assert [item["invocation_id"] for item in invocations] == [invocation.invocation_id]
    assert invocations[0]["status"] == "dispatched"


def test_sdk_nonapproval_tool_end_completes_the_invocation_started_by_the_hook(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="browser_task",
        tool_lifecycle_sink=sink,
    )
    arguments = '{"tab_id":"tab_9","url":"https://example.test/reports"}'
    context = SimpleNamespace(tool_name="desktop_browser.browser_navigate", tool_arguments=arguments)
    tool = SimpleNamespace(name="browser_navigate")

    asyncio.run(hooks.on_tool_start(context, object(), tool))
    asyncio.run(hooks.on_tool_end(context, object(), tool, {"evidence_ref": "evidence:browser:tab_9"}))

    invocations = journal.tool_invocations_for_run("run_1")
    assert len(invocations) == 1
    assert invocations[0]["status"] == "completed"
    assert invocations[0]["evidence_ref"] == "evidence:browser:tab_9"


def test_sdk_tool_start_fails_closed_when_the_lifecycle_sink_cannot_persist():
    class BrokenSink:
        def ensure_dispatched_for_tool_start(self, **_kwargs):
            return False

    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="browser_task",
        tool_lifecycle_sink=BrokenSink(),
    )
    context = SimpleNamespace(
        tool_name="desktop_browser.browser_navigate",
        tool_arguments='{"tab_id":"tab_9","url":"https://example.test/reports"}',
    )

    with pytest.raises(RuntimeError, match="persist tool lifecycle"):
        asyncio.run(hooks.on_tool_start(context, object(), SimpleNamespace(name="browser_navigate")))


def test_sdk_tool_end_completes_the_matching_durable_invocation(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    guard = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink)
    invocation = guard.build_invocation(
        "project_filesystem_readonly.read_file",
        '{"path":"pyproject.toml"}',
    )
    sink.mark_dispatched(invocation)
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="task_1",
        tool_lifecycle_sink=sink,
    )
    context = SimpleNamespace(
        tool_name="project_filesystem_readonly.read_file",
        tool_arguments='{"path":"pyproject.toml"}',
    )

    asyncio.run(
        hooks.on_tool_end(
            context,
            object(),
            SimpleNamespace(name="read_file"),
            {"evidence_ref": "evidence:pyproject"},
        )
    )

    recorded = journal.tool_invocation(invocation.invocation_id)
    assert recorded is not None
    assert recorded["status"] == "completed"
    assert recorded["evidence_ref"] == "evidence:pyproject"


def test_lifecycle_sink_rejects_a_tool_callback_without_a_matching_invocation(tmp_path):
    _journal, sink = _journal_and_sink(tmp_path)

    with pytest.raises(KeyError, match="matching invocation"):
        sink.complete_for_tool(
            task_id="task_1",
            tool_name="project_filesystem_readonly.read_file",
            arguments='{"path":"pyproject.toml"}',
        )


def test_parallel_matching_tool_callbacks_complete_distinct_durable_invocations(tmp_path, monkeypatch):
    journal, sink = _journal_and_sink(tmp_path)
    first = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "project_filesystem_readonly.read_file",
        '{"path":"pyproject.toml"}',
    )
    second = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "project_filesystem_readonly.read_file",
        '{"path":"pyproject.toml"}',
    )
    sink.mark_dispatched(first)
    sink.mark_dispatched(second)

    original_complete = journal.complete_tool_invocation
    barrier = threading.Barrier(2)

    def synchronized_complete(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(journal, "complete_tool_invocation", synchronized_complete)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: sink.complete_for_tool(
                    task_id="task_1",
                    tool_name="project_filesystem_readonly.read_file",
                    arguments='{"path":"pyproject.toml"}',
                    evidence_ref="evidence:pyproject",
                ),
                range(2),
            )
        )

    assert results == [True, True]
    assert {journal.tool_invocation(first.invocation_id)["status"], journal.tool_invocation(second.invocation_id)["status"]} == {
        "completed"
    }


def test_completion_persistence_failure_keeps_the_callback_record_retryable(tmp_path, monkeypatch):
    journal, sink = _journal_and_sink(tmp_path)
    invocation = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "project_filesystem_readonly.read_file",
        '{"path":"pyproject.toml"}',
    )
    sink.mark_dispatched(invocation)
    original_complete = journal.complete_tool_invocation

    def unavailable(*_args, **_kwargs):
        raise OSError("journal temporarily unavailable")

    monkeypatch.setattr(journal, "complete_tool_invocation", unavailable)
    assert sink.complete_for_tool(
        task_id="task_1",
        tool_name="project_filesystem_readonly.read_file",
        arguments='{"path":"pyproject.toml"}',
    ) is False
    assert journal.tool_invocation(invocation.invocation_id)["status"] == "dispatched"

    monkeypatch.setattr(journal, "complete_tool_invocation", original_complete)
    assert sink.complete_for_tool(
        task_id="task_1",
        tool_name="project_filesystem_readonly.read_file",
        arguments='{"path":"pyproject.toml"}',
        evidence_ref="evidence:pyproject",
    ) is True
    assert journal.tool_invocation(invocation.invocation_id)["status"] == "completed"


def test_unmatched_sdk_callback_does_not_crash_the_agent_and_is_unknown_after_restart(tmp_path):
    journal, sink = _journal_and_sink(tmp_path)
    invocation = ToolInvocationGuard(run_id="run_1", task_id="task_1", lifecycle_sink=sink).build_invocation(
        "workspace_edit.write_file",
        '{"path":"runtime/a.py","content":"x"}',
    )
    sink.mark_dispatched(invocation)
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="task_1",
        tool_lifecycle_sink=sink,
    )

    asyncio.run(
        hooks.on_tool_end(
            SimpleNamespace(
                tool_name="project_filesystem_readonly.read_file",
                tool_arguments='{"path":"pyproject.toml"}',
            ),
            object(),
            SimpleNamespace(name="read_file"),
            {"result": "ok"},
        )
    )

    assert journal.tool_invocation(invocation.invocation_id)["status"] == "dispatched"
    RecoveryCoordinator(journal).scan_startup()
    assert journal.tool_invocation(invocation.invocation_id)["status"] == "unknown"


def test_approval_token_is_bound_to_the_runtime_run_and_attempt():
    first = ToolInvocationGuard(run_id="run_1", task_id="task_1", attempt=1).build_invocation(
        "workspace_edit.write_file",
        '{"path":"runtime/a.py","content":"x"}',
    )
    resumed = ToolInvocationGuard(run_id="run_2", task_id="task_1", attempt=2).build_invocation(
        "workspace_edit.write_file",
        '{"content":"x","path":"runtime/a.py"}',
    )

    assert first.approval_token.arguments_hash == resumed.approval_token.arguments_hash
    assert first.approval_token.token_id != resumed.approval_token.token_id


def test_lifecycle_dispatch_failure_does_not_resume_an_approved_tool_in_safe_mode(monkeypatch):
    state = _State()
    calls = 0

    class FirstResult:
        interruptions = [_Interruption()]

        def to_state(self):
            return state

    class BrokenLifecycleSink:
        def prepare(self, *_args, **_kwargs):
            return False

        def mark_dispatched(self, *_args, **_kwargs):
            return False

        def cancel(self, *_args, **_kwargs):
            return True

    async def fake_run_agent_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FirstResult()
        pytest.fail("the SDK must not resume a tool without a durable dispatched record")

    class Session:
        async def request_tool_approval(self, *_args, **_kwargs):
            return "yes"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            type("Hooks", (), {"tool_events": []})(),
            session=Session(),
            lifecycle_sink=BrokenLifecycleSink(),
            run_id="run_1",
            task_id="task_1",
        )
    )

    assert calls == 1
    assert result.final_output


def test_lifecycle_prepare_failure_cannot_be_bypassed_by_a_later_dispatch_result(monkeypatch):
    state = _State()
    calls = 0

    class FirstResult:
        interruptions = [_Interruption()]

        def to_state(self):
            return state

    class InconsistentLifecycleSink:
        def prepare(self, *_args, **_kwargs):
            return False

        def mark_dispatched(self, *_args, **_kwargs):
            return True

        def cancel(self, *_args, **_kwargs):
            return True

    async def fake_run_agent_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FirstResult()
        pytest.fail("the SDK must not resume after lifecycle prepare failed")

    class Session:
        async def request_tool_approval(self, *_args, **_kwargs):
            return "yes"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            type("Hooks", (), {"tool_events": []})(),
            session=Session(),
            lifecycle_sink=InconsistentLifecycleSink(),
            run_id="run_1",
            task_id="task_1",
        )
    )

    assert calls == 1
    assert result.final_output
