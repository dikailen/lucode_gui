from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event

from runtime.safety.command_analyzer import analyze_command
from runtime.safety.permissions import evaluate_permission, load_effective_permissions
from runtime.terminal.models import CommandDecision, CommandRequest, CommandResult, CommandStatus
from runtime.terminal.policy_sandbox import PolicySandbox


class CommandGateway:
    """Shared entry point for controlled local command execution."""

    def __init__(self, *, workspace_root: Path | str, sandbox: PolicySandbox | None = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.sandbox = sandbox or PolicySandbox(workspace_root=self.workspace_root)

    def evaluate(self, request: CommandRequest) -> CommandDecision:
        analysis = analyze_command(request.command)
        if analysis.should_deny or analysis.decision == "deny":
            return CommandDecision(
                decision="deny",
                risk_level=analysis.risk_level,
                reason=analysis.blocking_summary or analysis.decision_reason,
                findings=analysis.findings,
            )

        policy = load_effective_permissions(self.workspace_root)
        permission = evaluate_permission(policy, "shell", command=" ".join(analysis.argv))
        if permission.decision == "deny":
            return CommandDecision(
                decision="deny",
                risk_level=analysis.risk_level,
                reason=permission.reason,
                findings=analysis.findings,
                permission_decision=permission.decision,
                permission_reason=permission.reason,
            )

        decision = analysis.decision
        if request.source != "user" and permission.decision == "ask":
            decision = "ask"
        if request.require_approval and decision == "allow":
            decision = "ask"
        return CommandDecision(
            decision=decision,
            risk_level=analysis.risk_level,
            reason=analysis.decision_reason or permission.reason,
            findings=analysis.findings,
            permission_decision=permission.decision,
            permission_reason=permission.reason,
        )

    def run(self, request: CommandRequest, *, cancel_event: Event | None = None) -> CommandResult:
        started_at = datetime.now()
        decision = self.evaluate(request)
        cwd_error = self._validate_cwd(request.cwd)
        if cwd_error:
            decision = CommandDecision(
                decision="deny",
                risk_level="high",
                reason=cwd_error,
                findings=decision.findings,
                permission_decision=decision.permission_decision,
                permission_reason=decision.permission_reason,
            )
        if not decision.approved_for_execution and not (
            request.approval_granted and decision.requires_approval
        ):
            ended_at = datetime.now()
            return CommandResult(
                command=request.command,
                cwd=self._safe_cwd(request.cwd),
                returncode=-1,
                stdout="",
                stderr=f"Command denied: {decision.reason}",
                status=CommandStatus.DENIED,
                decision=decision,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=_duration_ms(started_at, ended_at),
                source=request.source,
                reason=request.reason,
            )

        analysis = analyze_command(request.command)
        cwd = self._safe_cwd(request.cwd)
        sandbox_result = self.sandbox.run(
            analysis.argv,
            cwd=cwd,
            timeout_seconds=request.timeout_seconds,
            cancel_event=cancel_event,
        )
        ended_at = datetime.now()
        status = _status_from_execution(
            sandbox_result.returncode,
            timed_out=sandbox_result.timed_out,
            cancelled=sandbox_result.cancelled,
        )
        return CommandResult(
            command=request.command,
            cwd=cwd,
            returncode=sandbox_result.returncode,
            stdout=_truncate(sandbox_result.stdout),
            stderr=_truncate(sandbox_result.stderr),
            status=status,
            decision=decision,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=_duration_ms(started_at, ended_at),
            source=request.source,
            reason=request.reason,
        )

    def _validate_cwd(self, cwd: str | Path) -> str:
        resolved = self._safe_cwd(cwd)
        try:
            if not resolved.is_relative_to(self.workspace_root):
                return f"cwd is outside workspace: {resolved}"
        except ValueError:
            return f"cwd is outside workspace: {resolved}"
        if not resolved.exists():
            return f"cwd does not exist: {resolved}"
        if not resolved.is_dir():
            return f"cwd is not a directory: {resolved}"
        return ""

    def _safe_cwd(self, cwd: str | Path) -> Path:
        candidate = Path(cwd)
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.workspace_root / candidate).resolve()


def _status_from_execution(returncode: int, *, timed_out: bool, cancelled: bool = False) -> CommandStatus:
    if cancelled:
        return CommandStatus.CANCELLED
    if timed_out or returncode == 124:
        return CommandStatus.TIMEOUT
    if returncode == 0:
        return CommandStatus.SUCCESS
    return CommandStatus.FAILED


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


def _truncate(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"
