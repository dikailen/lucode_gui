from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class PolicySandbox:
    """Run an argv command inside the current policy sandbox boundary.

    This is not an OS-level sandbox. It is the shared execution primitive for
    commands that have already passed analyzer and permission checks.
    """

    def __init__(self, *, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> SandboxExecutionResult:
        timeout = max(1, min(int(timeout_seconds or 60), 300))
        try:
            result = subprocess.run(
                list(argv),
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
        except FileNotFoundError:
            return SandboxExecutionResult(
                returncode=127,
                stdout="",
                stderr=f"Executable not found: {argv[0] if argv else ''}",
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxExecutionResult(
                returncode=124,
                stdout=_decode_timeout_output(exc.stdout),
                stderr=f"Command timed out after {timeout} seconds.",
                timed_out=True,
            )
        return SandboxExecutionResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value

