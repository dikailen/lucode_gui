from __future__ import annotations

from runtime.agent.tool_invocation_guard import ToolInvocationGuard


def test_once_approval_token_is_bound_to_canonical_arguments():
    guard = ToolInvocationGuard()
    first = guard.build_invocation(
        "workspace_edit.write_file",
        '{"path":"runtime/a.py","content":"x"}',
    )
    reordered = guard.build_invocation(
        "workspace_edit.write_file",
        '{"content":"x","path":"runtime/a.py"}',
    )

    guard.mark_once_approved(first)

    assert guard.once_approval_was_used(reordered) is True
    assert first.approval_token.arguments_hash == reordered.approval_token.arguments_hash


def test_once_approval_token_does_not_apply_to_different_arguments():
    guard = ToolInvocationGuard()
    first = guard.build_invocation(
        "workspace_edit.write_file",
        '{"path":"runtime/a.py","content":"x"}',
    )
    second = guard.build_invocation(
        "workspace_edit.write_file",
        '{"path":"runtime/b.py","content":"x"}',
    )

    guard.mark_once_approved(first)

    assert guard.once_approval_was_used(second) is False
    assert first.approval_token.token_id != second.approval_token.token_id
