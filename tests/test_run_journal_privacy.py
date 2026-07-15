from __future__ import annotations

import json

from runtime.recovery.journal import RunJournal


def test_run_journal_redacts_secrets_and_omits_raw_dom_terminal_and_base64_payloads(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-1", session_id="session-1", status="running")

    raw_token = "super-secret-token"
    raw_cookie = "session=private-cookie"
    raw_dom = "<html><body>" + ("private-dom " * 200) + "</body></html>"
    raw_stdout = "private terminal output " * 200
    raw_image = "data:image/png;base64," + ("A" * 600)
    event = journal.append_event(
        run_id="run-1",
        session_id="session-1",
        event_type="tool.completed",
        payload={
            "api_key": raw_token,
            "nested": {"Cookie": raw_cookie},
            "page_dom": raw_dom,
            "stdout": raw_stdout,
            "preview": raw_image,
        },
    )

    rendered = json.dumps(event.payload, ensure_ascii=False)
    assert raw_token not in rendered
    assert raw_cookie not in rendered
    assert raw_dom not in rendered
    assert raw_stdout not in rendered
    assert raw_image not in rendered
    assert "[redacted]" in rendered
    assert "[omitted:dom" in rendered
    assert "[omitted:terminal_output" in rendered
    assert "[omitted:base64" in rendered


def test_run_journal_sanitizes_checkpoint_state_before_calculating_checksum(tmp_path):
    journal = RunJournal(tmp_path)
    journal.create_run(run_id="run-1", session_id="session-1", status="running")

    checkpoint = journal.write_checkpoint(
        run_id="run-1",
        kind="tool.result",
        state={"authorization": "Bearer private", "result": "safe summary"},
        compatibility={"api_token": "private"},
    )
    loaded = journal.load_checkpoint(checkpoint.checkpoint_id)

    assert loaded.state["authorization"] == "[redacted]"
    assert loaded.compatibility["api_token"] == "[redacted]"
