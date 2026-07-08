from runtime.context.tool_dehydration import ToolDehydratedResult, dehydrate_tool_result


def test_browser_summary_keeps_page_identity_and_elements_without_full_dom():
    result = dehydrate_tool_result(
        tool="desktop_browser",
        action="browser_get_page_summary",
        raw_result={
            "url": "https://example.com/form",
            "title": "Example Form",
            "text": "A page with a customer form.",
            "elements": [
                {"selector": "#custname", "text": "Customer name", "tag": "input"},
                {"selector": "button[type=submit]", "text": "Submit", "tag": "button"},
            ],
            "dom": "<html>" + ("x" * 5000) + "</html>",
            "api_key": "secret-value",
        },
        evidence_ref="tool:browser:1",
        raw_artifact_ref="artifact:browser-dom:1",
    )

    assert isinstance(result, ToolDehydratedResult)
    assert "Example Form" in result.summary
    assert "https://example.com/form" in result.summary
    assert "#custname" in result.summary
    assert "secret-value" not in result.summary
    assert "<html>" not in result.summary
    assert result.key_fields["url"] == "https://example.com/form"
    assert result.key_fields["title"] == "Example Form"
    assert result.evidence_ref == "tool:browser:1"
    assert result.raw_artifact_ref == "artifact:browser-dom:1"
    assert "dom" in result.omitted_fields
    assert result.redacted is True


def test_command_output_keeps_command_returncode_and_tail_without_full_stdout():
    raw_stdout = "\n".join(f"line {index}" for index in range(200))
    result = dehydrate_tool_result(
        tool="terminal",
        action="run_command",
        raw_result={
            "command": "python -m pytest",
            "returncode": 1,
            "stdout": raw_stdout,
            "stderr": "FAILED test_example.py::test_x\nTraceback details",
        },
        evidence_ref="tool:terminal:2",
        raw_artifact_ref="artifact:terminal-output:2",
    )

    assert "python -m pytest" in result.summary
    assert "returncode=1" in result.summary
    assert "FAILED test_example.py::test_x" in result.summary
    assert "line 0" not in result.summary
    assert "line 199" in result.summary
    assert result.key_fields["returncode"] == 1
    assert result.evidence_ref == "tool:terminal:2"
    assert result.raw_artifact_ref == "artifact:terminal-output:2"
    assert "stdout" in result.omitted_fields


def test_mcp_json_keeps_business_fields_and_omits_protocol_noise():
    result = dehydrate_tool_result(
        tool="mcp.comfyui",
        action="list_models",
        raw_result={
            "jsonrpc": "2.0",
            "id": "123",
            "result": {
                "models": ["sdxl", "flux"],
                "status": "ok",
                "meta": {"trace": "x" * 2000},
            },
        },
        evidence_ref="tool:mcp:3",
        raw_artifact_ref="artifact:mcp-json:3",
    )

    assert "sdxl" in result.summary
    assert "flux" in result.summary
    assert "jsonrpc" not in result.summary
    assert "trace" not in result.summary
    assert result.key_fields["status"] == "ok"
    assert result.key_fields["models"] == ["sdxl", "flux"]
    assert result.evidence_ref == "tool:mcp:3"


def test_search_result_keeps_titles_urls_and_snippets():
    result = dehydrate_tool_result(
        tool="search",
        action="web_search",
        raw_result={
            "results": [
                {
                    "title": "Doc A",
                    "url": "https://docs.example/a",
                    "snippet": "Useful section A",
                    "raw_html": "<html>large</html>",
                },
                {
                    "title": "Doc B",
                    "url": "https://docs.example/b",
                    "snippet": "Useful section B",
                },
            ]
        },
        evidence_ref="tool:search:4",
        raw_artifact_ref="artifact:search:4",
    )

    assert "Doc A" in result.summary
    assert "https://docs.example/a" in result.summary
    assert "Useful section B" in result.summary
    assert "raw_html" not in result.summary
    assert len(result.key_fields["results"]) == 2
    assert result.evidence_ref == "tool:search:4"


def test_file_snapshot_keeps_path_hash_and_relevant_excerpt():
    result = dehydrate_tool_result(
        tool="file_reader",
        action="read_file",
        raw_result={
            "path": "runtime/context/ledger.py",
            "sha256": "abc123",
            "content": "first\n" + ("middle\n" * 300) + "important ending",
        },
        evidence_ref="read:runtime/context/ledger.py",
        raw_artifact_ref="artifact:file:ledger",
    )

    assert "runtime/context/ledger.py" in result.summary
    assert "abc123" in result.summary
    assert "important ending" in result.summary
    assert "middle\nmiddle\nmiddle\nmiddle" not in result.summary
    assert result.key_fields["path"] == "runtime/context/ledger.py"
    assert result.key_fields["sha256"] == "abc123"
    assert result.raw_artifact_ref == "artifact:file:ledger"


def test_missing_refs_are_explicitly_reported_instead_of_silently_dropped():
    result = dehydrate_tool_result(
        tool="unknown",
        action="call",
        raw_result={"status": "ok"},
    )

    assert result.evidence_ref == ""
    assert result.raw_artifact_ref == ""
    assert "missing_evidence_ref" in result.omitted_fields
    assert "missing_raw_artifact_ref" in result.omitted_fields


def test_generic_result_redacts_secret_keys_from_summary_and_key_fields():
    result = dehydrate_tool_result(
        tool="unknown",
        action="call",
        raw_result={
            "status": "ok",
            "api_key": "secret-value",
            "nested": {"token": "nested-secret", "visible": "safe"},
            "items": [{"password": "list-secret", "name": "visible-item"}],
        },
        evidence_ref="tool:unknown:5",
        raw_artifact_ref="artifact:unknown:5",
    )

    serialized_fields = str(result.key_fields)
    assert "secret-value" not in result.summary
    assert "nested-secret" not in result.summary
    assert "list-secret" not in result.summary
    assert "secret-value" not in serialized_fields
    assert "nested-secret" not in serialized_fields
    assert "list-secret" not in serialized_fields
    assert result.key_fields["status"] == "ok"
    assert result.key_fields["nested"]["visible"] == "safe"
    assert result.redacted is True
    assert "api_key" in result.omitted_fields
    assert "nested.token" in result.omitted_fields


def test_truncated_non_secret_summary_is_not_marked_as_redacted():
    result = dehydrate_tool_result(
        tool="terminal",
        action="run_command",
        raw_result={
            "command": "long public command",
            "returncode": 0,
            "stdout": "\n".join(f"public line {index} " + ("x" * 180) for index in range(40)),
            "stderr": "",
        },
        evidence_ref="tool:terminal:6",
        raw_artifact_ref="artifact:terminal:6",
    )

    assert len(result.summary) <= 1_200
    assert "[truncated" in result.summary
    assert result.redacted is False
