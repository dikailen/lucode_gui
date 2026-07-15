from __future__ import annotations

import uuid
from collections import defaultdict
from threading import RLock
from typing import Any, Callable

from runtime.recovery.journal import RunJournal


class JournalToolLifecycleSink:
    """Correlate one approved tool call with durable Journal state."""

    def __init__(
        self,
        journal: RunJournal,
        *,
        run_id: str,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._journal = journal
        self.run_id = str(run_id or "")
        self._on_error = on_error
        self._lock = RLock()
        self._invocations_by_signature: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    def prepare(self, invocation, *, task_id: str = "", attempt: int = 1) -> bool:
        invocation_id = str(getattr(invocation, "invocation_id", "") or "")
        tool_name = str(getattr(invocation, "tool_name", "") or "")
        token = getattr(invocation, "approval_token", None)
        arguments_hash = str(getattr(token, "arguments_hash", "") or "")
        side_effect_class = str(getattr(invocation, "side_effect_class", "unknown") or "unknown")
        try:
            self._journal.prepare_tool_invocation(
                invocation_id=invocation_id,
                run_id=self.run_id,
                task_id=str(task_id or ""),
                attempt=max(0, int(attempt)),
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                side_effect_class=side_effect_class,
            )
        except Exception as exc:
            self._report_error(exc)
            return False
        try:
            self._record_derived_postcondition(
                invocation_id=invocation_id,
                tool_name=tool_name,
                arguments=getattr(invocation, "arguments", None),
            )
        except Exception as exc:
            # Missing postcondition metadata only removes reconciliation eligibility;
            # the invocation remains protected by the P6 unknown-state barrier.
            self._report_error(exc)
        with self._lock:
            key = _signature(str(task_id or ""), tool_name, arguments_hash)
            if invocation_id not in self._invocations_by_signature[key]:
                self._invocations_by_signature[key].append(invocation_id)
        return True

    def mark_dispatched(self, invocation) -> bool:
        invocation_id = str(getattr(invocation, "invocation_id", "") or "")
        try:
            self._journal.mark_tool_invocation_dispatched(invocation_id)
        except Exception as exc:
            self._report_error(exc)
            return False
        return True

    def ensure_dispatched_for_tool_start(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments: str | None,
        attempt: int = 1,
    ) -> bool:
        """Durably record a tool that the SDK is about to execute.

        Approval-gated tools already have a prepared/dispatched invocation when
        they reach the SDK hook. Non-approval tools enter only through this hook,
        so they must be prepared and dispatched here before execution continues.
        """

        clean_task_id = str(task_id or "")
        clean_tool_name = str(tool_name or "")
        if not clean_tool_name:
            return False
        arguments_hash = _arguments_hash(arguments)
        existing = self._active_invocation_for_signature(clean_task_id, clean_tool_name, arguments_hash)
        if existing is not None:
            status = str(existing.get("status") or "")
            if status == "dispatched":
                return True
            if status == "prepared":
                try:
                    self._journal.mark_tool_invocation_dispatched(str(existing["invocation_id"]))
                except Exception as exc:
                    self._report_error(exc)
                    return False
                return True

        invocation_id = f"invocation_{uuid.uuid4().hex}"
        try:
            from runtime.tools.registry import classify_tool_side_effect

            side_effect_class = classify_tool_side_effect(clean_tool_name)
        except Exception:
            side_effect_class = "unknown"
        try:
            self._journal.prepare_tool_invocation(
                invocation_id=invocation_id,
                run_id=self.run_id,
                task_id=clean_task_id,
                attempt=max(0, int(attempt)),
                tool_name=clean_tool_name,
                arguments_hash=arguments_hash,
                side_effect_class=side_effect_class,
            )
            self._record_derived_postcondition(
                invocation_id=invocation_id,
                tool_name=clean_tool_name,
                arguments=arguments,
            )
            self._journal.mark_tool_invocation_dispatched(invocation_id)
        except Exception as exc:
            self._report_error(exc)
            return False
        with self._lock:
            key = _signature(clean_task_id, clean_tool_name, arguments_hash)
            self._invocations_by_signature[key].append(invocation_id)
        return True

    def cancel(self, invocation) -> bool:
        invocation_id = str(getattr(invocation, "invocation_id", "") or "")
        try:
            self._journal.cancel_prepared_tool_invocation(invocation_id)
        except Exception as exc:
            self._report_error(exc)
            return False
        return True

    def complete_for_tool(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments: str | None,
        evidence_ref: str = "",
        failed: bool = False,
    ) -> bool:
        # Reserve the in-memory candidate before touching SQLite. Parallel SDK
        # callbacks with identical arguments must not both complete the same row.
        with self._lock:
            invocation_id = self._matching_dispatched_invocation(task_id, tool_name, arguments)
            if not invocation_id:
                raise KeyError("no matching invocation for tool lifecycle callback")
            self._remove_invocation(task_id, tool_name, arguments, invocation_id)
        try:
            if failed:
                self._journal.fail_tool_invocation(invocation_id)
            else:
                self._journal.complete_tool_invocation(invocation_id, evidence_ref=evidence_ref)
        except Exception as exc:
            self._restore_dispatched_invocation(task_id, tool_name, arguments, invocation_id)
            self._report_error(exc)
            return False
        return True

    def _matching_dispatched_invocation(self, task_id: str, tool_name: str, arguments: str | None) -> str:
        arguments_hash = _arguments_hash(arguments)
        key = _signature(str(task_id or ""), str(tool_name or ""), arguments_hash)
        with self._lock:
            candidates = list(self._invocations_by_signature.get(key) or [])
        for invocation_id in candidates:
            record = self._journal.tool_invocation(invocation_id)
            if record is not None and record.get("status") == "dispatched":
                return invocation_id
        return ""

    def _active_invocation_for_signature(
        self,
        task_id: str,
        tool_name: str,
        arguments_hash: str,
    ) -> dict[str, Any] | None:
        key = _signature(task_id, tool_name, arguments_hash)
        with self._lock:
            candidates = list(self._invocations_by_signature.get(key) or [])
        for invocation_id in candidates:
            record = self._journal.tool_invocation(invocation_id)
            if record is not None and str(record.get("status") or "") in {"prepared", "dispatched"}:
                return record
        return None

    def _record_derived_postcondition(
        self,
        *,
        invocation_id: str,
        tool_name: str,
        arguments: str | None,
    ) -> None:
        try:
            from runtime.recovery.postconditions import derive_tool_postcondition

            postcondition = derive_tool_postcondition(
                tool_name,
                arguments,
                workspace_root=self._journal.workspace_root,
            )
            if postcondition is not None:
                kind, expectation = postcondition
                self._journal.record_tool_postcondition(
                    invocation_id=invocation_id,
                    kind=kind,
                    expectation=expectation,
                )
        except Exception as exc:
            # A missing deterministic check leaves the P6 unknown-state barrier in
            # place; it must not make the SDK hook appear durably recorded.
            raise RuntimeError("unable to record tool postcondition") from exc

    def _remove_invocation(self, task_id: str, tool_name: str, arguments: str | None, invocation_id: str) -> None:
        key = _signature(str(task_id or ""), str(tool_name or ""), _arguments_hash(arguments))
        with self._lock:
            candidates = self._invocations_by_signature.get(key)
            if not candidates:
                return
            self._invocations_by_signature[key] = [item for item in candidates if item != invocation_id]
            if not self._invocations_by_signature[key]:
                self._invocations_by_signature.pop(key, None)

    def _restore_dispatched_invocation(
        self,
        task_id: str,
        tool_name: str,
        arguments: str | None,
        invocation_id: str,
    ) -> None:
        try:
            record = self._journal.tool_invocation(invocation_id)
        except Exception:
            return
        if record is None or str(record.get("status") or "") != "dispatched":
            return
        key = _signature(str(task_id or ""), str(tool_name or ""), _arguments_hash(arguments))
        with self._lock:
            candidates = self._invocations_by_signature[key]
            if invocation_id not in candidates:
                candidates.insert(0, invocation_id)

    def _report_error(self, exc: Exception) -> None:
        if self._on_error is not None:
            self._on_error(exc)


def _signature(task_id: str, tool_name: str, arguments_hash: str) -> tuple[str, str, str]:
    return (str(task_id or ""), str(tool_name or ""), str(arguments_hash or ""))


def _arguments_hash(arguments: str | None) -> str:
    from runtime.agent.tool_invocation_guard import canonical_arguments_hash

    return canonical_arguments_hash(arguments)
