#!/usr/bin/env python3
"""Local HTTP server for Nixi messages and user-configured actions."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .actions import ActionRegistry
from .config import NixiConfig, load_config


class RequestError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class NixiRequestHandler(BaseHTTPRequestHandler):
    server: "NixiHTTPServer"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"ok": True, "service": "nixi-server"})
            return

        if self.path == "/actions":
            self._send_json({"actions": self.server.actions.list_actions()})
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_POST(self) -> None:
        if self.path == "/message":
            self._handle_message()
            return

        if self.path.startswith("/actions/"):
            action_name = unquote(self.path.removeprefix("/actions/")).strip("/")
            self._handle_action(action_name)
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

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

        action = self.server.actions.find_for_message(message)
        if action is None:
            self._send_json(
                {
                    "transcript": message,
                    "response": "I heard you, but I do not have a matching action yet.",
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

        self._send_json(
            {
                "response": f"Ran {action_name}." if result.ok else f"{action_name} failed.",
                "action": result.to_dict(),
            }
        )

    def _read_json(self, required: bool = True) -> dict[str, Any]:
        length = self._content_length()
        if length == 0:
            if required:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Missing JSON body")
            return {}

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid JSON body") from None

        return data if isinstance(data, dict) else {}

    def _content_length(self) -> int:
        return int(self.headers.get("Content-Length", "0"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)


class NixiHTTPServer(ThreadingHTTPServer):
    def __init__(self, config: NixiConfig) -> None:
        super().__init__((config.server.host, config.server.port), NixiRequestHandler)
        self.config = config
        self.actions = ActionRegistry(config.actions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Nixi server.")
    parser.add_argument("--config", type=Path, help="path to nixi.toml")
    parser.add_argument("--host", help="override configured host")
    parser.add_argument("--port", type=int, help="override configured port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    if args.host is not None or args.port is not None:
        config = NixiConfig(
            server=type(config.server)(
                host=args.host or config.server.host,
                port=args.port or config.server.port,
            ),
            voice=config.voice,
            actions=config.actions,
        )

    server = NixiHTTPServer(config)
    print(f"Nixi server listening on http://{config.server.host}:{config.server.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNixi server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
