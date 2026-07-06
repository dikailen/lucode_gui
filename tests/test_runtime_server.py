from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient, WebSocketDisconnect

from runtime.events import ExecutionEventBus
from runtime.config.model_config import provider_api_key_value
from runtime.server.app import create_app
from runtime.server.execution_bridge import KernelAgentLoopExecutor, RunExecutionRequest, RunExecutionResult
from runtime.server.run_manager import RuntimeRunManager


TOKEN = "test-runtime-token"


def _auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _quick_stage_runner(request):
    request.event_bus.emit(
        "PlanningStarted",
        "planning started",
        agent="orchestrator",
        status="running",
        payload={"route_type": "single_agent"},
    )
    return RunExecutionResult(final_output="mock final output")


def _client(tmp_path, *, token: str = TOKEN, model_catalog_provider=None, run_executor=None) -> TestClient:
    app = create_app(
        workspace_root=tmp_path,
        runtime_token=token,
        model_catalog_provider=model_catalog_provider
        or (
            lambda: {
                "models": [
                    {
                        "id": "openai/gpt-5.5",
                        "name": "GPT-5.5",
                        "provider": "openai",
                        "configured": True,
                    }
                ]
            }
        ),
        run_executor=run_executor or _quick_stage_runner,
    )
    return TestClient(app)


def _make_comfyui_portable(root):
    root.mkdir(parents=True)
    (root / "ComfyUI").mkdir()
    (root / "ComfyUI" / "main.py").write_text("# comfyui main\n", encoding="utf-8")
    (root / "python_embeded").mkdir()
    (root / "python_embeded" / "python.exe").write_text("", encoding="utf-8")
    (root / "run_nvidia_gpu.bat").write_text(
        ".\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build\npause\n",
        encoding="utf-8",
    )
    (root / "run_cpu.bat").write_text(
        ".\\python_embeded\\python.exe -s ComfyUI\\main.py --cpu --windows-standalone-build\npause\n",
        encoding="utf-8",
    )
    return root


def _wait_terminal_idle(client: TestClient, *, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    payload = {}
    while time.monotonic() < deadline:
        response = client.get("/api/terminal", headers=_auth_headers())
        assert response.status_code == 200, response.json()
        payload = response.json()
        if not payload["running"] and payload["last_result"] is not None:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"terminal did not become idle: {payload}")


def test_health_is_available_without_runtime_token(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["service"] == "lucode-runtime-server"


def test_health_reports_desktop_browser_bridge_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    client = _client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    bridge = response.json()["desktop_browser_bridge"]
    assert bridge == {
        "available": True,
        "url_configured": True,
        "token_configured": True,
    }
    assert response.json()["schema_version"] == "runtime_server.v1"
    assert response.json()["workspace_root"] == str(tmp_path.resolve())


def test_protected_http_routes_require_runtime_token(tmp_path):
    client = _client(tmp_path)

    missing = client.get("/api/models")
    wrong = client.get("/api/models", headers=_auth_headers("wrong-token"))

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_create_app_requires_token_when_auth_is_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LUCODE_RUNTIME_TOKEN", raising=False)

    with pytest.raises(ValueError, match="LUCODE_RUNTIME_TOKEN"):
        create_app(workspace_root=tmp_path)


def test_create_app_can_read_runtime_token_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_RUNTIME_TOKEN", TOKEN)
    client = TestClient(create_app(workspace_root=tmp_path))

    response = client.get("/api/models", headers=_auth_headers())

    assert response.status_code == 200


def test_server_cli_builds_app_from_args_and_environment(tmp_path, monkeypatch):
    from runtime.server.cli import build_app_from_args, build_arg_parser

    monkeypatch.setenv("LUCODE_RUNTIME_TOKEN", TOKEN)
    args = build_arg_parser().parse_args(["--workspace", str(tmp_path), "--host", "127.0.0.1", "--port", "43210"])
    client = TestClient(build_app_from_args(args))

    response = client.get("/api/health")

    assert args.host == "127.0.0.1"
    assert args.port == 43210
    assert response.status_code == 200
    assert response.json()["workspace_root"] == str(tmp_path.resolve())


def test_models_endpoint_returns_sanitized_catalog(tmp_path):
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "deepseek/deepseek-chat",
                    "display_name": "DeepSeek Chat",
                    "name": "deepseek-chat",
                    "provider": "deepseek",
                    "configured": True,
                    "api_key": "should-not-leak",
                },
                {
                    "id": "local/qwen",
                    "name": "Qwen Local",
                    "provider": "ollama",
                    "configured": False,
                },
            ]
        },
    )

    response = client.get("/api/models", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "models.v1",
        "models": [
            {
                "id": "deepseek/deepseek-chat",
                "display_name": "DeepSeek Chat",
                "provider": "deepseek",
                "configured": True,
            },
            {
                "id": "local/qwen",
                "display_name": "Qwen Local",
                "provider": "ollama",
                "configured": False,
            },
        ],
    }


