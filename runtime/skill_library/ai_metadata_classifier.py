from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from runtime.config.model_selection import model_usable_for_task
from runtime.safety.privacy import PrivacyPolicy
from runtime.skill_library.metadata_proposals import (
    SkillMetadataProposal,
    load_skill_metadata_proposals,
    skill_metadata_content_hash,
    upsert_metadata_proposal,
)
from runtime.skill_library.taxonomy import SkillTaxonomy, load_skill_taxonomy


MAX_BATCH_SIZE = 8
Classifier = Callable[[Sequence["SkillMetadataClassificationRequest"]], Sequence[dict[str, Any]]]


@dataclass(frozen=True)
class SkillMetadataClassificationRequest:
    """Small, path-free envelope sent to an explicitly selected classifier."""

    skill_id: str
    name: str
    summary: str
    tags: tuple[str, ...] = ()
    use_when: tuple[str, ...] = ()
    body_path: str = ""


@dataclass(frozen=True)
class SkillMetadataClassificationResult:
    persisted: tuple[SkillMetadataProposal, ...] = ()
    skipped: tuple[str, ...] = ()
    blocked_reason: str = ""
    requested: int = 0


def classify_pending_skill_metadata(
    workspace_root: Path | str,
    entries: Sequence[Any],
    *,
    classifier: Classifier,
    model_info: dict[str, Any] | None = None,
    privacy_mode: str = "local_first",
    taxonomy: SkillTaxonomy | None = None,
    max_items: int = MAX_BATCH_SIZE,
) -> SkillMetadataClassificationResult:
    """Classify incomplete workspace Skills in an explicit, bounded batch.

    This service has no Agent Loop or UI-render integration. Its caller must provide
    the classifier, which keeps network execution and scheduling outside this layer.
    """

    if model_info is not None and not model_usable_for_task(model_info, PrivacyPolicy(privacy_mode)):
        return SkillMetadataClassificationResult(blocked_reason="model_not_allowed")

    root = Path(workspace_root).resolve()
    candidates, skipped = _pending_candidates(root, entries, max_items=max_items)
    if not candidates:
        return SkillMetadataClassificationResult(skipped=tuple(skipped))

    requests = tuple(_request_for_entry(entry) for entry, _ in candidates)
    raw_results = list(classifier(requests) or [])
    normalized = _validate_results(raw_results, requests, taxonomy or load_skill_taxonomy())

    persisted = []
    candidate_by_id = {request.skill_id: (entry, skill_file) for request, (entry, skill_file) in zip(requests, candidates)}
    for item in normalized:
        entry, skill_file = candidate_by_id[item["skill_id"]]
        persisted.append(
            upsert_metadata_proposal(
                workspace_root=root,
                skill_id=str(getattr(entry, "id", "")),
                skill_file=skill_file,
                source="ai",
                payload=item["payload"],
                confidence=item["confidence"],
                reason=item["reason"],
            )
        )
    return SkillMetadataClassificationResult(
        persisted=tuple(persisted), skipped=tuple(skipped), requested=len(requests)
    )


def pending_skill_metadata_requests(
    workspace_root: Path | str,
    entries: Sequence[Any],
    *,
    max_items: int = MAX_BATCH_SIZE,
) -> tuple[SkillMetadataClassificationRequest, ...]:
    """Expose the compact, cache-aware batch envelope without running a model."""

    candidates, _ = _pending_candidates(Path(workspace_root).resolve(), entries, max_items=max_items)
    return tuple(_request_for_entry(entry) for entry, _skill_file in candidates)


def _pending_candidates(root: Path, entries: Sequence[Any], *, max_items: int) -> tuple[list[tuple[Any, Path]], list[str]]:
    pending: list[tuple[Any, Path]] = []
    skipped: list[str] = []
    for entry in entries:
        if not _eligible_entry(entry):
            continue
        skill_file = Path(str(getattr(entry, "body_path", "") or ""))
        if not skill_file.is_file():
            continue
        skill_id = str(getattr(entry, "id", "") or "").strip()
        if _has_current_ai_proposal(root, skill_id, skill_file):
            skipped.append(skill_id)
            continue
        if len(pending) < max(1, min(MAX_BATCH_SIZE, int(max_items or MAX_BATCH_SIZE))):
            pending.append((entry, skill_file))
    return pending, skipped


def _eligible_entry(entry: Any) -> bool:
    return (
        str(getattr(entry, "source", "")) == "workspace"
        and not bool(getattr(entry, "core", False))
        and str(getattr(entry, "metadata_status", "")) != "ready"
        and bool(str(getattr(entry, "id", "") or "").strip())
    )


def _has_current_ai_proposal(root: Path, skill_id: str, skill_file: Path) -> bool:
    version = skill_metadata_content_hash(skill_file)
    return any(
        proposal.source == "ai" and proposal.content_hash == version and proposal.status == "pending"
        for proposal in load_skill_metadata_proposals(root, skill_id=skill_id)
    )


def _request_for_entry(entry: Any) -> SkillMetadataClassificationRequest:
    return SkillMetadataClassificationRequest(
        skill_id=str(getattr(entry, "id", "") or "").strip(),
        name=_compact(getattr(entry, "name", ""), 160),
        summary=_compact(getattr(entry, "summary", ""), 700),
        tags=tuple(_compact(value, 80) for value in list(getattr(entry, "tags", ()) or ())[:12]),
        use_when=tuple(_compact(value, 180) for value in list(getattr(entry, "use_when", ()) or ())[:8]),
    )


def _validate_results(
    raw_results: Sequence[dict[str, Any]],
    requests: Sequence[SkillMetadataClassificationRequest],
    taxonomy: SkillTaxonomy,
) -> list[dict[str, Any]]:
    expected_ids = {request.skill_id for request in requests}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("AI metadata classifier result must be an object")
        skill_id = str(raw.get("skill_id") or "").strip()
        if skill_id not in expected_ids or skill_id in seen:
            raise ValueError("AI metadata classifier returned an unexpected Skill")
        categories = _string_list(raw.get("categories"))
        unknown = [category for category in categories if not taxonomy.has_category(category)]
        if unknown:
            raise ValueError(f"unknown Skill category: {unknown[0]}")
        normalized.append(
            {
                "skill_id": skill_id,
                "payload": {
                    "categories": categories,
                    "tags": _string_list(raw.get("tags")),
                    "use_when": _string_list(raw.get("use_when")),
                    "do_not_use_when": _string_list(raw.get("do_not_use_when")),
                },
                "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
                "reason": _compact(raw.get("reason", ""), 320),
            }
        )
        seen.add(skill_id)
    return normalized


def _string_list(value: Any, *, limit: int = 24) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    result: list[str] = []
    for raw in values:
        text = _compact(raw, 320)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()
