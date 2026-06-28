from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from runtime.safety.command_analyzer import CommandFinding


class CommandStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class CommandRequest:
    command: str
    reason: str = ""
    cwd: str | Path = "."
    source: str = "user"
    timeout_seconds: int = 60
    require_approval: bool = False
    approval_granted: bool = False


@dataclass(frozen=True)
class CommandDecision:
    decision: str
    risk_level: str
    reason: str
    findings: tuple[CommandFinding, ...] = field(default_factory=tuple)
    permission_decision: str = ""
    permission_reason: str = ""

    @property
    def approved_for_execution(self) -> bool:
        return self.decision in {"allow", "allow_limited"}

    @property
    def requires_approval(self) -> bool:
        return self.decision in {"ask", "sandbox_preview"}


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    status: CommandStatus
    decision: CommandDecision
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    source: str = "user"
    reason: str = ""
