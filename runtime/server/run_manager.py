from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from catalog_system.model_probe import fetch_upstream_models
from catalog_system.model_catalog import clear_model_catalog_cache, load_model_catalog
from runtime.config.extensions import discover_mcp_layers
from runtime.events import ExecutionEventBus
from runtime.config.model_config import (
    connect_provider,
    iter_model_roles,
    load_auth,
    load_effective_lucode_config,
    load_lucode_config,
    load_provider_catalog,
    model_ids_from_refs,
    normalize_provider_id,
    normalize_model_role,
    provider_api_key_value,
    provider_has_api_key,
    remove_provider_config,
    select_role_model_priority,
    set_allowed_worker_models,
    set_privacy_mode,
    set_query_refiner_enabled,
)
from runtime.config.execution_mode import execution_mode_policy
from runtime.config.model_selection import model_runtime_available
from runtime.config.settings import RuntimeSettings
from runtime.safety.privacy import normalize_privacy_mode
from runtime.history.store import HistoryFacade
from runtime.server.execution_bridge import (
    KernelAgentLoopExecutor,
    RunExecutionRequest,
    RunExecutor,
    call_run_executor,
    emit_execution_event_as_run_event,
)
from runtime.server.approval_session import RuntimeApprovalSession
from runtime.server.comfyui import (
    check_comfyui_connection,
    comfyui_state,
    detect_comfyui_installation_payload,
    save_comfyui_settings,
)
from runtime.server.event_stream import RunEventStream
from runtime.server.schemas import (
    MODEL_LIST_SCHEMA_VERSION,
    RUNTIME_SERVER_SCHEMA_VERSION,
    SESSION_MESSAGES_SCHEMA_VERSION,
    SESSION_SCHEMA_VERSION,
    TERMINAL_SCHEMA_VERSION,
    ServerRun,
    ServerSession,
    utc_now_iso,
)
from runtime.terminal import CommandResult, TerminalHistoryEntry, TerminalSession, TerminalTranscriptEntry
from runtime.tools.registry import CORE_SERVER_METADATA
from lucode.gui.i18n import load_gui_language, save_gui_language


ModelCatalogProvider = Callable[[], dict[str, Any]]


