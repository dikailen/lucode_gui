from __future__ import annotations

import hashlib
import json
import uuid
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
    run_id: str = ""
    attempt: int = 0


@dataclass(frozen=True)
class ToolInvocation:
    invocation_id: str
    tool_name: str
    arguments: str | None
    tool_rule: str
    approval_token: ApprovalToken
    side_effect_class: str = "unknown"


class ToolInvocationGuard:
    """Run-local approval state for concrete tool invocations."""

    def __init__(
        self,
        *,
        approval_policy=None,
        task_id: str = "",
        run_id: str = "",
        attempt: int = 1,
        lifecycle_sink=None,
    ) -> None:
        self.approval_policy = approval_policy
        self.task_id = str(task_id or getattr(approval_policy, "task_id", "") or "")
        self.run_id = str(run_id or "")
        self.attempt = max(0, int(attempt))
        self.lifecycle_sink = lifecycle_sink
        self._lifecycle_prepared_invocation_ids: set[str] = set()
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
            run_id=self.run_id,
            attempt=self.attempt,
            scope="once",
        )
        try:
            from runtime.tools.registry import classify_tool_side_effect

            side_effect_class = classify_tool_side_effect(name)
        except Exception:
            side_effect_class = "unknown"
        invocation = ToolInvocation(
            invocation_id=f"invocation_{uuid.uuid4().hex}",
            tool_name=name,
            arguments=arguments,
            tool_rule=rule,
            approval_token=token,
            side_effect_class=side_effect_class,
        )
        sink = self.lifecycle_sink
        if sink is not None:
            try:
                prepared = sink.prepare(invocation, task_id=self.task_id, attempt=self.attempt)
            except Exception:
                prepared = False
            if prepared is True:
                self._lifecycle_prepared_invocation_ids.add(invocation.invocation_id)
        return invocation

    def lifecycle_prepare_succeeded(self, invocation: ToolInvocation) -> bool:
        if self.lifecycle_sink is None:
            return True
        return invocation.invocation_id in self._lifecycle_prepared_invocation_ids

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
    run_id: str = "",
    attempt: int = 0,
    scope: str = "once",
) -> ApprovalToken:
    name = str(tool_name or "")
    rule = str(tool_rule or approval_tool_rule(name))
    args_hash = canonical_arguments_hash(arguments)
    clean_scope = str(scope or "once")
    clean_task_id = str(task_id or "")
    clean_run_id = str(run_id or "")
    clean_attempt = max(0, int(attempt))
    token_material = "\0".join(
        ["lucode_approval_v2", clean_scope, clean_run_id, str(clean_attempt), clean_task_id, name, rule, args_hash]
    )
    token_id = hashlib.sha256(token_material.encode("utf-8")).hexdigest()
    return ApprovalToken(
        scope=clean_scope,
        token_id=token_id,
        tool_name=name,
        tool_rule=rule,
        arguments_hash=args_hash,
        task_id=clean_task_id,
        run_id=clean_run_id,
        attempt=clean_attempt,
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
