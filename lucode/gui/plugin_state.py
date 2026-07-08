from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lucode.gui.sidebar_data import McpRow, SkillCard


STATE_FILE_NAME = "gui_plugin_state.json"
PLUGIN_MANIFEST_NAME = "lucode-plugin.json"
_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
_PLUGIN_SCHEMA_VERSION = "lucode_plugin.v1"
_MCP_RISK_LEVELS = {"low", "medium", "high", "critical"}

_CUSTOM_CHIP = "\u81ea\u5b9a\u4e49"
_LOCAL_CHIP = "\u672c\u5730"
_MCP_ADDED_STATUS = "\u5df2\u6dfb\u52a0"
_MCP_DISCONNECTED_DETAIL = "\u672a\u8fde\u63a5"


class PluginStateStore:
    """Persist GUI plugin state in the workspace-local Lucode state."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.path = self.workspace_root / ".lucode" / STATE_FILE_NAME
        self.local_skills_dir = self.workspace_root / ".lucode" / "skills"
        self.local_mcp_path = self.workspace_root / ".lucode" / "mcp_servers.json"
        self.local_plugins_dir = self.workspace_root / ".lucode" / "plugins"

    def load_removed_skill_ids(self) -> set[str]:
        data = self._read_state()
        raw_ids = data.get("removed_skill_ids") if isinstance(data, dict) else []
        if not isinstance(raw_ids, list):
            return set()
        return {skill_id for value in raw_ids if (skill_id := _normalize_plugin_id(value))}

    def mark_skill_removed(self, skill_id: str) -> set[str]:
        normalized = _normalize_plugin_id(skill_id)
        if not normalized:
            raise ValueError("Invalid skill id")
        data = self._read_state()
        removed = self.load_removed_skill_ids()
        removed.add(normalized)
        data["removed_skill_ids"] = sorted(removed)
        self._write_state(data)
        return removed

    def load_custom_skill_cards(self) -> list[SkillCard]:
        data = self._read_state()
        raw_cards = data.get("custom_skill_cards") if isinstance(data, dict) else []
        if not isinstance(raw_cards, list):
            return []
        cards: list[SkillCard] = []
        for item in raw_cards:
            if not isinstance(item, dict):
                continue
            skill_id = _normalize_plugin_id(item.get("id"))
            title = str(item.get("title") or skill_id).strip()
            if not skill_id or not title:
                continue
            raw_chips = item.get("chips")
            chips = tuple(str(chip).strip() for chip in raw_chips if str(chip).strip()) if isinstance(raw_chips, list) else ()
            cards.append(
                SkillCard(
                    id=skill_id,
                    title=title,
                    description=str(item.get("description") or "").strip(),
                    chips=chips or (_CUSTOM_CHIP, _LOCAL_CHIP),
                )
            )
        return cards

    def load_custom_mcp_rows(self) -> list[McpRow]:
        data = self._read_state()
        raw_rows = data.get("custom_mcp_rows") if isinstance(data, dict) else []
        if not isinstance(raw_rows, list):
            return []
        rows: list[McpRow] = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            mcp_id = _normalize_plugin_id(item.get("id"))
            title = str(item.get("title") or mcp_id).strip()
            if not mcp_id or not title:
                continue
            rows.append(
                McpRow(
                    id=mcp_id,
                    title=title,
                    status=str(item.get("status") or _MCP_ADDED_STATUS).strip(),
                    detail=str(item.get("detail") or _MCP_DISCONNECTED_DETAIL).strip(),
                )
            )
        return rows

    def load_installed_plugin_packages(self) -> list[dict[str, Any]]:
        data = self._read_state()
        raw_packages = data.get("installed_plugin_packages") if isinstance(data, dict) else []
        if not isinstance(raw_packages, list):
            return []
        packages: list[dict[str, Any]] = []
        for item in raw_packages:
            package = _plugin_package_summary(item)
            if package:
                packages.append(package)
        return packages

    def install_skill_from_path(self, source_path: str | Path) -> SkillCard:
        source = Path(source_path).resolve()
        prepared = _prepare_skill_source(source)
        skill_id = _normalize_plugin_id(prepared.name)
        if not skill_id:
            raise ValueError("Invalid skill folder name")
        target = self.local_skills_dir / skill_id
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prepared, target)
        metadata = _read_skill_metadata(target / "SKILL.md")
        card = SkillCard(
            id=skill_id,
            title=str(metadata.get("name") or _title_from_id(skill_id)).strip(),
            description=str(metadata.get("description") or "").strip(),
            chips=(_CUSTOM_CHIP, _LOCAL_CHIP),
        )
        self._upsert_custom_skill_card(card)
        return card

    def install_mcp_from_path(self, source_path: str | Path, *, require_risk_metadata: bool = False) -> McpRow:
        source = Path(source_path).resolve()
        servers = _read_mcp_servers_from_path(source, require_risk_metadata=require_risk_metadata)
        rows = self._upsert_mcp_servers(servers, detail_factory=lambda config: _MCP_DISCONNECTED_DETAIL)
        return rows[0]

    def register_external_mcp(self, payload: dict[str, Any]) -> McpRow:
        server_id, config = _external_mcp_config(payload)
        rows = self._upsert_mcp_servers({server_id: config}, detail_factory=_external_mcp_detail)
        return rows[0]

    def install_plugin_package_from_path(self, source_path: str | Path) -> dict[str, Any]:
        source = Path(source_path).resolve()
        prepared = _prepare_plugin_package_source(source)
        manifest = _read_plugin_manifest(prepared / PLUGIN_MANIFEST_NAME)
        plugin_id = _normalize_plugin_id(manifest.get("id") or prepared.name)
        if not plugin_id:
            raise ValueError("Invalid plugin package id")
        skill_paths = _string_list(manifest.get("skills") or [])
        mcp_template_paths = _string_list(manifest.get("mcp_templates") or [])
        for relative_path in skill_paths:
            _prepare_skill_source(_resolve_package_member(prepared, relative_path))
        for relative_path in mcp_template_paths:
            _read_mcp_servers_from_path(
                _resolve_package_member(prepared, relative_path),
                require_risk_metadata=True,
            )
        target = self.local_plugins_dir / plugin_id
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prepared, target)

        installed_skill_ids: list[str] = []
        for relative_path in skill_paths:
            card = self.install_skill_from_path(_resolve_package_member(target, relative_path))
            installed_skill_ids.append(card.id)

        installed_mcp_ids: list[str] = []
        for relative_path in mcp_template_paths:
            row = self.install_mcp_from_path(
                _resolve_package_member(target, relative_path),
                require_risk_metadata=True,
            )
            installed_mcp_ids.append(row.id)

        launch_profiles = _plugin_launch_profiles(manifest.get("launch_profiles"))
        self._upsert_installed_plugin_package(
            {
                "id": plugin_id,
                "title": str(manifest.get("title") or _title_from_id(plugin_id)).strip(),
                "description": str(manifest.get("description") or "").strip(),
                "source_path": str(source),
                "installed_path": str(target),
                "skill_ids": installed_skill_ids,
                "mcp_ids": installed_mcp_ids,
                "launch_profiles": launch_profiles,
            }
        )
        return {
            "id": plugin_id,
            "skill_ids": installed_skill_ids,
            "mcp_ids": installed_mcp_ids,
            "launch_profile_ids": [item["id"] for item in launch_profiles],
        }

    def uninstall_plugin_package(self, plugin_id: str) -> dict[str, Any]:
        normalized = _normalize_plugin_id(plugin_id)
        if not normalized:
            raise ValueError("Invalid plugin package id")
        data = self._read_state()
        raw_packages = data.get("installed_plugin_packages")
        packages = raw_packages if isinstance(raw_packages, list) else []
        package = next(
            (item for item in packages if isinstance(item, dict) and _normalize_plugin_id(item.get("id")) == normalized),
            None,
        )
        if package is None:
            raise ValueError(f"unknown plugin package id: {normalized}")

        skill_ids = _string_list(package.get("skill_ids"))
        mcp_ids = _string_list(package.get("mcp_ids"))
        data["installed_plugin_packages"] = [
            item
            for item in packages
            if not (isinstance(item, dict) and _normalize_plugin_id(item.get("id")) == normalized)
        ]
        data["custom_skill_cards"] = _remove_rows_by_ids(data.get("custom_skill_cards"), skill_ids)
        data["custom_mcp_rows"] = _remove_rows_by_ids(data.get("custom_mcp_rows"), mcp_ids)
        removed_skill_ids = [item for item in _string_list(data.get("removed_skill_ids")) if item not in set(skill_ids)]
        if removed_skill_ids:
            data["removed_skill_ids"] = sorted(removed_skill_ids)
        else:
            data.pop("removed_skill_ids", None)
        self._write_state(data)

        for skill_id in skill_ids:
            _remove_local_child_dir(self.local_skills_dir, skill_id)
        _remove_local_child_dir(self.local_plugins_dir, normalized)
        self._remove_mcp_servers(mcp_ids)
        return {
            "id": normalized,
            "skill_ids": skill_ids,
            "mcp_ids": mcp_ids,
        }

    def _upsert_mcp_servers(
        self,
        servers: dict[str, dict[str, Any]],
        *,
        detail_factory,
    ) -> list[McpRow]:
        current = self._read_mcp_servers()
        current_servers = current.setdefault("mcpServers", {})
        if not isinstance(current_servers, dict):
            current_servers = {}
            current["mcpServers"] = current_servers
        rows: list[McpRow] = []
        for server_id, config in servers.items():
            normalized = _normalize_plugin_id(server_id)
            if not normalized:
                continue
            current_servers[normalized] = dict(config)
            rows.append(
                McpRow(
                    id=normalized,
                    title=normalized,
                    status=_MCP_ADDED_STATUS,
                    detail=str(detail_factory(config)).strip() or _MCP_DISCONNECTED_DETAIL,
                )
            )
        if not rows:
            raise ValueError("No valid MCP server id found")
        self._write_mcp_servers(current)
        for row in rows:
            self._upsert_custom_mcp_row(row)
        return rows

    def _upsert_custom_skill_card(self, card: SkillCard) -> None:
        data = self._read_state()
        cards = [item for item in self.load_custom_skill_cards() if item.id != card.id]
        cards.append(card)
        data["custom_skill_cards"] = [asdict(item) for item in cards]
        removed = set(data.get("removed_skill_ids") or [])
        if card.id in removed:
            removed.remove(card.id)
            data["removed_skill_ids"] = sorted(removed)
        self._write_state(data)

    def _upsert_custom_mcp_row(self, row: McpRow) -> None:
        data = self._read_state()
        rows = [item for item in self.load_custom_mcp_rows() if item.id != row.id]
        rows.append(row)
        data["custom_mcp_rows"] = [asdict(item) for item in rows]
        self._write_state(data)

    def _upsert_installed_plugin_package(self, package: dict[str, Any]) -> None:
        data = self._read_state()
        packages = data.get("installed_plugin_packages")
        if not isinstance(packages, list):
            packages = []
        packages = [item for item in packages if not isinstance(item, dict) or item.get("id") != package["id"]]
        packages.append(package)
        data["installed_plugin_packages"] = packages
        self._write_state(data)

    def _read_mcp_servers(self) -> dict[str, Any]:
        try:
            data = json.loads(self.local_mcp_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"mcpServers": {}}
        except Exception:
            return {"mcpServers": {}}
        return data if isinstance(data, dict) else {"mcpServers": {}}

    def _write_mcp_servers(self, data: dict[str, Any]) -> None:
        self.local_mcp_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.local_mcp_path.with_suffix(f"{self.local_mcp_path.suffix}.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.local_mcp_path)

    def _remove_mcp_servers(self, mcp_ids: list[str]) -> None:
        normalized_ids = {_normalize_plugin_id(item) for item in mcp_ids}
        normalized_ids.discard("")
        if not normalized_ids:
            return
        current = self._read_mcp_servers()
        current_servers = current.get("mcpServers")
        if not isinstance(current_servers, dict):
            return
        changed = False
        for mcp_id in normalized_ids:
            if mcp_id in current_servers:
                current_servers.pop(mcp_id, None)
                changed = True
        if changed:
            current["mcpServers"] = current_servers
            self._write_mcp_servers(current)

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.path)


def _normalize_plugin_id(value: object) -> str:
    text = str(value or "").strip()
    if not text or not _PLUGIN_ID_PATTERN.match(text):
        return ""
    return text


def _plugin_package_summary(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    plugin_id = _normalize_plugin_id(item.get("id"))
    if not plugin_id:
        return {}
    title = str(item.get("title") or _title_from_id(plugin_id)).strip()
    return {
        "id": plugin_id,
        "title": title,
        "description": str(item.get("description") or "").strip(),
        "skill_ids": _string_list(item.get("skill_ids")),
        "mcp_ids": _string_list(item.get("mcp_ids")),
        "launch_profiles": _plugin_launch_profiles(item.get("launch_profiles")),
        "deletable": True,
    }


def _remove_rows_by_ids(value: Any, ids: list[str]) -> list[Any]:
    normalized_ids = {_normalize_plugin_id(item) for item in ids}
    normalized_ids.discard("")
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if not (isinstance(item, dict) and _normalize_plugin_id(item.get("id")) in normalized_ids)
    ]


def _remove_local_child_dir(root: Path, child_name: str) -> None:
    normalized = _normalize_plugin_id(child_name)
    if not normalized:
        return
    resolved_root = root.resolve()
    target = (resolved_root / normalized).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Plugin package uninstall target cannot escape local state") from exc
    if target.exists():
        shutil.rmtree(target)


def _prepare_skill_source(source: Path) -> Path:
    if source.is_dir():
        if not (source / "SKILL.md").exists():
            raise ValueError("Skill folder must contain SKILL.md")
        return source
    if source.is_file() and source.suffix.lower() == ".zip":
        extract_root = source.parent / f".{source.stem}_skill_extract"
        if extract_root.exists():
            shutil.rmtree(extract_root)
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_root)
        matches = [path.parent for path in extract_root.rglob("SKILL.md")]
        if not matches:
            raise ValueError("Skill zip must contain SKILL.md")
        return matches[0]
    raise ValueError("Skill install source must be a folder or zip")


def _prepare_plugin_package_source(source: Path) -> Path:
    if source.is_dir():
        if not (source / PLUGIN_MANIFEST_NAME).exists():
            raise ValueError(f"Plugin package must contain {PLUGIN_MANIFEST_NAME}")
        return source
    if source.is_file() and source.suffix.lower() == ".zip":
        extract_root = source.parent / f".{source.stem}_plugin_extract"
        if extract_root.exists():
            shutil.rmtree(extract_root)
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_root)
        matches = [path.parent for path in extract_root.rglob(PLUGIN_MANIFEST_NAME)]
        if not matches:
            raise ValueError(f"Plugin zip must contain {PLUGIN_MANIFEST_NAME}")
        return matches[0]
    raise ValueError("Plugin package install source must be a folder or zip")


def _read_skill_metadata(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    metadata: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def _read_plugin_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Plugin package must contain {PLUGIN_MANIFEST_NAME}") from exc
    except Exception as exc:
        raise ValueError("Invalid plugin manifest JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Plugin manifest must be a JSON object")
    schema_version = str(data.get("schema_version") or _PLUGIN_SCHEMA_VERSION)
    if schema_version != _PLUGIN_SCHEMA_VERSION:
        raise ValueError("Unsupported plugin manifest schema_version")
    if "id" in data and not _normalize_plugin_id(data.get("id")):
        raise ValueError("Invalid plugin manifest id")
    for key in ("skills", "mcp_templates"):
        if key in data and not isinstance(data.get(key), (list, tuple, str)):
            raise ValueError(f"Plugin manifest {key} must be a string or list")
        for relative_path in _string_list(data.get(key)):
            if Path(relative_path).is_absolute():
                raise ValueError(f"Plugin manifest {key} entries must be relative paths")
    return data


def _resolve_package_member(package_root: Path, relative_path: str) -> Path:
    text = str(relative_path or "").strip()
    if not text:
        raise ValueError("Plugin package member path is required")
    if Path(text).is_absolute():
        raise ValueError("Plugin package member path must be relative")
    root = package_root.resolve()
    target = (root / text).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Plugin package member path cannot escape the package") from exc
    return target


def _plugin_launch_profiles(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    profiles: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        profile_id = _normalize_plugin_id(item.get("id") or item.get("script") or item.get("label"))
        if not profile_id:
            continue
        profile = {
            "id": profile_id,
            "label": str(item.get("label") or _title_from_id(profile_id)).strip(),
            "script": str(item.get("script") or "").strip(),
        }
        description = str(item.get("description") or "").strip()
        if description:
            profile["description"] = description
        profiles.append(profile)
    return profiles


def _extract_mcp_servers(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        return {}
    raw = data.get("mcpServers")
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    server_id = data.get("id") or data.get("name")
    if server_id and ("command" in data or "url" in data):
        return {str(server_id): dict(data)}
    return {}


def _read_mcp_servers_from_path(source: Path, *, require_risk_metadata: bool = False) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("Invalid MCP JSON file") from exc
    servers = _extract_mcp_servers(data)
    if not servers:
        raise ValueError("No MCP servers found")
    _validate_mcp_servers(servers, require_risk_metadata=require_risk_metadata)
    return servers


def _validate_mcp_servers(servers: dict[str, dict[str, Any]], *, require_risk_metadata: bool = False) -> None:
    for raw_id, config in servers.items():
        server_id = _normalize_plugin_id(raw_id)
        if not server_id:
            raise ValueError("Invalid MCP server id")
        if not isinstance(config, dict):
            raise ValueError(f"MCP server {server_id} config must be an object")
        _validate_mcp_transport(server_id, config)
        if require_risk_metadata:
            _validate_mcp_risk_metadata(server_id, config)


def _validate_mcp_transport(server_id: str, config: dict[str, Any]) -> None:
    transport = str(config.get("transport") or "").strip().lower()
    if transport in {"http", "sse"}:
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError(f"MCP server {server_id} url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"MCP server {server_id} url must start with http:// or https://")
        return
    if transport == "stdio":
        if not str(config.get("command") or "").strip():
            raise ValueError(f"MCP server {server_id} command is required")
        return
    if "url" in config:
        url = str(config.get("url") or "").strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"MCP server {server_id} url must start with http:// or https://")
    if "command" in config and not str(config.get("command") or "").strip():
        raise ValueError(f"MCP server {server_id} command is required")


def _validate_mcp_risk_metadata(server_id: str, config: dict[str, Any]) -> None:
    risk_level = str(config.get("risk_level") or "").strip().lower()
    if risk_level not in _MCP_RISK_LEVELS:
        allowed = ", ".join(sorted(_MCP_RISK_LEVELS))
        raise ValueError(f"MCP server {server_id} risk_level must be one of: {allowed}")
    side_effects = str(config.get("side_effects") or "").strip()
    if not side_effects or side_effects.lower() == "unknown":
        raise ValueError(f"MCP server {server_id} side_effects is required")
    if "approval_required" not in config and "requires_approval" not in config:
        raise ValueError(f"MCP server {server_id} approval_required is required")


def _external_mcp_config(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    server_id = _normalize_plugin_id(payload.get("id") or payload.get("name"))
    if not server_id:
        raise ValueError("id is required")
    transport = str(payload.get("transport") or "stdio").strip().lower()
    if transport in {"http", "sse"}:
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return server_id, {"transport": transport, "url": url}
    if transport != "stdio":
        raise ValueError("transport must be stdio, http, or sse")
    command = str(payload.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    args = _string_list(payload.get("args") or [])
    config: dict[str, Any] = {
        "transport": "stdio",
        "command": command,
    }
    if args:
        config["args"] = args
    return server_id, config


def _external_mcp_detail(config: dict[str, Any]) -> str:
    transport = str(config.get("transport") or "stdio").strip().lower()
    if transport in {"http", "sse"}:
        return f"{transport} - {config.get('url') or ''}".strip()
    command = str(config.get("command") or "").strip()
    return f"stdio - {command}".strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _title_from_id(value: str) -> str:
    return " ".join(part.capitalize() for part in str(value or "").replace("-", "_").split("_") if part)
