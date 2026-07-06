from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from runtime.server.schemas import utc_now_iso


COMFYUI_SCHEMA_VERSION = "comfyui.v1"
COMFYUI_DETECTION_SCHEMA_VERSION = "comfyui_detection.v1"
DEFAULT_COMFYUI_BASE_URL = "http://127.0.0.1:8188"
NVIDIA_LAUNCH_SCRIPT = "run_nvidia_gpu.bat"
CPU_LAUNCH_SCRIPT = "run_cpu.bat"
KNOWN_LAUNCH_SCRIPTS = (NVIDIA_LAUNCH_SCRIPT, CPU_LAUNCH_SCRIPT)


def comfyui_state(workspace_root: Path) -> dict[str, Any]:
    settings = _load_settings(workspace_root)
    base_url = str(settings.get("base_url") or DEFAULT_COMFYUI_BASE_URL).strip()
    return _payload(
        base_url=base_url,
        configured=bool(settings.get("base_url") or settings.get("install_path")),
        status="unknown",
        last_error="",
        checked_at="",
        endpoints={},
        installation=_installation_from_settings(settings),
    )


def save_comfyui_settings(workspace_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = _load_settings(workspace_root)
    raw_base_url = payload.get("base_url") if "base_url" in payload else current.get("base_url")
    base_url = normalize_comfyui_base_url(raw_base_url or DEFAULT_COMFYUI_BASE_URL)
    settings: dict[str, Any] = {"base_url": base_url}

    raw_install_path = payload.get("install_path") if "install_path" in payload else current.get("install_path")
    install_path = str(raw_install_path or "").strip()
    if install_path:
        launch_script = str(payload.get("launch_script") or current.get("launch_script") or "").strip()
        installation = detect_comfyui_installation(install_path, launch_script=launch_script, configured=True)
        if not installation["valid"]:
            raise ValueError("; ".join(installation["validation_errors"]) or "invalid ComfyUI installation path")
        settings.update(
            {
                "install_path": installation["install_path"],
                "resolved_root": installation["resolved_root"],
                "launch_script": installation["launch_script"],
                "launch_mode": installation["launch_mode"],
            }
        )
    elif "install_path" in payload:
        settings.pop("install_path", None)
        settings.pop("resolved_root", None)
        settings.pop("launch_script", None)
        settings.pop("launch_mode", None)

    _save_settings(workspace_root, settings)
    return comfyui_state(workspace_root)


def detect_comfyui_installation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": COMFYUI_DETECTION_SCHEMA_VERSION,
        **detect_comfyui_installation(
            payload.get("install_path"),
            launch_script=str(payload.get("launch_script") or "").strip(),
            configured=False,
        ),
    }


def detect_comfyui_installation(
    install_path: Any,
    *,
    launch_script: str = "",
    configured: bool = False,
) -> dict[str, Any]:
    clean_path = str(install_path or "").strip()
    if not clean_path:
        return _installation_payload(
            install_path="",
            resolved_root="",
            configured=False,
            valid=False,
            status="unconfigured",
            launch_mode="",
            launch_script="",
            launch_command="",
            available_launch_scripts=[],
            validation_errors=[],
        )

    requested = Path(clean_path).expanduser()
    first_errors: list[str] = []
    for candidate in _candidate_install_roots(requested):
        available_scripts = _available_launch_scripts(candidate)
        errors = _validate_install_root(candidate, available_scripts)
        if errors:
            if not first_errors:
                first_errors = errors
            continue
        selected_script = _select_launch_script(available_scripts, launch_script)
        if not selected_script:
            return _installation_payload(
                install_path=clean_path,
                resolved_root=str(candidate.resolve()),
                configured=configured,
                valid=False,
                status="invalid_launch_script",
                launch_mode="",
                launch_script="",
                launch_command="",
                available_launch_scripts=available_scripts,
                validation_errors=[f"launch_script is not available: {launch_script}"],
            )
        return _installation_payload(
            install_path=clean_path,
            resolved_root=str(candidate.resolve()),
            configured=configured,
            valid=True,
            status="launchable",
            launch_mode=_launch_mode(selected_script),
            launch_script=selected_script,
            launch_command=_launch_command(selected_script),
            available_launch_scripts=available_scripts,
            validation_errors=[],
        )

    return _installation_payload(
        install_path=clean_path,
        resolved_root="",
        configured=configured,
        valid=False,
        status="invalid_path",
        launch_mode="",
        launch_script="",
        launch_command="",
        available_launch_scripts=[],
        validation_errors=first_errors or ["path does not look like a ComfyUI portable installation"],
    )


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
        configured=bool(settings.get("base_url") or settings.get("install_path")),
        status="online" if online else "offline",
        last_error="; ".join(errors),
        checked_at=utc_now_iso(),
        endpoints=endpoint_results,
        installation=_installation_from_settings(settings),
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
    installation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": COMFYUI_SCHEMA_VERSION,
        "base_url": base_url,
        "configured": configured,
        "status": status,
        "last_error": last_error,
        "checked_at": checked_at,
        "endpoints": dict(endpoints),
        "installation": dict(installation),
    }


