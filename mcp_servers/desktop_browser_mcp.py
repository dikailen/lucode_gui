import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP

try:
    from mcp_servers.core.operation_log import append_operation_log
except ModuleNotFoundError:
    from operation_log import append_operation_log


mcp = FastMCP("desktop_browser", log_level="ERROR")


def _project_root() -> Path:
    return Path(os.environ["DESKTOP_BROWSER_PROJECT_ROOT"]).resolve()


def _quarantine_dir() -> Path:
    return Path(os.environ["DESKTOP_BROWSER_QUARANTINE_DIR"]).resolve()


def _operation_log() -> Path:
    return _quarantine_dir() / "operations.jsonl"


def _bridge_base_url() -> str:
    value = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_URL") or "").strip()
    if not value:
        raise RuntimeError("Desktop browser bridge URL is not configured.")
    return value.rstrip("/")


def _bridge_token() -> str:
    value = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN") or "").strip()
    if not value:
        raise RuntimeError("Desktop browser bridge token is not configured.")
    return value


def _bridge_request(method: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {_bridge_token()}",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{_bridge_base_url()}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(_bridge_error_message(detail) or f"Desktop browser bridge request failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Desktop browser bridge request failed: {exc.reason}") from exc
    return json.loads(raw or "{}")


def _bridge_error_message(detail: str) -> str:
    text = str(detail or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        return str(parsed.get("error") or parsed.get("message") or "")
    return text


def _log_operation(
    tool_name: str,
    action: str,
    params_summary: dict,
    *,
    approval_required: bool,
    result_summary: str = "",
    status: str = "success",
    error: str = "",
) -> None:
    append_operation_log(
        _operation_log(),
        tool=tool_name,
        action=action,
        reason="desktop_browser_bridge",
        status=status,
        params_summary=params_summary,
        approval_required=approval_required,
        approval_note="Desktop browser actions are proxied through the local Electron bridge.",
        result_summary=result_summary,
        error=error,
    )


def _ensure_automation_enabled(tab_id: str = "") -> None:
    _bridge_request("POST", "/automation-permission", {"tab_id": tab_id, "enabled": True})


@mcp.tool(
    name="browser_list_tabs",
    description="List tabs managed by the embedded desktop browser.",
)
def browser_list_tabs() -> str:
    result = _bridge_request("GET", "/tabs")
    _log_operation(
        "desktop_browser.browser_list_tabs",
        "list_tabs",
        {"tab_id": "", "project_root": str(_project_root())},
        approval_required=False,
        result_summary=f"tab_count={len(result.get('tabs') or [])}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="browser_navigate",
    description="Open a URL in the embedded desktop browser. Creates a browser tab if needed.",
)
def browser_navigate(url: str, tab_id: str = "") -> str:
    result = _bridge_request("POST", "/navigate", {"url": url, "tab_id": tab_id})
    _log_operation(
        "desktop_browser.browser_navigate",
        "navigate",
        {"tab_id": tab_id, "url": url},
        approval_required=False,
        result_summary=f"active_tab_id={result.get('activeTabId') or ''}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="browser_get_page_summary",
    description="Read title, URL, text summary, headings, and actionable elements from the embedded browser page.",
)
def browser_get_page_summary(tab_id: str = "", max_text_length: int = 4000, max_elements: int = 80) -> str:
    result = _bridge_request(
        "POST",
        "/page-summary",
        {
            "tab_id": tab_id,
            "max_text_length": max_text_length,
            "max_elements": max_elements,
        },
    )
    _log_operation(
        "desktop_browser.browser_get_page_summary",
        "page_summary",
        {"tab_id": tab_id, "max_text_length": max_text_length, "max_elements": max_elements},
        approval_required=False,
        result_summary=f"url={result.get('url') or ''}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="browser_click_element",
    description="Click a CSS-selected element inside the embedded browser. Requires approval.",
)
def browser_click_element(selector: str, tab_id: str = "") -> str:
    _ensure_automation_enabled(tab_id)
    result = _bridge_request("POST", "/click", {"tab_id": tab_id, "selector": selector})
    _log_operation(
        "desktop_browser.browser_click_element",
        "click",
        {"tab_id": tab_id, "selector": selector},
        approval_required=True,
        result_summary=f"selector={selector}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="browser_set_input_value",
    description="Set the value of an input, textarea, or select element inside the embedded browser. Requires approval.",
)
def browser_set_input_value(selector: str, value: str, tab_id: str = "") -> str:
    _ensure_automation_enabled(tab_id)
    result = _bridge_request(
        "POST",
        "/set-input-value",
        {"tab_id": tab_id, "selector": selector, "value": value},
    )
    _log_operation(
        "desktop_browser.browser_set_input_value",
        "set_input_value",
        {"tab_id": tab_id, "selector": selector, "value_length": len(value)},
        approval_required=True,
        result_summary=f"selector={selector}; value_length={len(value)}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="browser_submit_form",
    description="Submit a CSS-selected form or submit button inside the embedded browser. Requires approval.",
)
def browser_submit_form(selector: str, tab_id: str = "") -> str:
    _ensure_automation_enabled(tab_id)
    result = _bridge_request("POST", "/submit-form", {"tab_id": tab_id, "selector": selector})
    _log_operation(
        "desktop_browser.browser_submit_form",
        "submit_form",
        {"tab_id": tab_id, "selector": selector},
        approval_required=True,
        result_summary=f"selector={selector}",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run("stdio")
