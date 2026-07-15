from pathlib import Path

from runtime.skill_library.taxonomy import classify_query_by_rules, load_skill_taxonomy


def test_default_taxonomy_contains_required_categories():
    taxonomy = load_skill_taxonomy()

    for category_id in [
        "programming/frontend",
        "programming/agent_loop",
        "design/comfyui",
        "documentation/planning",
        "tools/browser",
        "mcp/integration",
    ]:
        assert taxonomy.has_category(category_id)


def test_classify_query_by_rules_detects_frontend_and_agent_loop():
    taxonomy = load_skill_taxonomy()

    frontend = classify_query_by_rules("Fix Electron React UI layout in desktop/src", taxonomy)
    agent_loop = classify_query_by_rules("Planner worker scheduler agent loop context issue", taxonomy)

    assert "programming/frontend" in frontend
    assert "programming/agent_loop" in agent_loop


def test_classify_query_by_rules_detects_comfyui_and_browser():
    taxonomy = load_skill_taxonomy()

    comfyui = classify_query_by_rules("ComfyUI workflow node graph plugin", taxonomy)
    browser = classify_query_by_rules("embedded browser tab click form selector", taxonomy)

    assert "design/comfyui" in comfyui
    assert "tools/browser" in browser


def test_classify_query_by_rules_detects_writing_and_java_skill_domains():
    taxonomy = load_skill_taxonomy()

    writing = classify_query_by_rules("Edit text to remove generic AI writing patterns", taxonomy)
    java = classify_query_by_rules("Java Spring Boot MyBatis service implementation", taxonomy)

    assert "documentation" in writing
    assert "programming/backend" in java


def test_classify_query_by_rules_ignores_incidental_low_score_categories():
    taxonomy = load_skill_taxonomy()

    categories = classify_query_by_rules(
        "Java Spring Boot MyBatis service implementation with one UI mention",
        taxonomy,
    )

    assert categories == ["programming/backend"]


def test_suggest_categories_for_incomplete_skill_uses_its_metadata_without_mutating_it():
    from runtime.skill_library.schema import normalize_skill_metadata
    from runtime.skill_library.taxonomy import suggest_categories_for_skill

    entry = normalize_skill_metadata(
        {
            "id": "page_operator",
            "name": "Page Operator",
            "description": "Read browser pages and fill forms.",
            "tags": ["browser", "dom"],
            "use_when": ["Navigate a browser tab"],
            "do_not_use_when": ["Edit Python runtime"],
        },
        source="workspace",
        body_path="page_operator/SKILL.md",
    )

    suggestions = suggest_categories_for_skill(entry, load_skill_taxonomy())

    assert entry.category == ()
    assert suggestions == ["tools/browser"]


def test_load_skill_taxonomy_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "skill_taxonomy.json"
    path.write_text(
        '{"schema_version":"skill_taxonomy.v1","categories":[{"id":"programming","children":[{"id":"frontend"},{"id":"frontend"}]}]}',
        encoding="utf-8",
    )

    try:
        load_skill_taxonomy(Path(path))
    except ValueError as exc:
        assert "duplicate category id" in str(exc)
    else:
        raise AssertionError("duplicate category ids must be rejected")