def _installation_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    install_path = str(settings.get("install_path") or "").strip()
    if not install_path:
        return detect_comfyui_installation("", configured=False)
    return detect_comfyui_installation(
        install_path,
        launch_script=str(settings.get("launch_script") or "").strip(),
        configured=True,
    )


def _installation_payload(
    *,
    install_path: str,
    resolved_root: str,
    configured: bool,
    valid: bool,
    status: str,
    launch_mode: str,
    launch_script: str,
    launch_command: str,
    available_launch_scripts: list[str],
    validation_errors: list[str],
) -> dict[str, Any]:
    return {
        "install_path": install_path,
        "resolved_root": resolved_root,
        "configured": configured,
        "valid": valid,
        "status": status,
        "launch_mode": launch_mode,
        "launch_script": launch_script,
        "launch_command": launch_command,
        "available_launch_scripts": list(available_launch_scripts),
        "validation_errors": list(validation_errors),
    }


def _candidate_install_roots(path: Path) -> list[Path]:
    candidates = [path]
    nested = path / "ComfyUI_windows_portable"
    if nested not in candidates:
        candidates.append(nested)
    try:
        children = list(path.iterdir()) if path.is_dir() else []
    except OSError:
        children = []
    for child in children:
        if child.is_dir() and child not in candidates and (child / "ComfyUI" / "main.py").is_file():
            candidates.append(child)
    return candidates


def _validate_install_root(root: Path, available_scripts: list[str]) -> list[str]:
    errors: list[str] = []
    if not root.is_dir():
        errors.append("directory does not exist")
    if not (root / "ComfyUI" / "main.py").is_file():
        errors.append("missing ComfyUI/main.py")
    if not (root / "python_embeded" / "python.exe").is_file():
        errors.append("missing python_embeded/python.exe")
    if not available_scripts:
        errors.append("missing run_nvidia_gpu.bat or run_cpu.bat")
    return errors


def _available_launch_scripts(root: Path) -> list[str]:
    return [script for script in KNOWN_LAUNCH_SCRIPTS if (root / script).is_file()]


def _select_launch_script(available_scripts: list[str], requested: str) -> str:
    clean_requested = Path(str(requested or "").strip()).name
    if clean_requested:
        return clean_requested if clean_requested in available_scripts else ""
    for preferred in KNOWN_LAUNCH_SCRIPTS:
        if preferred in available_scripts:
            return preferred
    return ""


def _launch_mode(launch_script: str) -> str:
    if launch_script == NVIDIA_LAUNCH_SCRIPT:
        return "nvidia"
    if launch_script == CPU_LAUNCH_SCRIPT:
        return "cpu"
    return "custom"


def _launch_command(launch_script: str) -> str:
    args = [r".\python_embeded\python.exe", "-s", r"ComfyUI\main.py"]
    if launch_script == CPU_LAUNCH_SCRIPT:
        args.append("--cpu")
    args.append("--windows-standalone-build")
    return " ".join(args)


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
