from runtime.config.skill_frontmatter import parse_skill_frontmatter


def test_parse_skill_frontmatter_preserves_indented_mapping_metadata():
    metadata = parse_skill_frontmatter(
        """---
name: Release Review
distinguish_from:
  documentation: Use documentation planning for prose-only tasks.
  ui: Use UI review for visual-only tasks.
---
"""
    )

    assert metadata["distinguish_from"] == {
        "documentation": "Use documentation planning for prose-only tasks.",
        "ui": "Use UI review for visual-only tasks.",
    }
