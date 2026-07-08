from runtime.skill_library.retriever import retrieve_skill_candidates
from runtime.skill_library.schema import normalize_skill_metadata


def _entry(raw):
    return normalize_skill_metadata(raw, source="workspace", body_path=f".lucode/skills/{raw['id']}/SKILL.md")


def test_retrieve_skill_candidates_prefers_frontend_ui_skill():
    ui = _entry(
        {
            "id": "electron_ui_refactor",
            "name": "Electron UI Refactor",
            "description": "Improve Electron React UI layout.",
            "category": ["programming", "frontend"],
            "tags": ["electron", "react", "ui", "css"],
            "use_when": ["Modify Electron React UI"],
            "do_not_use_when": ["Modify Python agent loop"],
            "negative_queries": ["context compression strategy"],
            "scope_paths": ["desktop/src/**"],
        }
    )
    context = _entry(
        {
            "id": "context_ledger",
            "name": "Context Ledger",
            "description": "Context compression and ledger decisions.",
            "category": ["programming", "agent_loop"],
            "tags": ["context", "prompt"],
            "use_when": ["Design context compression"],
            "do_not_use_when": ["Modify Electron UI"],
            "negative_queries": ["button style fix"],
            "scope_paths": ["runtime/context/**"],
        }
    )

    candidates = retrieve_skill_candidates("Fix Electron React UI layout in desktop/src/styles.css", [context, ui])

    assert [candidate.entry.id for candidate in candidates][:1] == ["electron_ui_refactor"]
    assert any("scope" in reason for reason in candidates[0].reasons)


def test_retrieve_skill_candidates_uses_negative_queries_to_avoid_misfire():
    ui = _entry(
        {
            "id": "electron_ui_refactor",
            "name": "Electron UI Refactor",
            "description": "Improve Electron UI.",
            "category": ["programming", "frontend"],
            "tags": ["electron", "ui"],
            "use_when": ["Modify UI"],
            "do_not_use_when": ["Design context compression"],
            "negative_queries": ["context compression strategy"],
        }
    )

    candidates = retrieve_skill_candidates("Design context compression strategy", [ui])

    assert [candidate.entry.id for candidate in candidates] == []


def test_retrieve_skill_candidates_supports_explicit_skill_reference():
    ui = _entry(
        {
            "id": "electron_ui_refactor",
            "name": "Electron UI Refactor",
            "description": "Improve Electron UI.",
            "category": ["programming", "frontend"],
            "tags": ["electron", "ui"],
            "use_when": ["Modify UI"],
            "do_not_use_when": ["Runtime storage"],
        }
    )

    candidates = retrieve_skill_candidates("Use @electron_ui_refactor for this small UI change", [ui])

    assert candidates[0].entry.id == "electron_ui_refactor"
    assert candidates[0].explicit is True


def test_retrieve_skill_candidates_filters_disabled_and_incomplete_entries():
    disabled = _entry(
        {
            "id": "disabled_ui",
            "name": "Disabled UI",
            "description": "Disabled.",
            "category": ["programming", "frontend"],
            "tags": ["ui"],
            "use_when": ["UI"],
            "do_not_use_when": ["Runtime"],
            "enabled": False,
        }
    )
    incomplete = normalize_skill_metadata({"id": "thin"}, source="workspace", body_path="thin/SKILL.md")

    candidates = retrieve_skill_candidates("UI task", [disabled, incomplete], explicit_skill_ids=["disabled_ui", "thin"])

    assert candidates == []
