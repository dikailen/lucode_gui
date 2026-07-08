from runtime.context.token_counter import context_window_for_model, estimate_tokens


def test_estimate_tokens_handles_empty_and_plain_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") >= 2
    assert estimate_tokens("hello world") < 10


def test_estimate_tokens_counts_cjk_more_conservatively_than_ascii_chars():
    ascii_text = "a" * 40
    cjk_text = "上下文取舍策略"

    assert estimate_tokens(ascii_text) < len(ascii_text)
    assert estimate_tokens(cjk_text) >= len(cjk_text)


def test_context_window_uses_known_model_metadata_keys():
    assert context_window_for_model({"context_window_tokens": 128000}) == 128000
    assert context_window_for_model({"context_length": "65536"}) == 65536
    assert context_window_for_model({"max_context_tokens": 32768}) == 32768
    assert context_window_for_model({"max_input_tokens": 16000}) == 16000


def test_context_window_falls_back_to_conservative_default():
    assert context_window_for_model(None) == 32768
    assert context_window_for_model({}) == 32768
    assert context_window_for_model({"context_window_tokens": 0}) == 32768
