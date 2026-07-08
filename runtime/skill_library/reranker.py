from __future__ import annotations

from runtime.skill_library.retriever import _matches_any, _paths_match, _query_categories, score_skill_entry
from runtime.skill_library.schema import SkillCandidate


def rerank_skill_candidates(
    query: str,
    candidates: list[SkillCandidate],
    *,
    paths: list[str] | None = None,
    limit: int = 5,
) -> list[SkillCandidate]:
    categories = set(_query_categories(query))
    ranked: list[SkillCandidate] = []
    for candidate in candidates:
        entry = candidate.entry
        base_score, base_reasons, base_penalties, explicit = score_skill_entry(
            query,
            entry,
            paths=paths or [],
            explicit_ids={entry.id} if candidate.explicit else set(),
            query_categories=categories,
        )
        score = max(candidate.score, 0.0) + base_score
        reasons = list(candidate.reasons)
        penalties = list(candidate.penalties)
        for reason in base_reasons:
            if reason not in reasons:
                reasons.append(reason)
        for penalty in base_penalties:
            if penalty not in penalties:
                penalties.append(penalty)
        usage_boost, usage_penalty, usage_reasons, usage_penalties = _usage_adjustments(entry.usage)
        score += usage_boost
        score -= usage_penalty
        reasons.extend(reason for reason in usage_reasons if reason not in reasons)
        penalties.extend(penalty for penalty in usage_penalties if penalty not in penalties)
        if _paths_match(paths or [], entry.scope_paths) and "scope match" not in reasons:
            score += 0.25
            reasons.append("scope match")
        if _matches_any(query, entry.negative_queries) and "negative query match" not in penalties:
            score -= 0.3
            penalties.append("negative query match")
        ranked.append(
            SkillCandidate(
                entry=entry,
                score=round(score, 4),
                reasons=tuple(reasons),
                penalties=tuple(penalties),
                explicit=explicit or candidate.explicit,
            )
        )
    return sorted(ranked, key=lambda candidate: (-candidate.score, candidate.entry.id))[: max(1, int(limit or 5))]


def _usage_adjustments(usage: dict) -> tuple[float, float, list[str], list[str]]:
    try:
        used_count = int(usage.get("used_count") or 0)
        success_count = int(usage.get("success_count") or 0)
        misfire_count = int(usage.get("misfire_count") or 0)
        failure_count = int(usage.get("failure_count") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0, [], []
    if used_count <= 0:
        return 0.0, 0.0, [], []
    success_rate = success_count / max(1, used_count)
    misfire_rate = misfire_count / max(1, used_count)
    failure_rate = failure_count / max(1, used_count)
    boost = min(0.15, success_rate * 0.15)
    penalty = min(0.3, misfire_rate * 0.3) + min(0.12, failure_rate * 0.12)
    reasons = ["usage success boost"] if boost else []
    penalties = []
    if misfire_rate:
        penalties.append("usage misfire penalty")
    if failure_rate:
        penalties.append("usage failure penalty")
    return boost, penalty, reasons, penalties
