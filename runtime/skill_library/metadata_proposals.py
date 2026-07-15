from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.storage.sqlite_store import connect, initialize_sqlite_store
from runtime.skill_library.taxonomy import SkillTaxonomy, suggest_categories_for_skill


_SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_PAYLOAD_FIELDS = ("categories", "tags", "use_when", "do_not_use_when")
_PROPOSAL_SOURCES = {"rules", "ai"}


@dataclass(frozen=True)
class SkillMetadataProposal:
    proposal_id: str
    skill_id: str
    content_hash: str
    source: str
    status: str
    confidence: float
    payload: dict[str, list[str]]
    reason: str
    created_at: str
    updated_at: str


def upsert_rule_metadata_proposal(
    *,
    workspace_root: Path | str,
    skill_id: str,
    skill_file: Path | str,
    payload: dict[str, Any],
    reason: str = "",
) -> SkillMetadataProposal:
    return upsert_metadata_proposal(
        workspace_root=workspace_root,
        skill_id=skill_id,
        skill_file=skill_file,
        source="rules",
        payload=payload,
        confidence=1.0,
        reason=reason,
    )


def upsert_metadata_proposal(
    *,
    workspace_root: Path | str,
    skill_id: str,
    skill_file: Path | str,
    source: str,
    payload: dict[str, Any],
    confidence: float,
    reason: str = "",
) -> SkillMetadataProposal:
    root = Path(workspace_root).resolve()
    clean_id = _validate_workspace_skill(root, skill_id, skill_file)
    clean_source = str(source or "").strip().lower()
    if clean_source not in _PROPOSAL_SOURCES:
        raise ValueError("metadata proposal source is invalid")
    normalized_payload = _normalize_payload(payload)
    content_hash = _content_hash(Path(skill_file))
    proposal_id = _proposal_id(clean_id, content_hash, clean_source)
    now = _now()
    initialize_sqlite_store(root)
    with connect(root) as connection:
        connection.execute(
            """
            update skill_metadata_proposals
            set status = 'stale', updated_at = ?
            where skill_id = ? and source = ? and content_hash != ? and status = 'pending'
            """,
            (now, clean_id, clean_source, content_hash),
        )
        row = connection.execute(
            """
            select proposal_id, skill_id, content_hash, source, status, confidence, payload_json, reason, created_at, updated_at
            from skill_metadata_proposals
            where proposal_id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                insert into skill_metadata_proposals(
                  proposal_id, skill_id, content_hash, source, status, confidence,
                  payload_json, reason, created_at, updated_at
                ) values (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    clean_id,
                    content_hash,
                    clean_source,
                    max(0.0, min(1.0, float(confidence))),
                    json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True),
                    _single_line(reason, limit=320),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                select proposal_id, skill_id, content_hash, source, status, confidence, payload_json, reason, created_at, updated_at
                from skill_metadata_proposals where proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
    return _proposal_from_row(row)


def skill_metadata_content_hash(skill_file: Path | str) -> str:
    """Return the version key used for proposal caching; never expose file content."""

    return _content_hash(Path(skill_file))


def load_skill_metadata_proposals(
    workspace_root: Path | str,
    *,
    skill_id: str = "",
    statuses: tuple[str, ...] | None = None,
) -> list[SkillMetadataProposal]:
    root = Path(workspace_root).resolve()
    initialize_sqlite_store(root)
    clauses: list[str] = []
    values: list[Any] = []
    if skill_id:
        clauses.append("skill_id = ?")
        values.append(str(skill_id).strip())
    if statuses:
        normalized_statuses = [str(status).strip() for status in statuses if str(status).strip()]
        if normalized_statuses:
            clauses.append(f"status in ({','.join('?' for _ in normalized_statuses)})")
            values.extend(normalized_statuses)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    with connect(root) as connection:
        rows = connection.execute(
            f"""
            select proposal_id, skill_id, content_hash, source, status, confidence, payload_json, reason, created_at, updated_at
            from skill_metadata_proposals
            {where}
            order by case status when 'pending' then 0 when 'stale' then 1 else 2 end, updated_at desc, proposal_id desc
            """,
            values,
        ).fetchall()
    return [_proposal_from_row(row) for row in rows]


def delete_skill_metadata_proposals(workspace_root: Path | str, *, skill_id: str) -> int:
    root = Path(workspace_root).resolve()
    clean_id = str(skill_id or "").strip()
    if not _SKILL_ID_PATTERN.fullmatch(clean_id):
        raise ValueError("skill_id is invalid")
    initialize_sqlite_store(root)
    with connect(root) as connection:
        cursor = connection.execute("delete from skill_metadata_proposals where skill_id = ?", (clean_id,))
    return max(0, int(cursor.rowcount or 0))


def sync_rule_metadata_proposals(
    workspace_root: Path | str,
    entries: list[Any],
    *,
    taxonomy: SkillTaxonomy,
) -> dict[str, SkillMetadataProposal]:
    """Persist deterministic suggestions for incomplete workspace Skills only."""

    proposals: dict[str, SkillMetadataProposal] = {}
    for entry in entries:
        if (
            str(getattr(entry, "source", "")) != "workspace"
            or bool(getattr(entry, "core", False))
            or str(getattr(entry, "metadata_status", "")) == "ready"
        ):
            continue
        skill_file = Path(str(getattr(entry, "body_path", "") or ""))
        if not skill_file.is_file():
            continue
        payload = build_rule_metadata_payload(entry, taxonomy=taxonomy)
        if not any(payload.values()):
            continue
        # Database/UI identity is the normalized entry ID; folder names may retain
        # casing and hyphens from third-party archives.
        skill_id = str(getattr(entry, "id", "")).strip()
        proposals[str(getattr(entry, "id", ""))] = upsert_rule_metadata_proposal(
            workspace_root=workspace_root,
            skill_id=skill_id,
            skill_file=skill_file,
            payload=payload,
            reason="rules: indexed metadata and taxonomy",
        )
    return proposals


def build_rule_metadata_payload(entry: Any, *, taxonomy: SkillTaxonomy) -> dict[str, list[str]]:
    categories = _values(getattr(entry, "category", ())) or suggest_categories_for_skill(entry, taxonomy)
    tags = _values(getattr(entry, "tags", ())) or _suggest_tags(entry)
    use_when = _values(getattr(entry, "use_when", ())) or _suggest_use_when(entry)
    return _normalize_payload(
        {
            "categories": categories,
            "tags": tags,
            "use_when": use_when,
            "do_not_use_when": _values(getattr(entry, "do_not_use_when", ())),
        }
    )


def _validate_workspace_skill(workspace_root: Path, skill_id: str, skill_file: Path | str) -> str:
    clean_id = str(skill_id or "").strip()
    if not _SKILL_ID_PATTERN.fullmatch(clean_id):
        raise ValueError("only workspace Skills can have metadata proposals")
    root = (workspace_root / ".lucode" / "skills").resolve()
    target = Path(skill_file).resolve()
    if target.name != "SKILL.md" or target.parent.parent != root or not target.is_file():
        raise ValueError("only workspace Skills can have metadata proposals")
    return clean_id


def _normalize_payload(payload: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in _PAYLOAD_FIELDS:
        values: list[str] = []
        for raw_value in _values(dict(payload or {}).get(key)):
            value = _single_line(raw_value, limit=320)
            if value and value not in values:
                values.append(value)
            if len(values) >= 24:
                break
        result[key] = values
    return result


def _proposal_id(skill_id: str, content_hash: str, source: str) -> str:
    value = f"{source}:{skill_id}:{content_hash}".encode("utf-8")
    return f"skill-proposal:{hashlib.sha256(value).hexdigest()[:24]}"


def _content_hash(skill_file: Path) -> str:
    return hashlib.sha256(skill_file.read_bytes()).hexdigest()


def _proposal_from_row(row: Any) -> SkillMetadataProposal:
    if row is None:
        raise ValueError("metadata proposal was not persisted")
    try:
        raw_payload = json.loads(str(row[6] or "{}"))
    except json.JSONDecodeError:
        raw_payload = {}
    return SkillMetadataProposal(
        proposal_id=str(row[0]),
        skill_id=str(row[1]),
        content_hash=str(row[2]),
        source=str(row[3]),
        status=str(row[4]),
        confidence=float(row[5] or 0.0),
        payload=_normalize_payload(raw_payload if isinstance(raw_payload, dict) else {}),
        reason=str(row[7] or ""),
        created_at=str(row[8] or ""),
        updated_at=str(row[9] or ""),
    )


def _single_line(value: object, *, limit: int) -> str:
    text = str(value or "").strip()
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("metadata proposal values must be single line")
    return text[:limit]


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _suggest_tags(entry: Any, *, limit: int = 8) -> list[str]:
    stop_words = {"and", "are", "for", "from", "generic", "main", "remove", "skill", "the", "this", "to", "with", "zh"}
    text = " ".join(
        str(value or "") for value in (getattr(entry, "name", ""), getattr(entry, "summary", ""))
    ).casefold()
    tags: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9]{3,}", text):
        if token in stop_words or token in tags:
            continue
        tags.append(token)
        if len(tags) >= limit:
            break
    return tags


def _suggest_use_when(entry: Any) -> list[str]:
    summary = str(getattr(entry, "summary", "") or "").strip()
    if not summary:
        return []
    first_sentence = re.split(r"(?<=[.!?。！？])\s*", summary, maxsplit=1)[0].strip()
    return [first_sentence[:320]] if first_sentence else []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
