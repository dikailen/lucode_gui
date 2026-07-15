from pathlib import Path

import pytest


def _write_skill(root: Path, folder: str, description: str) -> Path:
    skill_file = root / ".lucode" / "skills" / folder / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        "---\n"
        f"name: {folder}\n"
        f"description: {description}\n"
        "---\n\n"
        "# Skill body\n",
        encoding="utf-8",
    )
    return skill_file


def test_rule_proposal_is_persisted_without_mutating_skill_source(tmp_path):
    from runtime.skill_library.metadata_proposals import load_skill_metadata_proposals, upsert_rule_metadata_proposal

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    original = skill_file.read_text(encoding="utf-8")

    proposal = upsert_rule_metadata_proposal(
        workspace_root=tmp_path,
        skill_id="humanizer",
        skill_file=skill_file,
        payload={
            "categories": ["documentation"],
            "tags": ["editing", "writing"],
            "use_when": ["Edit or review text."],
            "do_not_use_when": [],
        },
        reason="rules: description contains writing and editing terms",
    )

    loaded = load_skill_metadata_proposals(tmp_path, skill_id="humanizer")

    assert proposal.status == "pending"
    assert proposal.source == "rules"
    assert loaded == [proposal]
    assert skill_file.read_text(encoding="utf-8") == original


def test_new_skill_content_version_stales_previous_pending_rule_proposal(tmp_path):
    from runtime.skill_library.metadata_proposals import load_skill_metadata_proposals, upsert_rule_metadata_proposal

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    first = upsert_rule_metadata_proposal(
        workspace_root=tmp_path,
        skill_id="humanizer",
        skill_file=skill_file,
        payload={"categories": ["documentation"], "tags": ["editing"], "use_when": ["Edit text."], "do_not_use_when": []},
    )
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nUpdated instructions.\n", encoding="utf-8")

    second = upsert_rule_metadata_proposal(
        workspace_root=tmp_path,
        skill_id="humanizer",
        skill_file=skill_file,
        payload={"categories": ["documentation"], "tags": ["editing"], "use_when": ["Edit text."], "do_not_use_when": []},
    )
    loaded = load_skill_metadata_proposals(tmp_path, skill_id="humanizer")

    assert first.proposal_id != second.proposal_id
    assert second.status == "pending"
    assert [proposal.status for proposal in loaded] == ["pending", "stale"]


def test_rule_sync_persists_only_incomplete_workspace_skill_suggestions(tmp_path):
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.metadata_proposals import load_skill_metadata_proposals, sync_rule_metadata_proposals
    from runtime.skill_library.schema import normalize_skill_metadata
    from runtime.skill_library.taxonomy import load_skill_taxonomy

    incomplete_file = _write_skill(tmp_path, "page_operator", "Read browser pages and fill forms.")
    ready_file = _write_skill(tmp_path, "release_review", "Review release changes before publishing.")
    ready_file.write_text(
        "---\n"
        "name: release_review\n"
        "description: Review release changes before publishing.\n"
        "category: [programming, testing]\n"
        "tags: [release]\n"
        "use_when: [Review a release]\n"
        "do_not_use_when: [Write marketing copy]\n"
        "---\n\n# Skill body\n",
        encoding="utf-8",
    )
    entries = [
        normalize_skill_metadata(read_skill_frontmatter(incomplete_file), source="workspace", body_path=str(incomplete_file), folder="page_operator"),
        normalize_skill_metadata(read_skill_frontmatter(ready_file), source="workspace", body_path=str(ready_file), folder="release_review"),
    ]

    proposals = sync_rule_metadata_proposals(tmp_path, entries, taxonomy=load_skill_taxonomy())

    assert set(proposals) == {"page_operator"}
    proposal = proposals["page_operator"]
    assert proposal.payload["categories"] == ["tools/browser"]
    assert proposal.payload["tags"] == ["page", "operator", "read", "browser", "pages", "fill", "forms"]
    assert proposal.payload["use_when"] == ["Read browser pages and fill forms."]
    assert load_skill_metadata_proposals(tmp_path, skill_id="release_review") == []


def test_plugin_state_exposes_persisted_rule_proposal_for_incomplete_skill(tmp_path):
    from runtime.server.run_manager import RuntimeRunManager
    from runtime.skill_library.metadata_proposals import load_skill_metadata_proposals

    _write_skill(tmp_path, "page_operator", "Read browser pages and fill forms.")

    payload = RuntimeRunManager(tmp_path).plugin_state()
    entry = next(item for item in payload["skill_library"] if item["id"] == "page_operator")
    proposals = load_skill_metadata_proposals(tmp_path, skill_id="page_operator")

    assert entry["metadata_proposal"]["source"] == "rules"
    assert entry["metadata_proposal"]["status"] == "pending"
    assert entry["metadata_proposal"]["payload"]["categories"] == ["tools/browser"]
    assert len(proposals) == 1


