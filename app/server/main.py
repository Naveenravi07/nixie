#!/usr/bin/env python3
"""Local HTTP server for Nixi messages and user-configured actions."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.environment import load_environment

from .actions import ActionRegistry
from .config import NixiConfig, load_config
from .llm import VertexChat
from .server_log import ServerConsole


class RequestError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class NixiRequestHandler(BaseHTTPRequestHandler):
    server: "NixiHTTPServer"

    def do_GET(self) -> None:
        if not self._begin_request():
            return
        if self.path == "/health":
            self._send_json({"ok": True, "service": "nixi-server"})
            return

        if self.path == "/actions":
            self._send_json({"actions": self.server.actions.list_actions()})
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_POST(self) -> None:
        if not self._begin_request():
            return
        if self.path == "/message":
            self._handle_message()
            return

        if self.path.startswith("/actions/"):
            action_name = unquote(self.path.removeprefix("/actions/")).strip("/")
            self._handle_action(action_name)
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def log_message(self, fmt: str, *args: object) -> None:
        # Rich request/response records replace BaseHTTPRequestHandler's line log.
        return

    def _handle_message(self) -> None:
        try:
            payload = self._read_json()
        except RequestError as error:
            self._send_error(error.status, error.message)
            return

        message = str(payload.get("message", "")).strip()
        if not message:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing message")
            return

        request_id = str(payload.get("request_id", "")).strip() or self._request_id
        action = self.server.actions.find_for_message(message)
        if action is None:
            self._is_llm_call = True
            grounded = self.server.chat.should_use_google_search(message)
            started = time.perf_counter()
            try:
                response = self.server.chat.reply(message, request_id=request_id)
            except RuntimeError as error:
                self.server.console.llm_call(
                    request_id=request_id,
                    model=self.server.config.llm.model,
                    prompt=message,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    response=str(error),
                    error=True,
                    grounded=grounded,
                )
                self._send_error(HTTPStatus.BAD_GATEWAY, str(error))
                return
            self.server.console.llm_call(
                request_id=request_id,
                model=self.server.config.llm.model,
                prompt=message,
                duration_ms=round((time.perf_counter() - started) * 1000),
                response=response,
                grounded=grounded,
            )
            self._send_json(
                {
                    "request_id": request_id,
                    "transcript": message,
                    "response": response,
                    "action": None,
                }
            )
            return

        try:
            result = self.server.actions.run(action.name, payload.get("args", {}))
        except ValueError as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
            return

        response = f"Ran {action.name}." if result.ok else f"{action.name} failed."
        self._send_json(
            {
                "request_id": request_id,
                "transcript": message,
                "response": response,
                "action": result.to_dict(),
            }
        )

    def _handle_action(self, action_name: str) -> None:
        if not action_name:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing action name")
            return

        try:
            payload = self._read_json(required=False)
        except RequestError as error:
            self._send_error(error.status, error.message)
            return

        try:
            result = self.server.actions.run(action_name, payload.get("args", {}))
        except KeyError as error:
            self._send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        except ValueError as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
            return

        response = f"Ran {action_name}." if result.ok else f"{action_name} failed."
        self._send_json(
            {
                "response": response,
                "action": result.to_dict(),
            }
        )

    def _begin_request(self) -> bool:
        self._request_started = time.perf_counter()
        self._request_id = uuid.uuid4().hex[:12]
        self._cached_request_body = None
        self._request_body_for_log: Any | None = None
        self._is_llm_call = False
        try:
            raw = self._raw_request_body()
        except RequestError as error:
            self._request_body_for_log = {"error": error.message}
            self._send_error(error.status, error.message)
            return False

        body: Any | None = None
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw.decode("utf-8", errors="replace")
            if isinstance(body, dict):
                supplied_id = str(body.get("request_id", "")).strip()
                if supplied_id:
                    self._request_id = supplied_id
        self._request_body_for_log = body
        return True

    def _read_json(self, required: bool = True) -> dict[str, Any]:
        raw = self._raw_request_body()
        if not raw:
            if required:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Missing JSON body")
            return {}

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid JSON body") from None

        return data if isinstance(data, dict) else {}

    def _content_length(self) -> int:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from None
        if length < 0:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
        return length

    def _raw_request_body(self) -> bytes:
        cached = getattr(self, "_cached_request_body", None)
        if cached is not None:
            return cached
        length = self._content_length()
        self._cached_request_body = self.rfile.read(length) if length else b""
        return self._cached_request_body

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        disconnected = False
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            disconnected = True
        if not getattr(self, "_is_llm_call", False):
            self.server.console.response(
                request_id=getattr(self, "_request_id", "unknown"),
                method=self.command,
                path=self.path,
                status=int(status),
                duration_ms=round(
                    (
                        time.perf_counter()
                        - getattr(self, "_request_started", time.perf_counter())
                    )
                    * 1000
                ),
                body=getattr(self, "_request_body_for_log", None),
                disconnected=disconnected,
            )

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)


class NixiHTTPServer(ThreadingHTTPServer):
    def __init__(self, config: NixiConfig, console: ServerConsole | None = None) -> None:
        super().__init__((config.server.host, config.server.port), NixiRequestHandler)
        self.config = config
        self.console = console or ServerConsole()
        self.actions = ActionRegistry(config.actions)
        self.chat = VertexChat(config.llm)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Nixi server.")
    parser.add_argument("--config", type=Path, help="path to nixi.toml")
    parser.add_argument("--host", help="override configured host")
    parser.add_argument("--port", type=int, help="override configured port")
    return parser.parse_args()


def main() -> None:
    load_environment()
    args = parse_args()
    config = load_config(args.config)
    if args.host is not None or args.port is not None:
        config = NixiConfig(
            server=type(config.server)(
                host=args.host or config.server.host,
                port=args.port or config.server.port,
            ),
            voice=config.voice,
            llm=config.llm,
            stt=config.stt,
            tts=config.tts,
            actions=config.actions,
        )

    try:
        server = NixiHTTPServer(config)
    except RuntimeError as error:
        raise SystemExit(f"nixi-server: {error}") from None
    server.console.startup(
        host=config.server.host,
        port=config.server.port,
        model=config.llm.model,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.console.stopped()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
