"""Configuration loading for Nixi."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "nixi.toml"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class VoiceConfig:
    wake_phrases: tuple[str, ...] = (
        "hey nixi",
        "hey nixie",
        "hey nicki",
        "hey nicky",
        "hey nick see",
        "hey next see",
        "hey next thing",
    )
    model: str = "tiny.en"
    language: str = "en"
    sample_rate: int = 16_000
    speech_threshold: int = 500
    silence_ms: int = 400
    speech_start_ms: int = 150
    min_speech_ms: int = 250
    max_utterance_seconds: float = 8.0
    command_timeout_seconds: float = 8.0
    calibration_ms: int = 1_000
    adaptive_noise_ratio: float = 2.5
    microphone_target: str = ""


@dataclass(frozen=True)
class ActionConfig:
    name: str
    command: str
    description: str = ""
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class NixiConfig:
    server: ServerConfig
    voice: VoiceConfig
    actions: dict[str, ActionConfig]


def load_config(path: Path | None = None) -> NixiConfig:
    config_path = path or Path(os.environ.get("NIXI_CONFIG", DEFAULT_CONFIG_PATH))
    data = _read_toml(config_path)

    server_data = data.get("server", {})
    server = ServerConfig(
        host=str(server_data.get("host", ServerConfig.host)),
        port=int(server_data.get("port", ServerConfig.port)),
    )

    voice_data = data.get("voice", {})
    voice = VoiceConfig(
        wake_phrases=tuple(
            str(phrase).strip().lower()
            for phrase in voice_data.get("wake_phrases", VoiceConfig.wake_phrases)
            if str(phrase).strip()
        ),
        model=str(voice_data.get("model", VoiceConfig.model)),
        language=str(voice_data.get("language", VoiceConfig.language)),
        sample_rate=int(voice_data.get("sample_rate", VoiceConfig.sample_rate)),
        speech_threshold=int(voice_data.get("speech_threshold", VoiceConfig.speech_threshold)),
        silence_ms=int(voice_data.get("silence_ms", VoiceConfig.silence_ms)),
        speech_start_ms=int(voice_data.get("speech_start_ms", VoiceConfig.speech_start_ms)),
        min_speech_ms=int(voice_data.get("min_speech_ms", VoiceConfig.min_speech_ms)),
        max_utterance_seconds=float(
            voice_data.get("max_utterance_seconds", VoiceConfig.max_utterance_seconds)
        ),
        command_timeout_seconds=float(
            voice_data.get("command_timeout_seconds", VoiceConfig.command_timeout_seconds)
        ),
        calibration_ms=int(voice_data.get("calibration_ms", VoiceConfig.calibration_ms)),
        adaptive_noise_ratio=float(
            voice_data.get("adaptive_noise_ratio", VoiceConfig.adaptive_noise_ratio)
        ),
        microphone_target=str(
            voice_data.get("microphone_target", VoiceConfig.microphone_target)
        ),
    )

    actions = {
        name: _parse_action(name, action_data)
        for name, action_data in data.get("actions", {}).items()
    }

    return NixiConfig(server=server, voice=voice, actions=actions)


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
