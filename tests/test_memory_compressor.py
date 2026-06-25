from __future__ import annotations

from runtime.memory.compressor import compress_distilled_entry, compress_entry_summary


def _entry(summary: str = "Task intent maps to project path: Memory resolver -> runtime/memory/resolver.py") -> dict:
    return {
        "kind": "path_mapping",
        "summary": summary,
        "tags": ["memory", "resolver"],
        "source": "distiller",
        "metadata": {
            "confidence": 0.85,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "fingerprint": "path_mapping:resolver",
            "injection_policy": "auto",
            "evidence": ["task_completed", "audit_file=runtime/memory/resolver.py"],
            "action": "Memory resolver -> runtime/memory/resolver.py",
        },
    }


def test_compress_distilled_entry_is_noop_without_compressor():
    entry = _entry()

    compressed = compress_distilled_entry(entry)

    assert compressed == entry
    assert compressed is not entry


def test_compress_distilled_entry_accepts_valid_short_summary():
    entry = _entry()

    def fake_compressor(payload):
        assert payload["kind"] == "path_mapping"
        assert payload["scope"] == ["runtime/memory/resolver.py"]
        return "Memory resolver work maps to runtime/memory/resolver.py."

    compressed = compress_distilled_entry(entry, compressor=fake_compressor)

    assert compressed["summary"] == "Memory resolver work maps to runtime/memory/resolver.py."
    assert compressed["kind"] == entry["kind"]
    assert compressed["metadata"]["confidence"] == entry["metadata"]["confidence"]
    assert compressed["metadata"]["scope"] == entry["metadata"]["scope"]
    assert compressed["metadata"]["fingerprint"] == entry["metadata"]["fingerprint"]
    assert compressed["metadata"]["summary_compressed"] is True
    assert compressed["metadata"]["original_summary"] == entry["summary"]
    assert "summary_compressed" in compressed["metadata"]["compression_reasons"]


def test_compress_distilled_entry_rejects_overlong_summary():
    entry = _entry()

    compressed = compress_distilled_entry(entry, compressor=lambda payload: "x" * 220, max_summary_chars=120)

    assert compressed["summary"] == entry["summary"]
    assert compressed["metadata"].get("summary_compressed") is not True
    assert "compression_rejected_too_long" in compressed["metadata"]["compression_reasons"]


def test_compress_distilled_entry_rejects_secret_output():
    entry = _entry()

    compressed = compress_distilled_entry(
        entry,
        compressor=lambda payload: "Use token=sk-secret123456 for runtime/memory/resolver.py.",
    )

    assert compressed["summary"] == entry["summary"]
    assert "sk-secret123456" not in str(compressed)
    assert "compression_rejected_sensitive_output" in compressed["metadata"]["compression_reasons"]


def test_compress_distilled_entry_rejects_summary_that_drops_scope_path():
    entry = _entry()

    compressed = compress_distilled_entry(entry, compressor=lambda payload: "Memory resolver work maps to the module.")

    assert compressed["summary"] == entry["summary"]
    assert "compression_rejected_missing_scope" in compressed["metadata"]["compression_reasons"]


def test_compress_entry_summary_returns_original_on_compressor_failure():
    entry = _entry()

    def broken_compressor(payload):
        raise RuntimeError("model unavailable")

    result = compress_entry_summary(entry, compressor=broken_compressor)

    assert result.summary == entry["summary"]
    assert result.accepted is False
    assert "compression_failed" in result.reasons
