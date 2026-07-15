from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ATTACHMENT_SCHEMA_VERSION = "attachment.v1"
MAX_ATTACHMENT_COUNT = 8
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_TEXT_EXCERPT_BYTES = 48 * 1024
MAX_TEXT_EXCERPT_CHARS = 16_000

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".cmd",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".mdx",
    ".php",
    ".properties",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".mdx": "text/markdown",
    ".toml": "application/toml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


@dataclass(frozen=True)
class AttachmentEnvelope:
    attachment_id: str
    name: str
    media_type: str
    kind: str
    size_bytes: int
    sha256: str
    stored_path: str
    privacy_level: str
    source_labels: tuple[str, ...]
    evidence_ref: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATTACHMENT_SCHEMA_VERSION,
            "attachment_id": self.attachment_id,
            "name": self.name,
            "media_type": self.media_type,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "stored_path": self.stored_path,
            "privacy_level": self.privacy_level,
            "source_labels": list(self.source_labels),
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class StagedAttachments:
    envelopes: tuple[AttachmentEnvelope, ...] = ()
    inline_files: tuple[dict[str, str], ...] = ()

    def public_items(self) -> list[dict[str, Any]]:
        return [item.to_public_dict() for item in self.envelopes]


@dataclass(frozen=True)
class _AttachmentSource:
    path: Path
    name: str
    size_bytes: int
    media_type: str
    kind: str


def stage_run_attachments(
    *,
    workspace_root: Path,
    session_id: str,
    run_id: str,
    requested: Any,
) -> StagedAttachments:
    items = _requested_items(requested)
    if not items:
        return StagedAttachments()
    if len(items) > MAX_ATTACHMENT_COUNT:
        raise ValueError(f"attachments accepts at most {MAX_ATTACHMENT_COUNT} files")

    workspace = Path(workspace_root).resolve()
    safe_session_id = _safe_id(session_id, "session_id")
    safe_run_id = _safe_id(run_id, "run_id")
    sources = tuple(_validate_source(item) for item in items)
    total_size = sum(item.size_bytes for item in sources)
    if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
        raise ValueError(
            f"attachment total size exceeds {MAX_TOTAL_ATTACHMENT_BYTES} bytes"
        )

    storage_root = workspace / ".lucode" / "attachments" / safe_session_id / safe_run_id
    envelopes: list[AttachmentEnvelope] = []
    inline_files: list[dict[str, str]] = []
    copied_paths: list[Path] = []
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        for source in sources:
            attachment_id = f"att_{uuid.uuid4().hex}"
            stored_name = f"{attachment_id}_{_safe_filename(source.name)}"
            target = storage_root / stored_name
            digest = _copy_with_sha256(source.path, target)
            copied_paths.append(target)
            stored_path = target.relative_to(workspace).as_posix()
            envelope = AttachmentEnvelope(
                attachment_id=attachment_id,
                name=source.name,
                media_type=source.media_type,
                kind=source.kind,
                size_bytes=source.size_bytes,
                sha256=digest,
                stored_path=stored_path,
                privacy_level="private",
                source_labels=("user_attachment", source.kind),
                evidence_ref=f"attachment:{attachment_id}",
            )
            envelopes.append(envelope)
            inline_files.append(
                {
                    "path": stored_path,
                    "content": _inline_content(source, envelope),
                }
            )
    except Exception:
        for path in copied_paths:
            try:
                path.unlink()
            except OSError:
                pass
        _remove_empty_attachment_dirs(storage_root, workspace)
        raise
    return StagedAttachments(tuple(envelopes), tuple(inline_files))


def remove_session_attachments(workspace_root: Path, session_id: str) -> None:
    workspace = Path(workspace_root).resolve()
    safe_session_id = _safe_id(session_id, "session_id")
    attachments_root = (workspace / ".lucode" / "attachments").resolve()
    target = (attachments_root / safe_session_id).resolve()
    if target.parent != attachments_root or not target.exists():
        return
    shutil.rmtree(target)


def _requested_items(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("attachments must be a list")
    return list(value)


def _validate_source(item: Any) -> _AttachmentSource:
    raw_path = item.get("path") if isinstance(item, dict) else item
    text_path = str(raw_path or "").strip()
    if not text_path:
        raise ValueError("attachment path is required")
    path = Path(text_path).expanduser()
    if path.is_symlink():
        raise ValueError(f"attachment symlinks are not allowed: {path.name}")
    if not path.exists():
        raise ValueError(f"attachment does not exist: {path.name or text_path}")
    if not path.is_file():
        raise ValueError(f"attachment must be a file: {path.name or text_path}")
    size_bytes = path.stat().st_size
    if size_bytes > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"attachment is too large: {path.name} exceeds {MAX_ATTACHMENT_BYTES} bytes"
        )
    media_type = _media_type(path)
    kind = _attachment_kind(path, media_type)
    return _AttachmentSource(
        path=path.resolve(),
        name=path.name,
        size_bytes=size_bytes,
        media_type=media_type,
        kind=kind,
    )


def _copy_with_sha256(source: Path, target: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        while True:
            chunk = reader.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            writer.write(chunk)
    return digest.hexdigest()


def _inline_content(source: _AttachmentSource, envelope: AttachmentEnvelope) -> str:
    if source.kind == "text":
        return _read_text_excerpt(source.path)
    if source.kind == "image":
        return (
            f"Image attachment {envelope.name} ({envelope.media_type}, "
            f"{envelope.size_bytes} bytes). visual content not extracted in AttachmentEnvelope v1."
        )
    return (
        f"Binary attachment {envelope.name} ({envelope.media_type}, "
        f"{envelope.size_bytes} bytes). Content not extracted in AttachmentEnvelope v1."
    )


def _read_text_excerpt(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_TEXT_EXCERPT_BYTES + 1)
    truncated = len(raw) > MAX_TEXT_EXCERPT_BYTES
    text = raw[:MAX_TEXT_EXCERPT_BYTES].decode("utf-8", errors="replace")
    if len(text) > MAX_TEXT_EXCERPT_CHARS:
        text = text[:MAX_TEXT_EXCERPT_CHARS]
        truncated = True
    text = text.replace("\x00", "").strip()
    if truncated:
        text = f"{text}\n\n[attachment excerpt truncated]"
    return text


def _media_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    guessed, _encoding = mimetypes.guess_type(path.name, strict=False)
    return str(guessed or "application/octet-stream")


def _attachment_kind(path: Path, media_type: str) -> str:
    if media_type.casefold().startswith("image/"):
        return "image"
    if path.suffix.casefold() in _TEXT_EXTENSIONS:
        return "text"
    return "binary"


def _safe_id(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or text in {".", ".."}
        or len(text) > 180
        or any(character in text for character in '/\\<>:"|?*')
        or any(ord(character) < 32 for character in text)
    ):
        raise ValueError(f"invalid {field_name}")
    return text


def _safe_filename(value: str) -> str:
    name = _SAFE_NAME_RE.sub("_", Path(str(value or "attachment")).name).strip("._")
    return name[:120] or "attachment"


def _remove_empty_attachment_dirs(start: Path, workspace: Path) -> None:
    stop = (workspace / ".lucode" / "attachments").resolve()
    current = start.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
