from runtime.skill_library.resolver import SkillResolver
from runtime.skill_library.renderer import render_candidates_for_planner
from runtime.skill_library.schema import SkillCandidate, normalize_skill_metadata


def _entry(raw):
    return normalize_skill_metadata(raw, source="workspace", body_path=f".lucode/skills/{raw['id']}/SKILL.md")


def _candidate(raw, score=0.7, reasons=("use_when match",), penalties=()):
    return SkillCandidate(entry=_entry(raw), score=score, reasons=tuple(reasons), penalties=tuple(penalties))


def test_resolver_returns_top_metadata_candidates_without_task_bindings():
    ui = _entry(
        {
            "id": "electron_ui",
            "name": "Electron UI",
            "description": "Electron React UI layout work.",
            "category": ["programming", "frontend"],
            "tags": ["electron", "react", "ui"],
            "use_when": ["Fix Electron React UI layout"],
            "do_not_use_when": ["Context compression"],
        }
    )
    context = _entry(
        {
            "id": "context_ledger",
            "name": "Context Ledger",
            "description": "Context compression and prompt budget work.",
            "category": ["programming", "agent_loop"],
            "tags": ["context", "prompt"],
            "use_when": ["Context compression"],
            "do_not_use_when": ["UI layout"],
            "negative_queries": ["button style"],
        }
    )
    resolver = SkillResolver(entries=[context, ui], candidate_limit=20, planner_limit=3)

    pack = resolver.resolve_for_planner("Fix Electron React UI layout in desktop/src/App.tsx")

    assert [candidate.entry.id for candidate in pack.candidates] == ["electron_ui"]
    assert pack.candidate_skill_ids == ("electron_ui",)
    assert not hasattr(pack, "task_bindings")
    assert not hasattr(pack, "adopted_skill_ids")


def test_renderer_limits_candidates_and_does_not_render_skill_body_or_paths():
    candidates = [
        _candidate(
            {
                "id": "first_skill",
                "name": "First Skill",
                "description": "First metadata summary.",
                "category": ["programming", "frontend"],
                "tags": ["ui"],
                "use_when": ["Use first"],
                "do_not_use_when": ["Do not use first"],
            },
            score=0.9,
        ),
        _candidate(
            {
                "id": "second_skill",
                "name": "Second Skill",
                "description": "Second metadata summary.",
                "category": ["programming", "backend"],
                "tags": ["runtime"],
                "use_when": ["Use second"],
                "do_not_use_when": ["Do not use second"],
            },
            score=0.8,
        ),
        _candidate(
            {
                "id": "third_skill",
                "name": "Third Skill",
                "description": "Third metadata summary.",
                "category": ["tools", "terminal"],
                "tags": ["shell"],
                "use_when": ["Use third"],
                "do_not_use_when": ["Do not use third"],
            },
            score=0.7,
        ),
        _candidate(
            {
                "id": "fourth_skill",
                "name": "Fourth Skill",
                "description": "Fourth metadata summary.",
                "category": ["documentation", "planning"],
                "tags": ["plan"],
                "use_when": ["Use fourth"],
                "do_not_use_when": ["Do not use fourth"],
            },
            score=0.6,
        ),
    ]

    rendered = render_candidates_for_planner(candidates, max_candidates=3, max_chars=1200)

    assert "first_skill" in rendered
    assert "third_skill" in rendered
    assert "fourth_skill" not in rendered
    assert "SKILL.md" not in rendered
    assert "body_path" not in rendered
    assert "Skill body" not in rendered
    assert len(rendered) <= 1200


def test_renderer_budget_truncates_metadata_without_prompt_growth():
    long_text = "very long metadata " * 80
    candidates = [
        _candidate(
            {
                "id": "long_skill",
                "name": "Long Skill",
                "description": long_text,
                "category": ["programming", "backend"],
                "tags": ["runtime", "tests"],
                "use_when": [long_text],
                "do_not_use_when": [long_text],
            },
            score=0.9,
        )
    ]

    rendered = render_candidates_for_planner(candidates, max_candidates=5, max_chars=320)

    assert len(rendered) <= 320
    assert rendered.endswith("...[truncated]")


def test_renderer_is_candidate_only_and_does_not_emit_skill_interface_contract():
    rendered = render_candidates_for_planner(
        [
            _candidate(
                {
                    "id": "code_engineer",
                    "name": "Code Engineer",
                    "description": "Code work.",
                    "category": ["programming", "backend"],
                    "tags": ["code"],
                    "use_when": ["Fix code"],
                    "do_not_use_when": ["Casual chat"],
                }
            )
        ]
    )

    assert "skill_interface" not in rendered
    assert "task_bindings" not in rendered
    assert "adopted_skill_ids" not in rendered
