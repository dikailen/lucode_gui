from runtime.skill_library.reranker import rerank_skill_candidates
from runtime.skill_library.schema import SkillCandidate, normalize_skill_metadata


def _entry(raw):
    return normalize_skill_metadata(raw, source="workspace", body_path=f".lucode/skills/{raw['id']}/SKILL.md")


def test_rerank_skill_candidates_boosts_matching_scope():
    scoped = _entry(
        {
            "id": "desktop_ui",
            "name": "Desktop UI",
            "description": "Desktop UI work.",
            "category": ["programming", "frontend"],
            "tags": ["ui"],
            "use_when": ["UI work"],
            "do_not_use_when": ["Runtime work"],
            "scope_paths": ["desktop/src/**"],
        }
    )
    generic = _entry(
        {
            "id": "generic_ui",
            "name": "Generic UI",
            "description": "Generic UI work.",
            "category": ["programming", "frontend"],
            "tags": ["ui"],
            "use_when": ["UI work"],
            "do_not_use_when": ["Runtime work"],
        }
    )

    ranked = rerank_skill_candidates(
        "Fix UI",
        [SkillCandidate(generic, 0.55), SkillCandidate(scoped, 0.55)],
        paths=["desktop/src/app/App.tsx"],
    )

    assert ranked[0].entry.id == "desktop_ui"
    assert any("scope" in reason for reason in ranked[0].reasons)


def test_rerank_skill_candidates_penalizes_negative_query_and_misfire_usage():
    good = _entry(
        {
            "id": "context_ledger",
            "name": "Context Ledger",
            "description": "Context ledger work.",
            "category": ["programming", "agent_loop"],
            "tags": ["context"],
            "use_when": ["Context compression"],
            "do_not_use_when": ["UI style"],
        }
    )
    bad = _entry(
        {
            "id": "ui_refactor",
            "name": "UI Refactor",
            "description": "UI work.",
            "category": ["programming", "frontend"],
            "tags": ["ui"],
            "use_when": ["UI style"],
            "do_not_use_when": ["Context compression"],
            "negative_queries": ["context compression"],
            "usage": {"used_count": 10, "misfire_count": 8},
        }
    )

    ranked = rerank_skill_candidates(
        "context compression",
        [SkillCandidate(bad, 0.7), SkillCandidate(good, 0.55)],
    )

    assert ranked[0].entry.id == "context_ledger"
    assert any("negative" in penalty or "misfire" in penalty for penalty in ranked[1].penalties)
