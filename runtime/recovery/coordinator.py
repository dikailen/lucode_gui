from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from runtime.recovery.checkpoint_codec import PIPELINE_CHECKPOINT_SCHEMA_VERSION, decode_pipeline_checkpoint
from runtime.recovery.envelope import RecoveryEnvelope
from runtime.recovery.journal import RunJournal
from runtime.recovery.models import RecoveryDecision, utc_now_iso


@dataclass(frozen=True)
class RecoveryClaim:
    source_run_id: str
    owner_id: str
    envelope: RecoveryEnvelope


class RecoveryCoordinator:
    """Classifies interrupted runs without scheduling execution or tools."""

    def __init__(self, journal: RunJournal, *, now: Callable[[], str] = utc_now_iso) -> None:
        self._journal = journal
        self._now = now

    def scan_startup(self) -> list[RecoveryDecision]:
        now = self._now()
        self._journal.expire_pending_approvals(now=now)
        self._journal.mark_running_dispatched_tool_invocations_unknown()
        interrupted = self._journal.mark_unleased_running_runs_interrupted(now=now)
        return [
            RecoveryDecision(
                run_id=item["run_id"],
                session_id=item["session_id"],
                action="replan",
                reason_code="interrupted_runtime",
            )
            for item in interrupted
        ]

    def claim_for_message(
        self,
        *,
        session_id: str,
        owner_id: str,
        lease_seconds: int = 300,
    ) -> RecoveryClaim | None:
        now = self._now()
        claimed = self._journal.claim_latest_recovery_run(
            session_id=session_id,
            owner_id=owner_id,
            now=now,
            expires_at=_add_seconds(now, lease_seconds),
        )
        if claimed is None:
            return None
        try:
            checkpoint = self._journal.latest_checkpoint_for_run(str(claimed["run_id"]))
            if checkpoint is None:
                state = {
                    "schema_version": PIPELINE_CHECKPOINT_SCHEMA_VERSION,
                    "route_type": "",
                    "reason": "checkpoint unavailable",
                    "tasks": [],
                    "accepted_evidence": {"mode": "off", "claims": [], "evidence": [], "blocked_claims": []},
                    "blocked_items": [{"task_id": "", "reason_code": "checkpoint_unavailable"}],
                }
                compatibility = {"snapshot_schema": PIPELINE_CHECKPOINT_SCHEMA_VERSION}
                checkpoint_id = ""
                checkpoint_kind = ""
            else:
                state = decode_pipeline_checkpoint(checkpoint.state)
                compatibility = dict(checkpoint.compatibility or {})
                checkpoint_id = checkpoint.checkpoint_id
                checkpoint_kind = checkpoint.kind
            reconciled_items = _reconcile_verified_tool_postconditions(
                self._journal,
                run_id=str(claimed["run_id"]),
                session_id=str(claimed["session_id"]),
                checkpoint_id=checkpoint_id,
            )
            try:
                from runtime.recovery.policy import recovery_blocked_items_for_tool_invocations

                lifecycle_blocked = recovery_blocked_items_for_tool_invocations(
                    self._journal.tool_invocations_for_run(str(claimed["run_id"]))
                )
            except Exception:
                lifecycle_blocked = [{"task_id": "", "reason_code": "tool_lifecycle_unavailable"}]
            state = dict(state)
            state["blocked_items"] = _merge_blocked_items(
                list(state.get("blocked_items") or []),
                lifecycle_blocked,
            )
            envelope = RecoveryEnvelope(
                source_run_id=str(claimed["run_id"]),
                session_id=str(claimed["session_id"]),
                checkpoint_id=checkpoint_id,
                checkpoint_kind=checkpoint_kind,
                checkpoint=state,
                compatibility=compatibility,
                blocked_items=tuple(state.get("blocked_items") or ()),
                reconciled_items=tuple(reconciled_items),
            )
            return RecoveryClaim(
                source_run_id=str(claimed["run_id"]),
                owner_id=str(owner_id),
                envelope=envelope,
            )
        except Exception:
            try:
                self._journal.release_recovery_claim(
                    run_id=str(claimed["run_id"]),
                    owner_id=str(owner_id),
                )
            except Exception:
                pass
            raise

    def complete_claim(self, claim: RecoveryClaim) -> bool:
        return self._journal.complete_recovery_claim(
            run_id=claim.source_run_id,
            owner_id=claim.owner_id,
        )

    def release_claim(self, claim: RecoveryClaim) -> bool:
        return self._journal.release_recovery_claim(
            run_id=claim.source_run_id,
            owner_id=claim.owner_id,
        )

    def block_claim(self, claim: RecoveryClaim) -> bool:
        return self._journal.block_recovery_claim(
            run_id=claim.source_run_id,
            owner_id=claim.owner_id,
        )


def _add_seconds(value: str, seconds: int) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    result = parsed + timedelta(seconds=max(1, int(seconds)))
    return result.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _merge_blocked_items(*groups) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for group in groups:
        for item in list(group or []):
            if not isinstance(item, dict):
                continue
            normalized = {
                "task_id": str(item.get("task_id") or ""),
                "reason_code": str(item.get("reason_code") or ""),
            }
            if normalized not in result:
                result.append(normalized)
    return result


def _reconcile_verified_tool_postconditions(
    journal: RunJournal,
    *,
    run_id: str,
    session_id: str,
    checkpoint_id: str,
) -> list[dict[str, object]]:
    from runtime.recovery.postconditions import reconcile_pending_tool_postconditions

    reconcile_pending_tool_postconditions(journal, run_id=run_id)
    records = journal.verified_tool_postconditions_for_run(run_id)
    reconciled: list[dict[str, object]] = []
    for record in records:
        invocation_id = str(record.get("invocation_id") or "")
        event_seq = int(record.get("event_seq") or 0)
        if event_seq <= 0:
            event = journal.append_and_bind_verified_tool_postcondition_event(
                invocation_id=invocation_id,
                run_id=run_id,
                session_id=session_id,
            )
            event_seq = int(event.seq)
            record = dict(record)
            record["event_seq"] = event_seq
        if str(record.get("invocation_status") or "") == "unknown":
            journal.reconcile_unknown_tool_invocation(invocation_id)
        elif str(record.get("invocation_status") or "") != "reconciled":
            raise ValueError("verified postcondition invocation is not recoverable")
        reconciled.append(
            {
                "invocation_id": invocation_id,
                "task_id": str(record.get("task_id") or ""),
                "tool_name": str(record.get("tool_name") or ""),
                "kind": str(record.get("kind") or ""),
                "evidence_ref": str(record.get("evidence_ref") or ""),
                "source_run_id": run_id,
                "checkpoint_id": checkpoint_id,
                "event_seq": event_seq,
                "observed": dict(record.get("observed") or {}),
            }
        )
    return reconciled
