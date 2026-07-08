from runtime.context.budget import decide_context_budget


def test_budget_modes_follow_pressure_thresholds():
    window = 10_000

    assert decide_context_budget(5_900, window).mode == "normal"
    assert decide_context_budget(6_000, window).mode == "warning"
    assert decide_context_budget(7_200, window).mode == "compress"
    assert decide_context_budget(8_800, window).mode == "emergency"
    assert decide_context_budget(9_500, window).mode == "hard_limit"


def test_budget_shrinks_recent_turns_as_pressure_rises():
    window = 10_000

    normal = decide_context_budget(1_000, window)
    compress = decide_context_budget(7_500, window)
    emergency = decide_context_budget(9_000, window)

    assert normal.keep_messages == 6
    assert compress.keep_messages < normal.keep_messages
    assert emergency.keep_messages < compress.keep_messages
    assert emergency.max_summary_chars < normal.max_summary_chars


def test_large_tool_schema_reduces_available_history_even_before_hard_limit():
    decision = decide_context_budget(
        estimated_input_tokens=4_000,
        context_window_tokens=10_000,
        tool_schema_tokens=3_500,
        evidence_tokens=500,
    )

    assert decision.mode in {"compress", "emergency", "hard_limit"}
    assert decision.triggered is True
    assert decision.keep_messages <= 4
    assert "tool_schema_pressure" in decision.reasons


def test_budget_never_allows_less_than_two_recent_messages():
    decision = decide_context_budget(50_000, 10_000)

    assert decision.mode == "hard_limit"
    assert decision.keep_messages == 2
    assert decision.triggered is True
