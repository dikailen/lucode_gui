from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION
from runtime.recovery.coordinator import RecoveryCoordinator
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.journal import RunJournal
from runtime.recovery.policy import evaluate_recovery_plan, recovery_compatibility_for_plan
from runtime.recovery.tool_lifecycle import JournalToolLifecycleSink
from runtime.hooks.task_scope import TaskScopedHooks


def _prepared_file_write(journal: RunJournal) -> None:
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="write_task",
        attempt=1,
        tool_name="workspace_edit.write_file",
        arguments_hash="arguments_hash",
        side_effect_class="non_idempotent",
    )


def test_journal_persists_an_immutable_file_sha256_postcondition(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    expected_sha256 = hashlib.sha256(b"verified content").hexdigest()

    stored = journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": expected_sha256,
        },
    )

    assert stored["invocation_id"] == "invocation_1"
    assert stored["status"] == "pending"
    assert stored["expectation"] == {
        "path": "notes.txt",
        "expected_sha256": expected_sha256,
    }


def test_journal_rejects_a_tampered_postcondition_for_the_same_invocation(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={"path": "notes.txt", "expected_sha256": "a" * 64},
    )

    with pytest.raises(ValueError, match="immutable"):
        journal.record_tool_postcondition(
            invocation_id="invocation_1",
            kind="workspace_file_sha256.v1",
            expectation={"path": "notes.txt", "expected_sha256": "b" * 64},
        )


def test_journal_rejects_a_postcondition_kind_that_does_not_belong_to_the_tool(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_submit",
        run_id="run_1",
        task_id="browser_task",
        attempt=1,
        tool_name="desktop_browser.browser_submit_form",
        arguments_hash="arguments_hash",
        side_effect_class="non_idempotent",
    )

    with pytest.raises(ValueError, match="not allowed"):
        journal.record_tool_postcondition(
            invocation_id="invocation_submit",
            kind="browser_navigation_url_sha256.v1",
            expectation={"tab_id": "tab_9", "expected_url_sha256": "a" * 64},
        )


def test_file_postcondition_reconciler_verifies_a_matching_unknown_write(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")
    content = "verified content"
    (tmp_path / "notes.txt").write_text(content, encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )

    from runtime.recovery.postconditions import reconcile_pending_tool_postconditions

    reconciled = reconcile_pending_tool_postconditions(journal, run_id="run_1")
    expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    assert reconciled == [
        {
            "invocation_id": "invocation_1",
            "task_id": "write_task",
            "kind": "workspace_file_sha256.v1",
            "status": "verified",
            "evidence_ref": f"postcondition:invocation_1:{expected_sha256[:16]}",
            "observed": {"path": "notes.txt", "sha256": expected_sha256},
        }
    ]
    assert journal.tool_postcondition("invocation_1")["status"] == "verified"


def test_file_postcondition_reconciler_marks_a_hash_mismatch_without_replaying(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")
    (tmp_path / "notes.txt").write_text("different content", encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(b"verified content").hexdigest(),
        },
    )

    from runtime.recovery.postconditions import reconcile_pending_tool_postconditions

    reconciled = reconcile_pending_tool_postconditions(journal, run_id="run_1")

    assert reconciled[0]["status"] == "mismatch"
    assert journal.tool_postcondition("invocation_1")["status"] == "mismatch"
    assert journal.tool_invocation("invocation_1")["status"] == "unknown"


def test_browser_navigation_postcondition_verifies_only_an_existing_tab_and_never_persists_the_url(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="browser_task",
        attempt=1,
        tool_name="desktop_browser.browser_navigate",
        arguments_hash="arguments_hash",
        side_effect_class="read_only",
    )
    target_url = "https://example.test/reports?temporary_secret=never-store-this"

    from runtime.recovery.postconditions import (
        derive_tool_postcondition,
        reconcile_pending_tool_postconditions,
    )

    derived = derive_tool_postcondition(
        "desktop_browser.browser_navigate",
        '{"tab_id":"tab_9","url":"https://example.test/reports?temporary_secret=never-store-this"}',
        workspace_root=tmp_path,
    )

    assert derived is not None
    kind, expectation = derived
    assert kind == "browser_navigation_url_sha256.v1"
    assert expectation["tab_id"] == "tab_9"
    assert expectation["expected_url_sha256"] == hashlib.sha256(target_url.encode("utf-8")).hexdigest()
    assert target_url not in str(expectation)

    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind=kind,
        expectation=expectation,
    )
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")

    reconciled = reconcile_pending_tool_postconditions(
        journal,
        run_id="run_1",
        browser_tabs_reader=lambda: {
            "activeTabId": "tab_9",
            "tabs": [{"tabId": "tab_9", "url": target_url}],
        },
    )

    expected_hash = hashlib.sha256(target_url.encode("utf-8")).hexdigest()
    assert reconciled == [
        {
            "invocation_id": "invocation_1",
            "task_id": "browser_task",
            "kind": "browser_navigation_url_sha256.v1",
            "status": "verified",
            "evidence_ref": f"postcondition:invocation_1:{expected_hash[:16]}",
            "observed": {"tab_id": "tab_9", "url_sha256": expected_hash},
        }
    ]
    stored = journal.tool_postcondition("invocation_1")
    assert stored is not None
    assert target_url not in str(stored)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("desktop_browser.browser_navigate", '{"url":"https://example.test/reports"}'),
        ("desktop_browser.browser_navigate", '{"tab_id":"tab_9","url":"https://"}'),
        ("desktop_browser.browser_click_element", '{"tab_id":"tab_9","selector":"#submit"}'),
        ("desktop_browser.browser_set_input_value", '{"tab_id":"tab_9","selector":"#name","value":"Alice"}'),
        ("desktop_browser.browser_submit_form", '{"tab_id":"tab_9","selector":"form"}'),
    ],
)
def test_browser_mutations_and_ambiguous_navigation_never_receive_a_recovery_postcondition(tmp_path, tool_name, arguments):
    from runtime.recovery.postconditions import derive_tool_postcondition

    assert derive_tool_postcondition(tool_name, arguments, workspace_root=tmp_path) is None


