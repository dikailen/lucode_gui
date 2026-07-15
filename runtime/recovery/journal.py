from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.recovery.migrations import initialize_run_recovery_schema
from runtime.recovery.checkpoint_codec import sanitize_checkpoint_payload
from runtime.recovery.models import (
    CorruptCheckpointError,
    CorruptRunEventError,
    RunCheckpoint,
    RunJournalEvent,
    canonical_json,
    checkpoint_checksum,
    utc_now_iso,
)
from runtime.recovery.sanitizer import sanitize_payload
from runtime.storage.sqlite_store import connect


TOOL_INVOCATION_STATUSES = frozenset(
    {"prepared", "dispatched", "completed", "failed", "cancelled", "unknown", "reconciled"}
)
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "abandoned")
PRUNED_EVENT_PAYLOAD = {"retention": "payload_pruned.v1"}


class RunJournal:
    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.initialization = initialize_run_recovery_schema(self.workspace_root)

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        status: str,
        client_request_id: str = "",
        created_at: str = "",
    ) -> None:
        now = str(created_at or utc_now_iso())
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into agent_runs(
                  run_id, session_id, client_request_id, status, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(run_id) do nothing
                """,
                (str(run_id), str(session_id), str(client_request_id), str(status), now, now),
            )

    def append_event(
        self,
        *,
        run_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RunJournalEvent:
        clean_payload = _as_mapping(sanitize_payload(payload or {}))
        payload_json = canonical_json(clean_payload)
        now = utc_now_iso()
        with connect(self.workspace_root) as connection:
            connection.execute("begin immediate")
            updated = connection.execute(
                "update agent_runs set next_event_seq = next_event_seq + 1, updated_at = ? where run_id = ?",
                (now, str(run_id)),
            )
            if updated.rowcount != 1:
                raise KeyError(f"unknown run_id: {run_id}")
            row = connection.execute(
                "select next_event_seq from agent_runs where run_id = ?",
                (str(run_id),),
            ).fetchone()
            seq = int(row[0])
            connection.execute(
                """
                insert into run_events(
                  event_id, run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"event_{uuid.uuid4().hex}",
                    str(run_id),
                    str(session_id),
                    seq,
                    str(event_type),
                    payload_json,
                    _sha256(payload_json),
                    now,
                ),
            )
        return RunJournalEvent(
            run_id=str(run_id),
            session_id=str(session_id),
            seq=seq,
            event_type=str(event_type),
            payload=clean_payload,
            created_at=now,
        )

    def events_for_run(self, run_id: str) -> list[RunJournalEvent]:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at
                from run_events where run_id = ? order by seq asc
                """,
                (str(run_id),),
            ).fetchall()
        events: list[RunJournalEvent] = []
        expected_seq = 1
        for row in rows:
            seq = int(row[2])
            if seq != expected_seq:
                raise CorruptRunEventError(
                    f"run event sequence gap: {run_id}: expected {expected_seq}, found {seq}"
                )
            payload_json = str(row[4])
            if _sha256(payload_json) != str(row[5]):
                raise CorruptRunEventError(f"run event checksum mismatch: {run_id}:{row[2]}")
            events.append(
                RunJournalEvent(
                    run_id=str(row[0]),
                    session_id=str(row[1]),
                    seq=seq,
                    event_type=str(row[3]),
                    payload=_as_mapping(json.loads(payload_json)),
                    created_at=str(row[6]),
                )
            )
            expected_seq += 1
        return events

    def find_run_by_client_request_id(self, client_request_id: str) -> dict[str, str] | None:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select run_id, session_id, status, created_at, updated_at
                from agent_runs
                where client_request_id = ? and client_request_id != ''
                order by updated_at desc, run_id desc
                limit 1
                """,
                (str(client_request_id or ""),),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "session_id": str(row[1]),
            "status": str(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }

    def update_run_status(self, *, run_id: str, status: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                "update agent_runs set status = ?, updated_at = ? where run_id = ?",
                (str(status), utc_now_iso(), str(run_id)),
            )
            return updated.rowcount == 1

    def mark_unleased_running_runs_interrupted(self, *, now: str) -> list[dict[str, str]]:
        with connect(self.workspace_root) as connection:
            connection.execute("begin immediate")
            rows = connection.execute(
                """
                select run_id, session_id
                from agent_runs
                where status = 'running'
                  and recovery_state = 'none'
                  and (lease_owner = '' or lease_expires_at <= ?)
                order by updated_at desc, run_id desc
                """,
                (str(now),),
            ).fetchall()
            if rows:
                connection.executemany(
                    """
                    update agent_runs
                    set recovery_state = 'interrupted', interrupted_at = ?, updated_at = ?
                    where run_id = ?
                    """,
                    [(str(now), str(now), str(row[0])) for row in rows],
                )
        return [{"run_id": str(row[0]), "session_id": str(row[1])} for row in rows]

    def recovery_run(self, run_id: str) -> dict[str, str] | None:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select run_id, session_id, status, recovery_state, interrupted_at
                from agent_runs where run_id = ?
                """,
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "session_id": str(row[1]),
            "status": str(row[2]),
            "recovery_state": str(row[3]),
            "interrupted_at": str(row[4]),
        }

    def claim_latest_recovery_run(
        self,
        *,
        session_id: str,
        owner_id: str,
        now: str,
        expires_at: str,
    ) -> dict[str, str] | None:
        """Atomically claim the latest interrupted run for one ordinary message."""

        with connect(self.workspace_root) as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select run_id, session_id, status, recovery_state, interrupted_at,
                       lease_owner, lease_expires_at
                from agent_runs
                where session_id = ?
                  and status = 'running'
                  and recovery_state in ('interrupted', 'recovering', 'blocked')
                order by updated_at desc, run_id desc
                limit 1
                """,
                (str(session_id),),
            ).fetchone()
            if row is None:
                return None
            current_owner = str(row[5] or "")
            current_expiry = str(row[6] or "")
            if current_owner and current_owner != str(owner_id) and current_expiry > str(now):
                raise RecoveryLeaseUnavailableError(str(row[0]))
            updated = connection.execute(
                """
                update agent_runs
                set recovery_state = 'recovering', lease_owner = ?, lease_expires_at = ?, updated_at = ?
                where run_id = ?
                  and status = 'running'
                  and recovery_state in ('interrupted', 'recovering', 'blocked')
                """,
                (str(owner_id), str(expires_at), str(now), str(row[0])),
            )
            if updated.rowcount != 1:
                raise RecoveryLeaseUnavailableError(str(row[0]))
        return {
            "run_id": str(row[0]),
            "session_id": str(row[1]),
            "status": str(row[2]),
            "recovery_state": "recovering",
            "interrupted_at": str(row[4]),
            "lease_owner": str(owner_id),
            "lease_expires_at": str(expires_at),
        }

    def record_pending_approval(
        self,
        *,
        approval_id: str,
        run_id: str,
        action_digest: str,
        invocation_id: str = "",
        requested_at: str = "",
    ) -> None:
        now = str(requested_at or utc_now_iso())
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into run_approvals(
                  approval_id, run_id, invocation_id, action_digest, status, decision, requested_at, resolved_at
                ) values (?, ?, ?, ?, 'requested', '', ?, '')
                on conflict(approval_id) do nothing
                """,
                (str(approval_id), str(run_id), str(invocation_id), str(action_digest), now),
            )

    def expire_pending_approvals(self, *, now: str) -> int:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update run_approvals
                set status = 'expired', resolved_at = ?
                where status = 'requested'
                """,
                (str(now),),
            )
            return int(updated.rowcount)

    def approval_status(self, approval_id: str) -> str:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                "select status from run_approvals where approval_id = ?",
                (str(approval_id),),
            ).fetchone()
        return str(row[0]) if row is not None else ""

    def prepare_tool_invocation(
        self,
        *,
        invocation_id: str,
        run_id: str,
        task_id: str,
        attempt: int,
        tool_name: str,
        arguments_hash: str,
        side_effect_class: str,
    ) -> dict[str, Any]:
        immutable = {
            "run_id": str(run_id),
            "task_id": str(task_id),
            "attempt": max(0, int(attempt)),
            "tool_name": str(tool_name),
            "arguments_hash": str(arguments_hash),
            "side_effect_class": _normalize_side_effect_class(side_effect_class),
        }
        clean_id = str(invocation_id or "").strip()
        if not clean_id:
            raise ValueError("invocation_id is required")
        if not immutable["run_id"] or not immutable["tool_name"] or not immutable["arguments_hash"]:
            raise ValueError("run_id, tool_name, and arguments_hash are required")
        now = utc_now_iso()
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select invocation_id, run_id, task_id, attempt, tool_name, arguments_hash,
                       side_effect_class, status, evidence_ref, created_at, updated_at
                from tool_invocations where invocation_id = ?
                """,
                (clean_id,),
            ).fetchone()
            if row is not None:
                existing = _tool_invocation_row(row)
                if any(existing[key] != value for key, value in immutable.items()):
                    raise ValueError("invocation immutable fields do not match existing record")
                return existing
            connection.execute(
                """
                insert into tool_invocations(
                  invocation_id, run_id, task_id, attempt, tool_name, arguments_hash,
                  side_effect_class, status, evidence_ref, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'prepared', '', ?, ?)
                """,
                (
                    clean_id,
                    immutable["run_id"],
                    immutable["task_id"],
                    immutable["attempt"],
                    immutable["tool_name"],
                    immutable["arguments_hash"],
                    immutable["side_effect_class"],
                    now,
                    now,
                ),
            )
        return self.tool_invocation(clean_id) or {}

    def tool_invocation(self, invocation_id: str) -> dict[str, Any] | None:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select invocation_id, run_id, task_id, attempt, tool_name, arguments_hash,
                       side_effect_class, status, evidence_ref, created_at, updated_at
                from tool_invocations where invocation_id = ?
                """,
                (str(invocation_id or ""),),
            ).fetchone()
        return _tool_invocation_row(row) if row is not None else None

    def tool_invocations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select invocation_id, run_id, task_id, attempt, tool_name, arguments_hash,
                       side_effect_class, status, evidence_ref, created_at, updated_at
                from tool_invocations where run_id = ? order by created_at asc, invocation_id asc
                """,
                (str(run_id or ""),),
            ).fetchall()
        return [_tool_invocation_row(row) for row in rows]

    def record_tool_postcondition(
        self,
        *,
        invocation_id: str,
        kind: str,
        expectation: dict[str, Any],
    ) -> dict[str, Any]:
        clean_invocation_id = str(invocation_id or "").strip()
        clean_kind, clean_expectation = _normalize_postcondition_expectation(kind, expectation)
        invocation = self.tool_invocation(clean_invocation_id)
        if invocation is None:
            raise KeyError(f"unknown invocation_id: {clean_invocation_id}")
        if not _postcondition_kind_is_allowed_for_tool(clean_kind, str(invocation.get("tool_name") or "")):
            raise ValueError("tool postcondition kind is not allowed for this tool")
        expectation_json = canonical_json(clean_expectation)
        expectation_checksum = _sha256(expectation_json)
        now = utc_now_iso()
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select invocation_id, kind, expectation_json, expectation_checksum,
                       status, evidence_ref, observed_json, event_seq, created_at, updated_at
                from tool_postconditions where invocation_id = ?
                """,
                (clean_invocation_id,),
            ).fetchone()
            if row is not None:
                existing = _tool_postcondition_row(row)
                if (
                    existing["kind"] != clean_kind
                    or existing["expectation_checksum"] != expectation_checksum
                ):
                    raise ValueError("tool postcondition immutable fields do not match existing record")
                return existing
            connection.execute(
                """
                insert into tool_postconditions(
                  invocation_id, kind, expectation_json, expectation_checksum,
                  status, evidence_ref, observed_json, event_seq, created_at, updated_at
                ) values (?, ?, ?, ?, 'pending', '', '{}', 0, ?, ?)
                """,
                (clean_invocation_id, clean_kind, expectation_json, expectation_checksum, now, now),
            )
        return self.tool_postcondition(clean_invocation_id) or {}

    def tool_postcondition(self, invocation_id: str) -> dict[str, Any] | None:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select invocation_id, kind, expectation_json, expectation_checksum,
                       status, evidence_ref, observed_json, event_seq, created_at, updated_at
                from tool_postconditions where invocation_id = ?
                """,
                (str(invocation_id or "").strip(),),
            ).fetchone()
        return _tool_postcondition_row(row) if row is not None else None

    def pending_tool_postconditions_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select invocation.invocation_id, invocation.run_id, invocation.task_id,
                       invocation.tool_name, invocation.status,
                       postcondition.kind, postcondition.expectation_json,
                       postcondition.expectation_checksum, postcondition.status,
                       postcondition.evidence_ref, postcondition.observed_json,
                       postcondition.event_seq, postcondition.created_at, postcondition.updated_at
                from tool_postconditions as postcondition
                join tool_invocations as invocation on invocation.invocation_id = postcondition.invocation_id
                where invocation.run_id = ?
                  and invocation.status = 'unknown'
                  and postcondition.status = 'pending'
                order by postcondition.created_at asc, postcondition.invocation_id asc
                """,
                (str(run_id or ""),),
            ).fetchall()
        return [_pending_tool_postcondition_row(row) for row in rows]

    def verified_tool_postconditions_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select invocation.invocation_id, invocation.run_id, invocation.task_id,
                       invocation.tool_name, invocation.status,
                       postcondition.kind, postcondition.expectation_json,
                       postcondition.expectation_checksum, postcondition.status,
                       postcondition.evidence_ref, postcondition.observed_json,
                       postcondition.event_seq, postcondition.created_at, postcondition.updated_at
                from tool_postconditions as postcondition
                join tool_invocations as invocation on invocation.invocation_id = postcondition.invocation_id
                where invocation.run_id = ?
                  and invocation.status in ('unknown', 'reconciled')
                  and postcondition.status = 'verified'
                order by postcondition.updated_at asc, postcondition.invocation_id asc
                """,
                (str(run_id or ""),),
            ).fetchall()
        return [_pending_tool_postcondition_row(row) for row in rows]

    def append_and_bind_verified_tool_postcondition_event(
        self,
        *,
        invocation_id: str,
        run_id: str,
        session_id: str,
    ) -> RunJournalEvent:
        """Atomically bind one verified postcondition to its durable event.

        The legacy separate append/bind calls can leave an unbound event if a
        process dies between transactions. This operation adopts such a matching
        legacy event, or creates and binds one in the same SQLite transaction.
        """

        clean_invocation_id = str(invocation_id or "").strip()
        clean_run_id = str(run_id or "").strip()
        clean_session_id = str(session_id or "").strip()
        if not clean_invocation_id or not clean_run_id or not clean_session_id:
            raise ValueError("tool postcondition event binding requires invocation, run, and session ids")
        with connect(self.workspace_root) as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                """
                select invocation.run_id, run.session_id, invocation.task_id,
                       postcondition.kind, postcondition.expectation_json,
                       postcondition.status, postcondition.evidence_ref,
                       postcondition.observed_json, postcondition.event_seq
                from tool_postconditions as postcondition
                join tool_invocations as invocation on invocation.invocation_id = postcondition.invocation_id
                join agent_runs as run on run.run_id = invocation.run_id
                where postcondition.invocation_id = ?
                """,
                (clean_invocation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown tool postcondition invocation_id: {clean_invocation_id}")
            if str(row[0]) != clean_run_id:
                raise ValueError("tool postcondition does not belong to the recovery run")
            if str(row[1]) != clean_session_id:
                raise ValueError("tool postcondition does not belong to the recovery session")
            if str(row[5]) != "verified":
                raise ValueError("only verified tool postconditions can bind an event")

            kind = str(row[3])
            expectation = _as_mapping(json.loads(str(row[4] or "{}")))
            observed = _normalize_postcondition_observed(
                kind,
                expectation,
                _as_mapping(json.loads(str(row[7] or "{}"))),
                status="verified",
            )
            payload = _as_mapping(
                sanitize_payload(
                    {
                        "invocation_id": clean_invocation_id,
                        "task_id": str(row[2] or ""),
                        "kind": kind,
                        "evidence_ref": str(row[6] or ""),
                        "observed": observed,
                    }
                )
            )
            payload_json = canonical_json(payload)
            bound_event_seq = int(row[8] or 0)
            if bound_event_seq > 0:
                event = _load_postcondition_event(
                    connection,
                    run_id=clean_run_id,
                    event_seq=bound_event_seq,
                    payload_json=payload_json,
                )
                if event is None:
                    raise ValueError("tool postcondition event binding is invalid")
                if event.session_id != clean_session_id:
                    raise ValueError("tool postcondition event belongs to another session")
                return event

            event = _find_matching_postcondition_event(
                connection,
                run_id=clean_run_id,
                payload_json=payload_json,
            )
            if event is not None and event.session_id != clean_session_id:
                raise ValueError("matching tool postcondition event belongs to another session")
            if event is None:
                now = utc_now_iso()
                updated = connection.execute(
                    "update agent_runs set next_event_seq = next_event_seq + 1, updated_at = ? where run_id = ?",
                    (now, clean_run_id),
                )
                if updated.rowcount != 1:
                    raise KeyError(f"unknown run_id: {clean_run_id}")
                next_row = connection.execute(
                    "select next_event_seq from agent_runs where run_id = ?",
                    (clean_run_id,),
                ).fetchone()
                event = RunJournalEvent(
                    run_id=clean_run_id,
                    session_id=clean_session_id,
                    seq=int(next_row[0]),
                    event_type="tool.postcondition_verified",
                    payload=payload,
                    created_at=now,
                )
                connection.execute(
                    """
                    insert into run_events(
                      event_id, run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"event_{uuid.uuid4().hex}",
                        event.run_id,
                        event.session_id,
                        event.seq,
                        event.event_type,
                        payload_json,
                        _sha256(payload_json),
                        event.created_at,
                    ),
                )
            updated = connection.execute(
                """
                update tool_postconditions
                set event_seq = ?, updated_at = ?
                where invocation_id = ? and status = 'verified' and event_seq = 0
                """,
                (event.seq, utc_now_iso(), clean_invocation_id),
            )
            if updated.rowcount != 1:
                raise ValueError("tool postcondition event binding changed concurrently")
        return event

    def resolve_tool_postcondition(
        self,
        *,
        invocation_id: str,
        status: str,
        observed: dict[str, Any],
        evidence_ref: str = "",
    ) -> dict[str, Any]:
        clean_invocation_id = str(invocation_id or "").strip()
        current = self.tool_postcondition(clean_invocation_id)
        if current is None:
            raise KeyError(f"unknown tool postcondition invocation_id: {clean_invocation_id}")
        clean_status = str(status or "").strip().lower()
        if clean_status not in {"verified", "mismatch"}:
            raise ValueError("tool postcondition status must be verified or mismatch")
        clean_observed = _normalize_postcondition_observed(
            current["kind"],
            current["expectation"],
            observed,
            status=clean_status,
        )
        clean_evidence_ref = str(evidence_ref or "").strip()
        if clean_status == "verified" and not clean_evidence_ref:
            raise ValueError("verified tool postcondition requires an evidence_ref")
        if str(current["status"]) != "pending":
            if (
                str(current["status"]) == clean_status
                and current["observed"] == clean_observed
                and str(current["evidence_ref"]) == clean_evidence_ref
            ):
                return current
            raise ValueError("tool postcondition status is already resolved")
        now = utc_now_iso()
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update tool_postconditions
                set status = ?, evidence_ref = ?, observed_json = ?, updated_at = ?
                where invocation_id = ? and status = 'pending'
                """,
                (
                    clean_status,
                    clean_evidence_ref,
                    canonical_json(clean_observed),
                    now,
                    clean_invocation_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("tool postcondition status changed concurrently")
        return self.tool_postcondition(clean_invocation_id) or {}

    def mark_tool_invocation_dispatched(self, invocation_id: str) -> dict[str, Any]:
        return self._transition_tool_invocation(
            invocation_id,
            from_statuses={"prepared"},
            to_status="dispatched",
        )

    def complete_tool_invocation(self, invocation_id: str, *, evidence_ref: str = "") -> dict[str, Any]:
        return self._transition_tool_invocation(
            invocation_id,
            from_statuses={"dispatched"},
            to_status="completed",
            evidence_ref=evidence_ref,
        )

    def fail_tool_invocation(self, invocation_id: str) -> dict[str, Any]:
        return self._transition_tool_invocation(
            invocation_id,
            from_statuses={"prepared", "dispatched"},
            to_status="failed",
        )

    def cancel_prepared_tool_invocation(self, invocation_id: str) -> dict[str, Any]:
        return self._transition_tool_invocation(
            invocation_id,
            from_statuses={"prepared"},
            to_status="cancelled",
        )

    def mark_dispatched_tool_invocations_unknown(self, run_id: str) -> int:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update tool_invocations
                set status = 'unknown', updated_at = ?
                where run_id = ? and status = 'dispatched'
                """,
                (utc_now_iso(), str(run_id)),
            )
            return int(updated.rowcount)

    def reconcile_unknown_tool_invocation(self, invocation_id: str) -> dict[str, Any]:
        clean_invocation_id = str(invocation_id or "").strip()
        postcondition = self.tool_postcondition(clean_invocation_id)
        if postcondition is None:
            raise KeyError(f"unknown tool postcondition invocation_id: {clean_invocation_id}")
        invocation = self.tool_invocation(clean_invocation_id)
        if invocation is None:
            raise KeyError(f"unknown invocation_id: {clean_invocation_id}")
        if not _postcondition_kind_is_allowed_for_tool(
            str(postcondition.get("kind") or ""),
            str(invocation.get("tool_name") or ""),
        ):
            raise ValueError("tool postcondition kind is not allowed for this tool")
        if (
            str(postcondition["status"]) != "verified"
            or not str(postcondition["evidence_ref"] or "").strip()
            or int(postcondition["event_seq"]) <= 0
        ):
            raise ValueError("tool postcondition is not durably verified")
        return self._transition_tool_invocation(
            clean_invocation_id,
            from_statuses={"unknown"},
            to_status="reconciled",
            evidence_ref=str(postcondition["evidence_ref"]),
        )

    def mark_running_dispatched_tool_invocations_unknown(self) -> int:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update tool_invocations
                set status = 'unknown', updated_at = ?
                where status = 'dispatched'
                  and run_id in (select run_id from agent_runs where status = 'running')
                """,
                (utc_now_iso(),),
            )
            return int(updated.rowcount)

    def _transition_tool_invocation(
        self,
        invocation_id: str,
        *,
        from_statuses: set[str],
        to_status: str,
        evidence_ref: str = "",
    ) -> dict[str, Any]:
        normalized_target = _normalize_invocation_status(to_status)
        allowed = {_normalize_invocation_status(item) for item in from_statuses}
        clean_id = str(invocation_id or "").strip()
        current = self.tool_invocation(clean_id)
        if current is None:
            raise KeyError(f"unknown invocation_id: {clean_id}")
        if str(current["status"]) not in allowed:
            raise ValueError(
                f"invalid invocation transition: {current['status']} -> {normalized_target}"
            )
        now = utc_now_iso()
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update tool_invocations
                set status = ?, evidence_ref = ?, updated_at = ?
                where invocation_id = ? and status = ?
                """,
                (
                    normalized_target,
                    str(evidence_ref or current.get("evidence_ref") or ""),
                    now,
                    clean_id,
                    str(current["status"]),
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("tool invocation status changed concurrently")
        return self.tool_invocation(clean_id) or {}

    def abandon_interrupted_run(self, run_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs
                set status = 'abandoned', recovery_state = 'none', updated_at = ?
                where run_id = ? and recovery_state = 'interrupted'
                """,
                (utc_now_iso(), str(run_id)),
            )
            return updated.rowcount == 1

    def resolve_approval(self, *, approval_id: str, status: str, decision: str, resolved_at: str) -> bool:
        if not str(approval_id or "").strip():
            return False
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update run_approvals
                set status = ?, decision = ?, resolved_at = ?
                where approval_id = ? and status = 'requested'
                """,
                (str(status), str(decision), str(resolved_at), str(approval_id)),
            )
            return updated.rowcount == 1

    def acquire_recovery_lease(
        self,
        *,
        run_id: str,
        owner_id: str,
        now: str,
        expires_at: str,
    ) -> bool:
        with connect(self.workspace_root) as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select lease_owner, lease_expires_at from agent_runs where run_id = ?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown run_id: {run_id}")
            current_owner = str(row[0] or "")
            current_expiry = str(row[1] or "")
            if current_owner and current_owner != str(owner_id) and current_expiry > str(now):
                return False
            updated = connection.execute(
                """
                update agent_runs set lease_owner = ?, lease_expires_at = ?, updated_at = ?
                where run_id = ?
                """,
                (str(owner_id), str(expires_at), utc_now_iso(), str(run_id)),
            )
            return updated.rowcount == 1

    def release_recovery_lease(self, *, run_id: str, owner_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs set lease_owner = '', lease_expires_at = '', updated_at = ?
                where run_id = ? and lease_owner = ?
                """,
                (utc_now_iso(), str(run_id), str(owner_id)),
            )
            return updated.rowcount == 1

    def release_recovery_claim(self, *, run_id: str, owner_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs
                set recovery_state = 'interrupted', lease_owner = '', lease_expires_at = '', updated_at = ?
                where run_id = ? and recovery_state = 'recovering' and lease_owner = ?
                """,
                (utc_now_iso(), str(run_id), str(owner_id)),
            )
            return updated.rowcount == 1

    def complete_recovery_claim(self, *, run_id: str, owner_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs
                set status = 'abandoned', recovery_state = 'none',
                    lease_owner = '', lease_expires_at = '', updated_at = ?
                where run_id = ? and recovery_state = 'recovering' and lease_owner = ?
                """,
                (utc_now_iso(), str(run_id), str(owner_id)),
            )
            return updated.rowcount == 1

    def block_recovery_claim(self, *, run_id: str, owner_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs
                set recovery_state = 'blocked', lease_owner = '', lease_expires_at = '', updated_at = ?
                where run_id = ? and recovery_state = 'recovering' and lease_owner = ?
                """,
                (utc_now_iso(), str(run_id), str(owner_id)),
            )
            return updated.rowcount == 1

    def write_checkpoint(
        self,
        *,
        run_id: str,
        kind: str,
        state: dict[str, Any],
        compatibility: dict[str, Any],
        event_seq: int = 0,
    ) -> RunCheckpoint:
        clean_state = _as_mapping(sanitize_checkpoint_payload(state))
        clean_compatibility = _as_mapping(sanitize_payload(compatibility))
        checksum = checkpoint_checksum(kind=str(kind), state=clean_state, compatibility=clean_compatibility)
        checkpoint = RunCheckpoint(
            checkpoint_id=f"checkpoint_{uuid.uuid4().hex}",
            run_id=str(run_id),
            kind=str(kind),
            event_seq=int(event_seq),
            state=clean_state,
            compatibility=clean_compatibility,
            checksum=checksum,
            created_at=utc_now_iso(),
        )
        with connect(self.workspace_root) as connection:
            connection.execute(
                """
                insert into run_checkpoints(
                  checkpoint_id, run_id, kind, event_seq, state_json, compatibility_json, checksum, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.run_id,
                    checkpoint.kind,
                    checkpoint.event_seq,
                    canonical_json(checkpoint.state),
                    canonical_json(checkpoint.compatibility),
                    checkpoint.checksum,
                    checkpoint.created_at,
                ),
            )
        return checkpoint

    def load_checkpoint(self, checkpoint_id: str) -> RunCheckpoint:
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select checkpoint_id, run_id, kind, event_seq, state_json, compatibility_json, checksum, created_at
                from run_checkpoints where checkpoint_id = ?
                """,
                (str(checkpoint_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown checkpoint_id: {checkpoint_id}")
        state = _as_mapping(json.loads(str(row[4])))
        compatibility = _as_mapping(json.loads(str(row[5])))
        checksum = str(row[6])
        expected = checkpoint_checksum(kind=str(row[2]), state=state, compatibility=compatibility)
        if checksum != expected:
            raise CorruptCheckpointError(f"checkpoint checksum mismatch: {checkpoint_id}")
        return RunCheckpoint(
            checkpoint_id=str(row[0]),
            run_id=str(row[1]),
            kind=str(row[2]),
            event_seq=int(row[3]),
            state=state,
            compatibility=compatibility,
            checksum=checksum,
            created_at=str(row[7]),
        )

    def latest_checkpoint_for_run(
        self,
        run_id: str,
        *,
        kinds: tuple[str, ...] | list[str] | None = None,
    ) -> RunCheckpoint | None:
        clean_kinds = [str(item) for item in list(kinds or []) if str(item or "").strip()]
        parameters: list[Any] = [str(run_id)]
        where_kind = ""
        if clean_kinds:
            where_kind = " and kind in (" + ",".join("?" for _ in clean_kinds) + ")"
            parameters.extend(clean_kinds)
        with connect(self.workspace_root) as connection:
            row = connection.execute(
                """
                select checkpoint_id
                from run_checkpoints
                where run_id = ?
                """
                + where_kind
                + " order by event_seq desc, rowid desc limit 1",
                tuple(parameters),
            ).fetchone()
        if row is None:
            return None
        return self.load_checkpoint(str(row[0]))

    def final_checkpoint_run_ids(self, *, limit: int = 200) -> list[str]:
        with connect(self.workspace_root) as connection:
            rows = connection.execute(
                """
                select checkpoint.run_id, max(checkpoint.created_at) as latest_created_at
                from run_checkpoints as checkpoint
                join agent_runs as run on run.run_id = checkpoint.run_id
                where checkpoint.kind = 'final.ready'
                  and run.status in ('running', 'completed')
                group by checkpoint.run_id
                order by latest_created_at desc, checkpoint.run_id desc
                limit ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def mark_checkpointed_run_completed(self, run_id: str) -> bool:
        with connect(self.workspace_root) as connection:
            updated = connection.execute(
                """
                update agent_runs
                set status = 'completed', recovery_state = 'none',
                    lease_owner = '', lease_expires_at = '', updated_at = ?
                where run_id = ?
                """,
                (utc_now_iso(), str(run_id)),
            )
            return updated.rowcount == 1

    def prune_terminal_event_payloads(
        self,
        *,
        before: str,
        dry_run: bool = True,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Prune detail payloads only for expired terminal runs.

        This is an explicit maintenance operation. It never deletes run rows,
        checkpoints, or evidence references, and does not run during startup.
        """

        cutoff = _normalize_retention_cutoff(before)
        max_runs = max(1, int(limit))
        marker_json = canonical_json(PRUNED_EVENT_PAYLOAD)
        marker_checksum = _sha256(marker_json)
        with connect(self.workspace_root) as connection:
            if dry_run:
                rows = _terminal_event_payload_prune_candidates(connection, cutoff=cutoff, limit=max_runs)
            else:
                connection.execute("begin immediate")
                rows = _terminal_event_payload_prune_candidates(connection, cutoff=cutoff, limit=max_runs)
                for run_id, event_count in rows:
                    updated = connection.execute(
                        """
                        update run_events
                        set payload_json = ?, payload_checksum = ?
                        where run_id = ? and created_at < ? and payload_json != ?
                        """,
                        (marker_json, marker_checksum, str(run_id), cutoff, marker_json),
                    )
                    if updated.rowcount != int(event_count):
                        raise RuntimeError("terminal event payload retention changed concurrently")
        return {
            "run_ids": tuple(str(run_id) for run_id, _event_count in rows),
            "event_count": sum(int(event_count) for _run_id, event_count in rows),
            "dry_run": bool(dry_run),
        }


def _terminal_event_payload_prune_candidates(connection, *, cutoff: str, limit: int) -> list[tuple[str, int]]:
    marker_json = canonical_json(PRUNED_EVENT_PAYLOAD)
    placeholders = ", ".join("?" for _status in TERMINAL_RUN_STATUSES)
    rows = connection.execute(
        """
        select run.run_id, count(event.event_id)
        from agent_runs as run
        join run_events as event on event.run_id = run.run_id
        where run.status in ("""
        + placeholders
        + """)
          and run.updated_at < ?
          and event.created_at < ?
          and event.payload_json != ?
        group by run.run_id
        order by min(event.created_at) asc, run.run_id asc
        limit ?
        """,
        (*TERMINAL_RUN_STATUSES, cutoff, cutoff, marker_json, max(1, int(limit))),
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def _normalize_retention_cutoff(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("retention cutoff must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("retention cutoff must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_invocation_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in TOOL_INVOCATION_STATUSES:
        raise ValueError(f"unsupported tool invocation status: {status or 'missing'}")
    return status


def _normalize_side_effect_class(value: str) -> str:
    try:
        from runtime.tools.registry import normalize_tool_side_effect_class

        return normalize_tool_side_effect_class(value)
    except Exception:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"read_only", "idempotent_write", "non_idempotent"} else "unknown"


def _tool_invocation_row(row) -> dict[str, Any]:
    return {
        "invocation_id": str(row[0]),
        "run_id": str(row[1]),
        "task_id": str(row[2]),
        "attempt": int(row[3]),
        "tool_name": str(row[4]),
        "arguments_hash": str(row[5]),
        "side_effect_class": str(row[6]),
        "status": str(row[7]),
        "evidence_ref": str(row[8]),
        "created_at": str(row[9]),
        "updated_at": str(row[10]),
    }


def _normalize_postcondition_expectation(kind: str, expectation: dict[str, Any]) -> tuple[str, dict[str, str]]:
    clean_kind = str(kind or "").strip()
    source = dict(expectation or {}) if isinstance(expectation, dict) else {}
    if clean_kind == "browser_navigation_url_sha256.v1":
        tab_id = str(source.get("tab_id") or "").strip()
        expected_url_sha256 = str(source.get("expected_url_sha256") or "").strip().lower()
        if not _is_postcondition_tab_id(tab_id):
            raise ValueError("browser navigation postcondition requires a stable tab_id")
        if not _is_postcondition_sha256(expected_url_sha256):
            raise ValueError("browser navigation postcondition requires a URL sha256 digest")
        return clean_kind, {"tab_id": tab_id, "expected_url_sha256": expected_url_sha256}
    if clean_kind != "workspace_file_sha256.v1":
        raise ValueError(f"unsupported tool postcondition kind: {clean_kind or 'missing'}")
    path = str(source.get("path") or "").strip().replace("\\", "/")
    expected_sha256 = str(source.get("expected_sha256") or "").strip().lower()
    if not path or path.startswith("/") or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError("workspace file postcondition path must be a normalized relative path")
    if not _is_postcondition_sha256(expected_sha256):
        raise ValueError("workspace file postcondition requires a sha256 digest")
    return clean_kind, {"path": path, "expected_sha256": expected_sha256}


def _tool_postcondition_row(row) -> dict[str, Any]:
    expectation_json = str(row[2] or "{}")
    observed_json = str(row[6] or "{}")
    return {
        "invocation_id": str(row[0]),
        "kind": str(row[1]),
        "expectation": _as_mapping(json.loads(expectation_json)),
        "expectation_checksum": str(row[3]),
        "status": str(row[4]),
        "evidence_ref": str(row[5]),
        "observed": _as_mapping(json.loads(observed_json)),
        "event_seq": int(row[7]),
        "created_at": str(row[8]),
        "updated_at": str(row[9]),
    }


def _find_matching_postcondition_event(connection, *, run_id: str, payload_json: str) -> RunJournalEvent | None:
    row = connection.execute(
        """
        select run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at
        from run_events
        where run_id = ? and event_type = 'tool.postcondition_verified' and payload_json = ?
        order by seq asc
        limit 1
        """,
        (str(run_id), str(payload_json)),
    ).fetchone()
    return _run_journal_event_from_row(row) if row is not None else None


def _load_postcondition_event(
    connection,
    *,
    run_id: str,
    event_seq: int,
    payload_json: str,
) -> RunJournalEvent | None:
    row = connection.execute(
        """
        select run_id, session_id, seq, event_type, payload_json, payload_checksum, created_at
        from run_events where run_id = ? and seq = ?
        """,
        (str(run_id), int(event_seq)),
    ).fetchone()
    if row is None:
        return None
    event = _run_journal_event_from_row(row)
    if event.event_type != "tool.postcondition_verified" or canonical_json(event.payload) != str(payload_json):
        return None
    return event


def _run_journal_event_from_row(row) -> RunJournalEvent:
    payload_json = str(row[4])
    if _sha256(payload_json) != str(row[5]):
        raise CorruptRunEventError(f"run event checksum mismatch: {row[0]}:{row[2]}")
    payload = _as_mapping(json.loads(payload_json))
    return RunJournalEvent(
        run_id=str(row[0]),
        session_id=str(row[1]),
        seq=int(row[2]),
        event_type=str(row[3]),
        payload=payload,
        created_at=str(row[6]),
    )


def _pending_tool_postcondition_row(row) -> dict[str, Any]:
    expectation_json = str(row[6] or "{}")
    observed_json = str(row[10] or "{}")
    return {
        "invocation_id": str(row[0]),
        "run_id": str(row[1]),
        "task_id": str(row[2]),
        "tool_name": str(row[3]),
        "invocation_status": str(row[4]),
        "kind": str(row[5]),
        "expectation": _as_mapping(json.loads(expectation_json)),
        "expectation_checksum": str(row[7]),
        "status": str(row[8]),
        "evidence_ref": str(row[9]),
        "observed": _as_mapping(json.loads(observed_json)),
        "event_seq": int(row[11]),
        "created_at": str(row[12]),
        "updated_at": str(row[13]),
    }


def _normalize_postcondition_observed(
    kind: str,
    expectation: dict[str, Any],
    observed: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    clean_kind, clean_expectation = _normalize_postcondition_expectation(kind, expectation)
    source = dict(observed or {}) if isinstance(observed, dict) else {}
    if clean_kind == "browser_navigation_url_sha256.v1":
        tab_id = str(source.get("tab_id") or clean_expectation["tab_id"]).strip()
        if tab_id != clean_expectation["tab_id"]:
            raise ValueError("browser navigation observed tab_id does not match expectation")
        actual_url_sha256 = str(source.get("url_sha256") or "").strip().lower()
        if actual_url_sha256 and not _is_postcondition_sha256(actual_url_sha256):
            raise ValueError("browser navigation observed URL sha256 is invalid")
        if status == "verified":
            if actual_url_sha256 != clean_expectation["expected_url_sha256"]:
                raise ValueError("verified browser navigation URL hash does not match expectation")
            return {"tab_id": tab_id, "url_sha256": actual_url_sha256}
        result: dict[str, Any] = {"tab_id": tab_id}
        if actual_url_sha256:
            result["url_sha256"] = actual_url_sha256
        if source.get("exists") is False:
            result["exists"] = False
        return result
    if clean_kind != "workspace_file_sha256.v1":
        raise ValueError(f"unsupported tool postcondition kind: {clean_kind}")
    path = str(source.get("path") or clean_expectation["path"]).strip().replace("\\", "/")
    if path != clean_expectation["path"]:
        raise ValueError("tool postcondition observed path does not match expectation")
    actual_sha256 = str(source.get("sha256") or "").strip().lower()
    if actual_sha256:
        if len(actual_sha256) != 64 or any(char not in "0123456789abcdef" for char in actual_sha256):
            raise ValueError("tool postcondition observed sha256 is invalid")
    if status == "verified":
        if actual_sha256 != clean_expectation["expected_sha256"]:
            raise ValueError("verified tool postcondition hash does not match expectation")
        return {"path": path, "sha256": actual_sha256}
    result: dict[str, Any] = {"path": path}
    if actual_sha256:
        result["sha256"] = actual_sha256
    if source.get("exists") is False:
        result["exists"] = False
    return result


def _is_postcondition_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _is_postcondition_tab_id(value: str) -> bool:
    return bool(value and len(value) <= 256 and not any(ord(char) < 32 for char in value))


def _postcondition_kind_is_allowed_for_tool(kind: str, tool_name: str) -> bool:
    normalized_kind = str(kind or "").strip()
    normalized_tool_name = str(tool_name or "").strip().lower()
    if normalized_kind == "workspace_file_sha256.v1":
        return normalized_tool_name in {"workspace_edit.write_file", "workspace_edit.create_file"}
    if normalized_kind == "browser_navigation_url_sha256.v1":
        return normalized_tool_name == "desktop_browser.browser_navigate"
    return False


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class RecoveryLeaseUnavailableError(RuntimeError):
    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        super().__init__(f"recovery lease is already held for run: {self.run_id}")
