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