def test_browser_navigation_url_mismatch_stays_unknown_without_replay(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="browser_task",
        attempt=1,
        tool_name="desktop_browser.browser_navigate",
        arguments_hash="arguments_hash",
        side_effect_class="read_only",
    )
    target_url = "https://example.test/reports"
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="browser_navigation_url_sha256.v1",
        expectation={
            "tab_id": "tab_9",
            "expected_url_sha256": hashlib.sha256(target_url.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")

    from runtime.recovery.postconditions import reconcile_pending_tool_postconditions

    reconciled = reconcile_pending_tool_postconditions(
        journal,
        run_id="run_1",
        browser_tabs_reader=lambda: {
            "activeTabId": "tab_9",
            "tabs": [{"tabId": "tab_9", "url": "https://example.test/other"}],
        },
    )

    assert reconciled[0]["status"] == "mismatch"
    assert journal.tool_postcondition("invocation_1")["status"] == "mismatch"
    assert journal.tool_invocation("invocation_1")["status"] == "unknown"


def test_browser_postcondition_probe_refuses_a_non_loopback_bridge(monkeypatch):
    from runtime.recovery import postconditions

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "https://example.test")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "not-for-network")

    def unexpected_network_call(*_args, **_kwargs):
        raise AssertionError("postcondition probe must not send its bridge token off-host")

    monkeypatch.setattr(postconditions, "urlopen", unexpected_network_call)

    with pytest.raises(OSError, match="loopback"):
        postconditions._read_browser_tabs_from_env()


def test_browser_postcondition_probe_reads_an_authenticated_loopback_tab_state(monkeypatch):
    from runtime.recovery import postconditions

    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - HTTPServer callback name
            received["path"] = self.path
            received["authorization"] = str(self.headers.get("Authorization") or "")
            payload = json.dumps({"activeTabId": "tab_9", "tabs": [{"tabId": "tab_9", "url": "https://example.test"}]}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "loopback-token")

        state = postconditions._read_browser_tabs_from_env()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert state == {"activeTabId": "tab_9", "tabs": [{"tabId": "tab_9", "url": "https://example.test"}]}
    assert received == {"path": "/tabs", "authorization": "Bearer loopback-token"}


