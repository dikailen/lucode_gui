from __future__ import annotations


def test_parse_planner_result_forces_browser_worker_route_when_desktop_browser_is_available(monkeypatch):
    from planning.planner_schema import parse_planner_result

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")

    result = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "planner under-routed the request",
          "refined_request": "Open the embedded browser for https://example.com/login and click the sign in button."
        }
        """,
        fallback_user_input="Open the embedded browser for https://example.com/login and click the sign in button.",
    )

    assert result.route_type == "single_agent"
    assert len(result.tasks) == 1
    assert result.tasks[0].skill_id == "project_explorer"
    assert result.tasks[0].mcp == ["desktop_browser"]
    assert "browser" in result.tasks[0].instruction.lower()


def test_parse_planner_result_does_not_route_simple_chat_to_browser_from_malformed_model_output(monkeypatch):
    from planning.planner_schema import parse_planner_result

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")

    result = parse_planner_result(
        "I will open the browser at http://localhost:8000 and list browser tabs.",
        fallback_user_input="raw_user_input: \u4f60\u597d\nrefined_request: \u4f60\u597d",
    )

    assert result.route_type == "direct_answer"
    assert result.tasks == []


def test_parse_planner_result_does_not_route_simple_chat_json_to_browser_from_context_labels(monkeypatch):
    from planning.planner_schema import parse_planner_result

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")

    result = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "simple greeting",
          "refined_request": "\u4f60\u597d",
          "direct_answer_instruction": "Answer the greeting directly."
        }
        """,
        fallback_user_input="原始用户输入：\u4f60\u597d\nrefiner_raw_user_input：\u4f60\u597d\nrefined_request：\u4f60\u597d",
    )

    assert result.route_type == "direct_answer"
    assert result.tasks == []


def test_parse_planner_result_rewrites_web_search_task_for_explicit_desktop_browser(monkeypatch):
    from planning.planner_schema import parse_planner_result
    from runtime.capabilities.resolver import CapabilityResolver

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    user_input = "Use the embedded browser to open https://example.com and read the page title and summary."

    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner picked web_fetch",
          "refined_request": "Use the embedded browser to open https://example.com and read the page title and summary.",
          "tasks": [
            {
              "id": "fetch_example",
              "title": "Fetch example.com",
              "instruction": "Use web_fetch to fetch https://example.com and return the title and summary.",
              "skill_id": "project_explorer",
              "model": "worker-model",
              "mcp": ["web_search"],
              "acceptance_criteria": ["Return title and summary"]
            }
          ]
        }
        """,
        fallback_user_input=user_input,
    )

    assert result.route_type == "single_agent"
    assert result.tasks[0].mcp == ["desktop_browser"]
    assert "embedded desktop browser" in result.tasks[0].instruction.lower()
    binding = CapabilityResolver().resolve_task(result.tasks[0])
    assert binding.mcp == ("desktop_browser",)


def test_parse_planner_result_rewrites_chinese_embedded_browser_web_search_task(monkeypatch):
    from planning.planner_schema import parse_planner_result
    from runtime.capabilities.resolver import CapabilityResolver

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    user_input = "用内置浏览器打开 https://example.com，读取页面标题和页面摘要。"

    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner picked web_fetch",
          "refined_request": "用内置浏览器打开 https://example.com，读取页面标题和页面摘要。",
          "tasks": [
            {
              "id": "fetch_example",
              "title": "获取 example.com 的标题和摘要",
              "instruction": "使用 web_fetch 抓取 https://example.com 并提取页面标题和摘要。",
              "skill_id": "project_explorer",
              "model": "worker-model",
              "mcp": ["web_search"],
              "acceptance_criteria": ["返回标题和摘要"]
            }
          ]
        }
        """,
        fallback_user_input=user_input,
    )

    assert result.route_type == "single_agent"
    assert result.tasks[0].mcp == ["desktop_browser"]
    assert "web_fetch" in result.tasks[0].instruction
    assert "embedded desktop browser" in result.tasks[0].instruction.lower()
    binding = CapabilityResolver().resolve_task(result.tasks[0])
    assert binding.mcp == ("desktop_browser",)


def test_parse_planner_result_fallback_natural_language_browser_refusal_routes_to_desktop_browser(monkeypatch):
    from planning.planner_schema import parse_planner_result

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    user_input = (
        "Use the embedded browser to open https://www.w3schools.com/html/tryit.asp?filename=tryhtml_form_submit, "
        "read the page summary, fill the first text input with LucodeTest, do not submit, and wait for approval."
    )

    result = parse_planner_result(
        "I do not have browser automation tools, so I cannot directly operate the page.",
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )

    assert result.route_type == "single_agent"
    assert len(result.tasks) == 1
    assert result.tasks[0].id == "desktop_browser_task"
    assert result.tasks[0].mcp == ["desktop_browser"]
    assert "workspace_edit" not in result.tasks[0].mcp
    assert result.memory_interface["execution_route"] == "desktop_browser"


def test_parse_planner_result_clarify_browser_refusal_routes_to_desktop_browser(monkeypatch):
    from planning.planner_schema import parse_planner_result

    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    user_input = (
        "Use the built-in browser to open https://www.w3schools.com/html/tryit.asp?filename=tryhtml_form_submit, "
        "read the page summary, fill the first text input with LucodeTest, do not submit, and wait for approval."
    )

    result = parse_planner_result(
        """
        {
          "route_type": "clarify",
          "reason": "The current system has no built-in browser or DOM operation tool.",
          "refined_request": "Use the built-in browser to open https://www.w3schools.com/html/tryit.asp?filename=tryhtml_form_submit, read the page summary, fill the first text input with LucodeTest, do not submit, and wait for approval.",
          "clarifying_question": "I can only use web_fetch. Do you want a text summary instead?"
        }
        """,
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )

    assert result.route_type == "single_agent"
    assert result.clarifying_question == ""
    assert len(result.tasks) == 1
    assert result.tasks[0].id == "desktop_browser_task"
    assert result.tasks[0].mcp == ["desktop_browser"]
    assert result.memory_interface["browser_binding"] == "desktop_browser"


def test_parse_planner_result_preserves_p7_reliability_fields():
    from planning.planner_schema import parse_planner_result

    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "needs code inspection",
          "refined_request": "Inspect runtime auth flow.",
          "tasks": [
            {
              "id": "inspect_auth",
              "title": "Inspect auth",
              "instruction": "Read runtime/auth.py and summarize the auth flow.",
              "skill_id": "project_explorer",
              "model": "worker-model",
              "mcp": ["project_filesystem_readonly"],
              "read_set": ["runtime/auth.py"],
              "sensitivity": "project_private",
              "difficulty": "simple",
              "placement": {
                "planner_side": "local",
                "executor_side": "local"
              },
              "evidence_requirements": [
                "file_snapshot:runtime/auth.py"
              ]
            }
          ]
        }
        """,
        fallback_user_input="Inspect runtime auth flow.",
    )

    task = result.tasks[0]
    assert task.sensitivity == "project_private"
    assert task.difficulty == "simple"
    assert task.placement == {"planner_side": "local", "executor_side": "local"}
    assert task.evidence_requirements == ["file_snapshot:runtime/auth.py"]
