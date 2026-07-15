from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def derive_tool_postcondition(
    tool_name: str,
    arguments: str | None,
    *,
    workspace_root: Path | str,
) -> tuple[str, dict[str, str]] | None:
    """Derive only deterministic postconditions without retaining raw secrets."""

    normalized_tool_name = str(tool_name or "").strip().lower()
    try:
        payload = json.loads(str(arguments or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        if normalized_tool_name != "desktop_browser.browser_navigate":
            return None
    if normalized_tool_name in {
        "workspace_edit.write_file",
        "workspace_edit.create_file",
    }:
        if not isinstance(payload.get("content"), str):
            return None
        raw_path = str(payload.get("target_path") or "").strip()
        if not raw_path:
            return None
        root = Path(workspace_root).resolve()
        candidate = Path(raw_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if resolved == root or root not in resolved.parents:
            return None
        relative_path = resolved.relative_to(root).as_posix()
        return (
            "workspace_file_sha256.v1",
            {
                "path": relative_path,
                "expected_sha256": hashlib.sha256(payload["content"].encode("utf-8")).hexdigest(),
            },
        )
    if normalized_tool_name != "desktop_browser.browser_navigate":
        return None
    tab_id = str(payload.get("tab_id") or "").strip()
    url = _explicit_browser_url(payload.get("url"))
    if not tab_id or not url:
        # A navigate call that creates an anonymous tab cannot be bound to one
        # durable browser surface after a process interruption.
        return None
    return (
        "browser_navigation_url_sha256.v1",
        {
            "tab_id": tab_id,
            "expected_url_sha256": _sha256_text(url),
        },
    )


def reconcile_pending_tool_postconditions(
    journal,
    *,
    run_id: str,
    browser_tabs_reader: Callable[[], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Verify persisted local-file and explicitly bound browser-navigation state."""

    reconciled: list[dict[str, Any]] = []
    for record in list(journal.pending_tool_postconditions_for_run(run_id) or []):
        result = _reconcile_record(
            journal.workspace_root,
            record,
            browser_tabs_reader=browser_tabs_reader,
        )
        stored = journal.resolve_tool_postcondition(
            invocation_id=str(record["invocation_id"]),
            status=str(result["status"]),
            observed=dict(result["observed"]),
            evidence_ref=str(result["evidence_ref"]),
        )
        reconciled.append(
            {
                "invocation_id": str(record["invocation_id"]),
                "task_id": str(record["task_id"]),
                "kind": str(record["kind"]),
                "status": str(stored["status"]),
                "evidence_ref": str(stored["evidence_ref"]),
                "observed": dict(stored["observed"]),
            }
        )
    return reconciled


def _reconcile_record(
    workspace_root: Path | str,
    record: dict[str, Any],
    *,
    browser_tabs_reader: Callable[[], dict[str, Any]] | None,
) -> dict[str, Any]:
    kind = str(record.get("kind") or "")
    if kind == "browser_navigation_url_sha256.v1":
        return _reconcile_browser_navigation(record, browser_tabs_reader=browser_tabs_reader)
    if kind != "workspace_file_sha256.v1":
        return {"status": "mismatch", "evidence_ref": "", "observed": {}}
    expectation = dict(record.get("expectation") or {})
    relative_path = str(expectation.get("path") or "").strip()
    expected_sha256 = str(expectation.get("expected_sha256") or "").strip().lower()
    try:
        root = Path(workspace_root).resolve()
        candidate = (root / relative_path).resolve()
        if candidate == root or root not in candidate.parents or not candidate.is_file():
            return {
                "status": "mismatch",
                "evidence_ref": "",
                "observed": {"path": relative_path, "exists": False},
            }
        actual_sha256 = _sha256_file(candidate)
    except (OSError, ValueError):
        return {
            "status": "mismatch",
            "evidence_ref": "",
            "observed": {"path": relative_path},
        }
    observed = {"path": relative_path, "sha256": actual_sha256}
    if actual_sha256 != expected_sha256:
        return {"status": "mismatch", "evidence_ref": "", "observed": observed}
    invocation_id = str(record.get("invocation_id") or "")
    return {
        "status": "verified",
        "evidence_ref": f"postcondition:{invocation_id}:{actual_sha256[:16]}",
        "observed": observed,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reconcile_browser_navigation(
    record: dict[str, Any],
    *,
    browser_tabs_reader: Callable[[], dict[str, Any]] | None,
) -> dict[str, Any]:
    expectation = dict(record.get("expectation") or {})
    tab_id = str(expectation.get("tab_id") or "").strip()
    expected_url_sha256 = str(expectation.get("expected_url_sha256") or "").strip().lower()
    if not tab_id or not _is_sha256(expected_url_sha256):
        return {"status": "mismatch", "evidence_ref": "", "observed": {}}
    try:
        state = (browser_tabs_reader or _read_browser_tabs_from_env)()
        tabs = list(state.get("tabs") or []) if isinstance(state, dict) else []
        tab = next(
            (
                item
                for item in tabs
                if isinstance(item, dict)
                and str(item.get("tabId") or item.get("tab_id") or item.get("sessionId") or "") == tab_id
            ),
            None,
        )
    except (OSError, ValueError, TypeError):
        return {"status": "mismatch", "evidence_ref": "", "observed": {"tab_id": tab_id}}
    if tab is None:
        return {
            "status": "mismatch",
            "evidence_ref": "",
            "observed": {"tab_id": tab_id, "exists": False},
        }
    actual_url = _explicit_browser_url(tab.get("url"))
    actual_url_sha256 = _sha256_text(actual_url) if actual_url else ""
    observed = {"tab_id": tab_id}
    if actual_url_sha256:
        observed["url_sha256"] = actual_url_sha256
    if actual_url_sha256 != expected_url_sha256:
        return {"status": "mismatch", "evidence_ref": "", "observed": observed}
    invocation_id = str(record.get("invocation_id") or "")
    return {
        "status": "verified",
        "evidence_ref": f"postcondition:{invocation_id}:{actual_url_sha256[:16]}",
        "observed": observed,
    }


def _read_browser_tabs_from_env() -> dict[str, Any]:
    base_url = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("LUCODE_DESKTOP_BROWSER_BRIDGE_TOKEN") or "").strip()
    if not base_url or not token:
        raise OSError("desktop browser bridge is unavailable")
    if not _is_loopback_bridge_base_url(base_url):
        raise OSError("desktop browser bridge must use a loopback base URL")
    request = Request(
        f"{base_url}/tabs",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (HTTPError, URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OSError("desktop browser tab probe failed") from exc
    if not isinstance(payload, dict):
        raise OSError("desktop browser tab probe returned an invalid payload")
    return payload


def _explicit_browser_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
        return ""
    return url


def _is_loopback_bridge_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() in {"http", "https"}
        and str(parsed.hostname or "").lower() in {"127.0.0.1", "::1", "localhost"}
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
