from runtime.skill_library.schema import SkillIndexEntry, normalize_skill_metadata, skill_entry_to_dict


def test_normalize_skill_metadata_accepts_new_fields():
    raw = {
        "id": "electron-ui-refactor",
        "name": "Electron UI Refactor",
        "description": "Improve Electron React layouts and interaction quality.",
        "category": ["programming", "frontend"],
        "tags": ["electron", "react", "ui"],
        "use_when": ["Modify Electron React UI"],
        "do_not_use_when": ["Modify Python agent loop"],
        "negative_queries": ["context compression strategy"],
        "distinguish_from": {"context-ledger": "Context ledger is for prompt compression."},
        "scope_paths": ["desktop/src/**"],
        "verification": ["cd desktop && npm run typecheck"],
        "risk_level": "low",
    }

    entry = normalize_skill_metadata(raw, source="workspace", body_path=".lucode/skills/electron-ui-refactor/SKILL.md")

    assert isinstance(entry, SkillIndexEntry)
    assert entry.id == "electron_ui_refactor"
    assert entry.name == "Electron UI Refactor"
    assert entry.summary == "Improve Electron React layouts and interaction quality."
    assert entry.category == ("programming", "frontend")
    assert entry.tags == ("electron", "react", "ui")
    assert entry.use_when == ("Modify Electron React UI",)
    assert entry.do_not_use_when == ("Modify Python agent loop",)
    assert entry.negative_queries == ("context compression strategy",)
    assert entry.distinguish_from == {"context-ledger": "Context ledger is for prompt compression."}
    assert entry.scope_paths == ("desktop/src/**",)
    assert entry.verification == ("cd desktop && npm run typecheck",)
    assert entry.metadata_status == "ready"
    assert entry.assignable is True


def test_missing_required_metadata_is_marked_incomplete():
    entry = normalize_skill_metadata({"id": "demo"}, source="workspace", body_path="demo/SKILL.md")

    assert entry.metadata_status == "incomplete"
    assert entry.assignable is False
    assert set(entry.missing_fields) >= {"name", "description", "category", "tags", "use_when", "do_not_use_when"}


def test_core_and_disabled_skills_are_not_assignable():
    core_entry = normalize_skill_metadata(
        {
            "id": "code_engineer",
            "name": "Code Engineer",
            "description": "Core coding contract.",
            "category": ["programming"],
            "tags": ["code"],
            "use_when": ["Implement code"],
            "do_not_use_when": ["Casual chat"],
        },
        source="core",
        body_path="core_skills/code-engineer/SKILL.md",
    )
    disabled_entry = normalize_skill_metadata(
        {
            "id": "ui-helper",
            "name": "UI Helper",
            "description": "UI helper.",
            "category": ["programming", "frontend"],
            "tags": ["ui"],
            "use_when": ["UI work"],
            "do_not_use_when": ["Runtime work"],
            "enabled": False,
        },
        source="workspace",
        body_path=".lucode/skills/ui-helper/SKILL.md",
    )

    assert core_entry.core is True
    assert core_entry.assignable is False
    assert disabled_entry.enabled is False
    assert disabled_entry.assignable is False


def test_skill_entry_serializes_without_losing_metadata():
    entry = normalize_skill_metadata(
        {
            "id": "comfyui_operator",
            "name": "ComfyUI Operator",
            "description": "Operate ComfyUI workflows.",
            "category": ["design", "comfyui"],
            "tags": ["comfyui"],
            "use_when": ["ComfyUI workflow task"],
            "do_not_use_when": ["Normal code review"],
        },
        source="workspace",
        body_path=".lucode/skills/comfyui_operator/SKILL.md",
    )

    data = skill_entry_to_dict(entry)

    assert data["id"] == "comfyui_operator"
    assert data["category"] == ["design", "comfyui"]
    assert data["metadata_status"] == "ready"
    assert data["body_path"].endswith("SKILL.md")
