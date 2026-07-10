from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
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


def test_run_execution_request_context_fields_are_optional(tmp_path):
    event_bus = ExecutionEventBus()
    request = RunExecutionRequest(
        run_id="run_test",
        session_id="session_test",
        user_input="hello",
        workspace_root=tmp_path.resolve(),
        event_bus=event_bus,
        cancel_requested=asyncio.Event(),
    )

    assert request.history_facade is None
    assert request.model_info == {}
    assert request.routing_input == ""


def test_runtime_run_manager_passes_context_observe_inputs_to_executor(tmp_path):
    captured = {}

    async def runner(request):
        captured["request"] = request
        return RunExecutionResult(final_output="ok")

    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "openai/gpt-5.5",
                    "name": "GPT-5.5",
                    "provider": "openai",
                    "configured": True,
                    "context_window_tokens": 128000,
                }
            ]
        },
        run_executor=runner,
    )
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "context observe"},
    ).json()["session_id"]
    run_response = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "你好"},
    )

    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    request = captured["request"]
    assert request.user_input == "你好"
    assert request.routing_input == "你好"
    assert request.history_facade is not None
    messages = request.history_facade.load_messages(session_id)
    assert {"role": "user", "content": "你好"} in [
        {"role": message["role"], "content": message["content"]}
        for message in messages
    ]
    assert request.model_info["id"] == "openai/gpt-5.5"
    assert request.model_info["context_window_tokens"] == 128000


def test_runtime_context_model_info_uses_smallest_runtime_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_USER_HOME", str(tmp_path / "home"))
    for name in (
        "AGENTS_QUERY_REFINER_MODEL_PRIORITY",
        "AGENTS_ORCHESTRATOR_MODEL_PRIORITY",
        "AGENTS_EXECUTOR_MODEL_PRIORITY",
        "AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY",
        "AGENTS_ALLOWED_WORKER_MODELS",
        "AGENTS_EXECUTION_MODE",
        "AGENTS_PRIVACY_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".lucode").mkdir()
    (tmp_path / ".lucode" / "config.toml").write_text(
        "\n".join(
            [
                'mode = "auto"',
                'allowed_worker_models = ["tiny_worker_model"]',
                "",
                "[roles]",
                'orchestrator = ["large_orchestrator_model"]',
                'executor = ["medium_executor_model"]',
                'final_synthesizer = ["large_final_model"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured = {}

    async def runner(request):
        captured["request"] = request
        return RunExecutionResult(final_output="ok")

    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "large_orchestrator_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 128000,
                },
                {
                    "id": "large_final_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 64000,
                },
                {
                    "id": "medium_executor_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 32000,
                    "supports_tools": True,
                },
                {
                    "id": "tiny_worker_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 8192,
                    "supports_tools": True,
                },
            ]
        },
        run_executor=runner,
    )
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "context budget model"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "hello"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    request = captured["request"]
    assert request.model_info["id"] == "tiny_worker_model"
    assert request.model_info["context_window_tokens"] == 8192


def test_runtime_context_model_info_omits_sensitive_model_fields(tmp_path):
    manager = RuntimeRunManager(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "secret_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 4096,
                    "api_key": "plain-secret",
                    "api_key_value": "resolved-secret",
                    "token": "token-secret",
                    "authorization": "bearer secret",
                }
            ]
        },
    )

    model_info = manager._context_model_info()

    assert model_info["id"] == "secret_model"
    assert model_info["context_window_tokens"] == 4096
    serialized = json.dumps(model_info, ensure_ascii=False)
    assert "plain-secret" not in serialized
    assert "resolved-secret" not in serialized
    assert "token-secret" not in serialized
    assert "bearer secret" not in serialized


