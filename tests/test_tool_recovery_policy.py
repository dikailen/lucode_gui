from __future__ import annotations

from runtime.recovery.policy import evaluate_tool_recovery, recovery_blocked_items_for_tool_invocations


def test_prepared_readonly_invocation_can_start_a_new_safe_attempt():
    decision = evaluate_tool_recovery(
        {
            "status": "prepared",
            "side_effect_class": "read_only",
            "arguments_hash": "hash_a",
        },
        arguments_hash="hash_a",
    )

    assert decision.action == "retry"
    assert decision.requires_new_approval is False


def test_prepared_mutation_requires_replan_and_new_approval():
    decision = evaluate_tool_recovery(
        {
            "status": "prepared",
            "side_effect_class": "non_idempotent",
            "arguments_hash": "hash_a",
        },
        arguments_hash="hash_a",
    )

    assert decision.action == "replan"
    assert decision.requires_new_approval is True


def test_tampered_recovery_arguments_cannot_reuse_the_old_invocation():
    decision = evaluate_tool_recovery(
        {
            "status": "prepared",
            "side_effect_class": "read_only",
            "arguments_hash": "hash_a",
        },
        arguments_hash="hash_b",
    )

    assert decision.action == "replan"
    assert decision.reason_code == "arguments_hash_mismatch"
    assert decision.requires_new_approval is True


def test_dispatched_and_unknown_invocations_become_recovery_blockers():
    blocked = recovery_blocked_items_for_tool_invocations(
        [
            {
                "task_id": "browser_task",
                "status": "dispatched",
                "side_effect_class": "non_idempotent",
            },
            {
                "task_id": "terminal_task",
                "status": "unknown",
                "side_effect_class": "unknown",
            },
        ]
    )

    assert blocked == [
        {"task_id": "browser_task", "reason_code": "tool_dispatched_unknown"},
        {"task_id": "terminal_task", "reason_code": "tool_unknown_side_effect"},
    ]
