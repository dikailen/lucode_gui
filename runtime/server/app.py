from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from runtime.server.auth import RuntimeAuth, RuntimeAuthError
from runtime.server.execution_bridge import RunExecutor
from runtime.server.run_manager import ModelCatalogProvider, RuntimeRunManager


def create_app(
    *,
    workspace_root: Path | str | None = None,
    runtime_token: str | None = None,
    require_auth: bool = True,
    model_catalog_provider: ModelCatalogProvider | None = None,
    run_executor: RunExecutor | None = None,
    run_manager: RuntimeRunManager | None = None,
) -> Starlette:
    manager = run_manager or RuntimeRunManager(
        Path(workspace_root or ".").resolve(),
        model_catalog_provider=model_catalog_provider,
        run_executor=run_executor,
    )
    auth = RuntimeAuth.from_env(token=runtime_token, enabled=require_auth)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(manager.health())

    async def models(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        return JSONResponse(manager.list_models())

    async def model_settings(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        return JSONResponse(manager.model_settings())

    async def update_model_role(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(
                manager.update_model_role(
                    role=str(request.path_params.get("role") or ""),
                    model_id=str(payload.get("model_id") or ""),
                )
            )
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def update_query_refiner(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.update_query_refiner_enabled(bool(payload.get("enabled"))))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def update_privacy(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.update_privacy_mode(str(payload.get("mode") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def update_worker_pool(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            model_ids = payload.get("model_ids")
            if model_ids is None:
                model_ids = payload.get("allowed_worker_models")
            return JSONResponse(manager.update_worker_pool(model_ids))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def update_language(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.update_language(str(payload.get("language") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def create_provider(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.upsert_provider(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def provider_catalog(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.provider_catalog())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def fetch_provider_models(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.fetch_provider_models(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def update_provider(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(
                manager.upsert_provider(
                    payload,
                    provider_id=str(request.path_params.get("provider_id") or ""),
                )
            )
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def delete_provider(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.delete_provider(str(request.path_params.get("provider_id") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def plugin_state(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.plugin_state())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def delete_skill(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.delete_skill(str(request.path_params.get("skill_id") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def install_skill(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.install_skill(str(payload.get("path") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def install_mcp(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.install_mcp(str(payload.get("path") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def install_plugin_package(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.install_plugin_package(str(payload.get("path") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def register_external_mcp(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.register_external_mcp(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def comfyui_state(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.comfyui_state())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def update_comfyui_settings(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.update_comfyui_settings(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def detect_comfyui(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.detect_comfyui_installation(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def check_comfyui(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(await asyncio.to_thread(manager.check_comfyui, payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def terminal_state(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.terminal_state())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def terminal_run(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.terminal_run(payload))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except RuntimeError as exc:
            return _error("conflict", str(exc), status_code=409)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def terminal_stop(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.terminal_stop())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def terminal_clear(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.terminal_clear())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)

    async def terminal_rerun(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.terminal_rerun())
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except RuntimeError as exc:
            return _error("conflict", str(exc), status_code=409)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def terminal_cwd(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.terminal_set_cwd(str(payload.get("cwd") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)
        except (FileNotFoundError, NotADirectoryError) as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def list_sessions(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        return JSONResponse(manager.list_sessions())

    async def create_session(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(manager.create_session(title=str(payload.get("title") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def session_messages(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.load_session_messages(str(request.path_params.get("session_id") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("not_found", str(exc), status_code=404)

    async def delete_session(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.delete_session(str(request.path_params.get("session_id") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("not_found", str(exc), status_code=404)

    async def create_run(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            result = manager.start_run(
                session_id=str(payload.get("session_id") or ""),
                user_input=str(payload.get("input") or payload.get("user_input") or ""),
            )
            return JSONResponse(result)
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def stop_run(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            return JSONResponse(manager.stop_run(str(request.path_params.get("run_id") or "")))
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("not_found", str(exc), status_code=404)

    async def resolve_run_approval(request: Request) -> JSONResponse:
        try:
            auth.require_http(request)
            payload = await _json_body(request)
            return JSONResponse(
                manager.resolve_run_approval(
                    str(request.path_params.get("run_id") or ""),
                    str(request.path_params.get("approval_id") or ""),
                    str(payload.get("decision") or ""),
                )
            )
        except RuntimeAuthError:
            return _error("unauthorized", "invalid runtime token", status_code=401)
        except ValueError as exc:
            return _error("bad_request", str(exc), status_code=400)

    async def run_events(websocket: WebSocket) -> None:
        run_id = str(websocket.path_params.get("run_id") or "")
        if not auth.is_websocket_authorized(websocket):
            await websocket.close(code=1008)
            return
        if not manager.has_run(run_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        for event in manager.events.snapshot(run_id):
            await websocket.send_json(event.to_dict())
        try:
            with manager.events.subscribe(run_id) as queue:
                while True:
                    event = await _next_stream_event_or_disconnect(websocket, queue)
                    if event is None:
                        break
                    await websocket.send_json(event.to_dict())
        except (asyncio.CancelledError, WebSocketDisconnect):
            return

    app = Starlette(
        debug=False,
        routes=[
            Route("/api/health", health, methods=["GET"]),
            Route("/api/models", models, methods=["GET"]),
            Route("/api/settings/models", model_settings, methods=["GET"]),
            Route("/api/settings/models/roles/{role}", update_model_role, methods=["PUT"]),
            Route("/api/settings/query-refiner", update_query_refiner, methods=["PUT"]),
            Route("/api/settings/privacy", update_privacy, methods=["PUT"]),
            Route("/api/settings/worker-pool", update_worker_pool, methods=["PUT"]),
            Route("/api/settings/language", update_language, methods=["PUT"]),
            Route("/api/settings/providers/catalog", provider_catalog, methods=["GET"]),
            Route("/api/settings/providers/fetch-models", fetch_provider_models, methods=["POST"]),
            Route("/api/settings/providers", create_provider, methods=["POST"]),
            Route("/api/settings/providers/{provider_id}", update_provider, methods=["PUT"]),
            Route("/api/settings/providers/{provider_id}", delete_provider, methods=["DELETE"]),
            Route("/api/plugins", plugin_state, methods=["GET"]),
            Route("/api/plugins/skills/install", install_skill, methods=["POST"]),
            Route("/api/plugins/mcp/install", install_mcp, methods=["POST"]),
            Route("/api/plugins/packages/install", install_plugin_package, methods=["POST"]),
            Route("/api/plugins/mcp/external", register_external_mcp, methods=["POST"]),
            Route("/api/plugins/skills/{skill_id}", delete_skill, methods=["DELETE"]),
            Route("/api/comfyui", comfyui_state, methods=["GET"]),
            Route("/api/comfyui", update_comfyui_settings, methods=["PUT"]),
            Route("/api/comfyui/detect", detect_comfyui, methods=["POST"]),
            Route("/api/comfyui/check", check_comfyui, methods=["POST"]),
            Route("/api/terminal", terminal_state, methods=["GET"]),
            Route("/api/terminal/run", terminal_run, methods=["POST"]),
            Route("/api/terminal/stop", terminal_stop, methods=["POST"]),
            Route("/api/terminal/clear", terminal_clear, methods=["POST"]),
            Route("/api/terminal/rerun", terminal_rerun, methods=["POST"]),
            Route("/api/terminal/cwd", terminal_cwd, methods=["PUT"]),
            Route("/api/sessions", list_sessions, methods=["GET"]),
            Route("/api/sessions", create_session, methods=["POST"]),
            Route("/api/sessions/{session_id}/messages", session_messages, methods=["GET"]),
            Route("/api/sessions/{session_id}", delete_session, methods=["DELETE"]),
            Route("/api/runs", create_run, methods=["POST"]),
            Route("/api/runs/{run_id}/stop", stop_run, methods=["POST"]),
            Route("/api/runs/{run_id}/approvals/{approval_id}", resolve_run_approval, methods=["POST"]),
            WebSocketRoute("/api/runs/{run_id}/events", run_events),
        ],
    )
    app.state.lucode_run_manager = manager
    app.state.lucode_auth = auth
    return app


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _error(code: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
            }
        },
        status_code=status_code,
    )


async def _next_stream_event_or_disconnect(websocket: WebSocket, queue):
    while True:
        event_task = asyncio.create_task(queue.get())
        disconnect_task = asyncio.create_task(websocket.receive())
        done, pending = await asyncio.wait(
            {event_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if disconnect_task in done:
            try:
                message = disconnect_task.result()
            except WebSocketDisconnect:
                return None
            if isinstance(message, dict) and message.get("type") == "websocket.disconnect":
                return None
            if event_task in done:
                return event_task.result()
            continue
        return event_task.result()
