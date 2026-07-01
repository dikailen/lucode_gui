from __future__ import annotations

import json

import pytest

from lucode.gui.plugin_state import PluginStateStore


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
