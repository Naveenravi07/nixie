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
    barge_in_threshold_multiplier: float = 1.6
    barge_in_speech_start_ms: int = 200
    microphone_target: str = ""


@dataclass(frozen=True)
class LLMConfig:
    model: str = "gemini-3.5-flash-lite"
    system_prompt: str = (
        "You are Nixi, a concise and friendly desktop voice assistant. "
        "Answer naturally for speech and avoid Markdown unless asked."
    )
    max_tokens: int = 1024
    thinking_level: str = "minimal"
    timeout_seconds: float = 30.0
    history_turns: int = 3
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
class ActionConfig:
    name: str
    command: str
    description: str = ""
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class NixiConfig:
    server: ServerConfig
    voice: VoiceConfig
    llm: LLMConfig
    stt: STTConfig
    tts: TTSConfig
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
        barge_in_threshold_multiplier=float(
            voice_data.get(
                "barge_in_threshold_multiplier",
                VoiceConfig.barge_in_threshold_multiplier,
            )
        ),
        barge_in_speech_start_ms=int(
            voice_data.get("barge_in_speech_start_ms", VoiceConfig.barge_in_speech_start_ms)
        ),
        microphone_target=str(
            voice_data.get("microphone_target", VoiceConfig.microphone_target)
        ),
    )

    llm_data = data.get("llm", {})
    llm = LLMConfig(
        model=str(llm_data.get("model", LLMConfig.model)),
        system_prompt=str(llm_data.get("system_prompt", LLMConfig.system_prompt)),
        max_tokens=int(llm_data.get("max_tokens", LLMConfig.max_tokens)),
        thinking_level=str(llm_data.get("thinking_level", LLMConfig.thinking_level)),
        timeout_seconds=float(llm_data.get("timeout_seconds", LLMConfig.timeout_seconds)),
        history_turns=int(llm_data.get("history_turns", LLMConfig.history_turns)),
        google_search_enabled=bool(
            llm_data.get("google_search_enabled", LLMConfig.google_search_enabled)
        ),
    )

    stt_data = data.get("stt", {})
    stt = STTConfig(
        enabled=bool(stt_data.get("enabled", STTConfig.enabled)),
        model=str(stt_data.get("model", STTConfig.model)),
        language=str(stt_data.get("language", STTConfig.language)),
        mode=str(stt_data.get("mode", STTConfig.mode)),
        stream_type=str(stt_data.get("stream_type", STTConfig.stream_type)),
        threshold=float(stt_data.get("threshold", STTConfig.threshold)),
        silence_ms=int(stt_data.get("silence_ms", STTConfig.silence_ms)),
        min_speech_ms=int(stt_data.get("min_speech_ms", STTConfig.min_speech_ms)),
        timeout_seconds=float(stt_data.get("timeout_seconds", STTConfig.timeout_seconds)),
    )

    tts_data = data.get("tts", {})
    tts = TTSConfig(
        enabled=bool(tts_data.get("enabled", TTSConfig.enabled)),
        model=str(tts_data.get("model", TTSConfig.model)),
        language=str(tts_data.get("language", TTSConfig.language)),
        speaker=str(tts_data.get("speaker", TTSConfig.speaker)),
        pace=float(tts_data.get("pace", TTSConfig.pace)),
        sample_rate=int(tts_data.get("sample_rate", TTSConfig.sample_rate)),
        temperature=float(tts_data.get("temperature", TTSConfig.temperature)),
        timeout_seconds=float(tts_data.get("timeout_seconds", TTSConfig.timeout_seconds)),
    )

    actions = {
        name: _parse_action(name, action_data)
        for name, action_data in data.get("actions", {}).items()
    }

    return NixiConfig(
        server=server,
        voice=voice,
        llm=llm,
        stt=stt,
        tts=tts,
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