class RuntimeRunManager:
    """Server facade for sessions, model metadata, and Agent Loop run events."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        model_catalog_provider: ModelCatalogProvider | None = None,
        history_facade: HistoryFacade | None = None,
        event_stream: RunEventStream | None = None,
        run_executor: RunExecutor | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._model_catalog_provider = model_catalog_provider or load_model_catalog
        self._history_facade = history_facade or HistoryFacade(self.workspace_root)
        self._run_executor = run_executor or KernelAgentLoopExecutor()
        self.events = event_stream or RunEventStream()
        self._sessions: dict[str, ServerSession] = {}
        self._runs: dict[str, ServerRun] = {}
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._run_cancel_events: dict[str, asyncio.Event] = {}
        self._approval_sessions: dict[str, RuntimeApprovalSession] = {}
        self._terminal_session = TerminalSession(workspace_root=self.workspace_root)

    def health(self) -> dict[str, Any]:
        bridge_url = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_URL") or "").strip()
        bridge_token = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN") or "").strip()
        return {
            "ok": True,
            "service": "lucode-runtime-server",
            "schema_version": RUNTIME_SERVER_SCHEMA_VERSION,
            "workspace_root": str(self.workspace_root),
            "desktop_browser_bridge": {
                "available": bool(bridge_url and bridge_token),
                "url_configured": bool(bridge_url),
                "token_configured": bool(bridge_token),
            },
        }

    def terminal_state(self) -> dict[str, Any]:
        return _terminal_state_payload(self._terminal_session)

    def terminal_set_cwd(self, cwd: str) -> dict[str, Any]:
        self._terminal_session.set_cwd(str(cwd or "").strip() or self.workspace_root)
        return self.terminal_state()

    def terminal_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("command") or "").strip()
        if not command:
            raise ValueError("command is required")
        command_id = self._terminal_session.start(
            command,
            reason=str(payload.get("reason") or "manual terminal command"),
            source="user",
            cwd=str(payload.get("cwd") or "").strip() or None,
            timeout_seconds=_terminal_timeout(payload.get("timeout_seconds")),
        )
        state = self.terminal_state()
        state["started_command_id"] = command_id
        return state

    def terminal_stop(self) -> dict[str, Any]:
        stopped = self._terminal_session.cancel()
        state = self.terminal_state()
        state["stop_requested"] = stopped
        return state

    def terminal_clear(self) -> dict[str, Any]:
        self._terminal_session.clear()
        return self.terminal_state()

    def terminal_rerun(self) -> dict[str, Any]:
        if not self._terminal_session.history:
            raise ValueError("terminal session has no command history to rerun")
        entry = self._terminal_session.history[-1]
        command_id = self._terminal_session.start(
            entry.command,
            reason=entry.reason,
            source=entry.source,
            cwd=entry.cwd,
        )
        state = self.terminal_state()
        state["started_command_id"] = command_id
        return state

    def list_models(self) -> dict[str, Any]:
        try:
            catalog = self._model_catalog_provider() or {}
        except Exception:
            catalog = {}
        models = [_sanitize_model(item) for item in catalog.get("models", []) if isinstance(item, dict)]
        return {
            "schema_version": MODEL_LIST_SCHEMA_VERSION,
            "models": [item for item in models if item["id"]],
        }

    def model_settings(self) -> dict[str, Any]:
        raw_models = self._catalog_models()
        models = [_sanitize_model_settings_item(item) for item in raw_models if item.get("id")]
        providers = _provider_summaries(models, workspace_root=self.workspace_root)
        roles = _role_summaries(models, workspace_root=self.workspace_root)
        preferences = _runtime_preference_summary(workspace_root=self.workspace_root)
        return {
            "schema_version": "model_settings.v1",
            "summary": {
                "model_count": len(models),
                "configured_model_count": sum(1 for item in models if item["configured"]),
                "provider_count": len(providers),
                "configured_provider_count": sum(1 for item in providers if item["configured"]),
            },
            "models": models,
            "providers": providers,
            "roles": roles,
            "runtime_preferences": preferences,
            "ui_preferences": {
                "language": load_gui_language(workspace_root=self.workspace_root),
            },
        }

    def update_model_role(self, *, role: str, model_id: str) -> dict[str, Any]:
        role_id = normalize_model_role(role)
        clean_model_id = str(model_id or "").strip()
        if not clean_model_id:
            raise ValueError("model_id is required")
        raw_models = self._catalog_models()
        model_info = next((item for item in raw_models if str(item.get("id") or "") == clean_model_id), None)
        if model_info is None:
            raise ValueError(f"unknown model_id: {clean_model_id}")
        if not model_info.get("configured"):
            raise ValueError(f"model is not configured: {clean_model_id}")
        model_ref = _model_ref_for_settings(model_info)
        if not model_ref:
            raise ValueError(f"model cannot be saved as a role reference: {clean_model_id}")
        select_role_model_priority(
            workspace_root=self.workspace_root,
            role=role_id,
            refs=[model_ref],
        )
        clear_model_catalog_cache()
        return self.model_settings()

    def update_query_refiner_enabled(self, enabled: bool) -> dict[str, Any]:
        set_query_refiner_enabled(bool(enabled), workspace_root=self.workspace_root)
        return self.model_settings()

    def update_privacy_mode(self, mode: str) -> dict[str, Any]:
        clean_mode = normalize_privacy_mode(mode)
        set_privacy_mode(clean_mode, workspace_root=self.workspace_root)
        return self.model_settings()

    def update_worker_pool(self, model_ids: list[str] | tuple[str, ...] | str | None) -> dict[str, Any]:
        configured_ids = {
            str(item.get("id") or "").strip()
            for item in self._catalog_models()
            if item.get("id") and item.get("configured")
        }
        selected: list[str] = []
        for raw_model_id in _string_list(model_ids):
            model_id = str(raw_model_id or "").strip()
            if not model_id or model_id in selected:
                continue
            if configured_ids and model_id not in configured_ids:
                continue
            selected.append(model_id)
        set_allowed_worker_models(selected, workspace_root=self.workspace_root)
        return self.model_settings()

    def update_language(self, language: str) -> dict[str, Any]:
        save_gui_language(language, workspace_root=self.workspace_root)
        return self.model_settings()

    def provider_catalog(self) -> dict[str, Any]:
        try:
            catalog = load_provider_catalog()
        except Exception:
            catalog = {}
        return {
            "schema_version": "provider_catalog.v1",
            "providers": [_sanitize_provider_catalog_item(provider_id, item) for provider_id, item in sorted(catalog.items())],
        }

    def fetch_provider_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = str(payload.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("base_url is required")
        backend_type = str(payload.get("compatible_type") or payload.get("backend_type") or "openai_compatible").strip()
        local = _payload_bool(payload.get("local"), backend_type == "ollama")
        api_key = str(payload.get("api_key") or "").strip()
        provider_id = str(payload.get("provider_id") or "").strip()
        if not api_key and provider_id:
            api_key = _existing_provider_api_key(normalize_provider_id(provider_id))
        if not local and backend_type != "ollama" and not api_key:
            raise ValueError("api_key is required")
        result = fetch_upstream_models(base_url, api_key, backend_type=backend_type)
        return {
            "schema_version": "provider_models.v1",
            "ok": bool(result.get("ok")),
            "models": _string_list(result.get("models") or []),
            "source": str(result.get("source") or "upstream"),
            "error": str(result.get("error") or ""),
        }

    def upsert_provider(self, payload: dict[str, Any], *, provider_id: str = "") -> dict[str, Any]:
        clean_provider_id = normalize_provider_id(str(provider_id or payload.get("provider_id") or ""))
        models = _string_list(payload.get("models") or payload.get("model_names"))
        if not models:
            raise ValueError("models is required")
        catalog = load_provider_catalog()
        preset = catalog.get(clean_provider_id) or {}
        local = _payload_bool(payload.get("local"), bool(preset.get("local", False)))
        custom = _payload_bool(payload.get("custom"), clean_provider_id not in catalog)
        api_key = str(payload.get("api_key") or "").strip()
        if not api_key and not local:
            api_key = _existing_provider_api_key(clean_provider_id)
        supports_tools_value = payload.get("supports_tools")
        supports_tools = supports_tools_value if isinstance(supports_tools_value, bool) else preset.get("supports_tools")
        connect_provider(
            clean_provider_id,
            api_key=api_key or None,
            workspace_root=self.workspace_root,
            homepage=str(payload.get("homepage") or preset.get("homepage") or "").strip() or None,
            base_url=str(payload.get("base_url") or preset.get("base_url") or "").strip() or None,
            models=models,
            display_name=str(payload.get("display_name") or preset.get("display_name") or clean_provider_id).strip(),
            compatible_type=str(
                payload.get("compatible_type")
                or payload.get("backend_type")
                or preset.get("compatible_type")
                or "openai_compatible"
            ).strip(),
            local=local,
            supports_tools=supports_tools,
            custom=custom,
        )
        clear_model_catalog_cache()
        result = self.model_settings()
        result["saved_provider_id"] = clean_provider_id
        return result

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        clean_provider_id = normalize_provider_id(str(provider_id or ""))
        remove_provider_config(
            clean_provider_id,
            workspace_root=self.workspace_root,
            remove_auth=True,
        )
        clear_model_catalog_cache()
        result = self.model_settings()
        result["deleted_provider_id"] = clean_provider_id
        return result

    def plugin_state(self) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore
        from lucode.gui.sidebar_data import load_default_mcp_rows, load_default_skill_cards

        store = PluginStateStore(self.workspace_root)
        removed_skill_ids = store.load_removed_skill_ids()
        skills = [
            _skill_card_to_dict(card)
            for card in [*load_default_skill_cards(), *store.load_custom_skill_cards()]
            if card.id not in removed_skill_ids
        ]
        mcp_rows = [_mcp_row_to_dict(row) for row in [*load_default_mcp_rows(), *store.load_custom_mcp_rows()]]
        return {
            "schema_version": "plugin_state.v1",
            "skills": skills,
            "mcp": mcp_rows,
            "runtime_capabilities": _runtime_capabilities_payload(self.workspace_root),
        }

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore
        from lucode.gui.sidebar_data import load_default_skill_cards

        clean_skill_id = str(skill_id or "").strip()
        if not clean_skill_id:
            raise ValueError("skill_id is required")
        store = PluginStateStore(self.workspace_root)
        cards = [*load_default_skill_cards(), *store.load_custom_skill_cards()]
        card = next((item for item in cards if item.id == clean_skill_id), None)
        if card is None:
            raise ValueError(f"unknown skill_id: {clean_skill_id}")
        if _is_core_skill_card(card):
            raise ValueError("core skills cannot be removed")
        store.mark_skill_removed(clean_skill_id)
        payload = self.plugin_state()
        payload["deleted_skill_id"] = clean_skill_id
        return payload

    def install_skill(self, source_path: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore

        clean_path = str(source_path or "").strip()
        if not clean_path:
            raise ValueError("path is required")
        store = PluginStateStore(self.workspace_root)
        card = store.install_skill_from_path(clean_path)
        payload = self.plugin_state()
        payload["installed_skill_id"] = card.id
        return payload

    def install_mcp(self, source_path: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore

        clean_path = str(source_path or "").strip()
        if not clean_path:
            raise ValueError("path is required")
        store = PluginStateStore(self.workspace_root)
        row = store.install_mcp_from_path(clean_path)
        payload = self.plugin_state()
        payload["installed_mcp_id"] = row.id
        return payload

    def register_external_mcp(self, payload: dict[str, Any]) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore

        store = PluginStateStore(self.workspace_root)
        row = store.register_external_mcp(dict(payload or {}))
        result = self.plugin_state()
        result["registered_mcp_id"] = row.id
        return result

    def comfyui_state(self) -> dict[str, Any]:
        return comfyui_state(self.workspace_root)

    def update_comfyui_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return save_comfyui_settings(self.workspace_root, dict(payload or {}))

    def detect_comfyui_installation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return detect_comfyui_installation_payload(dict(payload or {}))

    def check_comfyui(self, payload: dict[str, Any]) -> dict[str, Any]:
        return check_comfyui_connection(self.workspace_root, dict(payload or {}))

    def create_session(self, title: str = "") -> dict[str, Any]:
        clean_title = _clean_title(title)
        session_id = self._history_facade.history_store.start_session(clean_title)
        self._history_facade.history_store.append_event(
            session_id,
            {
                "type": "session_metadata",
                "title": clean_title,
            },
        )
        item = next((entry for entry in self._history_items() if entry.session_id == session_id), None)
        now = utc_now_iso()
        session = ServerSession(
            session_id=session_id,
            title=clean_title,
            created_at=item.created_at if item else now,
            updated_at=item.updated_at if item else now,
        )
        self._sessions[session.session_id] = session
        return session.to_dict()

    def list_sessions(self) -> dict[str, Any]:
        sessions = list(self._sessions.values())
        known = {item.session_id for item in sessions}
        for item in self._history_items():
            if item.session_id in known:
                continue
            known.add(item.session_id)
            title = self._stored_session_title(item.session_id) or item.title or item.session_id
            sessions.append(
                ServerSession(
                    session_id=item.session_id,
                    title=title,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "sessions": [item.to_dict() for item in sessions],
        }

    def load_session_messages(self, session_id: str) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id or (session_id not in self._sessions and not self._history_contains(session_id)):
            raise ValueError(f"unknown session_id: {session_id}")
        return {
            "schema_version": SESSION_MESSAGES_SCHEMA_VERSION,
            "session_id": session_id,
            "messages": self._history_facade.load_messages(session_id),
        }

    def delete_session(self, session_id: str) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if session_id not in self._sessions and not self._history_contains(session_id):
            raise ValueError(f"unknown session_id: {session_id}")
        result = self._history_facade.delete(session_id)
        self._sessions.pop(session_id, None)
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": result.session_id,
            "deleted": result.deleted,
            "title": result.title,
        }

    def start_run(self, *, session_id: str, user_input: str) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if session_id not in self._sessions and not self._history_contains(session_id):
            raise ValueError(f"unknown session_id: {session_id}")
        text = str(user_input or "").strip()
        if not text:
            raise ValueError("input is required")
        now = utc_now_iso()
        run = ServerRun(
            run_id=_new_id("run"),
            session_id=session_id,
            user_input=text,
            status="running",
            created_at=now,
            updated_at=now,
        )
        self._promote_placeholder_title_for_first_message(session_id, text, updated_at=now)
        self._history_facade.history_store.append_message(
            session_id,
            "user",
            text,
            metadata={"run_id": run.run_id},
        )
        self._runs[run.run_id] = run
        self._touch_memory_session(session_id, updated_at=now)
        cancel_requested = asyncio.Event()
        self._run_cancel_events[run.run_id] = cancel_requested
        self.events.emit(
            run_id=run.run_id,
            session_id=session_id,
            event_type="run.started",
            payload={"input": text},
        )
        self._run_tasks[run.run_id] = asyncio.create_task(self._execute_run(run, cancel_requested))
        return run.to_dict()

    def start_mock_run(self, *, session_id: str, user_input: str) -> dict[str, Any]:
        return self.start_run(session_id=session_id, user_input=user_input)

    def stop_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(str(run_id or "").strip())
        if run is None:
            raise ValueError(f"unknown run_id: {run_id}")
        if run.status in {"completed", "failed", "cancelled"}:
            return run.to_dict()
        approval_session = self._approval_sessions.get(run.run_id)
        if approval_session is not None:
            approval_session.cancel_pending("user_requested_stop")
        cancel_event = self._run_cancel_events.get(run.run_id)
        if cancel_event is not None:
            cancel_event.set()
        task = self._run_tasks.get(run.run_id)
        if task is not None and not task.done():
            task.cancel()
        self._mark_cancelled(run, reason="user_requested")
        return run.to_dict()

    def has_run(self, run_id: str) -> bool:
        return str(run_id or "").strip() in self._runs

    def resolve_run_approval(self, run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        run = self._runs.get(clean_run_id)
        if run is None:
            raise ValueError(f"unknown run_id: {run_id}")
        approval_session = self._approval_sessions.get(clean_run_id)
        if approval_session is None:
            raise ValueError(f"run has no pending approval session: {run_id}")
        return approval_session.resolve(approval_id, decision)

    def _history_items(self):
        try:
            return self._history_facade.list_items(limit=100)
        except Exception:
            return []

    def _catalog_models(self) -> list[dict[str, Any]]:
        try:
            catalog = self._model_catalog_provider() or {}
        except Exception:
            catalog = {}
        return [item for item in catalog.get("models", []) if isinstance(item, dict)]

    def _history_contains(self, session_id: str) -> bool:
        return any(item.session_id == session_id for item in self._history_items())

    def _stored_session_title(self, session_id: str) -> str:
        try:
            summary = self._history_facade.history_store._summarize(
                self._history_facade.history_store._path_for(session_id)
            )
        except Exception:
            return ""
        return summary.title if summary is not None else ""

    def _promote_placeholder_title_for_first_message(self, session_id: str, user_input: str, *, updated_at: str) -> None:
        current_title = self._sessions.get(session_id).title if session_id in self._sessions else self._stored_session_title(session_id)
        if not _is_placeholder_title(current_title):
            return
        try:
            existing_messages = self._history_facade.load_messages(session_id)
        except Exception:
            existing_messages = []
        if existing_messages:
            return
        title = _clean_title(user_input)
        self._history_facade.history_store.append_event(
            session_id,
            {
                "type": "session_metadata",
                "title": title,
            },
        )
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions[session_id] = ServerSession(
                session_id=session.session_id,
                title=title,
                created_at=session.created_at,
                updated_at=updated_at,
            )

    async def _execute_run(self, run: ServerRun, cancel_requested: asyncio.Event) -> None:
        event_bus = ExecutionEventBus()
        unsubscribe = event_bus.subscribe(
            lambda event: emit_execution_event_as_run_event(
                run_events=self.events,
                run_id=run.run_id,
                session_id=run.session_id,
                event=event,
            )
        )
        approval_session = RuntimeApprovalSession(
            run_id=run.run_id,
            session_id=run.session_id,
            run_events=self.events,
        )
        self._approval_sessions[run.run_id] = approval_session
        request = RunExecutionRequest(
            run_id=run.run_id,
            session_id=run.session_id,
            user_input=run.user_input,
            workspace_root=self.workspace_root,
            event_bus=event_bus,
            cancel_requested=cancel_requested,
            approval_session=approval_session,
        )
        try:
            result = await call_run_executor(self._run_executor, request)
        except asyncio.CancelledError:
            self._mark_cancelled(run, reason="user_requested")
            raise
        except Exception as exc:
            self._mark_failed(run, error=str(exc))
        else:
            if run.status != "cancelled":
                self._mark_completed(run, final_output=result.final_output, metadata=result.metadata)
        finally:
            approval_session.cancel_pending("run_finished")
            unsubscribe()
            self._run_tasks.pop(run.run_id, None)
            self._run_cancel_events.pop(run.run_id, None)
            self._approval_sessions.pop(run.run_id, None)

    def _mark_completed(self, run: ServerRun, *, final_output: str, metadata: dict[str, Any]) -> None:
        now = utc_now_iso()
        run.status = "completed"
        run.updated_at = now
        payload = {"final_output": str(final_output or "")}
        payload.update(dict(metadata or {}))
        self._history_facade.history_store.append_message(
            run.session_id,
            "assistant",
            str(final_output or ""),
            metadata={"run_id": run.run_id, **dict(metadata or {})},
        )
        self._touch_memory_session(run.session_id, updated_at=now)
        self.events.emit(
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="run.completed",
            payload=payload,
        )

    def _mark_failed(self, run: ServerRun, *, error: str) -> None:
        now = utc_now_iso()
        run.status = "failed"
        run.updated_at = now
        self._touch_memory_session(run.session_id, updated_at=now)
        self.events.emit(
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="run.failed",
            payload={"error": str(error or "")},
        )

    def _mark_cancelled(self, run: ServerRun, *, reason: str) -> None:
        if run.status == "cancelled":
            return
        now = utc_now_iso()
        run.status = "cancelled"
        run.updated_at = now
        self._touch_memory_session(run.session_id, updated_at=now)
        self.events.emit(
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="run.cancelled",
            payload={"reason": str(reason or "user_requested")},
        )

    def _touch_memory_session(self, session_id: str, *, updated_at: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        self._sessions[session_id] = ServerSession(
            session_id=session.session_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=updated_at,
        )


def _terminal_state_payload(session: TerminalSession) -> dict[str, Any]:
    return {
        "schema_version": TERMINAL_SCHEMA_VERSION,
        "workspace_root": str(session.workspace_root),
        "cwd": str(session.current_cwd),
        "running": session.is_running,
        "running_command_id": session.running_command_id,
        "running_command": session.running_command,
        "last_result": _terminal_result_payload(session.last_result),
        "history": [_terminal_history_payload(entry) for entry in session.history[-50:]],
        "transcript": [_terminal_transcript_payload(entry) for entry in session.transcript[-200:]],
    }


def _terminal_history_payload(entry: TerminalHistoryEntry) -> dict[str, Any]:
    return {
        "command": entry.command,
        "cwd": str(entry.cwd),
        "reason": entry.reason,
        "source": entry.source,
        "status": entry.status.value,
        "returncode": entry.returncode,
        "duration_ms": entry.duration_ms,
    }


def _terminal_transcript_payload(entry: TerminalTranscriptEntry) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "command_id": entry.command_id,
        "text": entry.text,
        "result": _terminal_result_summary(entry.result),
    }


def _terminal_result_summary(result: CommandResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status.value,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
    }


def _terminal_result_payload(result: CommandResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "command": result.command,
        "cwd": str(result.cwd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": result.status.value,
        "decision": _terminal_decision_payload(result),
        "started_at": result.started_at.isoformat(timespec="seconds"),
        "ended_at": result.ended_at.isoformat(timespec="seconds"),
        "duration_ms": result.duration_ms,
        "source": result.source,
        "reason": result.reason,
    }


def _terminal_decision_payload(result: CommandResult) -> dict[str, Any]:
    decision = result.decision
    return {
        "decision": decision.decision,
        "risk_level": decision.risk_level,
        "reason": decision.reason,
        "permission_decision": decision.permission_decision,
        "permission_reason": decision.permission_reason,
        "findings": [
            {
                "severity": getattr(finding, "severity", ""),
                "category": getattr(finding, "category", ""),
                "message": getattr(finding, "message", ""),
                "evidence": getattr(finding, "evidence", ""),
                "blocks_execution": bool(getattr(finding, "blocks_execution", False)),
            }
            for finding in decision.findings
        ],
    }


def _terminal_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 60
    return max(1, min(timeout, 300))


def _sanitize_model(item: dict[str, Any]) -> dict[str, Any]:
    model_id = str(item.get("id") or item.get("model") or "").strip()
    return {
        "id": model_id,
        "display_name": str(item.get("display_name") or item.get("name") or model_id).strip(),
        "provider": str(item.get("provider") or _provider_from_model_id(model_id)).strip(),
        "configured": bool(item.get("configured")),
    }


def _sanitize_model_settings_item(item: dict[str, Any]) -> dict[str, Any]:
    model_id = str(item.get("id") or item.get("model") or "").strip()
    return {
        "id": model_id,
        "ref": _model_ref_for_settings(item),
        "display_name": str(
            item.get("display_name") or item.get("display_name_zh") or item.get("name") or model_id
        ).strip(),
        "provider": str(item.get("provider") or _provider_from_model_id(model_id)).strip(),
        "configured": bool(item.get("configured")),
        "available": bool(item.get("configured")) and model_runtime_available(item),
        "backend_type": str(item.get("backend_type") or "").strip(),
        "model_name": str(item.get("model_name") or "").strip(),
        "privacy_level": str(item.get("privacy_level") or "").strip(),
        "supports_tools": bool(item.get("supports_tools")),
        "reasoning_level": str(item.get("reasoning_level") or "").strip(),
        "cost_level": str(item.get("cost_level") or "").strip(),
        "model_tier": str(item.get("model_tier") or "").strip(),
    }


def _sanitize_provider_catalog_item(provider_id: str, item: dict[str, Any]) -> dict[str, Any]:
    clean_provider_id = normalize_provider_id(str(provider_id or item.get("id") or ""))
    local = bool(item.get("local"))
    supports_tools = item.get("supports_tools")
    return {
        "provider": clean_provider_id,
        "display_name": str(item.get("display_name") or clean_provider_id).strip(),
        "homepage": str(item.get("homepage") or "").strip(),
        "base_url": str(item.get("base_url") or "").strip(),
        "compatible_type": str(item.get("compatible_type") or item.get("backend_type") or "openai_compatible").strip(),
        "models": _string_list(item.get("models") or []),
        "local": local,
        "supports_tools": supports_tools,
        "custom": bool(item.get("custom") or clean_provider_id == "custom_openai_compatible"),
        "reasoning_level": str(item.get("reasoning_level") or "").strip(),
        "cost_level": str(item.get("cost_level") or "").strip(),
    }


def _provider_summaries(models: list[dict[str, Any]], *, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for model in models:
        provider_id = str(model.get("provider") or "").strip() or "unknown"
        item = providers.setdefault(
            provider_id,
            {
                "provider": provider_id,
                "display_name": provider_id,
                "model_count": 0,
                "configured_model_count": 0,
                "configured": False,
            },
        )
        item["model_count"] += 1
        if model.get("configured"):
            item["configured_model_count"] += 1
            item["configured"] = True
    if workspace_root is not None:
        _merge_project_provider_summaries(providers, workspace_root=workspace_root)
    return [providers[key] for key in sorted(providers)]


def _merge_project_provider_summaries(providers: dict[str, dict[str, Any]], *, workspace_root: Path) -> None:
    try:
        config = load_lucode_config(workspace_root=workspace_root)
    except Exception:
        config = {}
    try:
        catalog = load_provider_catalog()
    except Exception:
        catalog = {}
    raw_providers = config.get("provider") if isinstance(config, dict) else {}
    if not isinstance(raw_providers, dict):
        return
    for raw_provider_id, raw_config in sorted(raw_providers.items()):
        if not isinstance(raw_config, dict):
            continue
        try:
            provider_id = normalize_provider_id(str(raw_provider_id))
        except ValueError:
            continue
        preset = catalog.get(provider_id) or {}
        provider_models = _string_list(raw_config.get("models") or [])
        item = providers.setdefault(
            provider_id,
            {
                "provider": provider_id,
                "display_name": provider_id,
                "model_count": 0,
                "configured_model_count": 0,
                "configured": False,
            },
        )
        if provider_models:
            item["model_count"] = max(int(item.get("model_count") or 0), len(provider_models))
            item["configured_model_count"] = max(int(item.get("configured_model_count") or 0), len(provider_models))
        item.update(
            {
                "display_name": str(
                    raw_config.get("display_name") or preset.get("display_name") or provider_id
                ).strip(),
                "models": provider_models,
                "homepage": str(raw_config.get("homepage") or preset.get("homepage") or "").strip(),
                "base_url": str(raw_config.get("base_url") or preset.get("base_url") or "").strip(),
                "compatible_type": str(
                    raw_config.get("compatible_type")
                    or raw_config.get("backend_type")
                    or preset.get("compatible_type")
                    or "openai_compatible"
                ).strip(),
                "local": bool(raw_config.get("local") or preset.get("local")),
                "supports_tools": raw_config.get("supports_tools", preset.get("supports_tools")),
                "custom": bool(raw_config.get("custom") or provider_id not in catalog),
                "key_configured": provider_has_api_key(provider_id),
                "key_hint": _provider_api_key_hint(provider_id),
                "configured": True,
            }
        )


def _role_summaries(models: list[dict[str, Any]], *, workspace_root: Path) -> list[dict[str, Any]]:
    model_ids = {str(item.get("id") or "") for item in models if item.get("id")}
    try:
        settings = RuntimeSettings.from_env(workspace_root=workspace_root)
    except Exception:
        settings = RuntimeSettings()
    explicit_roles: dict[str, Any] = {}
    try:
        config = load_effective_lucode_config(workspace_root=workspace_root)
        if isinstance(config.get("roles"), dict):
            explicit_roles = dict(config.get("roles") or {})
    except Exception:
        explicit_roles = {}
    roles: list[dict[str, Any]] = []
    for role_id, role_info in iter_model_roles():
        explicit_priority = [
            model_id for model_id in _model_ids_for_refs(explicit_roles.get(role_id) or [], models) if model_id in model_ids
        ]
        fallback_priority = [model_id for model_id in settings.model_priority_for(role_id) if model_id in model_ids]
        priority = explicit_priority or fallback_priority
        roles.append(
            {
                "role": role_id,
                "label": str(role_info.get("label") or role_id),
                "model_priority": priority,
                "selected_model_id": priority[0] if priority else "",
            }
        )
    return roles


def _runtime_preference_summary(*, workspace_root: Path) -> dict[str, Any]:
    try:
        settings = RuntimeSettings.from_env(workspace_root=workspace_root)
    except Exception:
        settings = RuntimeSettings()
    execution_mode = str(getattr(settings, "execution_mode", "") or "auto")
    return {
        "execution_mode": execution_mode,
        "privacy_mode": normalize_privacy_mode(str(getattr(settings, "privacy_mode", "") or "local_first")),
        "query_refiner_enabled": bool(getattr(settings, "query_refiner_enabled", False)),
        "allowed_worker_models": list(settings.worker_model_pool(None)),
        "worker_pool_available": bool(execution_mode_policy(execution_mode).parallel_enabled),
    }


def _provider_from_model_id(model_id: str) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    return ""


def _model_ref_for_settings(item: dict[str, Any]) -> str:
    provider_ref = str(item.get("provider_ref") or "").strip()
    if provider_ref:
        return provider_ref
    provider = str(item.get("provider") or "").strip()
    model_name = str(item.get("model_name") or item.get("model_name_value") or "").strip()
    if provider and model_name:
        return f"{provider}/{model_name}"
    return str(item.get("id") or item.get("model") or "").strip()


def _model_ids_for_refs(refs: Any, models: list[dict[str, Any]]) -> list[str]:
    ref_to_id = {
        str(item.get("ref") or "").strip(): str(item.get("id") or "").strip()
        for item in models
        if item.get("ref") and item.get("id")
    }
    resolved: list[str] = []
    for ref in _string_list(refs):
        model_id = ref_to_id.get(ref)
        if not model_id:
            inferred = model_ids_from_refs([ref])
            model_id = inferred[0] if inferred else ""
        if model_id and model_id not in resolved:
            resolved.append(model_id)
    return resolved


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _payload_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text not in {"0", "false", "no", "off", "disabled"}


def _existing_provider_api_key(provider_id: str) -> str:
    try:
        auth = load_auth()
    except Exception:
        return ""
    provider_auth = (auth.get("providers") or {}).get(provider_id) or {}
    return provider_api_key_value(provider_auth)


def _provider_api_key_hint(provider_id: str) -> str:
    api_key = _existing_provider_api_key(provider_id)
    if not api_key:
        return ""
    suffix = api_key[-4:] if len(api_key) >= 4 else api_key
    return f"****{suffix}"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean_title(title: Any) -> str:
    text = str(title or "").replace("\n", " ").strip()
    return text or "New chat"


def _is_placeholder_title(title: Any) -> bool:
    normalized = str(title or "").strip().casefold()
    return normalized in {"", "new chat", "untitled chat", "新会话", "未命名会话"}


def _runtime_capabilities_payload(workspace_root: Path) -> list[dict[str, Any]]:
    layers = discover_mcp_layers(SimpleNamespace(workspace_root=workspace_root))
    capabilities: list[dict[str, Any]] = []
    for item in layers.get("core") or []:
        if not bool(item.get("runtime_capability")):
            continue
        capabilities.append(_runtime_capability_to_dict(item))
    return capabilities


def _runtime_capability_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    capability_id = str(item.get("id") or "").strip()
    metadata = CORE_SERVER_METADATA.get(capability_id, {})
    summary_zh = str(item.get("summary_zh") or item.get("summary") or "").strip()
    summary = str(metadata.get("summary") or item.get("summary") or summary_zh).strip()
    return {
        "id": capability_id,
        "display_name": str(item.get("display_name_zh") or capability_id),
        "summary": summary,
        "summary_zh": summary_zh,
        "surface": str(item.get("runtime_surface") or "runtime"),
        "status_key": str(item.get("runtime_status_key") or "runtime_available"),
        "ability_keys": _runtime_capability_ability_keys(item),
        "risk_key": "approval_required" if bool(item.get("approval_required")) else "standard",
    }


def _runtime_capability_ability_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    tools = item.get("tools")
    if isinstance(tools, str):
        tool_names = [tools.strip()] if tools.strip() else []
    elif isinstance(tools, (list, tuple)):
        tool_names = [str(tool).strip() for tool in tools if str(tool).strip()]
    else:
        tool_names = []
    for tool_name in tool_names:
        key = _runtime_ability_key_for_tool(tool_name)
        if key and key not in keys:
            keys.append(key)
    return keys


def _runtime_ability_key_for_tool(tool_name: str) -> str:
    return {
        "browser_navigate": "navigate",
        "browser_get_page_summary": "page_summary",
        "browser_click_element": "controlled_click",
        "browser_set_input_value": "form_input",
        "browser_submit_form": "form_submit",
    }.get(str(tool_name or "").strip())


def _is_core_skill_card(card: Any) -> bool:
    return any(str(chip).strip() == "核心" for chip in getattr(card, "chips", ()) or ())


def _skill_card_to_dict(card: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(card, "id", "") or ""),
        "title": str(getattr(card, "title", "") or ""),
        "description": str(getattr(card, "description", "") or ""),
        "chips": [str(chip) for chip in getattr(card, "chips", ()) or ()],
        "core": _is_core_skill_card(card),
        "deletable": not _is_core_skill_card(card),
    }


def _mcp_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(row, "id", "") or ""),
        "title": str(getattr(row, "title", "") or ""),
        "status": str(getattr(row, "status", "") or ""),
        "detail": str(getattr(row, "detail", "") or ""),
    }
