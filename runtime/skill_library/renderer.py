from __future__ import annotations

from collections.abc import Sequence

from runtime.skill_library.schema import SkillCandidate


DEFAULT_PLANNER_CANDIDATE_LIMIT = 5
DEFAULT_PLANNER_RENDER_BUDGET = 1800


def render_candidates_for_planner(
    candidates: Sequence[SkillCandidate],
    *,
    max_candidates: int = DEFAULT_PLANNER_CANDIDATE_LIMIT,
    max_chars: int = DEFAULT_PLANNER_RENDER_BUDGET,
) -> str:
    """Render top Skill metadata candidates for a future Planner prompt.

    This renderer intentionally uses metadata only. It does not render body paths
    or read SKILL.md bodies; Worker body injection is a later phase.
    """

    kept = list(candidates)[: max(0, int(max_candidates or DEFAULT_PLANNER_CANDIDATE_LIMIT))]
    if not kept:
        return ""

    lines = [
        "Available Skill candidates (metadata only; Planner must explicitly adopt later):",
    ]
    for index, candidate in enumerate(kept, start=1):
        entry = candidate.entry
        lines.extend(
            [
                f"{index}. [{entry.id}] {entry.name} score={candidate.score:.4g}",
                f"   category: {_join(entry.category)}",
                f"   tags: {_join(entry.tags)}",
                f"   summary: {_compact(entry.summary, limit=220)}",
                f"   use_when: {_join_compact(entry.use_when, limit=240)}",
                f"   do_not_use_when: {_join_compact(entry.do_not_use_when, limit=220)}",
            ]
        )
        if candidate.reasons:
            lines.append(f"   reasons: {_join_compact(candidate.reasons, limit=180)}")
        if candidate.penalties:
            lines.append(f"   penalties: {_join_compact(candidate.penalties, limit=180)}")

    return _fit_budget("\n".join(lines), max_chars=max_chars)


def _join(values) -> str:
    return "/".join(str(value).strip() for value in values if str(value).strip()) or "-"


def _join_compact(values, *, limit: int) -> str:
    text = "; ".join(str(value).strip() for value in values if str(value).strip())
    return _compact(text, limit=limit) or "-"


def _compact(value, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _fit_budget(text: str, *, max_chars: int) -> str:
    budget = max(0, int(max_chars or DEFAULT_PLANNER_RENDER_BUDGET))
    if not budget or len(text) <= budget:
        return text
    suffix = "...[truncated]"
    if budget <= len(suffix):
        return suffix[:budget]
    return text[: budget - len(suffix)].rstrip() + suffix
