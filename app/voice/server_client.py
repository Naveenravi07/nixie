"""HTTP client used by the voice daemon to talk to the local Nixi server."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import ServerConfig


class ServerRequestError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NixiServerClient:
    def __init__(self, config: ServerConfig, timeout_seconds: float = 120) -> None:
        self.base_url = f"http://{config.host}:{config.port}"
        self.timeout_seconds = timeout_seconds

    def new_session(self) -> str:
        payload = json.dumps({}).encode()
        request = Request(
            f"{self.base_url}/session",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.load(response)
        except (HTTPError, URLError) as error:
            raise ServerRequestError(
                f"Failed to create session: {error}"
            ) from error
        session_id = str(result.get("session_id", "")).strip()
        if not session_id:
            raise ServerRequestError("Server returned empty session_id")
        return session_id

    def send_message(
        self, message: str, request_id: str, session_id: str | None = None,
    ) -> str:
        body: dict[str, Any] = {"message": message, "request_id": request_id}
        if session_id:
            body["session_id"] = session_id
        payload = json.dumps(body).encode()
        request = Request(
            f"{self.base_url}/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.load(response)
        except HTTPError as error:
            detail = self._http_error_detail(error)
            suffix = f": {detail}" if detail else ""
            raise ServerRequestError(
                f"Nixi server rejected the command: HTTP {error.code}{suffix}",
                status=error.code,
            ) from error
        except URLError as error:
            raise ServerRequestError(
                f"Cannot reach the Nixi server at {request.full_url}: {error.reason}"
            ) from error

        response_text = str(result.get("response", "Command processed.")).strip()
        return response_text or "Command processed."

    @staticmethod
    def _http_error_detail(error: HTTPError) -> str:
        try:
            payload = json.loads(error.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return ""
        return str(payload.get("error", "")).strip() if isinstance(payload, dict) else ""
