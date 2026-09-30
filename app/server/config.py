"""Configuration loading for Nixi."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "example_config" / "nixi.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "nixi" / "nixi.toml"


DEFAULT_SYSTEM_PROMPT = (
    "You are {agent_name}, a concise and friendly desktop voice assistant.\n"
    "Answer naturally for speech and avoid Markdown unless asked.\n"
    "\n"
    "You have access to tools on the user's Linux computer:\n"
    "\n"
    "1. **run_command** — Execute any read-only shell command and see its output.\n"
    "   Use this proactively to answer questions about the system, check configurations,\n"
    "   open applications (e.g. \"firefox\", \"code\"), open URLs (e.g. \"open https://twitter.com\"),\n"
    "   list files, check memory/cpu/disk usage, or gather any information.\n"
    "   Examples: free -h, df -h, ls ~/Documents, top -bn1 | head -20, xdg-open https://example.com\n"
    "\n"
    "2. **request_screenshot** — Ask the user for permission to capture their screen.\n"
    "   Use this when the user asks you to look at something, see their screen,\n"
    "   identify what is on screen, help with UI navigation, or anything that\n"
    "   requires visual context. The user will be shown a notification and must\n"
    "   approve before the screenshot is taken.\n"
    "\n"
    "3. **Pre-configured actions** — volume_up, volume_down, mute_audio, set_wallpaper,\n"
    "   brightness_up, brightness_down, take_screenshot, media_play_pause, media_next, media_prev.\n"
    "   Use these for specific media/brightness/wallpaper actions.\n"
    "\n"
    "Rules:\n"
    "- Always use run_command for system queries, opening apps, or opening websites.\n"
    "- Use request_screenshot when you need to see the user's screen.\n"
    "- Use the pre-configured actions for volume/brightness/media/wallpaper controls.\n"
    "- Be concise in your spoken responses (1-3 sentences max).\n"
    "- If a command fails, explain what went wrong briefly."
)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class VoiceConfig:
    sample_rate: int = 16_000
    command_timeout_seconds: float = 8.0
    microphone_target: str = ""


@dataclass(frozen=True)
class LLMConfig:
    model: str = "gemini-3.5-flash-lite"
    system_prompt: str = ""
    max_tokens: int = 1024
    thinking_level: str = "minimal"
    timeout_seconds: float = 30.0
    history_turns: int = 8
    google_search_enabled: bool = True


@dataclass(frozen=True)
class STTConfig:
    enabled: bool = True
    model: str = "saaras:v3-realtime"
    language: str = "en-IN"
    mode: str = "transcribe"
    stream_type: str = "fast"
    threshold: float = 0.3
    silence_ms: int = 500
    min_speech_ms: int = 250
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class TTSConfig:
    enabled: bool = True
    model: str = "bulbul:v3"
    language: str = "en-IN"
    speaker: str = "shubh"
    pace: float = 1.05
    sample_rate: int = 24_000
    temperature: float = 0.6
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = True
    approval_timeout_seconds: float = 15.0
    notify_command: str = "notify-send -a nixi -i camera-webcam '{title}' '{body}'"
    screenshot_command: str = "grim -"


@dataclass(frozen=True)
class ActionConfig:
    name: str
    command: str
    description: str = ""
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class NixiConfig:
    agent_name: str
    server: ServerConfig
    voice: VoiceConfig
    llm: LLMConfig
    stt: STTConfig
    tts: TTSConfig
    vision: VisionConfig
    actions: dict[str, ActionConfig]


def default_config() -> NixiConfig:
    """Return a fully-populated NixiConfig with all defaults from code."""
    agent_name = "Nixi"
    return NixiConfig(
        agent_name=agent_name,
        server=ServerConfig(),
        voice=VoiceConfig(),
        llm=LLMConfig(
            system_prompt=DEFAULT_SYSTEM_PROMPT.format(agent_name=agent_name),
        ),
        stt=STTConfig(),
        tts=TTSConfig(),
        vision=VisionConfig(),
        actions={},
    )


def resolve_config_path(path: Path | None = None) -> Path:
    """Resolve which nixi.toml to use.

    Order: explicit path > NIXI_CONFIG env > ~/.config/nixi/nixi.toml > repo default.
    """
    if path is not None:
        return path
    if os.environ.get("NIXI_CONFIG"):
        return Path(os.environ["NIXI_CONFIG"])
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> NixiConfig:
    config_path = resolve_config_path(path)
    data = _read_toml(config_path)

    defaults = default_config()

    agent_name = str(data.get("agent_name", defaults.agent_name))

    server_data = data.get("server", {})
    server = ServerConfig(
        host=str(server_data.get("host", defaults.server.host)),
        port=int(server_data.get("port", defaults.server.port)),
    )

    voice_data = data.get("voice", {})
    voice = VoiceConfig(
        sample_rate=int(voice_data.get("sample_rate", defaults.voice.sample_rate)),
        command_timeout_seconds=float(
            voice_data.get("command_timeout_seconds", defaults.voice.command_timeout_seconds)
        ),
        microphone_target=str(
            voice_data.get("microphone_target", defaults.voice.microphone_target)
        ),
    )

    llm_data = data.get("llm", {})
    system_prompt_raw = llm_data.get("system_prompt", "")
    if system_prompt_raw:
        system_prompt = str(system_prompt_raw)
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT.format(agent_name=agent_name)

    llm = LLMConfig(
        model=str(llm_data.get("model", defaults.llm.model)),
        system_prompt=system_prompt,
        max_tokens=int(llm_data.get("max_tokens", defaults.llm.max_tokens)),
        thinking_level=str(llm_data.get("thinking_level", defaults.llm.thinking_level)),
        timeout_seconds=float(llm_data.get("timeout_seconds", defaults.llm.timeout_seconds)),
        history_turns=int(llm_data.get("history_turns", defaults.llm.history_turns)),
        google_search_enabled=bool(
            llm_data.get("google_search_enabled", defaults.llm.google_search_enabled)
        ),
    )

    stt_data = data.get("stt", {})
    stt = STTConfig(
        enabled=bool(stt_data.get("enabled", defaults.stt.enabled)),
        model=str(stt_data.get("model", defaults.stt.model)),
        language=str(stt_data.get("language", defaults.stt.language)),
        mode=str(stt_data.get("mode", defaults.stt.mode)),
        stream_type=str(stt_data.get("stream_type", defaults.stt.stream_type)),
        threshold=float(stt_data.get("threshold", defaults.stt.threshold)),
        silence_ms=int(stt_data.get("silence_ms", defaults.stt.silence_ms)),
        min_speech_ms=int(stt_data.get("min_speech_ms", defaults.stt.min_speech_ms)),
        timeout_seconds=float(stt_data.get("timeout_seconds", defaults.stt.timeout_seconds)),
    )

    tts_data = data.get("tts", {})
    tts = TTSConfig(
        enabled=bool(tts_data.get("enabled", defaults.tts.enabled)),
        model=str(tts_data.get("model", defaults.tts.model)),
        language=str(tts_data.get("language", defaults.tts.language)),
        speaker=str(tts_data.get("speaker", defaults.tts.speaker)),
        pace=float(tts_data.get("pace", defaults.tts.pace)),
        sample_rate=int(tts_data.get("sample_rate", defaults.tts.sample_rate)),
        temperature=float(tts_data.get("temperature", defaults.tts.temperature)),
        timeout_seconds=float(tts_data.get("timeout_seconds", defaults.tts.timeout_seconds)),
    )

    actions = {
        name: _parse_action(name, action_data)
        for name, action_data in data.get("actions", {}).items()
    }

    vision_data = data.get("vision", {})
    vision = VisionConfig(
        enabled=bool(vision_data.get("enabled", defaults.vision.enabled)),
        approval_timeout_seconds=float(
            vision_data.get("approval_timeout_seconds", defaults.vision.approval_timeout_seconds)
        ),
        notify_command=str(
            vision_data.get("notify_command", defaults.vision.notify_command)
        ),
        screenshot_command=str(
            vision_data.get("screenshot_command", defaults.vision.screenshot_command)
        ),
    )

    return NixiConfig(
        agent_name=agent_name,
        server=server,
        voice=voice,
        llm=llm,
        stt=stt,
        tts=tts,
        vision=vision,
        actions=actions,
    )


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
