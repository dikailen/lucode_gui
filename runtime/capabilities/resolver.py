from __future__ import annotations

from dataclasses import dataclass
import os
import re


EXCLUSIVE_MCP_RESOURCE_LOCKS = {
    "command_runner": "terminal",
    "safe_backup": "workspace_backup",
    "desktop_browser": "browser_session",
}

DESKTOP_BROWSER_MCP_ID = "desktop_browser"
BROWSER_SURFACE_MARKERS = (
    "embedded browser",
    "desktop browser",
    "built-in browser",
    "browser panel",
    "browser tab",
    "desktop_browser",
    "内置浏览器",
    "桌面浏览器",
    "浏览器面板",
    "浏览器标签",
)
BROWSER_TOOL_MARKERS = (
    "browser_navigate",
    "browser_get_page_summary",
    "browser_click_element",
    "browser_set_input_value",
    "browser_submit_form",
)
BROWSER_ACTION_MARKERS = (
    "open",
    "navigate",
    "read",
    "page summary",
    "click",
    "fill",
    "set input",
    "submit",
    "form",
    "selector",
    "dom",
    "打开",
    "跳转",
    "读取",
    "页面摘要",
    "点击",
    "填表",
    "输入",
    "提交",
    "表单",
    "选择器",
)
WEB_SEARCH_ONLY_MARKERS = (
    "web search",
    "search the web",
    "browse the web",
    "official docs",
    "official link",
    "return only links",
    "联网搜索",
    "联网上搜索",
    "官方文档",
    "官方链接",
    "返回链接",
)


@dataclass(frozen=True)
class CapabilityBinding:
    task_id: str
    mcp: tuple[str, ...] = ()
    read_set: tuple[str, ...] = ()
    write_intent: tuple[str, ...] = ()
    resource_locks: tuple[str, ...] = ()
    source: str = "planner_task"
    reasons: tuple[str, ...] = ()

    @property
    def requires_tools(self) -> bool:
        return bool(self.mcp)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "mcp": list(self.mcp),
            "read_set": list(self.read_set),
            "write_intent": list(self.write_intent),
            "resource_locks": list(self.resource_locks),
            "source": self.source,
            "reasons": list(self.reasons),
        }


class CapabilityResolver:
    """Resolve task capabilities before binding MCP servers.

    Stage 7 deliberately preserves planner-provided task.mcp behavior. The
    resolver is the stable seam future plugins can extend without adding new
    execution modes.
    """

    def resolve_task(self, task) -> CapabilityBinding:
        mcp_values = list(getattr(task, "mcp", []) or [])
        reasons = ["planner_task_mcp_passthrough"]
        if _desktop_browser_available():
            reasons.append("desktop_browser_available")
            if _needs_desktop_browser(task):
                mcp_values.append(DESKTOP_BROWSER_MCP_ID)
                reasons.append("desktop_browser_interaction_detected")
        mcp = _string_tuple(mcp_values)
        read_set = _string_tuple(getattr(task, "read_set", []) or [])
        write_intent = _string_tuple(getattr(task, "write_intent", []) or [])
        resource_locks = _resource_locks_for_task(task, mcp)
        if write_intent and "workspace_edit" in mcp:
            reasons.append("declared_write_scope")
        if read_set:
            reasons.append("declared_read_scope")
        if resource_locks:
            reasons.append("exclusive_resource_lock")
        return CapabilityBinding(
            task_id=str(getattr(task, "id", "") or ""),
            mcp=mcp,
            read_set=read_set,
            write_intent=write_intent,
            resource_locks=resource_locks,
            source="capability_resolver" if DESKTOP_BROWSER_MCP_ID in mcp else "planner_task",
            reasons=tuple(reasons),
        )


def _resource_locks_for_task(task, mcp: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for item in list(getattr(task, "resource_locks", []) or []):
        clean = _normalize_resource(item)
        if clean:
            values.append(clean)
    for mcp_id in mcp:
        lock = EXCLUSIVE_MCP_RESOURCE_LOCKS.get(mcp_id)
        if lock:
            values.append(lock)
    return _dedupe_tuple(values)


def _string_tuple(values) -> tuple[str, ...]:
    return _dedupe_tuple(str(item or "").strip() for item in list(values or []) if str(item or "").strip())


def _desktop_browser_available() -> bool:
    return bool(
        str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_URL") or "").strip()
        and str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN") or "").strip()
    )


def _needs_desktop_browser(task) -> bool:
    text = "\n".join(
        [
            str(getattr(task, "title", "") or ""),
            str(getattr(task, "instruction", "") or ""),
        ]
    ).lower()
    if not text.strip():
        return False
    if any(marker in text for marker in BROWSER_TOOL_MARKERS):
        return True
    has_page_action = any(marker in text for marker in BROWSER_ACTION_MARKERS)
    has_url_page_action = bool(re.search(r"https?://\S+", text) and has_page_action)
    has_browser_surface = any(marker in text for marker in BROWSER_SURFACE_MARKERS)
    if any(marker in text for marker in WEB_SEARCH_ONLY_MARKERS) and not (
        has_browser_surface and has_page_action
    ) and not has_url_page_action:
        return False
    if has_url_page_action:
        return True
    if not has_browser_surface:
        return False
    return has_page_action


def _normalize_resource(value) -> str:
    clean = str(value or "").strip().replace("\\", "/").strip("/")
    while "//" in clean:
        clean = clean.replace("//", "/")
    return clean.lower()


def _dedupe_tuple(values) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return tuple(result)
