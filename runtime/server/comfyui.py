from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from runtime.server.schemas import utc_now_iso


COMFYUI_SCHEMA_VERSION = "comfyui.v1"
DEFAULT_COMFYUI_BASE_URL = "http://127.0.0.1:8188"


def comfyui_state(workspace_root: Path) -> dict[str, Any]:
    settings = _load_settings(workspace_root)
    base_url = str(settings.get("base_url") or DEFAULT_COMFYUI_BASE_URL).strip()
    return _payload(
        base_url=base_url,
        configured=bool(settings.get("base_url")),
        status="unknown",
        last_error="",
        checked_at="",
        endpoints={},
    )


def save_comfyui_settings(workspace_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = normalize_comfyui_base_url(payload.get("base_url"))
    _save_settings(workspace_root, {"base_url": base_url})
    return comfyui_state(workspace_root)


def check_comfyui_connection(workspace_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    settings = _load_settings(workspace_root)
    raw_base_url = payload.get("base_url") or settings.get("base_url") or DEFAULT_COMFYUI_BASE_URL
    base_url = normalize_comfyui_base_url(raw_base_url)
    endpoint_results: dict[str, bool] = {}
    errors: list[str] = []
    for endpoint in ("system_stats", "queue"):
        try:
            _comfyui_http_get_json(f"{base_url}/{endpoint}", timeout_seconds=2.0)
        except Exception as exc:  # noqa: BLE001 - health checks must report failures, not crash the runtime API.
            endpoint_results[endpoint] = False
            errors.append(f"{endpoint}: {_short_error(exc)}")
        else:
            endpoint_results[endpoint] = True
    online = all(endpoint_results.values())
    return _payload(
        base_url=base_url,
        configured=bool(settings.get("base_url")),
        status="online" if online else "offline",
        last_error="; ".join(errors),
        checked_at=utc_now_iso(),
        endpoints=endpoint_results,
    )


def normalize_comfyui_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("base_url is required")
    if "://" not in text:
        text = f"http://{text}"
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an http or https URL")
    normalized = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return normalized.rstrip("/") or f"{parsed.scheme}://{parsed.netloc}"


def _payload(
    *,
    base_url: str,
    configured: bool,
    status: str,
    last_error: str,
    checked_at: str,
    endpoints: dict[str, bool],
) -> dict[str, Any]:
    return {
        "schema_version": COMFYUI_SCHEMA_VERSION,
        "base_url": base_url,
        "configured": configured,
        "status": status,
        "last_error": last_error,
        "checked_at": checked_at,
        "endpoints": dict(endpoints),
    }


def _settings_path(workspace_root: Path) -> Path:
    return Path(workspace_root).resolve() / ".lucode" / "comfyui.json"


def _load_settings(workspace_root: Path) -> dict[str, Any]:
    path = _settings_path(workspace_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_settings(workspace_root: Path, payload: dict[str, Any]) -> None:
    path = _settings_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _comfyui_http_get_json(url: str, *, timeout_seconds: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - user-configured local health URL.
        raw = response.read(512 * 1024)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _short_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason or exc)
    return str(exc)
