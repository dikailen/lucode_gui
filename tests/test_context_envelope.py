from __future__ import annotations


def test_context_envelope_keeps_dehydrated_browser_data_and_audit_refs_only():
    from runtime.context.envelope import ContextEnvelope
    from runtime.context.tool_dehydration import dehydrate_tool_result

    dehydrated = dehydrate_tool_result(
        tool="desktop_browser",
        action="summary",
        raw_result={"url": "https://example.com", "title": "Dashboard", "dom": "<html>secret</html>"},
        evidence_ref="evidence:browser:1",
        raw_artifact_ref="artifact:browser:1",
    )
    envelope = ContextEnvelope.from_tool_result(tool="desktop_browser", action="summary", result=dehydrated)

    assert envelope.privacy_level == "local_only"
    assert envelope.evidence_refs == ("evidence:browser:1",)
    assert envelope.raw_artifact_ref == "artifact:browser:1"
    assert "<html>" not in envelope.dehydrated_summary
    assert envelope.token_budget > 0


def test_run_context_observes_tool_envelope_without_rendering_raw_refs_into_prompt(tmp_path):
    from runtime.execution.run_context import RunContextStore

    store = RunContextStore(tmp_path)
    store.record_tool_output(
        tool="terminal",
        action="run_command",
        summary="command completed",
        task_id="task-1",
        evidence_ref="evidence:terminal:1",
        raw_artifact_ref="artifact:terminal:1",
    )

    envelope = store.context_envelopes()[0]

    assert envelope.evidence_refs == ("evidence:terminal:1",)
    assert envelope.raw_artifact_ref == "artifact:terminal:1"
    assert "artifact:terminal:1" not in store.render_for_task()


def test_retrieval_policy_keeps_current_state_and_evidence_out_of_cold_storage():
    from runtime.context.envelope import ContextEnvelope
    from runtime.context.retrieval_policy import decide_retrieval_policy

    current_task = ContextEnvelope(source_type="task_state", dehydrated_summary="current task")
    accepted_evidence = ContextEnvelope(source_type="accepted_evidence", dehydrated_summary="accepted")
    skill_doc = ContextEnvelope(source_type="skill_metadata", dehydrated_summary="UI skill")
    terminal = ContextEnvelope(source_type="terminal_output", privacy_level="local_only", dehydrated_summary="local output")

    assert decide_retrieval_policy(current_task).allowed_for_embedding is False
    assert decide_retrieval_policy(accepted_evidence).allowed_for_embedding is False
    assert decide_retrieval_policy(skill_doc).allowed_for_embedding is True
    assert decide_retrieval_policy(terminal).allowed_for_embedding is False


def test_cold_store_admission_only_reports_policy_without_persisting_content():
    from runtime.context.cold_store import admit_to_cold_store
    from runtime.context.envelope import ContextEnvelope

    admission = admit_to_cold_store(ContextEnvelope(source_type="failure_lesson", dehydrated_summary="avoid stale index"))

    assert admission.accepted is True
    assert admission.reason == "cold_context_allowed"
