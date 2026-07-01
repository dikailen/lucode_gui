from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event


@dataclass(frozen=True)
class SandboxExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


class PolicySandbox:
    """Run an argv command inside the current policy sandbox boundary.

    This is not an OS-level sandbox. It is the shared execution primitive for
    commands that have already passed analyzer and permission checks.
    """

    def __init__(self, *, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        cancel_event: Event | None = None,
    ) -> SandboxExecutionResult:
        timeout = max(1, min(int(timeout_seconds or 60), 300))
        if cancel_event and cancel_event.is_set():
            return SandboxExecutionResult(
                returncode=-9,
                stdout="",
                stderr="Command cancelled before start.",
                cancelled=True,
            )
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError:
            return SandboxExecutionResult(
                returncode=127,
                stdout="",
                stderr=f"Executable not found: {argv[0] if argv else ''}",
            )

        started_at = time.monotonic()
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.05)
                return SandboxExecutionResult(
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            except subprocess.TimeoutExpired:
                if cancel_event and cancel_event.is_set():
                    process.kill()
                    stdout, stderr = process.communicate()
                    stderr = _append_process_message(stderr, "Command cancelled.")
                    return SandboxExecutionResult(
                        returncode=-9,
                        stdout=stdout,
                        stderr=stderr,
                        cancelled=True,
                    )
                if time.monotonic() - started_at >= timeout:
                    process.kill()
                    stdout, stderr = process.communicate()
                    stderr = _append_process_message(stderr, f"Command timed out after {timeout} seconds.")
                    return SandboxExecutionResult(
                        returncode=124,
                        stdout=stdout,
                        stderr=stderr,
                        timed_out=True,
                    )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _append_process_message(stderr: str | bytes | None, message: str) -> str:
    decoded = _decode_timeout_output(stderr).rstrip()
    if decoded:
        return f"{decoded}\n{message}"
    return message
