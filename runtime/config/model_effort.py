from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.config.model_config import load_lucode_config, save_lucode_config


REASONING_EFFORTS = ("auto", "low", "medium", "high", "xhigh")


def reasoning_effort_levels(model_info: dict[str, Any] | None) -> list[str]:
    info = dict(model_info or {})
    if info.get("supports_reasoning_effort") is not True:
        return []
    levels = [str(item).strip().lower() for item in list(info.get("reasoning_effort_levels") or [])]
    return [level for level in REASONING_EFFORTS[1:] if level in levels]


def selected_reasoning_effort(*, workspace_root: Path | str | None, model_id: str) -> str:
    config = load_lucode_config(workspace_root=workspace_root)
    values = config.get("reasoning_effort") if isinstance(config.get("reasoning_effort"), dict) else {}
    selected = str(values.get(str(model_id or "").strip()) or "auto").strip().lower()
    return selected if selected in REASONING_EFFORTS else "auto"


def set_reasoning_effort(*, workspace_root: Path | str | None, model_id: str, effort: str, model_info: dict[str, Any] | None) -> str:
    clean_model_id = str(model_id or "").strip()
    clean_effort = str(effort or "").strip().lower()
    if not clean_model_id:
        raise ValueError("model_id is required")
    if clean_effort not in REASONING_EFFORTS:
        raise ValueError("reasoning effort must be auto, low, medium, high, or xhigh")
    levels = reasoning_effort_levels(model_info)
    if clean_effort != "auto" and clean_effort not in levels:
        raise ValueError("model does not support the requested reasoning effort")
    config = load_lucode_config(workspace_root=workspace_root)
    values = dict(config.get("reasoning_effort") or {})
    if clean_effort == "auto":
        values.pop(clean_model_id, None)
    else:
        values[clean_model_id] = clean_effort
    if values:
        config["reasoning_effort"] = values
    else:
        config.pop("reasoning_effort", None)
    save_lucode_config(config, workspace_root=workspace_root)
    return clean_effort