def test_model_settings_endpoint_returns_sanitized_roles_and_provider_summary(tmp_path):
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "deepseek_deepseek_chat_model",
                    "display_name_zh": "DeepSeek Chat",
                    "provider": "deepseek",
                    "configured": True,
                    "api_key": "should-not-leak",
                    "base_url": "https://api.deepseek.example/v1",
                    "model_name": "deepseek-chat",
                    "backend_type": "openai_compatible",
                    "privacy_level": "cloud",
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "medium",
                    "model_tier": "large",
                },
                {
                    "id": "ollama_qwen_model",
                    "display_name": "Qwen Local",
                    "provider": "ollama",
                    "configured": False,
                    "model_name": "qwen",
                    "backend_type": "ollama",
                    "privacy_level": "local",
                    "supports_tools": False,
                },
            ]
        },
    )

    response = client.get("/api/settings/models", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "model_settings.v1"
    assert payload["summary"] == {
        "model_count": 2,
        "configured_model_count": 1,
        "provider_count": 2,
        "configured_provider_count": 1,
    }
    assert payload["models"][0] == {
        "id": "deepseek_deepseek_chat_model",
        "ref": "deepseek/deepseek-chat",
        "display_name": "DeepSeek Chat",
        "provider": "deepseek",
        "configured": True,
        "available": True,
        "backend_type": "openai_compatible",
        "model_name": "deepseek-chat",
        "privacy_level": "cloud",
        "supports_tools": True,
        "reasoning_level": "high",
        "cost_level": "medium",
        "model_tier": "large",
    }
    assert payload["providers"][0] == {
        "provider": "deepseek",
        "display_name": "deepseek",
        "model_count": 1,
        "configured_model_count": 1,
        "configured": True,
    }
    assert payload["roles"][0]["role"] == "query_refiner"
    assert payload["roles"][0]["label"] == "前置优化脑"
    assert "api_key" not in str(payload)


def test_model_settings_endpoint_returns_runtime_preferences_from_project_config(tmp_path):
    (tmp_path / ".lucode").mkdir()
    (tmp_path / ".lucode" / "config.toml").write_text(
        "\n".join(
            [
                'mode = "auto"',
                'privacy = "offline"',
                "query_refiner_enabled = true",
                'allowed_worker_models = ["deepseek_deepseek_chat_model"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "deepseek_deepseek_chat_model",
                    "display_name_zh": "DeepSeek Chat",
                    "provider": "deepseek",
                    "configured": True,
                    "model_name": "deepseek-chat",
                    "backend_type": "openai_compatible",
                    "privacy_level": "cloud",
                    "supports_tools": True,
                },
            ]
        },
    )

    response = client.get("/api/settings/models", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["runtime_preferences"] == {
        "execution_mode": "auto",
        "privacy_mode": "offline",
        "query_refiner_enabled": True,
        "allowed_worker_models": ["deepseek_deepseek_chat_model"],
        "worker_pool_available": True,
    }
    assert response.json()["ui_preferences"] == {
        "language": "zh",
    }


def test_model_role_update_saves_project_config_and_refreshes_settings(tmp_path):
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "deepseek_deepseek_chat_model",
                    "display_name_zh": "DeepSeek Chat",
                    "provider": "deepseek",
                    "configured": True,
                    "api_key": "should-not-leak",
                    "model_name": "deepseek-chat",
                    "backend_type": "openai_compatible",
                    "privacy_level": "cloud",
                    "supports_tools": True,
                },
                {
                    "id": "ollama_qwen_model",
                    "display_name": "Qwen Local",
                    "provider": "ollama",
                    "configured": True,
                    "model_name": "qwen",
                    "backend_type": "ollama",
                    "privacy_level": "local",
                    "supports_tools": True,
                },
            ]
        },
    )

    response = client.put(
        "/api/settings/models/roles/orchestrator",
        headers=_auth_headers(),
        json={"model_id": "deepseek_deepseek_chat_model"},
    )

    assert response.status_code == 200
    payload = response.json()
    role = next(item for item in payload["roles"] if item["role"] == "orchestrator")
    assert role["selected_model_id"] == "deepseek_deepseek_chat_model"
    assert role["model_priority"] == ["deepseek_deepseek_chat_model"]
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert "[roles]" in config_text
    assert 'orchestrator = ["deepseek/deepseek-chat"]' in config_text
    assert "should-not-leak" not in str(payload)


