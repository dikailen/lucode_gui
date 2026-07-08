from runtime.context.ledger import ContextLedgerInput, build_context_ledger


def _messages(count: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append(
            {
                "role": role,
                "content": f"old message {index} about background task {index}",
            }
        )
    return messages


def test_ledger_preserves_current_input_verbatim_under_pressure():
    current_input = "请只回答我这轮问题，不要继续旧任务：你好"
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="s1",
            current_input=current_input,
            messages=_messages(12),
            existing_summary="Earlier summary that mentions opening a browser.",
            project_experience=["Do not let old browser tasks pollute routing."],
            tool_schema_tokens=8_000,
            evidence_tokens=1_000,
            context_window_tokens=10_000,
        )
    )

    assert current_input in result.run_input
    assert result.run_input.rstrip().endswith(current_input)
    assert result.mode == "hard_limit"
    assert result.triggered is True
    assert len(result.recent_turns) == 2
    assert "history_background" in result.run_input
    assert "current_user_request" in result.run_input


def test_ledger_uses_dynamic_window_for_normal_budget():
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="s1",
            current_input="Continue with the latest answer.",
            messages=_messages(8),
            existing_summary="Short summary.",
            context_window_tokens=80_000,
        )
    )

    assert result.mode == "normal"
    assert result.triggered is False
    assert len(result.recent_turns) == 6
    assert "old message 7" in result.run_input
    assert "old message 2" in result.run_input


def test_ledger_truncates_summary_and_inline_files_before_current_input():
    current_input = "Final instruction must remain complete."
    long_summary = "summary-" + ("x" * 20_000)
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="s1",
            current_input=current_input,
            messages=_messages(4),
            existing_summary=long_summary,
            inline_files=[
                {
                    "path": "large.txt",
                    "content": "file-" + ("y" * 20_000),
                }
            ],
            tool_schema_tokens=8_500,
            context_window_tokens=10_000,
        )
    )

    assert current_input in result.run_input
    assert result.run_input.rstrip().endswith(current_input)
    assert len(result.session_summary) <= 900
    assert "summary_truncated" in result.dropped_sections
    assert "inline_files_truncated" in result.dropped_sections


def test_ledger_estimates_final_input_tokens():
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="s1",
            current_input="hello",
            messages=_messages(2),
            context_window_tokens=32_768,
        )
    )

    assert result.estimated_input_tokens > 0
    assert result.context_window_tokens == 32_768


def test_ledger_redacts_sensitive_background_without_changing_current_input():
    current_input = "Keep this exact current request."
    result = build_context_ledger(
        ContextLedgerInput(
            session_id="s1",
            current_input=current_input,
            messages=[
                {"role": "user", "content": "old token=abc123456 should be hidden"},
                {"role": "assistant", "content": "old key sk-testsecret123456 should be hidden"},
            ],
            existing_summary="previous api_key=secret-value",
            inline_files=[{"path": ".env", "content": "PASSWORD=super-secret"}],
            context_window_tokens=32_768,
        )
    )

    assert current_input in result.run_input
    assert result.run_input.rstrip().endswith(current_input)
    assert "abc123456" not in result.run_input
    assert "sk-testsecret123456" not in result.run_input
    assert "secret-value" not in result.run_input
    assert "super-secret" not in result.run_input
    assert "[redacted]" in result.run_input
