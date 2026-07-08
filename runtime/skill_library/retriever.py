from __future__ import annotations

import fnmatch
import re

from runtime.skill_library.schema import SkillCandidate, SkillIndexEntry
from runtime.skill_library.taxonomy import classify_query_by_rules, load_skill_taxonomy


MIN_RETRIEVAL_SCORE = 0.25


def retrieve_skill_candidates(
    query: str,
    entries: list[SkillIndexEntry],
    *,
    paths: list[str] | None = None,
    explicit_skill_ids: list[str] | None = None,
    limit: int = 20,
) -> list[SkillCandidate]:
    explicit_ids = {_normalize_id(value) for value in list(explicit_skill_ids or []) if str(value).strip()}
    explicit_ids.update(_explicit_skill_ids_from_query(query))
    all_paths = list(paths or []) + _paths_from_text(query)
    categories = set(_query_categories(query))
    candidates: list[SkillCandidate] = []
    for entry in entries:
        if not entry.enabled or not entry.assignable or entry.metadata_status != "ready":
            continue
        score, reasons, penalties, explicit = score_skill_entry(
            query,
            entry,
            paths=all_paths,
            explicit_ids=explicit_ids,
            query_categories=categories,
        )
        if explicit or score >= MIN_RETRIEVAL_SCORE:
            candidates.append(
                SkillCandidate(
                    entry=entry,
                    score=round(score, 4),
                    reasons=tuple(reasons),
                    penalties=tuple(penalties),
                    explicit=explicit,
                )
            )
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.entry.id))[: max(1, int(limit or 20))]


def score_skill_entry(
    query: str,
    entry: SkillIndexEntry,
    *,
    paths: list[str] | None = None,
    explicit_ids: set[str] | None = None,
    query_categories: set[str] | None = None,
) -> tuple[float, list[str], list[str], bool]:
    query_text = _normalize_text(query)
    score = 0.0
    reasons: list[str] = []
    penalties: list[str] = []
    explicit = entry.id in set(explicit_ids or set())
    if explicit:
        score += 1.0
        reasons.append("explicit skill reference")
    if _entry_categories(entry).intersection(set(query_categories or _query_categories(query))):
        score += 0.3
        reasons.append("category match")
    if _matches_any(query_text, entry.use_when):
        score += 0.4
        reasons.append("use_when match")
    tag_hits = [tag for tag in entry.tags if _token_in_query(tag, query_text)]
    if tag_hits:
        score += min(0.35, 0.12 * len(tag_hits))
        reasons.append("tag match")
    if _matches_any(query_text, [entry.name, entry.summary, entry.id.replace("_", " ")]):
        score += 0.2
        reasons.append("identity match")
    if _paths_match(paths or [], entry.scope_paths):
        score += 0.25
        reasons.append("scope match")
    if _matches_any(query_text, entry.do_not_use_when):
        score -= 0.35
        penalties.append("do_not_use_when match")
    if _matches_any(query_text, entry.negative_queries):
        score -= 0.6
        penalties.append("negative query match")
    return score, reasons, penalties, explicit


def _query_categories(query: str) -> list[str]:
    try:
        taxonomy = load_skill_taxonomy()
    except Exception:
        return []
    return classify_query_by_rules(query, taxonomy)


def _entry_categories(entry: SkillIndexEntry) -> set[str]:
    values = [str(item).strip() for item in entry.category if str(item).strip()]
    result = set(values)
    if len(values) >= 2 and "/" not in values[-1]:
        result.add("/".join(values[:2]))
    return result


def _explicit_skill_ids_from_query(query: str) -> set[str]:
    return {_normalize_id(match) for match in re.findall(r"@([A-Za-z0-9_-]+)", str(query or ""))}


def _paths_from_text(query: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./*-]+", str(query or ""))


def _paths_match(paths: list[str], patterns: tuple[str, ...]) -> bool:
    normalized_paths = [str(path).replace("\\", "/").strip() for path in paths if str(path).strip()]
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").replace("\\", "/").strip()
        if not pattern:
            continue
        for path in normalized_paths:
            if fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*")):
                return True
    return False


def _matches_any(query_text: str, values) -> bool:
    return any(_phrase_matches(query_text, value) for value in values if str(value).strip())


def _phrase_matches(query_text: str, value) -> bool:
    phrase = _normalize_text(value)
    if not phrase:
        return False
    if phrase in query_text or query_text in phrase:
        return True
    words = [word for word in re.findall(r"[a-z0-9_+-]+", phrase) if len(word) >= 2]
    if not words:
        return False
    return sum(1 for word in words if _token_in_query(word, query_text)) >= min(2, len(words))


def _token_in_query(token: str, query_text: str) -> bool:
    token = _normalize_text(token)
    if not token:
        return False
    if re.search(r"[a-z0-9]", token):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", query_text))
    return token in query_text


def _normalize_text(value) -> str:
    text = str(value or "").casefold().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_id(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", str(value or "").casefold().replace("-", "_"))).strip("_")