def test_runtime_context_model_info_uses_all_candidates_when_compute_placement_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_COMPUTE_PLACEMENT", "enforce")
    monkeypatch.setenv("LUCODE_USER_HOME", str(tmp_path / "home"))
    (tmp_path / ".lucode").mkdir()
    (tmp_path / ".lucode" / "config.toml").write_text(
        "\n".join(
            [
                'mode = "auto"',
                "",
                "[roles]",
                'orchestrator = ["large_cloud_model"]',
                'executor = ["large_cloud_model"]',
                'final_synthesizer = ["large_cloud_model"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    manager = RuntimeRunManager(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "large_cloud_model",
                    "provider": "openai",
                    "configured": True,
                    "backend_type": "openai_compatible",
                    "context_window_tokens": 128000,
                },
                {
                    "id": "small_local_model",
                    "provider": "ollama",
                    "configured": True,
                    "backend_type": "ollama",
                    "is_local": True,
                    "context_window_tokens": 4096,
                    "probe": {"status": "ok"},
                },
            ]
        },
    )

    model_info = manager._context_model_info()

    assert model_info["id"] == "small_local_model"
    assert model_info["context_window_tokens"] == 4096


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
                        "approval_required": True,
                        "side_effects": "controls_external_private_service",
                        "risk_level": "high",
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


def test_plugin_state_returns_installed_plugins_and_package_delete_uninstalls_assets(tmp_path):
    package = tmp_path / "demo_plugin"
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
    client = _client(tmp_path)

    install_response = client.post(
        "/api/plugins/packages/install",
        headers=_auth_headers(),
        json={"path": str(package)},
    )
    listed_response = client.get("/api/plugins", headers=_auth_headers())
    delete_response = client.delete("/api/plugins/packages/demo_plugin", headers=_auth_headers())

    assert install_response.status_code == 200
    listed = listed_response.json()
    assert listed["installed_plugins"] == [
        {
            "id": "demo_plugin",
            "title": "Demo Plugin",
            "description": "Demo optional plugin.",
            "skill_ids": ["demo_operator"],
            "mcp_ids": ["demo_graph"],
            "launch_profiles": [],
            "deletable": True,
        }
    ]
    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["deleted_plugin_id"] == "demo_plugin"
    assert deleted["installed_plugins"] == []
    assert "demo_operator" not in [item["id"] for item in deleted["skills"]]
    assert "demo_graph" not in [item["id"] for item in deleted["mcp"]]


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