def test_deleting_skill_proposals_removes_every_version_for_the_skill(tmp_path):
    from runtime.skill_library.metadata_proposals import (
        delete_skill_metadata_proposals,
        load_skill_metadata_proposals,
        upsert_rule_metadata_proposal,
    )

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    upsert_rule_metadata_proposal(
        workspace_root=tmp_path,
        skill_id="humanizer",
        skill_file=skill_file,
        payload={"categories": ["documentation"], "tags": ["editing"], "use_when": ["Edit text."], "do_not_use_when": []},
    )
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nNew version.\n", encoding="utf-8")
    upsert_rule_metadata_proposal(
        workspace_root=tmp_path,
        skill_id="humanizer",
        skill_file=skill_file,
        payload={"categories": ["documentation"], "tags": ["editing"], "use_when": ["Edit text."], "do_not_use_when": []},
    )

    deleted = delete_skill_metadata_proposals(tmp_path, skill_id="humanizer")

    assert deleted == 2
    assert load_skill_metadata_proposals(tmp_path, skill_id="humanizer") == []


def test_ai_batch_classifier_persists_sanitized_ai_proposal_once_per_skill_version(tmp_path):
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.ai_metadata_classifier import classify_pending_skill_metadata
    from runtime.skill_library.schema import normalize_skill_metadata

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    entry = normalize_skill_metadata(
        read_skill_frontmatter(skill_file), source="workspace", body_path=str(skill_file), folder="humanizer"
    )
    calls = []

    def classifier(requests):
        calls.append(requests)
        assert requests[0].body_path == ""
        return [{"skill_id": "humanizer", "categories": ["documentation"], "tags": ["editing"], "use_when": ["Edit prose."], "do_not_use_when": ["Do not use for backend work."], "confidence": 0.82, "reason": "Writing workflow."}]

    first = classify_pending_skill_metadata(tmp_path, [entry], classifier=classifier)
    second = classify_pending_skill_metadata(tmp_path, [entry], classifier=classifier)

    assert [item.source for item in first.persisted] == ["ai"]
    assert first.skipped == ()
    assert second.persisted == ()
    assert second.skipped == ("humanizer",)
    assert len(calls) == 1


def test_ai_batch_classifier_rejects_unknown_taxonomy_and_keeps_database_clean(tmp_path):
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.ai_metadata_classifier import classify_pending_skill_metadata
    from runtime.skill_library.metadata_proposals import load_skill_metadata_proposals
    from runtime.skill_library.schema import normalize_skill_metadata

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    entry = normalize_skill_metadata(
        read_skill_frontmatter(skill_file), source="workspace", body_path=str(skill_file), folder="humanizer"
    )

    def classifier(_requests):
        return [{"skill_id": "humanizer", "categories": ["made_up_category"], "confidence": 0.9}]

    with pytest.raises(ValueError, match="unknown Skill category"):
        classify_pending_skill_metadata(tmp_path, [entry], classifier=classifier)

    assert load_skill_metadata_proposals(tmp_path, skill_id="humanizer") == []


def test_ai_batch_classifier_does_not_run_when_model_is_not_privacy_eligible(tmp_path):
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.ai_metadata_classifier import classify_pending_skill_metadata
    from runtime.skill_library.schema import normalize_skill_metadata

    skill_file = _write_skill(tmp_path, "humanizer", "Edit text to remove generic AI writing patterns.")
    entry = normalize_skill_metadata(
        read_skill_frontmatter(skill_file), source="workspace", body_path=str(skill_file), folder="humanizer"
    )

    result = classify_pending_skill_metadata(
        tmp_path,
        [entry],
        classifier=lambda _requests: pytest.fail("classifier must not be called"),
        model_info={"configured": True, "backend_type": "openai", "id": "cloud"},
        privacy_mode="offline",
    )

    assert result.persisted == ()
    assert result.blocked_reason == "model_not_allowed"


def test_proposal_lookup_uses_normalized_skill_id_when_folder_keeps_hyphens(tmp_path):
    from runtime.config.skill_frontmatter import read_skill_frontmatter
    from runtime.skill_library.metadata_proposals import sync_rule_metadata_proposals
    from runtime.skill_library.schema import normalize_skill_metadata
    from runtime.skill_library.taxonomy import load_skill_taxonomy

    skill_file = _write_skill(tmp_path, "Humanizer-zh-main", "Edit text to remove generic AI writing patterns.")
    entry = normalize_skill_metadata(
        read_skill_frontmatter(skill_file),
        source="workspace",
        body_path=str(skill_file),
        folder="Humanizer-zh-main",
    )

    proposals = sync_rule_metadata_proposals(tmp_path, [entry], taxonomy=load_skill_taxonomy())

    assert entry.id == "humanizer_zh_main"
    assert proposals[entry.id].skill_id == entry.id
