from __future__ import annotations

import os
from dataclasses import dataclass


class RuntimeAuthError(Exception):
    """Raised when a local runtime request is not authorized."""


@dataclass(frozen=True)
class RuntimeAuth:
    token: str
    enabled: bool = True

    @classmethod
    def from_env(cls, *, token: str | None = None, enabled: bool = True) -> "RuntimeAuth":
        raw = token if token is not None else os.environ.get("LUCODE_RUNTIME_TOKEN")
        normalized = str(raw or "").strip()
        if enabled and not normalized:
            raise ValueError("LUCODE_RUNTIME_TOKEN is required when runtime server auth is enabled.")
        return cls(token=normalized, enabled=enabled)

    def require_http(self, request) -> None:
        if not self.enabled:
            return
        if _bearer_token(str(request.headers.get("authorization") or "")) != self.token:
            raise RuntimeAuthError("invalid runtime token")

    def is_websocket_authorized(self, websocket) -> bool:
        if not self.enabled:
            return True
        query_token = str(websocket.query_params.get("token") or "").strip()
        if query_token and query_token == self.token:
            return True
        header_token = _bearer_token(str(websocket.headers.get("authorization") or ""))
        if header_token and header_token == self.token:
            return True
        protocol_token = _token_from_websocket_protocol(str(websocket.headers.get("sec-websocket-protocol") or ""))
        return bool(protocol_token and protocol_token == self.token)


def _bearer_token(value: str) -> str:
    prefix = "bearer "
    text = str(value or "").strip()
    if not text.lower().startswith(prefix):
        return ""
    return text[len(prefix) :].strip()


def _token_from_websocket_protocol(value: str) -> str:
    for part in str(value or "").split(","):
        text = part.strip()
        if text.lower().startswith("lucode-token."):
            return text.split(".", 1)[1].strip()
    return ""
