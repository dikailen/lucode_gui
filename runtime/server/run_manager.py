from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from catalog_system.model_probe import fetch_upstream_models, probe_reasoning_effort_capabilities
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
    model_refs_from_config,
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
from runtime.config.model_effort import reasoning_effort_levels, selected_reasoning_effort, set_reasoning_effort
from runtime.config.execution_mode import execution_mode_policy
from runtime.config.model_selection import model_runtime_available
from runtime.config.settings import RuntimeSettings
from runtime.consistency.timeline import reliability_flags_from_env
from runtime.context.token_counter import context_window_for_model
from runtime.recovery.config import run_recovery_settings_from_env
from runtime.recovery.checkpoint_codec import decode_pipeline_checkpoint
from runtime.recovery.coordinator import RecoveryClaim, RecoveryCoordinator
from runtime.recovery.journal import RecoveryLeaseUnavailableError, RunJournal
from runtime.recovery.models import RecoveryDecision, canonical_json
from runtime.recovery.tool_lifecycle import JournalToolLifecycleSink
from runtime.history.titles import smart_session_title
from runtime.safety.privacy import PrivacyPolicy, normalize_privacy_mode
from runtime.history.store import HistoryFacade
from runtime.server.execution_bridge import (
    KernelAgentLoopExecutor,
    RunExecutionRequest,
    RunExecutor,
    call_run_executor,
    emit_execution_event_as_run_event,
)
from runtime.server.approval_session import RuntimeApprovalSession
from runtime.server.attachments import remove_session_attachments, stage_run_attachments
from runtime.server.comfyui import (
    check_comfyui_connection,
    comfyui_state,
    detect_comfyui_installation_payload,
    save_comfyui_settings,
)
from runtime.server.event_stream import RunEventStream
from runtime.server.schemas import (
    MODEL_LIST_SCHEMA_VERSION,
    RUN_LIST_SCHEMA_VERSION,
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
RUN_SNAPSHOT_SCHEMA_VERSION = "run_snapshot.v1"
MAX_RUN_SNAPSHOT_EVENTS = 180
MAX_RUN_SNAPSHOT_TEXT_CHARS = 700


class RunConflictError(ValueError):
    """Raised when a session lifecycle action conflicts with an active run."""


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
        self._run_recovery_settings = run_recovery_settings_from_env()
        self._journal_degraded_runs: dict[str, str] = {}
        self._run_journal = None
        if self._run_recovery_settings.records_journal:
            try:
                self._run_journal = RunJournal(self.workspace_root)
            except (OSError, sqlite3.Error) as exc:
                self._journal_degraded_runs["journal_init"] = str(exc)
        self._recovery_decisions: list[RecoveryDecision] = []
        self._recovery_claims_by_run: dict[str, RecoveryClaim] = {}
        self._recovery_envelopes_by_run: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, ServerSession] = {}
        self._runs: dict[str, ServerRun] = {}
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._run_cancel_events: dict[str, asyncio.Event] = {}
        self._approval_sessions: dict[str, RuntimeApprovalSession] = {}
        self._active_run_ids_by_session: dict[str, str] = {}
        self._client_request_runs: dict[str, tuple[str, str, str]] = {}
        self._model_probe_overrides: dict[str, dict[str, Any]] = {}
        self._terminal_session = TerminalSession(workspace_root=self.workspace_root)
        if self._run_journal is not None and self._run_recovery_settings.allows_event_reconnect:
            try:
                self._recovery_decisions = RecoveryCoordinator(self._run_journal).scan_startup()
                if self._run_recovery_settings.allows_safe_resume:
                    self._repair_checkpointed_finals()
            except Exception as exc:
                self._journal_degraded_runs["startup_recovery"] = str(exc)

    def health(self) -> dict[str, Any]:
        bridge_url = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_URL") or "").strip()
        bridge_token = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN") or "").strip()
        return {
            "ok": True,
            "service": "lucode-runtime-server",
            "schema_version": RUNTIME_SERVER_SCHEMA_VERSION,
            "workspace_root": str(self.workspace_root),
            "run_recovery": {
                "mode": self._run_recovery_settings.mode,
                "journal_enabled": self._run_journal is not None,
                "degraded": bool(self._journal_degraded_runs),
                "degraded_run_count": len(self._journal_degraded_runs),
            },
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
        models = [_sanitize_model_settings_item(item, workspace_root=self.workspace_root) for item in raw_models if item.get("id")]
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

    def update_model_reasoning_effort(self, *, model_id: str, effort: str) -> dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        model_info = next((item for item in self._catalog_models() if str(item.get("id") or "") == clean_model_id), None)
        if model_info is None:
            raise ValueError(f"unknown model_id: {clean_model_id}")
        if not model_info.get("configured"):
            raise ValueError(f"model is not configured: {clean_model_id}")
        set_reasoning_effort(
            workspace_root=self.workspace_root,
            model_id=clean_model_id,
            effort=effort,
            model_info=model_info,
        )
        return self.model_settings()

    def probe_model_reasoning_effort(self, *, model_id: str) -> dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        model_info = next((item for item in self._catalog_models() if str(item.get("id") or "") == clean_model_id), None)
        if model_info is None:
            raise ValueError(f"unknown model_id: {clean_model_id}")
        if not model_info.get("configured"):
            raise ValueError(f"model is not configured: {clean_model_id}")
        result = probe_reasoning_effort_capabilities(self.workspace_root, model_info)
        self._model_probe_overrides[clean_model_id] = {
            "supports_reasoning_effort": bool(result.get("supports_reasoning_effort")),
            "reasoning_effort_levels": list(result.get("reasoning_effort_levels") or []),
            "reasoning_effort": dict(result.get("reasoning_effort") or {}),
        }
        clear_model_catalog_cache()
        state = self.model_settings()
        state["probed_model_id"] = clean_model_id
        return state

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
            "skill_library": _skill_library_payload(self.workspace_root),
            "skill_library_categories": _skill_library_categories_payload(),
            "mcp": mcp_rows,
            "installed_plugins": store.load_installed_plugin_packages(),
            "runtime_capabilities": _runtime_capabilities_payload(self.workspace_root),
        }

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore
        from lucode.gui.sidebar_data import load_default_skill_cards
        from runtime.skill_library.metadata_proposals import delete_skill_metadata_proposals

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
        try:
            delete_skill_metadata_proposals(self.workspace_root, skill_id=clean_skill_id)
        except Exception:
            # Metadata proposal cleanup is auxiliary and must not block removing the Skill itself.
            pass
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

    def apply_skill_metadata_tuning(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from runtime.skill_library.indexer import build_skill_index
        from runtime.skill_library.metadata_editor import apply_skill_metadata_tuning

        result = apply_skill_metadata_tuning(
            workspace_root=self.workspace_root,
            skill_id=skill_id,
            categories=_string_list(payload.get("categories")) if "categories" in payload else None,
            tags=_string_list(payload.get("tags")) if "tags" in payload else None,
            use_when=_string_list(payload.get("use_when")) if "use_when" in payload else None,
            do_not_use_when=_string_list(payload.get("do_not_use_when")) if "do_not_use_when" in payload else None,
            negative_queries=_string_list(payload.get("negative_queries")),
            distinguish_from={str(key): str(value) for key, value in dict(payload.get("distinguish_from") or {}).items()},
        )
        build_skill_index(SimpleNamespace(workspace_root=self.workspace_root), write=True)
        state = self.plugin_state()
        state["updated_skill_id"] = result["skill_id"]
        return state

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore
        from runtime.skill_library.indexer import build_skill_index

        clean_skill_id = str(skill_id or "").strip()
        if not clean_skill_id:
            raise ValueError("skill_id is required")
        context = SimpleNamespace(workspace_root=self.workspace_root)
        entries = build_skill_index(context, write=False)
        entry = next((item for item in entries if item.id == clean_skill_id), None)
        if entry is None:
            raise ValueError(f"unknown skill_id: {clean_skill_id}")
        if entry.source != "workspace" or entry.core:
            raise ValueError("only workspace non-core skills can be enabled or disabled")
        PluginStateStore(self.workspace_root).set_skill_enabled(entry.id, bool(enabled))
        build_skill_index(context, write=True)
        state = self.plugin_state()
        state["updated_skill_id"] = entry.id
        return state

    def reindex_skill_library(self) -> dict[str, Any]:
        from runtime.skill_library.indexer import build_skill_index

        build_skill_index(SimpleNamespace(workspace_root=self.workspace_root), write=True)
        state = self.plugin_state()
        state["reindexed_skill_library"] = True
        return state


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

    def install_plugin_package(self, source_path: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore

        clean_path = str(source_path or "").strip()
        if not clean_path:
            raise ValueError("path is required")
        store = PluginStateStore(self.workspace_root)
        installed = store.install_plugin_package_from_path(clean_path)
        payload = self.plugin_state()
        payload["installed_plugin_id"] = installed["id"]
        payload["installed_skill_ids"] = installed["skill_ids"]
        payload["installed_mcp_ids"] = installed["mcp_ids"]
        payload["installed_launch_profile_ids"] = installed["launch_profile_ids"]
        return payload

    def uninstall_plugin_package(self, plugin_id: str) -> dict[str, Any]:
        from lucode.gui.plugin_state import PluginStateStore
        from runtime.skill_library.metadata_proposals import delete_skill_metadata_proposals

        clean_plugin_id = str(plugin_id or "").strip()
        if not clean_plugin_id:
            raise ValueError("plugin_id is required")
        store = PluginStateStore(self.workspace_root)
        removed = store.uninstall_plugin_package(clean_plugin_id)
        for skill_id in list(removed.get("skill_ids") or []):
            try:
                delete_skill_metadata_proposals(self.workspace_root, skill_id=str(skill_id))
            except Exception:
                # Proposal cleanup must not block package removal.
                continue
        payload = self.plugin_state()
        payload["deleted_plugin_id"] = removed["id"]
        payload["deleted_skill_ids"] = removed["skill_ids"]
        payload["deleted_mcp_ids"] = removed["mcp_ids"]
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

    def list_sessions(self, query: str = "", *, limit: int = 50, cursor: str = "") -> dict[str, Any]:
        clean_query = str(query or "").strip()
        safe_limit = min(100, max(1, int(limit or 50)))
        if clean_query:
            history_items, next_cursor, has_more = self._history_facade.search_page(
                clean_query,
                limit=safe_limit,
                cursor=cursor,
            )
        else:
            history_items, next_cursor, has_more = self._history_facade.list_items_page(
                limit=safe_limit,
                cursor=cursor,
            )
        sessions: list[ServerSession] = []
        for item in history_items:
            title = self._stored_session_title(item.session_id) or item.title or item.session_id
            sessions.append(
                ServerSession(
                    session_id=item.session_id,
                    title=title,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        sessions.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "sessions": [item.to_dict() for item in sessions],
            "next_cursor": next_cursor,
            "has_more": has_more,
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
        active_run_id = self._active_run_ids_by_session.get(session_id)
        if active_run_id:
            raise RunConflictError(
                f"session already has an active run: {active_run_id}; stop it before deleting the session"
            )
        if session_id not in self._sessions and not self._history_contains(session_id):
            raise ValueError(f"unknown session_id: {session_id}")
        result = self._history_facade.delete(session_id)
        remove_session_attachments(self.workspace_root, session_id)
        self._sessions.pop(session_id, None)
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": result.session_id,
            "deleted": result.deleted,
            "title": result.title,
        }

    def start_run(
        self,
        *,
        session_id: str,
        user_input: str,
        attachments: Any = None,
        client_request_id: str = "",
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if session_id not in self._sessions and not self._history_contains(session_id):
            raise ValueError(f"unknown session_id: {session_id}")
        text = str(user_input or "").strip()
        if not text:
            raise ValueError("input is required")
        request_id = str(client_request_id or "").strip()
        input_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if request_id:
            existing = self._client_request_runs.get(request_id)
            if existing is None:
                existing = self._journal_request_run(request_id)
            if existing is not None:
                existing_run_id, existing_session_id, existing_input_hash = existing
                if existing_session_id == session_id and existing_input_hash == input_hash:
                    existing_run = self._runs.get(existing_run_id)
                    if existing_run is not None:
                        return existing_run.to_dict()
                raise RunConflictError("client_request_id was already used for a different request")
        active_run_id = self._active_run_ids_by_session.get(session_id)
        if active_run_id:
            raise RunConflictError(f"session already has an active run: {active_run_id}")
        now = utc_now_iso()
        run_id = _new_id("run")
        recovery_claim = None
        if self._run_recovery_settings.allows_safe_resume and self._run_journal is not None:
            try:
                recovery_claim = RecoveryCoordinator(self._run_journal).claim_for_message(
                    session_id=session_id,
                    owner_id=run_id,
                )
            except RecoveryLeaseUnavailableError as exc:
                raise RunConflictError(f"recovery lease is already held for run: {exc.run_id}") from exc
            except Exception as exc:
                self._journal_degraded_runs[f"recovery_claim:{session_id}"] = str(exc)
        try:
            staged = stage_run_attachments(
                workspace_root=self.workspace_root,
                session_id=session_id,
                run_id=run_id,
                requested=attachments,
            )
        except Exception:
            if recovery_claim is not None and self._run_journal is not None:
                RecoveryCoordinator(self._run_journal).release_claim(recovery_claim)
            raise
        public_attachments = staged.public_items()
        run = ServerRun(
            run_id=run_id,
            session_id=session_id,
            user_input=text,
            status="running",
            created_at=now,
            updated_at=now,
            attachments=public_attachments,
            inline_files=[dict(item) for item in staged.inline_files],
        )
        try:
            self._promote_placeholder_title_for_first_message(session_id, text, updated_at=now)
            self._history_facade.history_store.append_message(
                session_id,
                "user",
                text,
                metadata={
                    "run_id": run.run_id,
                    **({"attachments": public_attachments} if public_attachments else {}),
                },
            )
        except Exception:
            if recovery_claim is not None and self._run_journal is not None:
                RecoveryCoordinator(self._run_journal).release_claim(recovery_claim)
            raise
        self._runs[run.run_id] = run
        if recovery_claim is not None:
            self._recovery_claims_by_run[run.run_id] = recovery_claim
            self._recovery_envelopes_by_run[run.run_id] = recovery_claim.envelope.to_dict()
        if request_id:
            self._client_request_runs[request_id] = (run.run_id, session_id, input_hash)
        self._touch_memory_session(session_id, updated_at=now)
        cancel_requested = asyncio.Event()
        self._run_cancel_events[run.run_id] = cancel_requested
        self._observe_run_created(run)
        self._emit_run_event(
            run_id=run.run_id,
            session_id=session_id,
            event_type="run.started",
            payload={
                "input": text,
                "input_hash": input_hash,
                **({"attachments": public_attachments} if public_attachments else {}),
            },
        )
        try:
            self._run_tasks[run.run_id] = asyncio.create_task(self._execute_run(run, cancel_requested))
        except Exception:
            self._runs.pop(run.run_id, None)
            self._run_cancel_events.pop(run.run_id, None)
            self._recovery_claims_by_run.pop(run.run_id, None)
            self._recovery_envelopes_by_run.pop(run.run_id, None)
            if recovery_claim is not None and self._run_journal is not None:
                RecoveryCoordinator(self._run_journal).release_claim(recovery_claim)
            raise
        self._active_run_ids_by_session[session_id] = run.run_id
        return run.to_dict()

    def start_mock_run(
        self,
        *,
        session_id: str,
        user_input: str,
        attachments: Any = None,
        client_request_id: str = "",
    ) -> dict[str, Any]:
        return self.start_run(
            session_id=session_id,
            user_input=user_input,
            attachments=attachments,
            client_request_id=client_request_id,
        )

    def list_active_runs(self) -> dict[str, Any]:
        active_runs = [run for run in self._runs.values() if run.status == "running"]
        active_runs.sort(key=lambda run: (run.updated_at, run.run_id), reverse=True)
        return {
            "schema_version": RUN_LIST_SCHEMA_VERSION,
            "runs": [run.to_dict() for run in active_runs],
        }

    def list_recovery_runs(self) -> dict[str, Any]:
        return {
            "schema_version": "run_recovery.v1",
            "runs": [decision.to_dict() for decision in self._recovery_decisions],
        }

    def abandon_recovery_run(self, run_id: str) -> dict[str, Any]:
        journal = self._run_journal
        clean_run_id = str(run_id or "").strip()
        if journal is None or not clean_run_id or not journal.abandon_interrupted_run(clean_run_id):
            raise ValueError(f"interrupted recovery run not found: {clean_run_id}")
        self._recovery_decisions = [item for item in self._recovery_decisions if item.run_id != clean_run_id]
        return {"abandoned": True, "run_id": clean_run_id}

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

    def _history_search_items(self, query: str):
        try:
            search = getattr(self._history_facade, "search")
        except Exception:
            search = None
        if search is None:
            return []
        try:
            return search(query, limit=100)
        except Exception:
            return []

    def _catalog_models(self) -> list[dict[str, Any]]:
        try:
            catalog = self._model_catalog_provider() or {}
        except Exception:
            catalog = {}
        models = [dict(item) for item in catalog.get("models", []) if isinstance(item, dict)]
        for item in models:
            override = self._model_probe_overrides.get(str(item.get("id") or ""))
            if override:
                item.update(override)
        return models

    def _context_model_info(self) -> dict[str, Any]:
        models = self._catalog_models()
        if not models:
            return {}
        settings = _runtime_settings_for_workspace(self.workspace_root)
        policy = PrivacyPolicy(normalize_privacy_mode(str(getattr(settings, "privacy_mode", "") or "local_first")))
        usable_by_id = _context_usable_models_by_id(models, policy=policy)
        candidates = _context_budget_candidates(
            settings=settings,
            models=models,
            usable_by_id=usable_by_id,
            workspace_root=self.workspace_root,
            policy=policy,
        )
        if not candidates:
            candidates = list(usable_by_id.values())
        if not candidates:
            candidates = [item for item in models if item.get("configured")] or models
        selected = _smallest_context_window_model(candidates)
        return _sanitize_context_model_info(selected)

    def _history_contains(self, session_id: str) -> bool:
        try:
            return self._history_facade.contains(session_id)
        except Exception:
            return False

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
        title = smart_session_title(user_input)
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
        unsubscribe = event_bus.subscribe(lambda event: self._emit_execution_event(run, event))
        approval_session = RuntimeApprovalSession(
            run_id=run.run_id,
            session_id=run.session_id,
            run_events=self.events,
            event_observer=self._observe_run_event,
            attempt_id=f"attempt:{run.run_id}",
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
            history_facade=self._history_facade,
            model_info=self._context_model_info(),
            routing_input=run.user_input,
            current_input_persisted=True,
            attachments=tuple(dict(item) for item in run.attachments),
            inline_files=tuple(dict(item) for item in run.inline_files),
            recovery_envelope=dict(self._recovery_envelopes_by_run.get(run.run_id) or {}),
            checkpoint_sink=self._checkpoint_sink_for_run(run),
            tool_lifecycle_sink=self._tool_lifecycle_sink_for_run(run),
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
                self._settle_recovery_claim(
                    run.run_id,
                    outcome=str((result.metadata or {}).get("recovery_outcome") or ""),
                )
        finally:
            approval_session.cancel_pending("run_finished")
            unsubscribe()
            self._run_tasks.pop(run.run_id, None)
            self._run_cancel_events.pop(run.run_id, None)
            self._approval_sessions.pop(run.run_id, None)
            if run.status != "completed":
                self._release_recovery_claim(run.run_id)
            self._recovery_claims_by_run.pop(run.run_id, None)
            self._recovery_envelopes_by_run.pop(run.run_id, None)
            if self._active_run_ids_by_session.get(run.session_id) == run.run_id:
                self._active_run_ids_by_session.pop(run.session_id, None)

    def _mark_completed(self, run: ServerRun, *, final_output: str, metadata: dict[str, Any]) -> None:
        now = utc_now_iso()
        run.status = "completed"
        run.updated_at = now
        self._observe_run_status(run)
        payload = {"final_output": str(final_output or "")}
        payload.update(dict(metadata or {}))
        self._emit_run_event(
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="run.completed",
            payload=payload,
        )
        history_metadata = {"run_id": run.run_id, **dict(metadata or {})}
        run_snapshot = self._run_snapshot_for_history(run)
        if run_snapshot["events"]:
            history_metadata["run_snapshot"] = run_snapshot
        self._history_facade.history_store.append_message(
            run.session_id,
            "assistant",
            str(final_output or ""),
            metadata=history_metadata,
        )
        self._touch_memory_session(run.session_id, updated_at=now)

    def _checkpoint_sink_for_run(self, run: ServerRun):
        journal = self._run_journal
        if journal is None or not self._run_recovery_settings.allows_safe_resume:
            return None

        def write(kind: str, state: dict[str, Any], compatibility: dict[str, Any]):
            return journal.write_checkpoint(
                run_id=run.run_id,
                kind=str(kind or ""),
                state=dict(state or {}),
                compatibility=dict(compatibility or {}),
            )

        return write

    def _tool_lifecycle_sink_for_run(self, run: ServerRun):
        journal = self._run_journal
        if journal is None or not self._run_recovery_settings.allows_safe_resume:
            return None

        def report_error(exc: Exception) -> None:
            self._journal_degraded_runs[f"tool_lifecycle:{run.run_id}"] = str(exc)

        return JournalToolLifecycleSink(journal, run_id=run.run_id, on_error=report_error)

    def _complete_recovery_claim(self, run_id: str) -> None:
        claim = self._recovery_claims_by_run.get(str(run_id))
        if claim is None or self._run_journal is None:
            return
        try:
            RecoveryCoordinator(self._run_journal).complete_claim(claim)
            self._recovery_decisions = [item for item in self._recovery_decisions if item.run_id != claim.source_run_id]
        except Exception as exc:
            self._journal_degraded_runs[f"recovery:{claim.source_run_id}"] = str(exc)

    def _settle_recovery_claim(self, run_id: str, *, outcome: str) -> None:
        claim = self._recovery_claims_by_run.get(str(run_id))
        if claim is None or self._run_journal is None:
            return
        coordinator = RecoveryCoordinator(self._run_journal)
        normalized = str(outcome or "").strip().lower()
        try:
            if normalized == "blocked":
                coordinator.block_claim(claim)
                return
            if normalized in {"completed", "superseded", "replan", "ignore_previous"}:
                coordinator.complete_claim(claim)
                self._recovery_decisions = [
                    item for item in self._recovery_decisions if item.run_id != claim.source_run_id
                ]
                return
            coordinator.release_claim(claim)
        except Exception as exc:
            self._journal_degraded_runs[f"recovery:{claim.source_run_id}"] = str(exc)

    def _release_recovery_claim(self, run_id: str) -> None:
        claim = self._recovery_claims_by_run.get(str(run_id))
        if claim is None or self._run_journal is None:
            return
        try:
            RecoveryCoordinator(self._run_journal).release_claim(claim)
        except Exception as exc:
            self._journal_degraded_runs[f"recovery:{claim.source_run_id}"] = str(exc)

    def _append_checkpointed_final_once(self, run_id: str) -> bool:
        journal = self._run_journal
        if journal is None:
            return False
        checkpoint = journal.latest_checkpoint_for_run(str(run_id), kinds=("final.ready",))
        run_record = journal.recovery_run(str(run_id))
        if checkpoint is None or run_record is None:
            return False
        if str(run_record.get("status") or "") not in {"running", "completed"}:
            return False
        state = decode_pipeline_checkpoint(checkpoint.state)
        final_output = str(state.get("final_output") or "")
        if not final_output:
            return False
        session_id = str(run_record.get("session_id") or "")
        if self._history_has_assistant_run(session_id, str(run_id)):
            journal.mark_checkpointed_run_completed(str(run_id))
            return False
        self._history_facade.history_store.append_message(
            session_id,
            "assistant",
            final_output,
            metadata={
                "run_id": str(run_id),
                "recovered_from_checkpoint": checkpoint.checkpoint_id,
            },
        )
        journal.mark_checkpointed_run_completed(str(run_id))
        return True

    def _repair_checkpointed_finals(self) -> None:
        journal = self._run_journal
        if journal is None:
            return
        run_ids = [decision.run_id for decision in self._recovery_decisions]
        for run_id in journal.final_checkpoint_run_ids():
            if run_id not in run_ids:
                run_ids.append(run_id)
        for run_id in run_ids:
            self._append_checkpointed_final_once(run_id)
        self._recovery_decisions = [
            decision
            for decision in self._recovery_decisions
            if str((journal.recovery_run(decision.run_id) or {}).get("status") or "") != "completed"
        ]

    def _history_has_assistant_run(self, session_id: str, run_id: str) -> bool:
        try:
            events = self._history_facade.history_store.load_events(session_id)
        except Exception:
            return False
        return any(
            str(item.get("type") or "") == "message"
            and str(item.get("role") or "") == "assistant"
            and str((item.get("metadata") or {}).get("run_id") or "") == str(run_id)
            for item in events
        )

    def _mark_failed(self, run: ServerRun, *, error: str) -> None:
        now = utc_now_iso()
        run.status = "failed"
        run.updated_at = now
        self._observe_run_status(run)
        self._touch_memory_session(run.session_id, updated_at=now)
        self._emit_run_event(
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
        self._observe_run_status(run)
        self._touch_memory_session(run.session_id, updated_at=now)
        self._emit_run_event(
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="run.cancelled",
            payload={"reason": str(reason or "user_requested")},
        )

    def _emit_execution_event(self, run: ServerRun, event) -> None:
        run_event = emit_execution_event_as_run_event(
            run_events=self.events,
            run_id=run.run_id,
            session_id=run.session_id,
            event=event,
        )
        self._observe_run_event(run_event)

    def _emit_run_event(self, *, run_id: str, session_id: str, event_type: str, payload: dict[str, Any] | None = None):
        run_event = self.events.emit(
            run_id=run_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        self._observe_run_event(run_event)
        return run_event

    def _observe_run_event(self, event) -> None:
        journal = self._run_journal
        if journal is None:
            return
        try:
            journal.append_event(
                run_id=event.run_id,
                session_id=event.session_id,
                event_type=event.type,
                payload=event.payload,
            )
            if event.type == "approval.requested":
                approval_id = str(event.payload.get("approval_id") or "")
                if approval_id:
                    journal.record_pending_approval(
                        approval_id=approval_id,
                        run_id=event.run_id,
                        invocation_id=str(event.payload.get("invocation_id") or ""),
                        action_digest=hashlib.sha256(
                            canonical_json(
                                {
                                    "tool_name": event.payload.get("tool_name"),
                                    "action": event.payload.get("action"),
                                    "arguments_summary": event.payload.get("arguments_summary"),
                                    "tool_rule": event.payload.get("tool_rule"),
                                    "attempt_id": event.payload.get("attempt_id"),
                                }
                            ).encode("utf-8")
                        ).hexdigest(),
                        requested_at=event.created_at,
                    )
            elif event.type == "approval.resolved":
                journal.resolve_approval(
                    approval_id=str(event.payload.get("approval_id") or ""),
                    status=str(event.payload.get("status") or "cancelled"),
                    decision=str(event.payload.get("decision") or ""),
                    resolved_at=event.created_at,
                )
        except Exception as exc:
            self._journal_degraded_runs[str(event.run_id)] = str(exc)

    def _observe_run_created(self, run: ServerRun) -> None:
        journal = self._run_journal
        if journal is None:
            return
        try:
            journal.create_run(
                run_id=run.run_id,
                session_id=run.session_id,
                status=run.status,
                client_request_id=next(
                    (
                        request_id
                        for request_id, (stored_run_id, _session_id, _input_hash) in self._client_request_runs.items()
                        if stored_run_id == run.run_id
                    ),
                    "",
                ),
                created_at=run.created_at,
            )
        except Exception as exc:
            self._journal_degraded_runs[run.run_id] = str(exc)

    def _observe_run_status(self, run: ServerRun) -> None:
        journal = self._run_journal
        if journal is None:
            return
        try:
            if not journal.update_run_status(run_id=run.run_id, status=run.status):
                raise KeyError(f"unknown run_id: {run.run_id}")
        except Exception as exc:
            self._journal_degraded_runs[run.run_id] = str(exc)

    def _journal_request_run(self, request_id: str) -> tuple[str, str, str] | None:
        journal = self._run_journal
        if journal is None:
            return None
        try:
            record = journal.find_run_by_client_request_id(request_id)
            if record is None:
                return None
            run_id = str(record["run_id"])
            events = journal.events_for_run(run_id)
            started = next((event for event in events if event.event_type == "run.started"), None)
            input_hash = str((started.payload if started else {}).get("input_hash") or "")
            if not input_hash:
                return None
            self._runs.setdefault(
                run_id,
                ServerRun(
                    run_id=run_id,
                    session_id=str(record["session_id"]),
                    user_input="",
                    status=str(record["status"]),
                    created_at=str(record["created_at"]),
                    updated_at=str(record["updated_at"]),
                ),
            )
            value = (run_id, str(record["session_id"]), input_hash)
            self._client_request_runs[request_id] = value
            return value
        except Exception as exc:
            self._journal_degraded_runs[f"request:{request_id}"] = str(exc)
            return None

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

    def _run_snapshot_for_history(self, run: ServerRun) -> dict[str, Any]:
        events = self.events.snapshot(run.run_id)[-MAX_RUN_SNAPSHOT_EVENTS:]
        return {
            "schema_version": RUN_SNAPSHOT_SCHEMA_VERSION,
            "run_status": run.status,
            "events": [_history_run_event_payload(event) for event in events],
        }


SENSITIVE_CONTEXT_MODEL_KEYS = {
    "api_key",
    "api_key_value",
    "api_key_encrypted",
    "authorization",
    "password",
    "secret",
    "token",
}


def _runtime_settings_for_workspace(workspace_root: Path) -> RuntimeSettings:
    try:
        return RuntimeSettings.from_env(workspace_root=workspace_root)
    except Exception:
        return RuntimeSettings()


def _context_usable_models_by_id(models: list[dict[str, Any]], *, policy: PrivacyPolicy) -> dict[str, dict[str, Any]]:
    usable: dict[str, dict[str, Any]] = {}
    for item in models:
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id or not item.get("configured"):
            continue
        if not policy.model_allowed(item):
            continue
        if not model_runtime_available(item):
            continue
        usable[model_id] = item
    return usable


def _context_budget_candidates(
    *,
    settings: RuntimeSettings,
    models: list[dict[str, Any]],
    usable_by_id: dict[str, dict[str, Any]],
    workspace_root: Path,
    policy: PrivacyPolicy,
) -> list[dict[str, Any]]:
    role_priorities = _context_role_priorities(settings, models, workspace_root=workspace_root)
    selected_ids: list[str] = []

    def add_id(model_id: str) -> None:
        clean_id = str(model_id or "").strip()
        if clean_id and clean_id in usable_by_id and clean_id not in selected_ids:
            selected_ids.append(clean_id)

    def add_selected_role(role: str) -> None:
        add_id(_first_context_model_id(role_priorities.get(role, []), usable_by_id, policy=policy))

    def add_executor_candidates() -> None:
        pool = [model_id for model_id in _string_list(settings.worker_model_pool(None)) if model_id in usable_by_id]
        if pool:
            for model_id in pool:
                add_id(model_id)
            return
        for model_id in policy.sort_model_ids(role_priorities.get("executor", []), usable_by_id):
            add_id(model_id)
        add_selected_role("executor")

    mode_policy = execution_mode_policy(str(getattr(settings, "execution_mode", "") or "auto"))
    if mode_policy.fast_single_agent:
        add_selected_role("executor")
    else:
        if bool(getattr(settings, "query_refiner_enabled", False)):
            add_selected_role("query_refiner")
        add_selected_role("orchestrator")
        add_selected_role("final_synthesizer")
        add_executor_candidates()
    if _compute_placement_enforced():
        for model_id in usable_by_id:
            add_id(model_id)

    return [usable_by_id[model_id] for model_id in selected_ids if model_id in usable_by_id]


def _context_role_priorities(
    settings: RuntimeSettings,
    models: list[dict[str, Any]],
    *,
    workspace_root: Path,
) -> dict[str, list[str]]:
    priorities: dict[str, list[str]] = {}
    for role_id, _role_info in iter_model_roles():
        try:
            priorities[role_id] = _dedupe_model_ids(settings.model_priority_for(role_id))
        except Exception:
            priorities[role_id] = []

    try:
        config = load_effective_lucode_config(workspace_root=workspace_root)
    except Exception:
        config = {}
    role_config = config.get("roles") if isinstance(config.get("roles"), dict) else {}
    default_ids = _model_ids_for_refs(model_refs_from_config(config), models) if config else []
    for role_id, _role_info in iter_model_roles():
        explicit_ids = _model_ids_for_refs(role_config.get(role_id) or [], models) if role_config else []
        if explicit_ids:
            priorities[role_id] = explicit_ids
        elif default_ids and not priorities.get(role_id):
            priorities[role_id] = list(default_ids)
    return priorities


def _first_context_model_id(
    preferred: list[str],
    usable_by_id: dict[str, dict[str, Any]],
    *,
    policy: PrivacyPolicy,
) -> str:
    for model_id in policy.sort_model_ids(_dedupe_model_ids(preferred), usable_by_id):
        if model_id in usable_by_id:
            return model_id
    return sorted(usable_by_id)[0] if usable_by_id else ""


def _compute_placement_enforced() -> bool:
    try:
        return str(reliability_flags_from_env().compute_placement or "").strip().lower() == "enforce"
    except Exception:
        return False


def _smallest_context_window_model(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    return min(
        enumerate(candidates),
        key=lambda pair: (context_window_for_model(pair[1]), pair[0]),
    )[1]


def _sanitize_context_model_info(item: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in dict(item or {}).items():
        clean_key = str(key)
        lower_key = clean_key.lower()
        if lower_key in SENSITIVE_CONTEXT_MODEL_KEYS:
            continue
        if any(marker in lower_key for marker in ("api_key", "secret", "token", "authorization", "password")):
            continue
        sanitized[clean_key] = value
    sanitized["context_window_tokens"] = context_window_for_model(item)
    return sanitized


def _dedupe_model_ids(model_ids: Any) -> list[str]:
    deduped: list[str] = []
    for model_id in _string_list(model_ids):
        if model_id not in deduped:
            deduped.append(model_id)
    return deduped


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


def _session_matches_query(session: ServerSession, query: str) -> bool:
    terms = [term.casefold() for term in str(query or "").split() if term.strip()]
    if not terms:
        return True
    haystack = " ".join(
        [
            str(session.session_id or ""),
            str(session.title or ""),
        ]
    ).casefold()
    return all(term in haystack for term in terms)


def _history_run_event_payload(event: Any) -> dict[str, Any]:
    payload = event.to_dict() if hasattr(event, "to_dict") else dict(event or {})
    payload["payload"] = _history_run_event_inner_payload(payload.get("payload"))
    return payload


def _history_run_event_inner_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if clean_key == "final_output":
            preview = _snapshot_text(item)
            if preview:
                payload["final_output_preview"] = preview
            continue
        payload[clean_key] = _snapshot_value(item)
    return payload


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, str):
        return _snapshot_text(value)
    if isinstance(value, list):
        return [_snapshot_value(item) for item in value[:80]]
    if isinstance(value, tuple):
        return [_snapshot_value(item) for item in value[:80]]
    if isinstance(value, dict):
        return {str(key): _snapshot_value(item) for key, item in list(value.items())[:80]}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _snapshot_text(value)


def _snapshot_text(value: Any) -> str:
    text = str(value or "")
    if len(text) <= MAX_RUN_SNAPSHOT_TEXT_CHARS:
        return text
    return text[:MAX_RUN_SNAPSHOT_TEXT_CHARS] + f"...[truncated {len(text) - MAX_RUN_SNAPSHOT_TEXT_CHARS} chars]"


def _sanitize_model(item: dict[str, Any]) -> dict[str, Any]:
    model_id = str(item.get("id") or item.get("model") or "").strip()
    return {
        "id": model_id,
        "display_name": str(item.get("display_name") or item.get("name") or model_id).strip(),
        "provider": str(item.get("provider") or _provider_from_model_id(model_id)).strip(),
        "configured": bool(item.get("configured")),
    }


def _sanitize_model_settings_item(item: dict[str, Any], *, workspace_root: Path | None = None) -> dict[str, Any]:
    model_id = str(item.get("id") or item.get("model") or "").strip()
    levels = reasoning_effort_levels(item)
    probe = dict(item.get("reasoning_effort") or {})
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
        "supports_reasoning_effort": bool(levels),
        "reasoning_effort_levels": (["auto", *levels] if levels else []),
        "selected_reasoning_effort": (
            selected_reasoning_effort(workspace_root=workspace_root, model_id=model_id) if levels else "auto"
        ),
        "reasoning_effort_probe_status": str(probe.get("status") or "unknown"),
        "reasoning_effort_verification": str(probe.get("verification") or ""),
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


def _skill_library_payload(workspace_root: Path) -> list[dict[str, Any]]:
    """Return display-safe Skill metadata without exposing local source paths."""
    try:
        from runtime.skill_library.indexer import load_skill_index
        from runtime.skill_library.metadata_tuner import suggest_metadata_tuning_from_usage
        from runtime.skill_library.metadata_proposals import sync_rule_metadata_proposals
        from runtime.skill_library.taxonomy import load_skill_taxonomy, suggest_categories_for_skill

        context = SimpleNamespace(workspace_root=workspace_root)
        entries = load_skill_index(context)
        taxonomy = load_skill_taxonomy()
        existing_negative_queries = {entry.id: entry.negative_queries for entry in entries}
        suggestions = {
            suggestion.skill_id: suggestion
            for suggestion in suggest_metadata_tuning_from_usage(
                workspace_root,
                existing_negative_queries=existing_negative_queries,
            )
        }
    except Exception:
        # The library is auxiliary UI data and must not block plugin management.
        return []

    try:
        persisted_proposals = sync_rule_metadata_proposals(workspace_root, entries, taxonomy=taxonomy)
    except Exception:
        # Proposal persistence must not hide an otherwise usable Skill library.
        persisted_proposals = {}

    payload: list[dict[str, Any]] = []
    for entry in entries:
        proposal = persisted_proposals.get(entry.id)
        rule_payload = _rule_payload_for_library_entry(entry, proposal, taxonomy)
        payload.append(
            _skill_library_entry_to_dict(
                entry,
                suggestions.get(entry.id),
                suggested_categories=[] if entry.category else rule_payload["categories"],
                suggested_tags=rule_payload["tags"],
                suggested_use_when=rule_payload["use_when"],
                metadata_proposal=proposal,
            )
        )
    return payload


def _skill_library_categories_payload() -> list[dict[str, str]]:
    try:
        from runtime.skill_library.taxonomy import load_skill_taxonomy

        taxonomy = load_skill_taxonomy()
    except Exception:
        return []
    return [
        {
            "id": category_id,
            "name": str(item.get("name") or category_id),
            "description": str(item.get("description") or ""),
        }
        for category_id, item in sorted(taxonomy.categories.items())
    ]


def _rule_payload_for_library_entry(entry: Any, proposal: Any | None, taxonomy: Any) -> dict[str, list[str]]:
    if proposal is not None and isinstance(getattr(proposal, "payload", None), dict):
        proposal_payload = dict(proposal.payload)
        return {
            "categories": [str(item) for item in list(proposal_payload.get("categories") or [])],
            "tags": [str(item) for item in list(proposal_payload.get("tags") or [])],
            "use_when": [str(item) for item in list(proposal_payload.get("use_when") or [])],
        }
    from runtime.skill_library.taxonomy import suggest_categories_for_skill

    return {
        "categories": suggest_categories_for_skill(entry, taxonomy),
        "tags": _suggest_skill_tags(entry),
        "use_when": _suggest_skill_use_when(entry),
    }


def _skill_library_entry_to_dict(
    entry: Any,
    suggestion: Any,
    *,
    suggested_categories: list[str],
    suggested_tags: list[str],
    suggested_use_when: list[str],
    metadata_proposal: Any | None = None,
) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "name": str(entry.name),
        "summary": str(entry.summary),
        "source": str(entry.source),
        "editable_metadata": str(entry.source) == "workspace" and not bool(entry.core),
        "category": [str(item) for item in entry.category],
        "tags": [str(item) for item in entry.tags],
        "use_when": [str(item) for item in entry.use_when],
        "do_not_use_when": [str(item) for item in entry.do_not_use_when],
        "negative_queries": [str(item) for item in entry.negative_queries],
        "distinguish_from": {str(key): str(value) for key, value in entry.distinguish_from.items()},
        "risk_level": str(entry.risk_level),
        "enabled": bool(entry.enabled),
        "core": bool(entry.core),
        "assignable": bool(entry.assignable),
        "metadata_status": str(entry.metadata_status),
        "missing_fields": [str(item) for item in entry.missing_fields],
        "usage": _skill_library_usage_to_dict(entry.usage),
        "metadata_proposal": _skill_metadata_proposal_to_dict(metadata_proposal),
        "suggestion": {
            "categories": [str(item) for item in suggested_categories],
            "tags": [str(item) for item in suggested_tags],
            "use_when": [str(item) for item in suggested_use_when],
            # Never invent exclusion rules for third-party Skills. The owner confirms them in the library.
            "do_not_use_when": [],
            "negative_queries": [str(item) for item in getattr(suggestion, "suggested_negative_queries", ()) or ()],
            "distinguish_from": {
                str(key): str(value)
                for key, value in dict(getattr(suggestion, "suggested_distinguish_from", {}) or {}).items()
            },
            "source_count": int(getattr(suggestion, "source_count", 0) or 0),
        },
    }


def _skill_metadata_proposal_to_dict(proposal: Any | None) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "proposal_id": str(getattr(proposal, "proposal_id", "")),
        "source": str(getattr(proposal, "source", "")),
        "status": str(getattr(proposal, "status", "")),
        "confidence": float(getattr(proposal, "confidence", 0.0) or 0.0),
        "payload": {
            str(key): [str(item) for item in list(values or [])]
            for key, values in dict(getattr(proposal, "payload", {}) or {}).items()
        },
        "reason": str(getattr(proposal, "reason", "")),
        "updated_at": str(getattr(proposal, "updated_at", "")),
    }


def _suggest_skill_tags(entry: Any, *, limit: int = 8) -> list[str]:
    """Offer bounded keyword hints without changing imported Skill metadata."""

    if list(getattr(entry, "tags", ()) or ()):
        return [str(item) for item in list(entry.tags) if str(item).strip()][:limit]
    stop_words = {
        "and", "are", "for", "from", "generic", "main", "remove", "skill", "the", "this", "to", "with", "zh",
    }
    text = " ".join(
        str(value or "")
        for value in (getattr(entry, "name", ""), getattr(entry, "summary", ""))
    ).casefold()
    tags: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9]{3,}", text):
        normalized = token.strip()
        if not normalized or normalized in stop_words or normalized in tags:
            continue
        tags.append(normalized)
        if len(tags) >= limit:
            break
    return tags


def _suggest_skill_use_when(entry: Any) -> list[str]:
    existing = [str(item) for item in list(getattr(entry, "use_when", ()) or ()) if str(item).strip()]
    if existing:
        return existing[:3]
    summary = str(getattr(entry, "summary", "") or "").strip()
    if not summary:
        return []
    first_sentence = re.split(r"(?<=[.!?。！？])\s*", summary, maxsplit=1)[0].strip()
    return [first_sentence[:320]] if first_sentence else []


def _skill_library_usage_to_dict(usage: Any) -> dict[str, Any]:
    raw = dict(usage or {}) if isinstance(usage, dict) else {}
    integer_keys = ("used_count", "success_count", "failure_count", "misfire_count", "rejected_by_planner_count")
    text_keys = ("last_used_at", "last_rejected_at")
    result = {key: max(0, int(raw.get(key) or 0)) for key in integer_keys}
    result.update({key: str(raw.get(key) or "") for key in text_keys})
    return result


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
