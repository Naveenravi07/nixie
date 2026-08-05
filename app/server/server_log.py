"""Compact Rich terminal logging for the Nixi HTTP server."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text


_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "password", "secret", "token")


def _redact(value: Any, key: str = "") -> Any:
    """Redact likely credentials while retaining useful request structure."""
    if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class ServerConsole:
    """Render one-line HTTP logs and one table per completed LLM call."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)

    @staticmethod
    def _clock() -> str:
        return datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]

    @staticmethod
    def _compact_json(value: Any) -> str:
        return json.dumps(_redact(value), ensure_ascii=False, separators=(",", ":"), default=str)

    def startup(self, *, host: str, port: int, model: str) -> None:
        self.console.print(
            f"[bright_black]{self._clock()}[/] [bold bright_cyan]NIXI[/] "
            f"listening=[cyan]http://{host}:{port}[/] "
            f"model=[magenta]{model}[/] provider=[green]Vertex AI Express[/]"
        )

    def response(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status: int,
        duration_ms: int,
        body: Any | None,
        disconnected: bool = False,
    ) -> None:
        if status >= 500:
            color, icon = "red", "✖"
        elif status >= 400:
            color, icon = "yellow", "!"
        else:
            color, icon = "green", "✓"
        delivery = " disconnected=true" if disconnected else ""
        body_text = f" body={self._compact_json(body)}" if body is not None else ""
        self.console.print(
            f"[bright_black]{self._clock()}[/] [bold {color}]{icon} {status}[/] "
            f"[bold]{method}[/] [white]{path}[/] id=[yellow]{request_id}[/] "
            f"duration=[magenta]{duration_ms}ms[/]{body_text}{delivery}"
        )

    def llm_call(
        self,
        *,
        request_id: str,
        model: str,
        prompt: str,
        response: str,
        duration_ms: int,
        error: bool = False,
        grounded: bool = False,
    ) -> None:
        color = "red" if error else "green"
        status = "FAILED" if error else "COMPLETED"
        table = Table(
            title=f"[bold magenta]◆ AI CALL[/] [bright_black]#{request_id}[/]",
            title_justify="left",
            border_style="magenta",
            show_header=False,
            pad_edge=True,
        )
        table.add_column("Field", style="bright_black", no_wrap=True, width=10)
        table.add_column("Value", overflow="fold")
        table.add_row("Model", Text(model, style="magenta"))
        table.add_row(
            "Grounding",
            Text("Google Search" if grounded else "Off", style="cyan" if grounded else "bright_black"),
        )
        table.add_row("Status", Text(status, style=f"bold {color}"))
        table.add_row("Duration", Text(f"{duration_ms} ms", style="cyan"))
        table.add_row("Prompt", Text(prompt, style="white"))
        table.add_row("Error" if error else "Response", Text(response, style=color if error else "white"))
        self.console.print(table)

    def stopped(self) -> None:
        self.console.print(
            f"[bright_black]{self._clock()}[/] [bold bright_black]NIXI server stopped[/]"
        )
