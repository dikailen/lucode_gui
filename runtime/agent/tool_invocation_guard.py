from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApprovalToken:
    scope: str
    token_id: str
    tool_name: str
    tool_rule: str
    arguments_hash: str
    task_id: str = ""


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    arguments: str | None
    tool_rule: str
    approval_token: ApprovalToken


class ToolInvocationGuard:
    """Run-local approval state for concrete tool invocations."""

    def __init__(self, *, approval_policy=None, task_id: str = "") -> None:
        self.approval_policy = approval_policy
        self.task_id = str(task_id or getattr(approval_policy, "task_id", "") or "")
        self._once_approved_tokens: set[str] = set()
        self._approved_tools_for_session: set[str] = set()
        self._approved_tool_rules: set[str] = set()

    def build_invocation(self, tool_name: str, arguments: str | None) -> ToolInvocation:
        name = str(tool_name or "")
        rule = approval_tool_rule(name)
        token = build_approval_token(
            name,
            arguments,
            tool_rule=rule,
            task_id=self.task_id,
            scope="once",
        )
        return ToolInvocation(
            tool_name=name,
            arguments=arguments,
            tool_rule=rule,
            approval_token=token,
        )

    def decide_policy(self, invocation: ToolInvocation):
        if self.approval_policy is None:
            return None
        return self.approval_policy.decide(invocation.tool_name, invocation.arguments)

    def session_approval_allows(self, invocation: ToolInvocation) -> bool:
        return (
            invocation.tool_name in self._approved_tools_for_session
            or invocation.tool_rule in self._approved_tool_rules
        )

    def mark_session_tool_approved(self, invocation: ToolInvocation) -> None:
        self._approved_tools_for_session.add(invocation.tool_name)

    def mark_session_rule_approved(self, invocation: ToolInvocation) -> None:
        self._approved_tool_rules.add(invocation.tool_rule)

    def mark_once_approved(self, invocation: ToolInvocation) -> ApprovalToken:
        token = invocation.approval_token
        self._once_approved_tokens.add(token.token_id)
        return token

    def once_approval_was_used(self, invocation: ToolInvocation) -> bool:
        return invocation.approval_token.token_id in self._once_approved_tokens


def build_approval_token(
    tool_name: str,
    arguments: str | None,
    *,
    tool_rule: str = "",
    task_id: str = "",
    scope: str = "once",
) -> ApprovalToken:
    name = str(tool_name or "")
    rule = str(tool_rule or approval_tool_rule(name))
    args_hash = canonical_arguments_hash(arguments)
    clean_scope = str(scope or "once")
    clean_task_id = str(task_id or "")
    token_material = "\0".join(["lucode_approval_v1", clean_scope, clean_task_id, name, rule, args_hash])
    token_id = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    return ApprovalToken(
        scope=clean_scope,
        token_id=token_id,
        tool_name=name,
        tool_rule=rule,
        arguments_hash=args_hash,
        task_id=clean_task_id,
    )


def canonical_arguments_hash(arguments: str | None) -> str:
    canonical = canonicalize_arguments(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_arguments(arguments: str | None) -> str:
    if arguments is None:
        return ""
    text = str(arguments)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(_normalize_json(parsed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def approval_tool_rule(tool_name: str) -> str:
    name = str(tool_name or "")
    if "." in name:
        return name.split(".", 1)[0]
    if "_" in name:
        return name.split("_", 1)[0]
    return name


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    return value
