"""User-configured local actions for Nixi."""

from __future__ import annotations

import random
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any

from app.config import ActionConfig

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# Paths
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_PATH = _REPO_ROOT / "config" / "nixi-profile.toml"

# Directories searched in order as fallback when no profile is available
_WALLPAPER_SEARCH_DIRS = [
    Path.home() / "Pictures" / "walls",
    Path.home() / "Pictures" / "wallpapers",
    Path.home() / "Pictures",
    Path.home() / "Wallpapers",
]


def _find_random_wallpaper() -> str | None:
    """
    Read from nixi-profile.toml's wallpaper file list if available.
    Otherwise, scan well-known picture directories as fallback.
    """
    # 1. Try reading from discovered wallpapers in nixi-profile.toml
    if _PROFILE_PATH.exists():
        try:
            with _PROFILE_PATH.open("rb") as f:
                profile = tomllib.load(f)
            files = profile.get("wallpapers", {}).get("files", [])
            if files:
                return random.choice(files)
        except Exception:
            pass

    # 2. Fallback: Scan directories manually
    candidates: list[Path] = []
    for directory in _WALLPAPER_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        for f in directory.rglob("*"):
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS:
                candidates.append(f)
        if candidates:
            break

    return str(random.choice(candidates)) if candidates else None


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

    def run(self, name: str, args: dict[str, Any] | None = None) -> ActionResult:
        action = self.actions.get(name)
        if action is None:
            raise KeyError(f"Unknown action: {name}")
        if not action.command:
            raise ValueError(f"Action has no command: {name}")

        resolved_args = self._resolve_args(name, action, dict(args or {}))
        command = render_command(action.command, resolved_args)
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

    def _resolve_args(
        self, name: str, action: ActionConfig, args: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Fill in missing or empty arguments before command rendering.

        set_wallpaper + missing/empty path  → pick a random image from ~/Pictures
        volume_up/down + missing amount     → default to 5%+/5%-
        brightness_up/down + missing amount → default to 5%+/5%-
        """
        # Wallpaper: auto-discover an image if path was not provided
        if name == "set_wallpaper" and not args.get("path", "").strip():
            found = _find_random_wallpaper()
            if found is None:
                raise ValueError(
                    "No wallpaper path given and no images found in ~/Pictures."
                )
            args["path"] = found

        # Volume / brightness: default amount if missing
        if name in ("volume_up", "brightness_up") and not args.get("amount", "").strip():
            args["amount"] = "5%+"
        if name in ("volume_down", "brightness_down") and not args.get("amount", "").strip():
            args["amount"] = "5%-"

        return args


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
