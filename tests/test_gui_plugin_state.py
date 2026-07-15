from __future__ import annotations

import json

import pytest

from lucode.gui.plugin_state import PluginStateStore


def _write_plugin_package(root):
    package = root / "demo_plugin"
    skill_dir = package / "skills" / "demo_operator"
    mcp_dir = package / "mcp"
    skill_dir.mkdir(parents=True)
    mcp_dir.mkdir(parents=True)
    (package / "lucode-plugin.json").write_text(
        json.dumps(
            {
                "schema_version": "lucode_plugin.v1",
                "id": "demo_plugin",
                "title": "Demo Plugin",
                "description": "Demo optional plugin.",
                "skills": ["skills/demo_operator"],
                "mcp_templates": ["mcp/demo.json"],
                "launch_profiles": [
                    {
                        "id": "windows_demo",
                        "label": "Windows Demo",
                        "script": "run_demo.bat",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Operator\ndescription: Operate demo service.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    (mcp_dir / "demo.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo_graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                        "approval_required": True,
                        "side_effects": "controls_external_private_service",
                        "risk_level": "high",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return package


def test_plugin_state_persists_removed_skill_ids(tmp_path):
    store = PluginStateStore(tmp_path)

    removed = store.mark_skill_removed("solo_executor_contract")

    assert removed == {"solo_executor_contract"}
    assert store.load_removed_skill_ids() == {"solo_executor_contract"}
    data = json.loads((tmp_path / ".lucode" / "gui_plugin_state.json").read_text(encoding="utf-8"))
    assert data["removed_skill_ids"] == ["solo_executor_contract"]


def test_plugin_state_rejects_invalid_skill_ids(tmp_path):
    store = PluginStateStore(tmp_path)

    with pytest.raises(ValueError):
        store.mark_skill_removed("../skills/code-engineer")

    assert not (tmp_path / ".lucode" / "gui_plugin_state.json").exists()


def test_plugin_state_persists_disabled_skill_ids_without_touching_skill_files(tmp_path):
    workspace = tmp_path / "workspace"
    skill_file = workspace / ".lucode" / "skills" / "demo_skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: Demo\n---\n", encoding="utf-8")
    store = PluginStateStore(workspace)

    disabled = store.set_skill_enabled("demo_skill", False)

    assert disabled is False
    assert store.load_disabled_skill_ids() == {"demo_skill"}
    assert skill_file.read_text(encoding="utf-8") == "---\nname: Demo\n---\n"
    store.set_skill_enabled("demo_skill", True)
    assert store.load_disabled_skill_ids() == set()


def test_plugin_state_installs_skill_folder_into_workspace_local_skills(tmp_path):
    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Source Skill\ndescription: Drag installed skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    store = PluginStateStore(tmp_path / "workspace")

    card = store.install_skill_from_path(source)

    target = tmp_path / "workspace" / ".lucode" / "skills" / "source-skill"
    assert target.exists()
    assert (target / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert card.id == "source-skill"
    assert card.title == "Source Skill"
    assert card.description == "Drag installed skill."
    assert store.load_custom_skill_cards() == [card]


def test_plugin_state_installs_mcp_json_into_workspace_local_config(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": "python",
                        "args": ["server.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = PluginStateStore(tmp_path / "workspace")

    row = store.install_mcp_from_path(config)

    assert row.id == "demo"
    assert row.title == "demo"
    assert store.load_custom_mcp_rows() == [row]
    data = json.loads((tmp_path / "workspace" / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["demo"]["command"] == "python"


def test_plugin_state_registers_external_stdio_mcp_config(tmp_path):
    store = PluginStateStore(tmp_path / "workspace")

    row = store.register_external_mcp(
        {
            "id": "local_docs",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py", "--stdio"],
        }
    )

    assert row.id == "local_docs"
    assert row.title == "local_docs"
    assert "stdio" in row.detail
    assert store.load_custom_mcp_rows() == [row]
    data = json.loads((tmp_path / "workspace" / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["local_docs"] == {
        "transport": "stdio",
        "command": "python",
        "args": ["server.py", "--stdio"],
    }


def test_plugin_state_registers_external_http_mcp_config(tmp_path):
    store = PluginStateStore(tmp_path / "workspace")

    row = store.register_external_mcp(
        {
            "id": "comfyui_graph",
            "transport": "http",
            "url": "http://127.0.0.1:8188/mcp",
        }
    )

    assert row.id == "comfyui_graph"
    assert "http" in row.detail
    data = json.loads((tmp_path / "workspace" / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["comfyui_graph"] == {
        "transport": "http",
        "url": "http://127.0.0.1:8188/mcp",
    }


def test_plugin_state_rejects_external_mcp_without_required_endpoint(tmp_path):
    store = PluginStateStore(tmp_path / "workspace")

    with pytest.raises(ValueError, match="command is required"):
        store.register_external_mcp({"id": "bad_stdio", "transport": "stdio"})

    with pytest.raises(ValueError, match="url is required"):
        store.register_external_mcp({"id": "bad_http", "transport": "http"})


def test_plugin_state_lists_and_uninstalls_plugin_package(tmp_path):
    package = _write_plugin_package(tmp_path)
    workspace = tmp_path / "workspace"
    store = PluginStateStore(workspace)

    installed = store.install_plugin_package_from_path(package)

    assert installed["id"] == "demo_plugin"
    assert store.load_installed_plugin_packages() == [
        {
            "id": "demo_plugin",
            "title": "Demo Plugin",
            "description": "Demo optional plugin.",
            "skill_ids": ["demo_operator"],
            "mcp_ids": ["demo_graph"],
            "launch_profiles": [
                {
                    "id": "windows_demo",
                    "label": "Windows Demo",
                    "script": "run_demo.bat",
                }
            ],
            "deletable": True,
        }
    ]

    removed = store.uninstall_plugin_package("demo_plugin")

    assert removed == {
        "id": "demo_plugin",
        "skill_ids": ["demo_operator"],
        "mcp_ids": ["demo_graph"],
    }
    assert store.load_installed_plugin_packages() == []
    assert store.load_custom_skill_cards() == []
    assert store.load_custom_mcp_rows() == []
    assert not (workspace / ".lucode" / "plugins" / "demo_plugin").exists()
    assert not (workspace / ".lucode" / "skills" / "demo_operator").exists()
    mcp_data = json.loads((workspace / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert "demo_graph" not in mcp_data["mcpServers"]


def test_plugin_package_rejects_mcp_template_without_risk_metadata(tmp_path):
    package = _write_plugin_package(tmp_path)
    (package / "mcp" / "demo.json").write_text(
        json.dumps({"mcpServers": {"demo_graph": {"transport": "http", "url": "http://127.0.0.1:8188/mcp"}}}),
        encoding="utf-8",
    )
    store = PluginStateStore(tmp_path / "workspace")

    with pytest.raises(ValueError, match="risk_level"):
        store.install_plugin_package_from_path(package)


def test_plugin_package_rejects_mcp_template_with_invalid_risk_metadata(tmp_path):
    package = _write_plugin_package(tmp_path)
    (package / "mcp" / "demo.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo_graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                        "approval_required": True,
                        "side_effects": "controls_external_private_service",
                        "risk_level": "severe",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = PluginStateStore(tmp_path / "workspace")

    with pytest.raises(ValueError, match="risk_level"):
        store.install_plugin_package_from_path(package)
