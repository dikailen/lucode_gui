from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommitGuardDecision:
    resource_id: str
    allowed: bool
    status: str
    reason: str = ""
    expected_sha256: str = ""
    current_sha256: str = ""
    exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_expected_file_sha256(
    project_root: Path | str,
    target: Path | str,
    *,
    expected_sha256: str | None,
    require_for_existing: bool = False,
    strict: bool = True,
) -> CommitGuardDecision:
    root = Path(project_root).resolve()
    path = Path(target)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not _is_within(path, root):
        return CommitGuardDecision(
            resource_id=str(path),
            allowed=False,
            status="rejected",
            reason="outside_project_root",
            exists=path.exists(),
        )
    resource_id = _relative(root, path)
    expected = str(expected_sha256 or "").strip().lower()

    if not expected:
        if require_for_existing and strict and path.exists() and path.is_file():
            return CommitGuardDecision(
                resource_id=resource_id,
                allowed=False,
                status="rejected",
                reason="missing_expected_sha256",
                exists=True,
                current_sha256=sha256_file(path),
            )
        return CommitGuardDecision(
            resource_id=resource_id,
            allowed=True,
            status="accepted",
            reason="not_required",
            exists=path.exists(),
            current_sha256=sha256_file(path) if path.exists() and path.is_file() else "",
        )

    if not path.exists():
        return CommitGuardDecision(
            resource_id=resource_id,
            allowed=False,
            status="rejected",
            reason="target_missing",
            expected_sha256=expected,
            exists=False,
        )
    if not path.is_file():
        return CommitGuardDecision(
            resource_id=resource_id,
            allowed=False,
            status="rejected",
            reason="not_file",
            expected_sha256=expected,
            exists=True,
        )
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        return CommitGuardDecision(
            resource_id=resource_id,
            allowed=False,
            status="rejected",
            reason="invalid_expected_sha256",
            expected_sha256=expected,
            exists=True,
            current_sha256=sha256_file(path),
        )

    current = sha256_file(path)
    if current != expected:
        return CommitGuardDecision(
            resource_id=resource_id,
            allowed=False,
            status="rejected",
            reason="sha256_mismatch",
            expected_sha256=expected,
            current_sha256=current,
            exists=True,
        )
    return CommitGuardDecision(
        resource_id=resource_id,
        allowed=True,
        status="accepted",
        reason="sha256_matched",
        expected_sha256=expected,
        current_sha256=current,
        exists=True,
    )


def assert_expected_file_sha256(
    project_root: Path | str,
    target: Path | str,
    *,
    expected_sha256: str | None,
    require_for_existing: bool = False,
    strict: bool = True,
) -> CommitGuardDecision:
    decision = validate_expected_file_sha256(
        project_root,
        target,
        expected_sha256=expected_sha256,
        require_for_existing=require_for_existing,
        strict=strict,
    )
    if not decision.allowed:
        raise ValueError(render_commit_guard_error(decision))
    return decision


def expected_sha256_map_for_paths(project_root: Path | str, paths: list[str] | tuple[str, ...]) -> dict[str, str]:
    root = Path(project_root).resolve()
    hashes: dict[str, str] = {}
    for raw_path in list(paths or []):
        target = _resolve_within(root, raw_path)
        if target is not None and target.is_file():
            hashes[_relative(root, target)] = sha256_file(target)
    return hashes


def render_commit_guard_error(decision: CommitGuardDecision) -> str:
    if decision.reason == "missing_expected_sha256":
        return (
            f"expected_sha256 is required for existing file {decision.resource_id}. "
            "Read the file first and pass its current SHA-256 digest before editing."
        )
    if decision.reason == "target_missing":
        return f"expected_sha256 was provided for {decision.resource_id}, but the target does not exist"
    if decision.reason == "not_file":
        return f"expected_sha256 can only verify files: {decision.resource_id}"
    if decision.reason == "invalid_expected_sha256":
        return "expected_sha256 must be a 64-character lowercase hex SHA-256 digest"
    if decision.reason == "sha256_mismatch":
        return (
            "expected_sha256 mismatch for "
            f"{decision.resource_id}: expected {decision.expected_sha256}, current {decision.current_sha256}. "
            "The file changed after it was read; re-read the file before editing."
        )
    if decision.reason == "outside_project_root":
        return f"commit guard target is outside project root: {decision.resource_id}"
    return f"commit guard rejected {decision.resource_id}: {decision.reason or decision.status}"


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_within(root: Path, raw_path: str | Path) -> Path | None:
    if not str(raw_path or "").strip():
        return None
    path = Path(str(raw_path).strip())
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
