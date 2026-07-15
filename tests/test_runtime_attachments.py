from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_stage_text_attachment_creates_private_envelope_and_bounded_inline_context(tmp_path):
    from runtime.server.attachments import stage_run_attachments

    source = tmp_path / "outside" / "notes.md"
    source.parent.mkdir()
    source.write_text("alpha\n" + ("details " * 4000), encoding="utf-8")

    result = stage_run_attachments(
        workspace_root=tmp_path / "workspace",
        session_id="session_1",
        run_id="run_1",
        requested=[{"path": str(source)}],
    )

    assert len(result.envelopes) == 1
    envelope = result.envelopes[0]
    assert envelope.name == "notes.md"
    assert envelope.kind == "text"
    assert envelope.media_type == "text/markdown"
    assert envelope.size_bytes == source.stat().st_size
    assert envelope.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert envelope.privacy_level == "private"
    assert envelope.evidence_ref.startswith("attachment:")
    assert str(source) not in str(envelope.to_public_dict())

    stored = (tmp_path / "workspace" / envelope.stored_path).resolve()
    assert stored.is_file()
    assert stored.read_bytes() == source.read_bytes()
    assert len(result.inline_files) == 1
    assert result.inline_files[0]["path"] == envelope.stored_path
    assert result.inline_files[0]["content"].startswith("alpha")
    assert len(result.inline_files[0]["content"]) < len(source.read_text(encoding="utf-8"))


def test_stage_image_attachment_keeps_only_metadata_out_of_prompt(tmp_path):
    from runtime.server.attachments import stage_run_attachments

    source = tmp_path / "screen.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"binary-image-data" * 50))

    result = stage_run_attachments(
        workspace_root=tmp_path / "workspace",
        session_id="session_1",
        run_id="run_1",
        requested=[str(source)],
    )

    envelope = result.envelopes[0]
    assert envelope.kind == "image"
    assert envelope.media_type == "image/png"
    assert len(result.inline_files) == 1
    context = result.inline_files[0]["content"]
    assert "visual content not extracted" in context
    assert "base64" not in context.casefold()
    assert "binary-image-data" not in context


@pytest.mark.parametrize("invalid_kind", ["missing", "directory", "symlink"])
def test_stage_attachment_rejects_invalid_sources(tmp_path, invalid_kind):
    from runtime.server.attachments import stage_run_attachments

    source = tmp_path / "source"
    if invalid_kind == "directory":
        source.mkdir()
    elif invalid_kind == "symlink":
        target = tmp_path / "target.txt"
        target.write_text("secret", encoding="utf-8")
        try:
            source.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation is unavailable on this Windows account")

    with pytest.raises(ValueError):
        stage_run_attachments(
            workspace_root=tmp_path / "workspace",
            session_id="session_1",
            run_id="run_1",
            requested=[{"path": str(source)}],
        )


def test_stage_attachment_rejects_count_item_and_total_limits(tmp_path, monkeypatch):
    import runtime.server.attachments as attachments

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("123456", encoding="utf-8")
    second.write_text("abcdef", encoding="utf-8")

    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_COUNT", 1)
    with pytest.raises(ValueError, match="at most 1"):
        attachments.stage_run_attachments(
            workspace_root=tmp_path / "workspace",
            session_id="session_1",
            run_id="run_count",
            requested=[str(first), str(second)],
        )

    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_COUNT", 8)
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 5)
    with pytest.raises(ValueError, match="too large"):
        attachments.stage_run_attachments(
            workspace_root=tmp_path / "workspace",
            session_id="session_1",
            run_id="run_item",
            requested=[str(first)],
        )

    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 10)
    monkeypatch.setattr(attachments, "MAX_TOTAL_ATTACHMENT_BYTES", 10)
    with pytest.raises(ValueError, match="total size"):
        attachments.stage_run_attachments(
            workspace_root=tmp_path / "workspace",
            session_id="session_1",
            run_id="run_total",
            requested=[str(first), str(second)],
        )


def test_stage_attachment_never_persists_external_source_path(tmp_path):
    from runtime.server.attachments import stage_run_attachments

    source = tmp_path / "external-private-name.txt"
    source.write_text("hello", encoding="utf-8")
    workspace = tmp_path / "workspace"

    result = stage_run_attachments(
        workspace_root=workspace,
        session_id="session_1",
        run_id="run_1",
        requested=[{"path": str(source), "name": "untrusted-name.txt"}],
    )

    public_text = str(result.envelopes[0].to_public_dict())
    assert str(source.parent) not in public_text
    assert Path(result.envelopes[0].stored_path).parts[:3] == (".lucode", "attachments", "session_1")


def test_stage_attachment_accepts_generated_unicode_session_ids_without_allowing_path_segments(tmp_path):
    from runtime.server.attachments import stage_run_attachments

    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")

    result = stage_run_attachments(
        workspace_root=tmp_path / "workspace",
        session_id="20260712T070434Z-中文会话-3a62ac4e",
        run_id="run_1",
        requested=[str(source)],
    )

    assert result.envelopes[0].name == "notes.txt"
    with pytest.raises(ValueError, match="invalid session_id"):
        stage_run_attachments(
            workspace_root=tmp_path / "workspace",
            session_id="../escape",
            run_id="run_2",
            requested=[str(source)],
        )
