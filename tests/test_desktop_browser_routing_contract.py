from __future__ import annotations

from planning.planner_schema import PlannedTask, parse_planner_result
from runtime.capabilities.resolver import CapabilityResolver


def _set_desktop_browser_bridge(monkeypatch) -> None:
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")


def test_normal_routes_do_not_trigger_desktop_browser_for_generic_input_or_chat(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)

    greeting = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "simple chat",
          "refined_request": "hello",
          "direct_answer_instruction": "Reply briefly."
        }
        """,
        fallback_user_input="raw_user_input: hello\nrefined_request: hello",
    )
    assert greeting.route_type == "direct_answer"
    assert greeting.tasks == []

    ui_question = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "UI design question",
          "refined_request": "Explain how to polish the chat input box and send button UI.",
          "direct_answer_instruction": "Answer the UI design question directly."
        }
        """,
        fallback_user_input=(
            "raw_user_input: Explain how to polish the chat input box and send button UI.\n"
            "refined_request: Explain how to polish the chat input box and send button UI."
        ),
    )
    assert ui_question.route_type == "direct_answer"
    assert ui_question.tasks == []

    project_task = PlannedTask(
        id="chat-ui-polish",
        title="Polish chat input box",
        instruction="Update the chat input box spacing and send button visual state.",
        skill_id="code_engineer",
        model="worker-model",
        mcp=["project_filesystem_readonly", "workspace_edit"],
        read_set=["desktop/src/app/components/ChatPane.tsx"],
        write_intent=["desktop/src/app/components/ChatPane.tsx"],
    )
    binding = CapabilityResolver().resolve_task(project_task)
    assert binding.mcp == ("project_filesystem_readonly", "workspace_edit")
    assert "desktop_browser" not in binding.mcp
    assert "desktop_browser_interaction_detected" not in binding.reasons

    browser_ui_task = PlannedTask(
        id="browser-panel-ui",
        title="Polish browser tab UI",
        instruction="Fix the BrowserPanel tab close button spacing and input alignment.",
        skill_id="code_engineer",
        model="worker-model",
        mcp=["project_filesystem_readonly", "workspace_edit"],
        read_set=["desktop/src/app/components/BrowserPanel.tsx"],
        write_intent=["desktop/src/app/components/BrowserPanel.tsx"],
    )
    browser_ui_binding = CapabilityResolver().resolve_task(browser_ui_task)
    assert browser_ui_binding.mcp == ("project_filesystem_readonly", "workspace_edit")
    assert "desktop_browser" not in browser_ui_binding.mcp


def test_negative_web_search_instruction_does_not_force_external_web_route(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)
    user_input = "Do not use web search. Answer directly: what is an API key?"

    planned = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "simple explanation with a negative web-search constraint",
          "refined_request": "Do not use web search. Answer directly: what is an API key?",
          "direct_answer_instruction": "Answer from general knowledge without tools."
        }
        """,
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )
    assert planned.route_type == "direct_answer"
    assert planned.tasks == []


def test_planner_supplied_desktop_browser_is_stripped_when_user_did_not_request_browser(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)

    planned = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner hallucinated a browser worker",
          "refined_request": "hello",
          "tasks": [
            {
              "id": "wrong_browser",
              "title": "Answer greeting",
              "instruction": "Reply to the greeting. No page interaction is needed.",
              "skill_id": "project_explorer",
              "model": "worker-model",
              "mcp": ["desktop_browser"]
            }
          ]
        }
        """,
        fallback_user_input="raw_user_input: hello\nrefined_request: hello",
    )

    assert planned.route_type == "single_agent"
    assert planned.tasks[0].mcp == []


def test_explicit_embedded_browser_routes_trigger_desktop_browser_precisely(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)
    user_input = (
        "Use the embedded browser to open https://example.com/login, "
        "read the page summary, and click #sign-in only after approval."
    )

    planned = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "planner under-routed an explicit browser task",
          "refined_request": "Use the embedded browser to open https://example.com/login, read the page summary, and click #sign-in only after approval."
        }
        """,
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )
    assert planned.route_type == "single_agent"
    assert len(planned.tasks) == 1
    assert planned.tasks[0].id == "desktop_browser_task"
    assert planned.tasks[0].mcp == ["desktop_browser"]
    assert planned.memory_interface["execution_route"] == "desktop_browser"

    resolver_task = PlannedTask(
        id="browser-click",
        title="Use embedded browser",
        instruction=user_input,
        skill_id="project_explorer",
        model="worker-model",
        mcp=["project_filesystem_readonly"],
    )
    binding = CapabilityResolver().resolve_task(resolver_task)
    assert binding.mcp == ("project_filesystem_readonly", "desktop_browser")
    assert binding.source == "capability_resolver"
    assert "desktop_browser_interaction_detected" in binding.reasons


def test_url_page_actions_trigger_desktop_browser_without_requiring_magic_phrase(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)
    user_input = "Open https://example.com/login and click #sign-in after approval."

    planned = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "planner under-routed a page action",
          "refined_request": "Open https://example.com/login and click #sign-in after approval."
        }
        """,
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )
    assert planned.route_type == "single_agent"
    assert planned.tasks[0].mcp == ["desktop_browser"]

    resolver_task = PlannedTask(
        id="url-click",
        title="Click page element",
        instruction=user_input,
        skill_id="project_explorer",
        model="worker-model",
        mcp=[],
    )
    binding = CapabilityResolver().resolve_task(resolver_task)
    assert binding.mcp == ("desktop_browser",)
    assert "desktop_browser_interaction_detected" in binding.reasons


def test_explicit_desktop_browser_tool_names_win_over_web_search_negative_text(monkeypatch):
    _set_desktop_browser_bridge(monkeypatch)
    user_input = (
        "Use desktop_browser browser_navigate to open https://example.com/search, "
        "then call browser_get_page_summary. Do not use web search."
    )

    planned = parse_planner_result(
        """
        {
          "route_type": "direct_answer",
          "reason": "planner under-routed explicit desktop browser tools",
          "refined_request": "Use desktop_browser browser_navigate to open https://example.com/search, then call browser_get_page_summary. Do not use web search."
        }
        """,
        fallback_user_input=f"raw_user_input: {user_input}\nrefined_request: {user_input}",
    )
    assert planned.route_type == "single_agent"
    assert planned.tasks[0].mcp == ["desktop_browser"]

    resolver_task = PlannedTask(
        id="explicit-browser-tool",
        title="Use desktop_browser tool",
        instruction=user_input,
        skill_id="project_explorer",
        model="worker-model",
        mcp=[],
    )
    binding = CapabilityResolver().resolve_task(resolver_task)
    assert binding.mcp == ("desktop_browser",)
    assert "desktop_browser_interaction_detected" in binding.reasons