def test_verified_browser_navigation_reuses_only_event_bound_recovery_evidence(tmp_path, monkeypatch):
    task = PlannedTask(
        id="browser_task",
        title="inspect the report page",
        instruction="continue the earlier browser navigation",
        skill_id="browser_operator",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="browser_task",
        attempt=1,
        tool_name="desktop_browser.browser_navigate",
        arguments_hash="arguments_hash",
        side_effect_class="read_only",
    )
    checkpoint = journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
    )
    target_url = "https://example.test/reports"
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="browser_navigation_url_sha256.v1",
        expectation={
            "tab_id": "tab_9",
            "expected_url_sha256": hashlib.sha256(target_url.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")
    monkeypatch.setattr(
        "runtime.recovery.postconditions._read_browser_tabs_from_env",
        lambda: {"activeTabId": "tab_9", "tabs": [{"tabId": "tab_9", "url": target_url}]},
    )

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    assert journal.tool_invocation("invocation_1")["status"] == "reconciled"
    assert claim.envelope.reconciled_items == (
        {
            "invocation_id": "invocation_1",
            "task_id": "browser_task",
            "tool_name": "desktop_browser.browser_navigate",
            "kind": "browser_navigation_url_sha256.v1",
            "evidence_ref": "postcondition:invocation_1:"
            + hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:16],
            "source_run_id": "run_1",
            "checkpoint_id": checkpoint.checkpoint_id,
            "event_seq": 1,
            "observed": {
                "tab_id": "tab_9",
                "url_sha256": hashlib.sha256(target_url.encode("utf-8")).hexdigest(),
            },
        },
    )

    decision = evaluate_recovery_plan(
        claim.envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.reusable_task_ids == ("browser_task",)
    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == ()


def test_sdk_started_browser_navigation_can_be_reconciled_after_an_interruption(tmp_path, monkeypatch):
    task = PlannedTask(
        id="browser_task",
        title="inspect the report page",
        instruction="continue the earlier browser navigation",
        skill_id="browser_operator",
        model="model-a",
        mcp=["desktop_browser"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
    )
    sink = JournalToolLifecycleSink(journal, run_id="run_1")
    hooks = TaskScopedHooks(
        type("Hooks", (), {"tool_events": []})(),
        task_id="browser_task",
        tool_lifecycle_sink=sink,
    )
    target_url = "https://example.test/reports"
    context = type(
        "ToolContext",
        (),
        {
            "tool_name": "desktop_browser.browser_navigate",
            "tool_arguments": '{"tab_id":"tab_9","url":"https://example.test/reports"}',
        },
    )()

    import asyncio

    asyncio.run(hooks.on_tool_start(context, object(), type("Tool", (), {"name": "browser_navigate"})()))
    monkeypatch.setattr(
        "runtime.recovery.postconditions._read_browser_tabs_from_env",
        lambda: {"activeTabId": "tab_9", "tabs": [{"tabId": "tab_9", "url": target_url}]},
    )

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")
    assert claim is not None
    decision = evaluate_recovery_plan(
        claim.envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert journal.tool_invocations_for_run("run_1")[0]["status"] == "reconciled"
    assert decision.reusable_task_ids == ("browser_task",)


def test_verified_file_postcondition_reuses_only_event_bound_recovery_evidence(tmp_path):
    task = PlannedTask(
        id="write_task",
        title="write notes",
        instruction="write the requested file",
        skill_id="code_engineer",
        model="model-a",
        mcp=["workspace_edit"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
    )
    content = "verified content"
    (tmp_path / "notes.txt").write_text(content, encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    assert journal.tool_invocation("invocation_1")["status"] == "reconciled"
    assert len(claim.envelope.reconciled_items) == 1
    reconciled = claim.envelope.reconciled_items[0]
    assert reconciled["invocation_id"] == "invocation_1"
    assert reconciled["task_id"] == "write_task"
    assert reconciled["checkpoint_id"] == claim.envelope.checkpoint_id
    assert reconciled["event_seq"] > 0
    assert claim.envelope.blocked_items == ()

    decision = evaluate_recovery_plan(
        claim.envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.reusable_task_ids == ("write_task",)
    assert decision.executable_task_ids == ()
    assert decision.blocked_task_ids == ()
    assert "postcondition" in decision.reused_outputs["write_task"]


def test_verified_postcondition_recovery_claim_is_idempotent_after_a_retry(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )
    content = "verified content"
    (tmp_path / "notes.txt").write_text(content, encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    first = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert first is not None
    assert coordinator.release_claim(first) is True
    second = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_3")

    assert second is not None
    events = [event for event in journal.events_for_run("run_1") if event.event_type == "tool.postcondition_verified"]
    assert len(events) == 1
    assert second.envelope.reconciled_items[0]["event_seq"] == events[0].seq


def test_postcondition_event_bind_retry_reuses_an_existing_unbound_durable_event(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.write_checkpoint(
        run_id="run_1",
        kind="plan.accepted",
        state={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility={"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION},
    )
    content = "verified content"
    (tmp_path / "notes.txt").write_text(content, encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()
    from runtime.recovery.postconditions import reconcile_pending_tool_postconditions

    reconcile_pending_tool_postconditions(journal, run_id="run_1")
    record = journal.verified_tool_postconditions_for_run("run_1")[0]
    first_event = journal.append_event(
        run_id="run_1",
        session_id="session_1",
        event_type="tool.postcondition_verified",
        payload={
            "invocation_id": "invocation_1",
            "task_id": "write_task",
            "kind": record["kind"],
            "evidence_ref": record["evidence_ref"],
            "observed": record["observed"],
        },
    )

    retry = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_3")

    assert retry is not None
    events = [event for event in journal.events_for_run("run_1") if event.event_type == "tool.postcondition_verified"]
    assert len(events) == 1
    assert retry.envelope.reconciled_items[0]["event_seq"] == first_event.seq


def test_concurrent_postcondition_event_binding_creates_one_durable_event(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    content = "verified content"
    expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={"path": "notes.txt", "expected_sha256": expected_sha256},
    )
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")
    journal.resolve_tool_postcondition(
        invocation_id="invocation_1",
        status="verified",
        evidence_ref=f"postcondition:invocation_1:{expected_sha256[:16]}",
        observed={"path": "notes.txt", "sha256": expected_sha256},
    )

    barrier = threading.Barrier(2)

    def append_and_bind():
        barrier.wait(timeout=5)
        return journal.append_and_bind_verified_tool_postcondition_event(
            invocation_id="invocation_1",
            run_id="run_1",
            session_id="session_1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        events = list(executor.map(lambda _index: append_and_bind(), range(2)))

    assert {event.seq for event in events} == {1}
    assert journal.tool_postcondition("invocation_1")["event_seq"] == 1
    assert [event.seq for event in journal.events_for_run("run_1") if event.event_type == "tool.postcondition_verified"] == [1]


def test_atomic_postcondition_binding_rejects_an_event_from_another_session(tmp_path):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    expected_sha256 = hashlib.sha256(b"verified content").hexdigest()
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={"path": "notes.txt", "expected_sha256": expected_sha256},
    )
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")
    journal.resolve_tool_postcondition(
        invocation_id="invocation_1",
        status="verified",
        evidence_ref=f"postcondition:invocation_1:{expected_sha256[:16]}",
        observed={"path": "notes.txt", "sha256": expected_sha256},
    )
    journal.append_event(
        run_id="run_1",
        session_id="other_session",
        event_type="tool.postcondition_verified",
        payload={
            "invocation_id": "invocation_1",
            "task_id": "write_task",
            "kind": "workspace_file_sha256.v1",
            "evidence_ref": f"postcondition:invocation_1:{expected_sha256[:16]}",
            "observed": {"path": "notes.txt", "sha256": expected_sha256},
        },
    )

    with pytest.raises(ValueError, match="session"):
        journal.append_and_bind_verified_tool_postcondition_event(
            invocation_id="invocation_1",
            run_id="run_1",
            session_id="session_1",
        )

    assert journal.tool_postcondition("invocation_1")["event_seq"] == 0


def test_recovery_coordinator_uses_atomic_postcondition_event_binding(tmp_path, monkeypatch):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    content = "verified content"
    (tmp_path / "notes.txt").write_text(content, encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={
            "path": "notes.txt",
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    journal.mark_tool_invocation_dispatched("invocation_1")

    coordinator = RecoveryCoordinator(journal)
    coordinator.scan_startup()

    def legacy_path_used(*_args, **_kwargs):
        pytest.fail("recovery coordinator must use atomic postcondition event binding")

    monkeypatch.setattr(journal, "append_event", legacy_path_used)

    claim = coordinator.claim_for_message(session_id="session_1", owner_id="runtime_2")

    assert claim is not None
    assert claim.envelope.reconciled_items[0]["event_seq"] == 1


def test_legacy_two_step_postcondition_event_apis_are_not_exposed(tmp_path):
    journal = RunJournal(tmp_path)

    assert not hasattr(journal, "bind_tool_postcondition_event")
    assert not hasattr(journal, "find_tool_postcondition_event_seq")


def test_file_postcondition_without_a_source_checkpoint_cannot_be_reused():
    task = PlannedTask(
        id="write_task",
        title="write notes",
        instruction="write the requested file",
        skill_id="code_engineer",
        model="model-a",
        mcp=["workspace_edit"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    envelope = RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="",
        checkpoint_kind="",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "checkpoint unavailable",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
        reconciled_items=(
            {
                "invocation_id": "invocation_1",
                "task_id": "write_task",
                "tool_name": "workspace_edit.write_file",
                "kind": "workspace_file_sha256.v1",
                "evidence_ref": "postcondition:invocation_1:abc",
                "source_run_id": "run_1",
                "checkpoint_id": "",
                "event_seq": 1,
                "observed": {"path": "notes.txt", "sha256": "a" * 64},
            },
        ),
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.reusable_task_ids == ()
    assert decision.blocked_task_ids == ("write_task",)


def test_file_postcondition_cannot_unlock_a_non_workspace_edit_task():
    task = PlannedTask(
        id="terminal_task",
        title="mutate through terminal",
        instruction="run the prior command",
        skill_id="code_engineer",
        model="model-a",
        mcp=["command_runner"],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    envelope = RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
        reconciled_items=(
            {
                "invocation_id": "invocation_1",
                "task_id": "terminal_task",
                "tool_name": "workspace_edit.write_file",
                "kind": "workspace_file_sha256.v1",
                "evidence_ref": "postcondition:invocation_1:0123456789abcdef",
                "source_run_id": "run_1",
                "checkpoint_id": "checkpoint_1",
                "event_seq": 1,
                "observed": {"path": "notes.txt", "sha256": "a" * 64},
            },
        ),
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.reusable_task_ids == ()
    assert decision.blocked_task_ids == ("terminal_task",)


@pytest.mark.parametrize(
    ("task_id", "mcp_id", "tool_name", "kind"),
    [
        ("browser_task", "desktop_browser", "desktop_browser.browser_submit_form", "browser_url.v1"),
        ("terminal_task", "command_runner", "command_runner.run_command", "terminal_postcondition.v1"),
        ("mcp_task", "private_crm", "private_crm.update_record", "mcp_read_after_write.v1"),
    ],
)
def test_unsupported_postconditions_never_unlock_external_mutations(task_id, mcp_id, tool_name, kind):
    task = PlannedTask(
        id=task_id,
        title="external mutation",
        instruction="continue the prior action",
        skill_id="code_engineer",
        model="model-a",
        mcp=[mcp_id],
    )
    plan = PlannerResult(
        route_type="single_agent",
        reason="continue",
        refined_request="continue",
        tasks=[task],
    )
    envelope = RecoveryEnvelope(
        source_run_id="run_1",
        session_id="session_1",
        checkpoint_id="checkpoint_1",
        checkpoint_kind="plan.accepted",
        checkpoint={
            "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
            "route_type": "single_agent",
            "reason": "previous task",
            "tasks": [],
            "accepted_evidence": {"mode": "observe", "claims": [], "evidence": [], "blocked_claims": []},
            "blocked_items": [],
        },
        compatibility=recovery_compatibility_for_plan(plan),
        reconciled_items=(
            {
                "invocation_id": "invocation_1",
                "task_id": task_id,
                "tool_name": tool_name,
                "kind": kind,
                "evidence_ref": "postcondition:invocation_1:0123456789abcdef",
                "source_run_id": "run_1",
                "checkpoint_id": "checkpoint_1",
                "event_seq": 1,
                "observed": {"path": "notes.txt", "sha256": "a" * 64},
            },
        ),
    )

    decision = evaluate_recovery_plan(
        envelope,
        plan,
        planner_disposition="continue_safe",
        current_compatibility=recovery_compatibility_for_plan(plan),
    )

    assert decision.reusable_task_ids == ()
    assert decision.blocked_task_ids == (task_id,)


def test_postcondition_read_failure_becomes_a_mismatch_without_replaying(tmp_path, monkeypatch):
    journal = RunJournal(tmp_path)
    _prepared_file_write(journal)
    journal.mark_tool_invocation_dispatched("invocation_1")
    journal.mark_dispatched_tool_invocations_unknown("run_1")
    (tmp_path / "notes.txt").write_text("content that cannot be read", encoding="utf-8")
    journal.record_tool_postcondition(
        invocation_id="invocation_1",
        kind="workspace_file_sha256.v1",
        expectation={"path": "notes.txt", "expected_sha256": "a" * 64},
    )

    from runtime.recovery import postconditions

    def unreadable(_path):
        raise OSError("file is unavailable")

    monkeypatch.setattr(postconditions, "_sha256_file", unreadable)
    reconciled = postconditions.reconcile_pending_tool_postconditions(journal, run_id="run_1")

    assert reconciled[0]["status"] == "mismatch"
    assert journal.tool_postcondition("invocation_1")["status"] == "mismatch"
    assert journal.tool_invocation("invocation_1")["status"] == "unknown"