def test_sessions_support_cursor_pagination_beyond_one_hundred_items(tmp_path):
    client = _client(tmp_path)
    store = client.app.state.lucode_run_manager._history_facade.history_store
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(125):
        store.append_event(
            f"session-{index:03d}",
            {
                "type": "session_metadata",
                "title": f"Session {index:03d}",
                "timestamp": started_at.isoformat().replace("+00:00", "Z"),
            },
        )

    first = client.get("/api/sessions?limit=50", headers=_auth_headers())
    second = client.get(
        f"/api/sessions?limit=50&cursor={first.json()['next_cursor']}",
        headers=_auth_headers(),
    )
    third = client.get(
        f"/api/sessions?limit=50&cursor={second.json()['next_cursor']}",
        headers=_auth_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert len(first.json()["sessions"]) == 50
    assert len(second.json()["sessions"]) == 50
    assert len(third.json()["sessions"]) == 25
    assert first.json()["sessions"][0]["session_id"] == "session-124"
    assert third.json()["sessions"][-1]["session_id"] == "session-000"
    assert first.json()["has_more"] is True
    assert second.json()["has_more"] is True
    assert third.json()["has_more"] is False
    assert third.json()["next_cursor"] == ""

    oldest = client.get("/api/sessions/session-000/messages", headers=_auth_headers())
    assert oldest.status_code == 200
    assert oldest.json()["session_id"] == "session-000"


def test_session_cursor_stays_stable_when_newer_sessions_are_added(tmp_path):
    client = _client(tmp_path)
    store = client.app.state.lucode_run_manager._history_facade.history_store
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(80):
        store.append_event(
            f"stable-session-{index:03d}",
            {
                "type": "session_metadata",
                "title": f"Stable session {index:03d}",
                "timestamp": started_at.isoformat().replace("+00:00", "Z"),
            },
        )

    first = client.get("/api/sessions?limit=20", headers=_auth_headers()).json()
    first_ids = [item["session_id"] for item in first["sessions"]]
    store.append_event(
        "stable-session-new",
        {
            "type": "session_metadata",
            "title": "Newer session",
            "timestamp": (started_at + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
    )
    second = client.get(
        f"/api/sessions?limit=20&cursor={first['next_cursor']}",
        headers=_auth_headers(),
    ).json()
    second_ids = [item["session_id"] for item in second["sessions"]]

    assert not set(first_ids).intersection(second_ids)
    assert second_ids[0] == "stable-session-059"
    assert "stable-session-new" not in second_ids


def test_session_cursor_stays_stable_when_a_prior_page_session_is_deleted(tmp_path):
    client = _client(tmp_path)
    store = client.app.state.lucode_run_manager._history_facade.history_store
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(80):
        store.append_event(
            f"delete-stable-session-{index:03d}",
            {
                "type": "session_metadata",
                "title": f"Stable session {index:03d}",
                "timestamp": started_at.isoformat().replace("+00:00", "Z"),
            },
        )

    first = client.get("/api/sessions?limit=20", headers=_auth_headers()).json()
    first_ids = [item["session_id"] for item in first["sessions"]]
    deleted = client.delete(f"/api/sessions/{first_ids[0]}", headers=_auth_headers())
    second = client.get(
        f"/api/sessions?limit=20&cursor={first['next_cursor']}",
        headers=_auth_headers(),
    ).json()

    assert deleted.status_code == 200
    assert second["sessions"][0]["session_id"] == "delete-stable-session-059"


def test_session_search_uses_an_independent_cursor_from_normal_history(tmp_path):
    client = _client(tmp_path)
    store = client.app.state.lucode_run_manager._history_facade.history_store
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(80):
        marker = "matching" if index % 2 == 0 else "other"
        store.append_event(
            f"search-session-{index:03d}",
            {
                "type": "session_metadata",
                "title": f"{marker} session {index:03d}",
                "timestamp": (started_at + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
            },
        )

    normal_first = client.get("/api/sessions?limit=10", headers=_auth_headers()).json()
    search_first = client.get("/api/sessions?q=matching&limit=10", headers=_auth_headers()).json()
    normal_second = client.get(
        f"/api/sessions?limit=10&cursor={normal_first['next_cursor']}",
        headers=_auth_headers(),
    ).json()
    search_second = client.get(
        f"/api/sessions?q=matching&limit=10&cursor={search_first['next_cursor']}",
        headers=_auth_headers(),
    ).json()

    assert normal_first["sessions"][0]["session_id"] == "search-session-079"
    assert normal_second["sessions"][0]["session_id"] == "search-session-069"
    assert search_first["sessions"][0]["session_id"] == "search-session-078"
    assert search_second["sessions"][0]["session_id"] == "search-session-058"
    assert all("matching" in item["title"] for item in search_first["sessions"] + search_second["sessions"])


def test_sessions_search_query_matches_history_content_without_returning_unmatched_sessions(tmp_path):
    async def runner(request):
        if "invoice" in request.user_input:
            return RunExecutionResult(final_output="Use the refund ledger reconciliation checklist.")
        return RunExecutionResult(final_output="Unrelated planning note.")

    client = _client(tmp_path, run_executor=runner)
    matching_session = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "Accounting question"},
    ).json()["session_id"]
    other_session = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "Browser question"},
    ).json()["session_id"]

    matching_run = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": matching_session, "input": "invoice workflow"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{matching_run}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    other_run = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": other_session, "input": "browser workflow"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{other_run}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    response = client.get("/api/sessions?q=refund%20ledger", headers=_auth_headers())

    assert response.status_code == 200
    session_ids = [item["session_id"] for item in response.json()["sessions"]]
    assert session_ids == [matching_session]


def test_sessions_search_query_uses_sqlite_fts_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    monkeypatch.setenv("LUCODE_CONTEXT_FTS", "on")

    async def runner(request):
        if "check sqlite" in request.user_input:
            return RunExecutionResult(final_output="Vectorless FTS probe marker.")
        return RunExecutionResult(final_output="Unrelated sqlite answer.")

    client = _client(tmp_path, run_executor=runner)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "FTS search case"},
    ).json()["session_id"]
    unmatched_session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "Other search case"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "check sqlite search"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()
    unmatched_run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": unmatched_session_id, "input": "other sqlite search"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{unmatched_run_id}/events?token={TOKEN}") as websocket:
        websocket.receive_json()
        websocket.receive_json()

    response = client.get("/api/sessions?q=Vectorless%20probe", headers=_auth_headers())

    assert response.status_code == 200
    assert [item["session_id"] for item in response.json()["sessions"]] == [session_id]


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
    assert listed[0]["title"] == "检查 Chat MVP 历史保存恢复"
    assert len(listed[0]["display_title"]) <= 36
    assert reloaded[0]["session_id"] == session_id
    assert reloaded[0]["title"] == "检查 Chat MVP 历史保存恢复"


