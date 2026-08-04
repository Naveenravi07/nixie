"""User-configured local actions for Lexi."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from string import Formatter
from typing import Any

from .config import ActionConfig


@dataclass(frozen=True)
class ActionResult:
    name: str
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class ActionRegistry:
    def __init__(self, actions: dict[str, ActionConfig]) -> None:
        self.actions = actions

    def list_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": action.name,
                "description": action.description,
                "triggers": list(action.triggers),
            }
            for action in self.actions.values()
        ]

    def find_for_message(self, message: str) -> ActionConfig | None:
        normalized = message.strip().lower()
        if not normalized:
            return None

        for action in self.actions.values():
            if normalized == action.name.replace("_", " "):
                return action
            if normalized == action.name:
                return action
            if any(trigger in normalized for trigger in action.triggers):
                return action

        return None

    def run(self, name: str, args: dict[str, Any] | None = None) -> ActionResult:
        action = self.actions.get(name)
        if action is None:
            raise KeyError(f"Unknown action: {name}")
        if not action.command:
            raise ValueError(f"Action has no command: {name}")

        command = render_command(action.command, args or {})
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            text=True,
            capture_output=True,
        )
        return ActionResult(
            name=name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )


def render_command(template: str, args: dict[str, Any]) -> str:
    return ShellCommandFormatter(args).format(template)


class ShellCommandFormatter(Formatter):
    def __init__(self, args: dict[str, Any]) -> None:
        super().__init__()
        self.args = args

    def get_value(self, key: object, args: tuple[object, ...], kwargs: dict[str, Any]) -> Any:
        if isinstance(key, str):
            return self.args.get(key, "")
        return super().get_value(key, args, kwargs)

    def format_field(self, value: Any, format_spec: str) -> str:
        if format_spec == "q":
            import shlex

            return shlex.quote(str(value))
        return super().format_field(value, format_spec)
