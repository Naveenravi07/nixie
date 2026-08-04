"""Configuration loading for the local Lexi server."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "lexi.toml"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class ActionConfig:
    name: str
    command: str
    description: str = ""
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class LexiConfig:
    server: ServerConfig
    actions: dict[str, ActionConfig]


def load_config(path: Path | None = None) -> LexiConfig:
    config_path = path or Path(os.environ.get("LEXI_CONFIG", DEFAULT_CONFIG_PATH))
    data = _read_toml(config_path)

    server_data = data.get("server", {})
    server = ServerConfig(
        host=str(server_data.get("host", ServerConfig.host)),
        port=int(server_data.get("port", ServerConfig.port)),
    )

    actions = {
        name: _parse_action(name, action_data)
        for name, action_data in data.get("actions", {}).items()
    }

    return LexiConfig(server=server, actions=actions)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def _parse_action(name: str, data: dict[str, Any]) -> ActionConfig:
    command = str(data.get("command", "")).strip()
    description = str(data.get("description", "")).strip()
    triggers = tuple(str(trigger).lower() for trigger in data.get("triggers", ()))
    return ActionConfig(
        name=name,
        command=command,
        description=description,
        triggers=triggers,
    )