def test_first_run_smart_title_is_dual_written_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCODE_CONTEXT_SQLITE", "dual_write")
    client = _client(tmp_path)
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "New chat"},
    ).json()["session_id"]
    run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "直接进入p4吧，做好风险处理"},
    ).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events?token={TOKEN}") as websocket:
        events = [websocket.receive_json() for _ in range(3)]

    import sqlite3

    connection = sqlite3.connect(tmp_path / ".lucode" / "lucode.db")
    try:
        sqlite_title = connection.execute(
            "select title from sessions where session_id = ?",
            (session_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    listed = client.get("/api/sessions", headers=_auth_headers()).json()["sessions"]

    assert events[-1]["type"] == "run.completed"
    assert listed[0]["title"] == "P4 风险处理"
    assert sqlite_title == "P4 风险处理"


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
        request.event_bus.emit(
            "PlanningCompleted",
            "planning completed",
            payload={
                "route_type": "single_agent",
                "tasks": [
                    {
                        "id": "inspect_history",
                        "title": "Inspect history persistence",
                        "model": "worker",
                        "mcp": ["project_filesystem_readonly"],
                        "parallel_group": "1",
                    }
                ],
            },
        )
        request.event_bus.emit(
            "AgentMessageDelta",
            "reading history store",
            task_id="inspect_history",
            payload={"text": "Read runtime/sessions/store.py"},
        )
        request.event_bus.emit(
            "TaskCompleted",
            "history persistence inspected",
            task_id="inspect_history",
            payload={"title": "Inspect history persistence"},
        )
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
        events = [websocket.receive_json() for _ in range(5)]

    assert events[-1]["type"] == "run.completed"
    response = client.get(f"/api/sessions/{session_id}/messages", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "messages.v1"
    assert payload["session_id"] == session_id
    assert payload["messages"][0] == {"role": "user", "content": "记住这次问题"}
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][1]["content"] == "这是最终回答"
    metadata = payload["messages"][1]["metadata"]
    assert metadata["run_id"] == run_id
    assert metadata["turn_status"] == "完成"
    assert metadata["run_snapshot"]["schema_version"] == "run_snapshot.v1"
    assert metadata["run_snapshot"]["run_status"] == "completed"
    assert [event["type"] for event in metadata["run_snapshot"]["events"]] == [
        "run.started",
        "planner.completed",
        "worker.delta",
        "task.completed",
        "run.completed",
    ]
    assert metadata["run_snapshot"]["events"][2]["payload"]["text"] == "Read runtime/sessions/store.py"
    assert "final_output" not in metadata["run_snapshot"]["events"][-1]["payload"]
    assert metadata["run_snapshot"]["events"][-1]["payload"]["final_output_preview"] == "这是最终回答"


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


def test_runtime_rejects_second_active_run_for_same_session(tmp_path):
    async def runner(request):
        await asyncio.Event().wait()

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    with TestClient(app) as client:
        session_id = client.post(
            "/api/sessions",
            headers=_auth_headers(),
            json={"title": "single active run"},
        ).json()["session_id"]
        first = client.post(
            "/api/runs",
            headers=_auth_headers(),
            json={"session_id": session_id, "input": "first request"},
        )

        second = client.post(
            "/api/runs",
            headers=_auth_headers(),
            json={"session_id": session_id, "input": "second request"},
        )

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "run_conflict"
        client.post(f"/api/runs/{first.json()['run_id']}/stop", headers=_auth_headers())


def test_runtime_rejects_deleting_session_with_active_run(tmp_path):
    async def runner(request):
        await asyncio.Event().wait()

    app = create_app(workspace_root=tmp_path, runtime_token=TOKEN, run_executor=runner)
    with TestClient(app) as client:
        session_id = client.post(
            "/api/sessions",
            headers=_auth_headers(),
            json={"title": "protected active session"},
        ).json()["session_id"]
        run = client.post(
            "/api/runs",
            headers=_auth_headers(),
            json={"session_id": session_id, "input": "keep running"},
        )

        deleted = client.delete(f"/api/sessions/{session_id}", headers=_auth_headers())

        assert deleted.status_code == 409
        assert deleted.json()["error"]["code"] == "run_conflict"
        client.post(f"/api/runs/{run.json()['run_id']}/stop", headers=_auth_headers())


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


def test_kernel_agent_loop_executor_observes_context_ledger_without_replacing_prompt(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    class BrowserHistory:
        def load_messages(self, session_id, limit=80):
            return [
                {"role": "user", "content": "Use the embedded browser to open https://example.com"},
                {"role": "assistant", "content": "desktop_browser navigation completed"},
            ]

        def load_context_summary(self, session_id, max_chars=2400):
            return "Old summary says: use desktop_browser and click a form."

    class FakeResponse:
        final_output = "direct answer"
        turn_status = "completed"
        stopped = False
        mcp_ids_used = []
        output_already_printed = False

    class FakeKernelFacade:
        def __init__(self, context):
            pass

        async def run_once(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["routing_input"] = kwargs.get("routing_input")
            return FakeResponse()

    monkeypatch.delenv("LUCODE_CONTEXT_LEDGER", raising=False)
    monkeypatch.setattr("runtime.kernel.KernelFacade", FakeKernelFacade)
    request = RunExecutionRequest(
        run_id="run_test",
        session_id="session_test",
        user_input="你好",
        workspace_root=tmp_path.resolve(),
        event_bus=ExecutionEventBus(),
        cancel_requested=asyncio.Event(),
        history_facade=BrowserHistory(),
        model_info={"context_window_tokens": 10_000},
        routing_input="你好",
    )

    result = asyncio.run(KernelAgentLoopExecutor()(request))

    assert seen["prompt"] == "你好"
    assert seen["routing_input"] == "你好"
    assert result.final_output == "direct answer"
    assert result.metadata["context_ledger"]["applied"] is False
    assert result.metadata["context_ledger"]["mode"] in {"normal", "soft_limit", "hard_limit"}
    assert result.metadata["context_ledger"]["summary_chars"] > 0
    assert result.metadata["tool_dehydration"]["count"] == 0
    metadata_text = json.dumps(result.metadata, ensure_ascii=False)
    assert "desktop_browser" not in metadata_text
    assert "https://example.com" not in metadata_text


def test_kernel_agent_loop_executor_enforce_mode_uses_ledger_prompt_with_raw_routing_input(
    tmp_path, monkeypatch
):
    seen: dict[str, object] = {}

    class BrowserHistory:
        def load_messages(self, session_id, limit=80):
            return [
                {"role": "user", "content": "Use the embedded browser to open https://example.com"},
                {"role": "assistant", "content": "desktop_browser navigation completed"},
            ]

        def load_context_summary(self, session_id, max_chars=2400):
            return "Old summary says: use desktop_browser and click a form."

    class FakeResponse:
        final_output = "ledger answer"
        turn_status = "completed"
        stopped = False
        mcp_ids_used = []
        output_already_printed = False

    class FakeKernelFacade:
        def __init__(self, context):
            pass

        async def run_once(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["routing_input"] = kwargs.get("routing_input")
            return FakeResponse()

    monkeypatch.setenv("LUCODE_CONTEXT_LEDGER", "enforce")
    monkeypatch.setattr("runtime.kernel.KernelFacade", FakeKernelFacade)
    request = RunExecutionRequest(
        run_id="run_test",
        session_id="session_test",
        user_input="你好",
        workspace_root=tmp_path.resolve(),
        event_bus=ExecutionEventBus(),
        cancel_requested=asyncio.Event(),
        history_facade=BrowserHistory(),
        model_info={"context_window_tokens": 10_000},
        routing_input="你好",
    )

    result = asyncio.run(KernelAgentLoopExecutor()(request))

    assert seen["prompt"] != "你好"
    assert "history_background" in str(seen["prompt"])
    assert "Old summary says: use desktop_browser" in str(seen["prompt"])
    assert str(seen["prompt"]).rstrip().endswith("你好")
    assert seen["routing_input"] == "你好"
    assert result.final_output == "ledger answer"
    assert result.metadata["context_ledger"]["applied"] is True
    metadata_text = json.dumps(result.metadata, ensure_ascii=False)
    assert "https://example.com" not in metadata_text


def test_runtime_second_turn_enforce_uses_history_prompt_but_keeps_raw_routing_input(
    tmp_path, monkeypatch
):
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, final_output):
            self.final_output = final_output
            self.turn_status = "completed"
            self.stopped = False
            self.mcp_ids_used = []
            self.output_already_printed = False

    class FakeKernelFacade:
        def __init__(self, context):
            pass

        async def run_once(self, prompt, **kwargs):
            calls.append(
                {
                    "prompt": prompt,
                    "routing_input": kwargs.get("routing_input"),
                }
            )
            return FakeResponse(f"answer {len(calls)}")

    monkeypatch.setenv("LUCODE_CONTEXT_LEDGER", "enforce")
    monkeypatch.setattr("runtime.kernel.KernelFacade", FakeKernelFacade)
    client = _client(
        tmp_path,
        model_catalog_provider=lambda: {
            "models": [
                {
                    "id": "openai/gpt-5.5",
                    "name": "GPT-5.5",
                    "provider": "openai",
                    "configured": True,
                    "context_window_tokens": 10_000,
                }
            ]
        },
        run_executor=KernelAgentLoopExecutor(),
    )
    session_id = client.post(
        "/api/sessions",
        headers=_auth_headers(),
        json={"title": "context sequence"},
    ).json()["session_id"]

    first_run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "你好"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{first_run_id}/events?token={TOKEN}") as websocket:
        first_events = [websocket.receive_json() for _ in range(2)]

    second_run_id = client.post(
        "/api/runs",
        headers=_auth_headers(),
        json={"session_id": session_id, "input": "继续解释"},
    ).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{second_run_id}/events?token={TOKEN}") as websocket:
        second_events = [websocket.receive_json() for _ in range(2)]

    assert first_events[-1]["type"] == "run.completed"
    assert second_events[-1]["type"] == "run.completed"
    assert len(calls) == 2
    assert str(calls[0]["prompt"]).count(str(calls[0]["routing_input"])) == 1
    assert str(calls[1]["prompt"]).count(str(calls[1]["routing_input"])) == 1
    assert calls[0]["routing_input"] == "你好"
    assert calls[1]["routing_input"] == "继续解释"
    assert calls[1]["prompt"] != "继续解释"
    assert "history_background" in str(calls[1]["prompt"])
    assert "你好" in str(calls[1]["prompt"])
    assert "answer 1" in str(calls[1]["prompt"])
    assert str(calls[1]["prompt"]).rstrip().endswith("继续解释")
    assert second_events[-1]["payload"]["context_ledger"]["applied"] is True


def test_kernel_agent_loop_executor_context_observe_failure_keeps_original_prompt(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    class FailingHistory:
        def load_messages(self, session_id, limit=80):
            raise RuntimeError("history unavailable")

    class FakeResponse:
        final_output = "fallback answer"
        turn_status = "completed"
        stopped = False
        mcp_ids_used = []
        output_already_printed = False

    class FakeKernelFacade:
        def __init__(self, context):
            pass

        async def run_once(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["routing_input"] = kwargs.get("routing_input")
            return FakeResponse()

    monkeypatch.delenv("LUCODE_CONTEXT_LEDGER", raising=False)
    monkeypatch.setattr("runtime.kernel.KernelFacade", FakeKernelFacade)
    request = RunExecutionRequest(
        run_id="run_test",
        session_id="session_test",
        user_input="hello",
        workspace_root=tmp_path.resolve(),
        event_bus=ExecutionEventBus(),
        cancel_requested=asyncio.Event(),
        history_facade=FailingHistory(),
        model_info={"context_window_tokens": 10_000},
        routing_input="hello",
    )

    result = asyncio.run(KernelAgentLoopExecutor()(request))

    assert seen["prompt"] == "hello"
    assert seen["routing_input"] == "hello"
    assert result.final_output == "fallback answer"
    assert result.metadata["context_ledger"]["mode"] == "observe"
    assert "history unavailable" in result.metadata["context_ledger"]["error"]


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