def test_query_refiner_update_persists_project_config_and_refreshes_settings(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/api/settings/query-refiner",
        headers=_auth_headers(),
        json={"enabled": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_preferences"]["query_refiner_enabled"] is True
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert "query_refiner_enabled = true" in config_text


def test_privacy_update_persists_project_config_and_refreshes_settings(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/api/settings/privacy",
        headers=_auth_headers(),
        json={"mode": "offline"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_preferences"]["privacy_mode"] == "offline"
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert 'privacy = "offline"' in config_text


def test_worker_pool_update_filters_to_configured_models_and_persists(tmp_path):
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "deepseek_deepseek_chat_model",
                    "display_name_zh": "DeepSeek Chat",
                    "provider": "deepseek",
                    "configured": True,
                    "model_name": "deepseek-chat",
                    "backend_type": "openai_compatible",
                    "privacy_level": "cloud",
                    "supports_tools": True,
                },
                {
                    "id": "ollama_qwen_model",
                    "display_name": "Qwen Local",
                    "provider": "ollama",
                    "configured": False,
                    "model_name": "qwen",
                    "backend_type": "ollama",
                    "privacy_level": "local",
                    "supports_tools": True,
                },
            ]
        },
    )

    response = client.put(
        "/api/settings/worker-pool",
        headers=_auth_headers(),
        json={
            "model_ids": [
                "deepseek_deepseek_chat_model",
                "ollama_qwen_model",
                "missing_model",
                "deepseek_deepseek_chat_model",
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_preferences"]["allowed_worker_models"] == ["deepseek_deepseek_chat_model"]
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert 'allowed_worker_models = ["deepseek_deepseek_chat_model"]' in config_text
    assert "missing_model" not in config_text


def test_language_update_persists_project_config_and_refreshes_settings(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/api/settings/language",
        headers=_auth_headers(),
        json={"language": "en"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui_preferences"] == {"language": "en"}
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert "[ui]" in config_text
    assert 'language = "en"' in config_text


def test_provider_create_update_delete_persists_config_and_keeps_api_key_out_of_responses(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_USER_HOME", str(tmp_path / "home"))
    client = _client(tmp_path, model_catalog_provider=lambda: {"models": []})

    create_response = client.post(
        "/api/settings/providers",
        headers=_auth_headers(),
        json={
            "provider_id": "my_proxy",
            "display_name": "My Proxy",
            "homepage": "https://proxy.example",
            "base_url": "https://proxy.example/v1",
            "api_key": "sk-test-secret",
            "models": ["qwen-max", "deepseek-chat"],
            "compatible_type": "openai_compatible",
            "supports_tools": True,
            "custom": True,
        },
    )

    assert create_response.status_code == 200
    created_payload = create_response.json()
    provider = next(item for item in created_payload["providers"] if item["provider"] == "my_proxy")
    assert provider["display_name"] == "My Proxy"
    assert provider["configured"] is True
    assert provider["configured_model_count"] == 2
    assert provider["models"] == ["qwen-max", "deepseek-chat"]
    assert provider["key_configured"] is True
    assert provider["key_hint"] == "****cret"
    assert provider["base_url"] == "https://proxy.example/v1"
    assert "sk-test-secret" not in str(created_payload)
    config_text = (tmp_path / ".lucode" / "config.toml").read_text(encoding="utf-8")
    assert "[provider.my_proxy]" in config_text
    assert 'models = ["qwen-max", "deepseek-chat"]' in config_text
    auth_payload = json.loads((tmp_path / "home" / "auth.json").read_text(encoding="utf-8"))
    stored_auth = auth_payload["providers"]["my_proxy"]
    assert "api_key" not in stored_auth
    assert "api_key_encrypted" in stored_auth
    assert "sk-test-secret" not in json.dumps(stored_auth)
    assert provider_api_key_value(stored_auth) == "sk-test-secret"

    update_response = client.put(
        "/api/settings/providers/my_proxy",
        headers=_auth_headers(),
        json={
            "display_name": "My Proxy Updated",
            "homepage": "https://proxy.example/docs",
            "base_url": "https://proxy.example/v1",
            "api_key": "",
            "models": ["qwen-max"],
            "compatible_type": "openai_compatible",
            "supports_tools": False,
            "custom": True,
        },
    )

    assert update_response.status_code == 200
    updated_payload = update_response.json()
    updated_provider = next(item for item in updated_payload["providers"] if item["provider"] == "my_proxy")
    assert updated_provider["display_name"] == "My Proxy Updated"
    assert updated_provider["configured_model_count"] == 1
    assert updated_provider["key_configured"] is True
    assert updated_provider["key_hint"] == "****cret"
    assert "sk-test-secret" not in str(updated_payload)
    updated_auth = json.loads((tmp_path / "home" / "auth.json").read_text(encoding="utf-8"))
    assert provider_api_key_value(updated_auth["providers"]["my_proxy"]) == "sk-test-secret"
    assert "sk-test-secret" not in json.dumps(updated_auth["providers"]["my_proxy"])

    delete_response = client.delete("/api/settings/providers/my_proxy", headers=_auth_headers())

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_provider_id"] == "my_proxy"
    assert "my_proxy" not in [item["provider"] for item in delete_response.json()["providers"]]
    assert "sk-test-secret" not in str(delete_response.json())
    final_auth = json.loads((tmp_path / "home" / "auth.json").read_text(encoding="utf-8"))
    assert "my_proxy" not in final_auth["providers"]


def test_provider_model_fetch_uses_transient_api_key_without_leaking_it(tmp_path, monkeypatch):
    def fake_fetch(base_url, api_key, backend_type="openai_compatible", timeout=5.0):
        assert base_url == "https://proxy.example/v1"
        assert api_key == "sk-transient-secret"
        assert backend_type == "openai_compatible"
        assert timeout == 5.0
        return {"ok": True, "models": ["qwen-max", "deepseek-chat"], "source": "upstream", "error": ""}

    monkeypatch.setattr("runtime.server.run_manager.fetch_upstream_models", fake_fetch)
    client = _client(tmp_path, model_catalog_provider=lambda: {"models": []})

    response = client.post(
        "/api/settings/providers/fetch-models",
        headers=_auth_headers(),
        json={
            "base_url": "https://proxy.example/v1",
            "api_key": "sk-transient-secret",
            "compatible_type": "openai_compatible",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "schema_version": "provider_models.v1",
        "ok": True,
        "models": ["qwen-max", "deepseek-chat"],
        "source": "upstream",
        "error": "",
    }
    assert "sk-transient-secret" not in str(payload)
    assert not (tmp_path / ".lucode" / "config.toml").exists()


def test_provider_catalog_endpoint_returns_safe_presets(tmp_path):
    client = _client(tmp_path, model_catalog_provider=lambda: {"models": []})

    response = client.get("/api/settings/providers/catalog", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "provider_catalog.v1"
    provider_ids = [item["provider"] for item in payload["providers"]]
    assert "deepseek" in provider_ids
    assert "custom_openai_compatible" in provider_ids
    deepseek = next(item for item in payload["providers"] if item["provider"] == "deepseek")
    custom = next(item for item in payload["providers"] if item["provider"] == "custom_openai_compatible")
    assert deepseek["display_name"] == "DeepSeek"
    assert deepseek["base_url"] == "https://api.deepseek.com"
    assert deepseek["models"]
    assert custom["custom"] is True
    assert "api_key" not in str(payload)


def test_model_role_update_rejects_unconfigured_models(tmp_path):
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "ollama_qwen_model",
                    "display_name": "Qwen Local",
                    "provider": "ollama",
                    "configured": False,
                    "model_name": "qwen",
                    "backend_type": "ollama",
                    "privacy_level": "local",
                    "supports_tools": True,
                },
            ]
        },
    )

    response = client.put(
        "/api/settings/models/roles/executor",
        headers=_auth_headers(),
        json={"model_id": "ollama_qwen_model"},
    )

    assert response.status_code == 400
    assert not (tmp_path / ".lucode" / "config.toml").exists()


def test_plugin_state_endpoint_returns_skills_and_mcp_rows(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/plugins", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "plugin_state.v1"
    assert [item["id"] for item in payload["skills"]][:4] == [
        "code_engineer",
        "project_explorer",
        "final_synthesizer",
        "skill_creator",
    ]
    assert payload["skills"][0]["core"] is True
    assert {item["id"] for item in payload["mcp"]} >= {"filesystem", "git", "browser", "image_draw"}
    assert payload["runtime_capabilities"] == []


def test_plugin_state_endpoint_returns_runtime_capabilities_from_runtime_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_URL", "http://127.0.0.1:41011")
    monkeypatch.setenv("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN", "token_1")
    client = _client(tmp_path)

    response = client.get("/api/plugins", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "plugin_state.v1"
    assert payload["runtime_capabilities"] == [
        {
            "id": "desktop_browser",
            "display_name": "桌面内置浏览器",
            "summary": "Operate the embedded desktop browser through a local authenticated bridge. DOM actions require approval.",
            "summary_zh": "通过本地认证桥操作 Electron 内置浏览器，可读页面摘要并执行受控点击、填表、提交。",
            "surface": "desktop",
            "status_key": "desktop_runtime",
            "ability_keys": [
                "navigate",
                "page_summary",
                "controlled_click",
                "form_input",
                "form_submit",
            ],
            "risk_key": "approval_required",
        }
    ]


def test_plugin_delete_rejects_core_skill_and_removes_custom_skill(tmp_path):
    custom_dir = tmp_path / "custom_skill"
    custom_dir.mkdir()
    (custom_dir / "SKILL.md").write_text(
        "---\nname: custom_skill\ndescription: Custom test skill\n---\n\n# Custom\n",
        encoding="utf-8",
    )
    from lucode.gui.plugin_state import PluginStateStore

    PluginStateStore(tmp_path).install_skill_from_path(custom_dir)
    client = _client(tmp_path)

    core_response = client.delete("/api/plugins/skills/code_engineer", headers=_auth_headers())
    custom_response = client.delete("/api/plugins/skills/custom_skill", headers=_auth_headers())
    listed = client.get("/api/plugins", headers=_auth_headers()).json()

    assert core_response.status_code == 400
    assert custom_response.status_code == 200
    assert "custom_skill" not in [item["id"] for item in listed["skills"]]


def test_plugin_skill_install_endpoint_copies_folder_and_refreshes_state(tmp_path):
    source = tmp_path / "drag_skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Drag Skill\ndescription: Installed from Electron drop.\n---\n\n# Drag Skill\n",
        encoding="utf-8",
    )
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/skills/install",
        headers=_auth_headers(),
        json={"path": str(source)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["installed_skill_id"] == "drag_skill"
    installed = next(item for item in payload["skills"] if item["id"] == "drag_skill")
    assert installed["title"] == "Drag Skill"
    assert installed["description"] == "Installed from Electron drop."
    assert installed["deletable"] is True
    assert (tmp_path / ".lucode" / "skills" / "drag_skill" / "SKILL.md").exists()


def test_plugin_skill_install_endpoint_rejects_invalid_sources(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/skills/install",
        headers=_auth_headers(),
        json={"path": str(tmp_path / "missing_skill")},
    )

    assert response.status_code == 400
    assert "Skill install source" in response.json()["error"]["message"]


def test_plugin_mcp_install_endpoint_imports_json_and_refreshes_state(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "external_demo": {
                        "url": "http://127.0.0.1:8765/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/mcp/install",
        headers=_auth_headers(),
        json={"path": str(config)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["installed_mcp_id"] == "external_demo"
    installed = next(item for item in payload["mcp"] if item["id"] == "external_demo")
    assert installed["title"] == "external_demo"
    assert (tmp_path / ".lucode" / "mcp_servers.json").exists()
    stored = json.loads((tmp_path / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert stored["mcpServers"]["external_demo"]["url"] == "http://127.0.0.1:8765/mcp"


def test_plugin_package_install_endpoint_installs_skills_and_mcp_templates(tmp_path):
    package = tmp_path / "comfyui_plugin"
    skill_dir = package / "skills" / "comfyui_operator"
    mcp_dir = package / "mcp"
    skill_dir.mkdir(parents=True)
    mcp_dir.mkdir(parents=True)
    (package / "lucode-plugin.json").write_text(
        json.dumps(
            {
                "schema_version": "lucode_plugin.v1",
                "id": "comfyui_plugin",
                "title": "ComfyUI Plugin",
                "description": "Optional ComfyUI capability package.",
                "skills": ["skills/comfyui_operator"],
                "mcp_templates": ["mcp/comfyui.json"],
                "launch_profiles": [
                    {
                        "id": "windows_nvidia",
                        "label": "Windows NVIDIA portable",
                        "script": "run_nvidia_gpu.bat",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ComfyUI Operator\ndescription: Operate ComfyUI workflows.\n---\n\n# ComfyUI Operator\n",
        encoding="utf-8",
    )
    (mcp_dir / "comfyui.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "comfyui_graph": {
                        "transport": "http",
                        "url": "http://127.0.0.1:8188/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/packages/install",
        headers=_auth_headers(),
        json={"path": str(package)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["installed_plugin_id"] == "comfyui_plugin"
    assert payload["installed_skill_ids"] == ["comfyui_operator"]
    assert payload["installed_mcp_ids"] == ["comfyui_graph"]
    installed_skill = next(item for item in payload["skills"] if item["id"] == "comfyui_operator")
    installed_mcp = next(item for item in payload["mcp"] if item["id"] == "comfyui_graph")
    assert installed_skill["title"] == "ComfyUI Operator"
    assert installed_skill["description"] == "Operate ComfyUI workflows."
    assert installed_mcp["title"] == "comfyui_graph"
    assert (tmp_path / ".lucode" / "plugins" / "comfyui_plugin" / "lucode-plugin.json").exists()
    assert (tmp_path / ".lucode" / "skills" / "comfyui_operator" / "SKILL.md").exists()
    stored = json.loads((tmp_path / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert stored["mcpServers"]["comfyui_graph"]["url"] == "http://127.0.0.1:8188/mcp"
    state = json.loads((tmp_path / ".lucode" / "gui_plugin_state.json").read_text(encoding="utf-8"))
    assert state["installed_plugin_packages"][0]["id"] == "comfyui_plugin"
    assert state["installed_plugin_packages"][0]["launch_profiles"][0]["script"] == "run_nvidia_gpu.bat"


def test_plugin_package_install_endpoint_rejects_invalid_packages(tmp_path):
    invalid_package = tmp_path / "invalid_plugin"
    invalid_package.mkdir()
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/packages/install",
        headers=_auth_headers(),
        json={"path": str(invalid_package)},
    )

    assert response.status_code == 400
    assert "lucode-plugin.json" in response.json()["error"]["message"]


def test_comfyui_sample_plugin_package_is_installable(tmp_path):
    sample_package = Path(__file__).resolve().parents[1] / "plugins" / "comfyui"
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/packages/install",
        headers=_auth_headers(),
        json={"path": str(sample_package)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["installed_plugin_id"] == "comfyui"
    assert payload["installed_skill_ids"] == ["comfyui_operator"]
    assert payload["installed_mcp_ids"] == ["comfyui_graph"]


def test_plugin_external_mcp_endpoint_registers_stdio_config(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/mcp/external",
        headers=_auth_headers(),
        json={
            "id": "local_docs",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py", "--stdio"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["registered_mcp_id"] == "local_docs"
    installed = next(item for item in payload["mcp"] if item["id"] == "local_docs")
    assert installed["title"] == "local_docs"
    assert "stdio" in installed["detail"]
    stored = json.loads((tmp_path / ".lucode" / "mcp_servers.json").read_text(encoding="utf-8"))
    assert stored["mcpServers"]["local_docs"] == {
        "transport": "stdio",
        "command": "python",
        "args": ["server.py", "--stdio"],
    }


def test_plugin_external_mcp_endpoint_rejects_invalid_config(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/plugins/mcp/external",
        headers=_auth_headers(),
        json={
            "id": "bad_http",
            "transport": "http",
        },
    )

    assert response.status_code == 400
    assert "url is required" in response.json()["error"]["message"]


def test_comfyui_endpoint_returns_default_state_and_requires_auth(tmp_path):
    client = _client(tmp_path)

    unauthorized = client.get("/api/comfyui")
    response = client.get("/api/comfyui", headers=_auth_headers())

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "comfyui.v1",
        "base_url": "http://127.0.0.1:8188",
        "configured": False,
        "status": "unknown",
        "last_error": "",
        "checked_at": "",
        "endpoints": {},
        "installation": {
            "install_path": "",
            "resolved_root": "",
            "configured": False,
            "valid": False,
            "status": "unconfigured",
            "launch_mode": "",
            "launch_script": "",
            "launch_command": "",
            "available_launch_scripts": [],
            "validation_errors": [],
        },
    }


def test_comfyui_endpoint_saves_normalized_url(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/api/comfyui",
        headers=_auth_headers(),
        json={"base_url": "127.0.0.1:8188/"},
    )

    assert response.status_code == 200
    assert response.json()["base_url"] == "http://127.0.0.1:8188"
    assert response.json()["configured"] is True
    stored = json.loads((tmp_path / ".lucode" / "comfyui.json").read_text(encoding="utf-8"))
    assert stored == {"base_url": "http://127.0.0.1:8188"}


def test_comfyui_detect_accepts_portable_root_without_saving(tmp_path):
    portable = _make_comfyui_portable(tmp_path / "ComfyUI_windows_portable")
    client = _client(tmp_path)

    response = client.post(
        "/api/comfyui/detect",
        headers=_auth_headers(),
        json={"install_path": str(portable)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "schema_version": "comfyui_detection.v1",
        "install_path": str(portable),
        "resolved_root": str(portable.resolve()),
        "configured": False,
        "valid": True,
        "status": "launchable",
        "launch_mode": "nvidia",
        "launch_script": "run_nvidia_gpu.bat",
        "launch_command": ".\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build",
        "available_launch_scripts": ["run_nvidia_gpu.bat", "run_cpu.bat"],
        "validation_errors": [],
    }
    assert not (tmp_path / ".lucode" / "comfyui.json").exists()


def test_comfyui_detect_accepts_outer_wrapper_directory(tmp_path):
    wrapper = tmp_path / "ComfyUI_windows_portable_nvidia"
    portable = _make_comfyui_portable(wrapper / "ComfyUI_windows_portable")
    client = _client(tmp_path)

    response = client.post(
        "/api/comfyui/detect",
        headers=_auth_headers(),
        json={"install_path": str(wrapper)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["install_path"] == str(wrapper)
    assert payload["resolved_root"] == str(portable.resolve())
    assert payload["valid"] is True
    assert payload["status"] == "launchable"
    assert payload["launch_script"] == "run_nvidia_gpu.bat"


def test_comfyui_detect_reports_invalid_path_without_saving(tmp_path):
    invalid = tmp_path / "not_comfyui"
    invalid.mkdir()
    client = _client(tmp_path)

    response = client.post(
        "/api/comfyui/detect",
        headers=_auth_headers(),
        json={"install_path": str(invalid)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["install_path"] == str(invalid)
    assert payload["resolved_root"] == ""
    assert payload["valid"] is False
    assert payload["status"] == "invalid_path"
    assert any("ComfyUI/main.py" in item for item in payload["validation_errors"])
    assert not (tmp_path / ".lucode" / "comfyui.json").exists()


def test_comfyui_endpoint_saves_install_path_and_launch_script(tmp_path):
    wrapper = tmp_path / "ComfyUI_windows_portable_nvidia"
    portable = _make_comfyui_portable(wrapper / "ComfyUI_windows_portable")
    client = _client(tmp_path)

    response = client.put(
        "/api/comfyui",
        headers=_auth_headers(),
        json={
            "base_url": "127.0.0.1:8188",
            "install_path": str(wrapper),
            "launch_script": "run_cpu.bat",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_url"] == "http://127.0.0.1:8188"
    assert payload["configured"] is True
    assert payload["installation"] == {
        "install_path": str(wrapper),
        "resolved_root": str(portable.resolve()),
        "configured": True,
        "valid": True,
        "status": "launchable",
        "launch_mode": "cpu",
        "launch_script": "run_cpu.bat",
        "launch_command": ".\\python_embeded\\python.exe -s ComfyUI\\main.py --cpu --windows-standalone-build",
        "available_launch_scripts": ["run_nvidia_gpu.bat", "run_cpu.bat"],
        "validation_errors": [],
    }
    stored = json.loads((tmp_path / ".lucode" / "comfyui.json").read_text(encoding="utf-8"))
    assert stored == {
        "base_url": "http://127.0.0.1:8188",
        "install_path": str(wrapper),
        "resolved_root": str(portable.resolve()),
        "launch_script": "run_cpu.bat",
        "launch_mode": "cpu",
    }


def test_comfyui_endpoint_rejects_invalid_install_path_without_saving(tmp_path):
    invalid = tmp_path / "not_comfyui"
    invalid.mkdir()
    client = _client(tmp_path)

    response = client.put(
        "/api/comfyui",
        headers=_auth_headers(),
        json={
            "base_url": "127.0.0.1:8188",
            "install_path": str(invalid),
        },
    )

    assert response.status_code == 400
    assert "ComfyUI/main.py" in response.json()["error"]["message"]
    assert not (tmp_path / ".lucode" / "comfyui.json").exists()


def test_comfyui_endpoint_rejects_invalid_url(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/api/comfyui",
        headers=_auth_headers(),
        json={"base_url": "file:///tmp/comfyui"},
    )

    assert response.status_code == 400
    assert "http or https" in response.json()["error"]["message"]


def test_comfyui_check_reports_online_from_saved_url(tmp_path, monkeypatch):
    from runtime.server import comfyui as comfyui_module

    requested_urls = []

    def fake_get_json(url, *, timeout_seconds):
        requested_urls.append((url, timeout_seconds))
        return {}

    monkeypatch.setattr(comfyui_module, "_comfyui_http_get_json", fake_get_json)
    client = _client(tmp_path)
    client.put("/api/comfyui", headers=_auth_headers(), json={"base_url": "http://127.0.0.1:8188"})

    response = client.post("/api/comfyui/check", headers=_auth_headers(), json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "online"
    assert payload["configured"] is True
    assert payload["last_error"] == ""
    assert payload["checked_at"]
    assert payload["endpoints"] == {"system_stats": True, "queue": True}
    assert requested_urls == [
        ("http://127.0.0.1:8188/system_stats", 2.0),
        ("http://127.0.0.1:8188/queue", 2.0),
    ]


def test_comfyui_check_reports_offline_without_failing_request(tmp_path, monkeypatch):
    from runtime.server import comfyui as comfyui_module

    def fake_get_json(_url, *, timeout_seconds):
        raise RuntimeError(f"connection refused after {timeout_seconds}s")

    monkeypatch.setattr(comfyui_module, "_comfyui_http_get_json", fake_get_json)
    client = _client(tmp_path)

    response = client.post(
        "/api/comfyui/check",
        headers=_auth_headers(),
        json={"base_url": "http://127.0.0.1:8188/"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_url"] == "http://127.0.0.1:8188"
    assert payload["status"] == "offline"
    assert payload["configured"] is False
    assert payload["endpoints"] == {"system_stats": False, "queue": False}
    assert "system_stats" in payload["last_error"]
    assert "queue" in payload["last_error"]


def test_terminal_endpoint_runs_command_and_returns_transcript(tmp_path):
    client = _client(tmp_path)
    command = f'"{sys.executable}" --version'

    initial = client.get("/api/terminal", headers=_auth_headers())
    unauthorized = client.get("/api/terminal")
    started = client.post(
        "/api/terminal/run",
        headers=_auth_headers(),
        json={"command": command, "timeout_seconds": 10},
    )
    finished = _wait_terminal_idle(client)

    assert initial.status_code == 200
    assert initial.json()["schema_version"] == "terminal.v1"
    assert initial.json()["cwd"] == str(tmp_path.resolve())
    assert unauthorized.status_code == 401
    assert started.status_code == 200
    assert started.json()["started_command_id"]
    assert finished["running"] is False
    assert finished["last_result"]["status"] == "success"
    assert finished["last_result"]["returncode"] == 0
    assert "Python" in finished["last_result"]["stdout"]
    assert finished["history"][-1]["command"] == command
    assert finished["history"][-1]["status"] == "success"
    assert [entry["kind"] for entry in finished["transcript"]] == ["command", "stdout", "result"]
    assert finished["transcript"][0]["text"] == command


def test_terminal_endpoint_supports_cwd_clear_and_rerun(tmp_path):
    (tmp_path / "pkg").mkdir()
    client = _client(tmp_path)
    command = f'"{sys.executable}" --version'

    cwd_response = client.put("/api/terminal/cwd", headers=_auth_headers(), json={"cwd": "pkg"})
    run_response = client.post("/api/terminal/run", headers=_auth_headers(), json={"command": command})
    first_finished = _wait_terminal_idle(client)
    clear_response = client.post("/api/terminal/clear", headers=_auth_headers(), json={})
    rerun_response = client.post("/api/terminal/rerun", headers=_auth_headers(), json={})
    rerun_finished = _wait_terminal_idle(client)

    assert cwd_response.status_code == 200
    assert cwd_response.json()["cwd"] == str((tmp_path / "pkg").resolve())
    assert run_response.status_code == 200
    assert first_finished["history"][-1]["cwd"] == str((tmp_path / "pkg").resolve())
    assert clear_response.status_code == 200
    assert clear_response.json()["transcript"] == []
    assert clear_response.json()["history"][-1]["command"] == command
    assert rerun_response.status_code == 200
    assert rerun_response.json()["started_command_id"]
    assert rerun_finished["last_result"]["status"] == "success"
    assert len(rerun_finished["history"]) == 2
    assert rerun_finished["history"][-1]["command"] == command


def test_terminal_endpoint_rejects_empty_or_concurrent_commands(tmp_path):
    client = _client(tmp_path)

    empty = client.post("/api/terminal/run", headers=_auth_headers(), json={"command": ""})
    started = client.post(
        "/api/terminal/run",
        headers=_auth_headers(),
        json={"command": f'"{sys.executable}" --version'},
    )
    concurrent = client.post(
        "/api/terminal/run",
        headers=_auth_headers(),
        json={"command": f'"{sys.executable}" --version'},
    )

    assert empty.status_code == 400
    assert started.status_code == 200
    if concurrent.status_code != 409:
        _wait_terminal_idle(client)
    else:
        assert "already has a running command" in concurrent.json()["error"]["message"]


def test_sessions_are_persisted_and_listed_with_stable_short_titles(tmp_path):
    client = _client(tmp_path)
    long_title = "请你帮我检查这个项目的 Runtime Server 事件桥和 React 聊天壳是否能稳定恢复历史会话"

    create_response = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": long_title},
    )
    list_response = client.get("/api/sessions", headers=_auth_headers())

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["schema_version"] == "session.v1"
    assert created["title"] == "请你帮我检查这个项目的 Runtime Server 事件桥和 React 聊天壳是否能稳定恢复历史会话"
    assert len(created["display_title"]) <= 36
    assert created["session_id"]
    assert list_response.status_code == 200
    assert list_response.json()["sessions"][0]["session_id"] == created["session_id"]
    assert list_response.json()["sessions"][0]["title"] == long_title
    assert len(list_response.json()["sessions"][0]["display_title"]) <= 36

    reloaded_client = _client(tmp_path)
    reloaded = reloaded_client.get("/api/sessions", headers=_auth_headers()).json()["sessions"]

    assert reloaded[0]["session_id"] == created["session_id"]
    assert reloaded[0]["title"] == long_title


def test_first_run_renames_placeholder_session_and_survives_reload(tmp_path):
    async def runner(request):
        request.event_bus.emit("PlanningStarted", "planning started", payload={"route_type": "direct_answer"})
        return RunExecutionResult(final_output="已完成")

    client = _client(tmp_path, run_executor=runner)
    first_question = "请检查当前项目的真实 Chat MVP 是否已经能保存历史并恢复"
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "新会话"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": first_question},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    listed = client.get("/api/sessions", headers=_auth_headers()).json()["sessions"]
    reloaded = _client(tmp_path).get("/api/sessions", headers=_auth_headers()).json()["sessions"]

    assert events[-1]["type"] == "run.completed"
    assert listed[0]["session_id"] == session_id
    assert listed[0]["title"] == first_question
    assert len(listed[0]["display_title"]) <= 36
    assert reloaded[0]["session_id"] == session_id
    assert reloaded[0]["title"] == first_question


def test_first_run_keeps_custom_session_title(tmp_path):
    client = _client(tmp_path)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "桌面端验收会话"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "请回答一个短句"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    listed = client.get("/api/sessions", headers=_auth_headers()).json()["sessions"]

    assert events[-1]["type"] == "run.completed"
    assert listed[0]["session_id"] == session_id
    assert listed[0]["title"] == "桌面端验收会话"


def test_run_appends_user_and_assistant_messages_and_messages_endpoint_loads_them(tmp_path):
    async def runner(request):
        request.event_bus.emit("PlanningStarted", "planning started", payload={"route_type": "direct_answer"})
        return RunExecutionResult(final_output="这是最终回答", metadata={"turn_status": "完成"})

    client = _client(tmp_path, run_executor=runner)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "历史测试"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "记住这次问题"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    assert events[-1]["type"] == "run.completed"
    response = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "messages.v1"
    assert payload["session_id"] == session_id
    assert payload["messages"] == [
        {"role": "user", "content": "记住这次问题"},
        {"role": "assistant", "content": "这是最终回答"},
    ]


def test_new_empty_session_messages_endpoint_returns_empty_list(tmp_path):
    client = _client(tmp_path)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "New empty chat"},
    ).json()["session_id"]

    response = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "messages.v1",
        "session_id": session_id,
        "messages": [],
    }


def test_memory_only_session_messages_endpoint_returns_empty_list(tmp_path):
    class LaggingHistoryFacade:
        def __init__(self):
            self.history_store = self

        def start_session(self, title):
            return "session_memory_only"

        def append_event(self, session_id, event):
            return None

        def list_items(self, limit=100):
            return []

        def load_messages(self, session_id):
            return []

    manager = RuntimeRunManager(tmp_path, history_facade=LaggingHistoryFacade(), run_executor=_quick_stage_runner)
    manager.create_session("Memory only chat")

    assert manager.load_session_messages("session_memory_only") == {
        "schema_version": "messages.v1",
        "session_id": "session_memory_only",
        "messages": [],
    }


def test_delete_session_removes_history_and_blocks_message_load(tmp_path):
    client = _client(tmp_path)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "删除测试"},
    ).json()["session_id"]

    delete_response = client.delete(f"/api/sessions/{session_id}", headers=_auth_headers())
    list_response = client.get("/api/sessions", headers=_auth_headers())
    messages_response = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers())

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    assert session_id not in [item["session_id"] for item in list_response.json()["sessions"]]
    assert messages_response.status_code == 404


def test_run_executor_events_are_bridged_to_versioned_websocket_events(tmp_path):
    async def runner(request):
        assert request.workspace_root == tmp_path.resolve()
        request.event_bus.emit(
            "PlanningStarted",
            "planning started",
            agent="orchestrator",
            status="running",
            payload={"route_type": "multi_agent"},
        )
        request.event_bus.emit(
            "AgentMessageDelta",
            "hello",
            agent="worker-1",
            status="streaming",
            payload={"text": "hello"},
        )
        return RunExecutionResult(final_output="final answer", metadata={"turn_status": "完成"})

    client = _client(tmp_path, run_executor=runner)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]

    run_response = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    )

    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(4)]

    assert [event["type"] for event in events] == [
        "run.started",
        "planner.started",
        "worker.delta",
        "run.completed",
    ]
    assert events[0]["schema_version"] == "run_event.v1"
    assert events[0]["run_id"] == run_id
    assert events[0]["session_id"] == session_id
    assert events[0]["seq"] == 1
    assert events[0]["payload"]["input"] == "检查项目结构"
    assert events[1]["payload"]["source_event_type"] == "PlanningStarted"
    assert events[1]["payload"]["route_type"] == "multi_agent"
    assert events[2]["payload"]["text"] == "hello"
    assert events[3]["payload"]["final_output"] == "final answer"
    assert events[3]["payload"]["turn_status"] == "完成"


def test_run_approval_endpoint_resolves_pending_approval_request(tmp_path):
    async def runner(request):
        answer = await request.approval_session.request_approval("Approve browser click?")
        return RunExecutionResult(final_output=f"approval={answer}")

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    with TestClient(app) as client:
        session_id = client.post(
            "/api/sessions",
            headers=_auth_headers(),
            json={"title": "approval test"},
        ).json()["session_id"]
        run_id = client.post(
            "/api/runs",
            headers=_auth_headers(),
            json={"session_id": session_id, "input": "click the browser button"},
        ).json()["run_id"]

        with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
            started = websocket.receive_json()
            requested = websocket.receive_json()
            assert started["type"] == "run.started"
            assert requested["type"] == "approval.requested"
            approval_id = requested["payload"]["approval_id"]
            assert requested["payload"]["prompt"] == "Approve browser click?"

            response = client.post(
                f"/api/runs/{run_id}/approvals/{approval_id}",
                headers=_auth_headers(),
                json={"decision": "approve"},
            )

            assert response.status_code == 200, response.json()
            assert response.json()["decision"] == "approve"
            resolved = websocket.receive_json()
            completed = websocket.receive_json()
            assert resolved["type"] == "approval.resolved"
            assert resolved["payload"]["approval_id"] == approval_id
            assert resolved["payload"]["decision"] == "approve"
            assert completed["type"] == "run.completed"
            assert completed["payload"]["final_output"] == "approval=yes"


def test_run_approval_endpoint_can_reject_pending_approval_request(tmp_path):
    async def runner(request):
        answer = await request.approval_session.request_approval("Reject browser click?")
        return RunExecutionResult(final_output=f"approval={answer}")

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    with TestClient(app) as client:
        session_id = client.post(
            "/api/sessions",
            headers=_auth_headers(),
            json={"title": "approval reject test"},
        ).json()["session_id"]
        run_id = client.post(
            "/api/runs",
            headers=_auth_headers(),
            json={"session_id": session_id, "input": "reject the browser button"},
        ).json()["run_id"]

        with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
            websocket.receive_json()
            requested = websocket.receive_json()
            approval_id = requested["payload"]["approval_id"]

            response = client.post(
                f"/api/runs/{run_id}/approvals/{approval_id}",
                headers=_auth_headers(),
                json={"decision": "reject"},
            )

            assert response.status_code == 200, response.json()
            assert response.json()["decision"] == "reject"
            resolved = websocket.receive_json()
            completed = websocket.receive_json()
            assert resolved["type"] == "approval.resolved"
            assert resolved["payload"]["status"] == "rejected"
            assert completed["type"] == "run.completed"
            assert completed["payload"]["final_output"] == "approval=no"


def test_stop_run_cancels_running_executor_and_emits_cancel_event(tmp_path):
    cancelled_flag = {"value": False}

    async def runner(request):
        request.event_bus.emit("PlanningStarted", "planning started", status="running")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled_flag["value"] = True
            raise

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    client = TestClient(app)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]
    run_response = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    )
    run_id = run_response.json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        first = websocket.receive_json()
        second = websocket.receive_json()
        assert first["schema_version"] == "run_event.v1"
        assert first["run_id"] == run_id
        assert first["session_id"] == session_id
        assert first["seq"] == 1
        assert first["type"] == "run.started"
        assert first["payload"]["input"] == "检查项目结构"
        assert second["seq"] == 2
        assert second["type"] == "planner.started"

        stop_response = client.post(f"/api/runs/{run_id}/stop", headers=_auth_headers())
        assert stop_response.status_code == 200
        assert stop_response.json()["status"] == "cancelled"

        cancelled = websocket.receive_json()
        assert cancelled["seq"] == 3
        assert cancelled["type"] == "run.cancelled"
        assert cancelled["payload"]["reason"] == "user_requested"
    assert cancelled_flag["value"] is True


def test_run_executor_failure_is_reported_as_failed_event(tmp_path):
    async def runner(request):
        request.event_bus.emit("PlanningStarted", "planning started", status="running")
        raise RuntimeError("planner exploded")

    client = _client(tmp_path, run_executor=runner)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    assert [event["type"] for event in events] == ["run.started", "planner.started", "run.failed"]
    assert events[-1]["payload"]["error"] == "planner exploded"


def test_stop_completed_run_does_not_rewrite_status_or_emit_cancel_event(tmp_path):
    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=_quick_stage_runner)
    client = TestClient(app)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    assert events[-1]["type"] == "run.completed"
    stop_response = client.post(f"/api/runs/{run_id}/stop", headers=_auth_headers())

    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "completed"
    assert "run.cancelled" not in [
        event.type for event in app.state.lucode_run_manager.events.snapshot(run_id)
    ]


def test_kernel_agent_loop_executor_invokes_kernel_facade_and_reuses_event_bus(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    class FakeResponse:
        final_output = "kernel final"
        turn_status = "完成"
        stopped = False
        mcp_ids_used = ["project_filesystem_readonly"]
        output_already_printed = False

    class FakeKernelFacade:
        def __init__(self, context):
            seen["workspace_root"] = context.workspace_root

        async def run_once(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["show_plan"] = kwargs.get("show_plan")
            event_bus = kwargs.get("event_bus")
            seen["event_bus"] = event_bus
            event_bus.emit("PlanningStarted", "planning started", payload={"route_type": "direct_answer"})
            return FakeResponse()

    monkeypatch.setattr("runtime.kernel.KernelFacade", FakeKernelFacade)
    event_bus = ExecutionEventBus()
    request = RunExecutionRequest(
        run_id="run_test",
        session_id="session_test",
        user_input="hello",
        workspace_root=tmp_path.resolve(),
        event_bus=event_bus,
        cancel_requested=asyncio.Event(),
    )

    result = asyncio.run(KernelAgentLoopExecutor()(request))

    assert seen["workspace_root"] == tmp_path.resolve()
    assert seen["prompt"] == "hello"
    assert seen["show_plan"] is True
    assert seen["event_bus"] is event_bus
    assert result.final_output == "kernel final"
    assert result.metadata["turn_status"] == "完成"
    assert result.metadata["mcp_ids_used"] == ["project_filesystem_readonly"]
    assert event_bus.snapshot()[0].event_type == "PlanningStarted"


def test_websocket_subscription_is_removed_after_client_disconnect(tmp_path):
    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=_quick_stage_runner)
    client = TestClient(app)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    assert app.state.lucode_run_manager.events.subscriber_count(run_id) == 0


def test_websocket_rejects_invalid_runtime_token(tmp_path):
    client = _client(tmp_path)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "运行测试"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "检查项目结构"},
    ).json()["run_id"]

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/runs/{run_id}/events?token=wrong-token"):
            pass
