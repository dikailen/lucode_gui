from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from runtime.terminal.command_gateway import CommandGateway
from runtime.terminal.models import CommandRequest, CommandResult, CommandStatus


@dataclass(frozen=True)
class TerminalHistoryEntry:
    command: str
    cwd: Path
    reason: str
    source: str
    status: CommandStatus
    returncode: int
    duration_ms: int


@dataclass(frozen=True)
class TerminalEvent:
    type: str
    command_id: str
    command: str
    cwd: Path
    message: str = ""
    result: CommandResult | None = None


@dataclass(frozen=True)
class TerminalTranscriptEntry:
    kind: str
    command_id: str
    text: str
    result: CommandResult | None = None


@dataclass
class _RunningCommand:
    command_id: str
    request: CommandRequest
    cancel_event: Event
    thread: Thread
    result: CommandResult | None = None


TerminalEventHandler = Callable[[TerminalEvent], None]


class TerminalSession:
    """Line-command terminal session backed by CommandGateway.

    This is intentionally not a PTY. It provides the runtime state that a future
    GUI panel can bind to while keeping command execution behind CommandGateway.
    """

    def __init__(
        self,
        *,
        workspace_root: Path | str,
        gateway: CommandGateway | None = None,
        event_handler: TerminalEventHandler | None = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.gateway = gateway or CommandGateway(workspace_root=self.workspace_root)
        self.current_cwd = self.workspace_root
        self.event_handler = event_handler
        self.history: list[TerminalHistoryEntry] = []
        self.transcript: list[TerminalTranscriptEntry] = []
        self.last_result: CommandResult | None = None
        self._running: _RunningCommand | None = None
        self._lock = Lock()

    @property
    def is_running(self) -> bool:
        running = self._running
        return bool(running and running.thread.is_alive())

    @property
    def running_command_id(self) -> str:
        running = self._running
        if running is None or not running.thread.is_alive():
            return ""
        return running.command_id

    @property
    def running_command(self) -> str:
        running = self._running
        if running is None or not running.thread.is_alive():
            return ""
        return running.request.command

    def set_cwd(self, cwd: str | Path) -> Path:
        resolved = self._resolve_cwd(cwd)
        if not resolved.exists():
            raise FileNotFoundError(f"cwd does not exist: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"cwd is not a directory: {resolved}")
        try:
            if not resolved.is_relative_to(self.workspace_root):
                raise ValueError(f"cwd is outside workspace: {resolved}")
        except ValueError as exc:
            raise ValueError(f"cwd is outside workspace: {resolved}") from exc
        self.current_cwd = resolved
        return resolved

    def run(
        self,
        command: str,
        *,
        reason: str = "",
        source: str = "user",
        cwd: str | Path | None = None,
        timeout_seconds: int = 60,
        require_approval: bool = False,
        approval_granted: bool = False,
    ) -> CommandResult:
        command_id = self.start(
            command,
            reason=reason,
            source=source,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            require_approval=require_approval,
            approval_granted=approval_granted,
        )
        result = self.wait(command_id=command_id)
        if result is None:
            raise RuntimeError("terminal command finished without a result")
        return result

    def start(
        self,
        command: str,
        *,
        reason: str = "",
        source: str = "user",
        cwd: str | Path | None = None,
        timeout_seconds: int = 60,
        require_approval: bool = False,
        approval_granted: bool = False,
    ) -> str:
        with self._lock:
            if self.is_running:
                raise RuntimeError("terminal session already has a running command")
            command_id = uuid4().hex
            request = CommandRequest(
                command=command,
                reason=reason,
                cwd=self._request_cwd(cwd),
                source=source,
                timeout_seconds=timeout_seconds,
                require_approval=require_approval,
                approval_granted=approval_granted,
            )
            cancel_event = Event()
            running = _RunningCommand(
                command_id=command_id,
                request=request,
                cancel_event=cancel_event,
                thread=Thread(
                    target=self._run_background,
                    args=(command_id, request, cancel_event),
                    name=f"lucode-terminal-{command_id[:8]}",
                    daemon=True,
                ),
            )
            self._running = running
            self._record_transcript("command", command_id, command)
            self._emit("command_started", command_id, command, self._resolve_cwd(request.cwd))
            running.thread.start()
            return command_id

    def wait(self, *, command_id: str | None = None, timeout_seconds: float | None = None) -> CommandResult | None:
        running = self._running
        if running is None:
            return self.last_result
        if command_id is not None and running.command_id != command_id:
            return self.last_result
        running.thread.join(timeout_seconds)
        if running.thread.is_alive():
            return None
        return running.result

    def cancel(self) -> bool:
        running = self._running
        if running is None or not running.thread.is_alive():
            return False
        running.cancel_event.set()
        return True

    def clear(self) -> None:
        self.transcript.clear()

    def rerun(self) -> CommandResult:
        if not self.history:
            raise RuntimeError("terminal session has no command history to rerun")
        entry = self.history[-1]
        return self.run(
            entry.command,
            reason=entry.reason,
            source=entry.source,
            cwd=entry.cwd,
        )

    def _run_background(self, command_id: str, request: CommandRequest, cancel_event: Event) -> None:
        result = self.gateway.run(request, cancel_event=cancel_event)
        running = self._running
        if running and running.command_id == command_id:
            running.result = result
        self.last_result = result
        self.history.append(
            TerminalHistoryEntry(
                command=result.command,
                cwd=result.cwd,
                reason=result.reason,
                source=result.source,
                status=result.status,
                returncode=result.returncode,
                duration_ms=result.duration_ms,
            )
        )
        if result.stdout:
            self._record_transcript("stdout", command_id, result.stdout)
            self._emit("stdout_chunk", command_id, result.command, result.cwd, result.stdout, result=result)
        if result.stderr:
            event_type = "command_denied" if result.status is CommandStatus.DENIED else "stderr_chunk"
            self._record_transcript("stderr", command_id, result.stderr)
            self._emit(event_type, command_id, result.command, result.cwd, result.stderr, result=result)
        finish_event = "command_cancelled" if result.status is CommandStatus.CANCELLED else "command_finished"
        self._record_transcript("result", command_id, result.status.value, result=result)
        self._emit(finish_event, command_id, result.command, result.cwd, result=result)
        if self._running and self._running.command_id == command_id:
            self._running = None

    def _emit(
        self,
        event_type: str,
        command_id: str,
        command: str,
        cwd: Path,
        message: str = "",
        *,
        result: CommandResult | None = None,
    ) -> None:
        if not self.event_handler:
            return
        self.event_handler(
            TerminalEvent(
                type=event_type,
                command_id=command_id,
                command=command,
                cwd=cwd,
                message=message,
                result=result,
            )
        )

    def _record_transcript(
        self,
        kind: str,
        command_id: str,
        text: str,
        *,
        result: CommandResult | None = None,
    ) -> None:
        self.transcript.append(
            TerminalTranscriptEntry(
                kind=kind,
                command_id=command_id,
                text=text,
                result=result,
            )
        )

    def _request_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None:
            return self.current_cwd
        return self.set_cwd(cwd)

    def _resolve_cwd(self, cwd: str | Path) -> Path:
        candidate = Path(cwd)
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.current_cwd / candidate).resolve()
