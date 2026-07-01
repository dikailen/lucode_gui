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
_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")

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

    def install_mcp_from_path(self, source_path: str | Path) -> McpRow:
        source = Path(source_path).resolve()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("Invalid MCP JSON file") from exc
        servers = _extract_mcp_servers(data)
        if not servers:
            raise ValueError("No MCP servers found")
        rows = self._upsert_mcp_servers(servers, detail_factory=lambda config: _MCP_DISCONNECTED_DETAIL)
        return rows[0]

    def register_external_mcp(self, payload: dict[str, Any]) -> McpRow:
        server_id, config = _external_mcp_config(payload)
        rows = self._upsert_mcp_servers({server_id: config}, detail_factory=_external_mcp_detail)
        return rows[0]

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
