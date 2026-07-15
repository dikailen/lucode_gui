from __future__ import annotations

import pytest

from runtime.recovery.journal import RunJournal
from runtime.tools.registry import CORE_SERVER_METADATA, TOOL_SIDE_EFFECT_CLASSES, classify_tool_side_effect


def test_every_core_tool_server_declares_a_recovery_side_effect_class():
    assert CORE_SERVER_METADATA
    assert {
        metadata.get("side_effects")
        for metadata in CORE_SERVER_METADATA.values()
    }.issubset(TOOL_SIDE_EFFECT_CLASSES)
    assert all(metadata.get("side_effects") for metadata in CORE_SERVER_METADATA.values())


@pytest.mark.parametrize(
    ("tool_name", "server_id", "expected"),
    [
        ("desktop_browser.browser_get_page_summary", "desktop_browser", "read_only"),
        ("desktop_browser.browser_submit_form", "desktop_browser", "non_idempotent"),
        ("project_filesystem_readonly.read_file", "project_filesystem_readonly", "read_only"),
        ("command_runner.run_command", "command_runner", "unknown"),
        ("private_crm.update_record", "private_crm", "non_idempotent"),
        ("private_docs.fetch", "private_docs", "read_only"),
    ],
)
def test_tool_side_effect_classifier_uses_tool_and_manifest_context(tool_name, server_id, expected):
    metadata = {
        "private_crm": {"side_effects": "non_idempotent", "trusted": True},
        "private_docs": {"side_effects": "read_only", "trusted": True},
    }.get(server_id)

    assert classify_tool_side_effect(tool_name, server_id=server_id, metadata=metadata) == expected


def test_untrusted_external_manifest_cannot_declare_a_tool_readonly():
    assert (
        classify_tool_side_effect(
            "private_crm.fetch_customer",
            server_id="private_crm",
            metadata={"side_effects": "read_only", "trusted": False},
        )
        == "unknown"
    )


def test_untrusted_external_tool_cannot_use_a_readonly_sounding_name():
    assert (
        classify_tool_side_effect(
            "private_crm.read_file",
            server_id="private_crm",
            metadata={"side_effects": "read_only", "trusted": False},
        )
        == "unknown"
    )


def test_trusted_external_manifest_overrides_a_readonly_sounding_tool_name():
    assert (
        classify_tool_side_effect(
            "private_crm.read_file",
            server_id="private_crm",
            metadata={"side_effects": "non_idempotent", "trusted": True},
        )
        == "non_idempotent"
    )


def test_unproven_tool_name_cannot_infer_a_core_readonly_capability():
    assert classify_tool_side_effect("desktop_browser.browser_get_page_summary") == "unknown"


def test_journal_persists_a_prepared_dispatch_completed_lifecycle(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")

    prepared = journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        tool_name="project_filesystem_readonly.read_file",
        arguments_hash="hash_a",
        side_effect_class="read_only",
    )
    dispatched = journal.mark_tool_invocation_dispatched("invocation_1")
    completed = journal.complete_tool_invocation("invocation_1", evidence_ref="evidence:file_a")

    assert prepared["status"] == "prepared"
    assert dispatched["status"] == "dispatched"
    assert completed["status"] == "completed"
    assert completed["evidence_ref"] == "evidence:file_a"


def test_journal_rejects_a_lifecycle_transition_that_skips_dispatch(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        tool_name="workspace_edit.write_file",
        arguments_hash="hash_a",
        side_effect_class="non_idempotent",
    )

    with pytest.raises(ValueError, match="prepared.*completed"):
        journal.complete_tool_invocation("invocation_1")


def test_journal_rejects_an_invocation_id_reused_with_tampered_arguments(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run_1", session_id="session_1", status="running")
    journal.prepare_tool_invocation(
        invocation_id="invocation_1",
        run_id="run_1",
        task_id="task_1",
        attempt=1,
        tool_name="workspace_edit.write_file",
        arguments_hash="hash_a",
        side_effect_class="non_idempotent",
    )

    with pytest.raises(ValueError, match="immutable fields"):
        journal.prepare_tool_invocation(
            invocation_id="invocation_1",
            run_id="run_1",
            task_id="task_1",
            attempt=1,
            tool_name="workspace_edit.write_file",
            arguments_hash="hash_b",
            side_effect_class="non_idempotent",
        )
