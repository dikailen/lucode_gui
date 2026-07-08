from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from runtime.skill_library.indexer import build_skill_index
from runtime.skill_library.renderer import (
    DEFAULT_PLANNER_CANDIDATE_LIMIT,
    DEFAULT_PLANNER_RENDER_BUDGET,
    render_candidates_for_planner,
)
from runtime.skill_library.reranker import rerank_skill_candidates
from runtime.skill_library.retriever import retrieve_skill_candidates
from runtime.skill_library.schema import SkillCandidate, SkillIndexEntry


DEFAULT_RETRIEVER_LIMIT = 20


@dataclass(frozen=True)
class SkillResolutionPack:
    query: str
    paths: tuple[str, ...] = field(default_factory=tuple)
    candidates: tuple[SkillCandidate, ...] = field(default_factory=tuple)

    @property
    def candidate_skill_ids(self) -> tuple[str, ...]:
        return tuple(candidate.entry.id for candidate in self.candidates)


class SkillResolver:
    """Read-only Skill candidate resolver for Planner-facing metadata.

    P4 deliberately stops at candidates. It does not bind tasks, mutate
    task.skill_id, or load Skill bodies.
    """

    def __init__(
        self,
        *,
        workspace_context=None,
        entries: Sequence[SkillIndexEntry] | None = None,
        candidate_limit: int = DEFAULT_RETRIEVER_LIMIT,
        planner_limit: int = DEFAULT_PLANNER_CANDIDATE_LIMIT,
        render_budget: int = DEFAULT_PLANNER_RENDER_BUDGET,
    ) -> None:
        self.workspace_context = workspace_context
        self._entries = tuple(entries) if entries is not None else None
        self.candidate_limit = max(1, int(candidate_limit or DEFAULT_RETRIEVER_LIMIT))
        self.planner_limit = max(1, int(planner_limit or DEFAULT_PLANNER_CANDIDATE_LIMIT))
        self.render_budget = max(1, int(render_budget or DEFAULT_PLANNER_RENDER_BUDGET))

    def resolve_for_planner(
        self,
        query: str,
        *,
        paths: list[str] | None = None,
        explicit_skill_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> SkillResolutionPack:
        clean_query = str(query or "")
        clean_paths = tuple(str(path).strip() for path in list(paths or []) if str(path).strip())
        entries = list(self._entries) if self._entries is not None else build_skill_index(
            self.workspace_context,
            write=False,
        )
        retrieved = retrieve_skill_candidates(
            clean_query,
            entries,
            paths=list(clean_paths),
            explicit_skill_ids=explicit_skill_ids,
            limit=self.candidate_limit,
        )
        planner_limit = max(1, int(limit or self.planner_limit))
        reranked = rerank_skill_candidates(
            clean_query,
            retrieved,
            paths=list(clean_paths),
            limit=planner_limit,
        )
        return SkillResolutionPack(
            query=clean_query,
            paths=clean_paths,
            candidates=tuple(reranked),
        )

    def render_for_planner(self, pack: SkillResolutionPack) -> str:
        return render_candidates_for_planner(
            pack.candidates,
            max_candidates=self.planner_limit,
            max_chars=self.render_budget,
        )
