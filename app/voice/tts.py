"""Sarvam streaming text-to-speech playback through PipeWire."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from contextlib import suppress
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.event_log import log_event
from app.config import TTSConfig


SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech/stream"


class SarvamSpeaker:
    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self.api_key = os.environ.get("SARVAM_API_KEY", "").strip()
        self._speak_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._player: subprocess.Popen[bytes] | None = None
        self._response: object | None = None
        if config.enabled and not self.api_key:
            raise RuntimeError("SARVAM_API_KEY is required while TTS is enabled.")

    def speak(
        self,
        text: str,
        request_id: str = "standalone",
        started_event: threading.Event | None = None,
    ) -> bool:
        if not self.config.enabled or not text.strip():
            if started_event is not None:
                started_event.set()
            return True

        payload = json.dumps(
            {
                "text": text.strip()[:3500],
                "target_language_code": self.config.language,
                "speaker": self.config.speaker,
                "pace": self.config.pace,
                "speech_sample_rate": self.config.sample_rate,
                "model": self.config.model,
                "temperature": self.config.temperature,
                "output_audio_codec": "wav",
            }
        ).encode("utf-8")
        request = Request(
            SARVAM_TTS_URL,
            data=payload,
            headers={
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        started = time.perf_counter()
        log_event(
            "voice",
            "sarvam.started",
            request_id,
            model=self.config.model,
            language=self.config.language,
            speaker=self.config.speaker,
            characters=len(text.strip()[:3500]),
        )
        with self._speak_lock:
            self._stop_requested.clear()
            if started_event is not None:
                started_event.set()
            try:
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    with self._state_lock:
                        self._response = response
                    completed = self._play_stream(response)
            except HTTPError as error:
                raise RuntimeError(
                    f"Sarvam TTS rejected the request: HTTP {error.code}."
                ) from error
            except URLError as error:
                raise RuntimeError(f"Sarvam TTS connection failed: {error.reason}.") from error
            finally:
                with self._state_lock:
                    self._response = None

        log_event(
            "voice",
            "sarvam.completed" if completed else "sarvam.interrupted",
            request_id,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return completed

    def stop(self) -> None:
        """Immediately stop current playback and cancel the TTS response stream."""
        self._stop_requested.set()
        with self._state_lock:
            player = self._player
            response = self._response
        if player is not None and player.poll() is None:
            player.terminate()
        if response is not None:
            with suppress(Exception):
                response.close()

    def _play_stream(self, response: object) -> bool:
        try:
            player = subprocess.Popen(["pw-play", "-"], stdin=subprocess.PIPE)
        except FileNotFoundError as error:
            raise RuntimeError("pw-play is required for TTS playback.") from error

        with self._state_lock:
            self._player = player
        assert player.stdin is not None
        try:
            while not self._stop_requested.is_set() and (
                chunk := response.read(64 * 1024)
            ):
                player.stdin.write(chunk)
        except BrokenPipeError as error:
            if not self._stop_requested.is_set():
                raise RuntimeError("PipeWire stopped during TTS playback.") from error
        # Closing HTTPResponse from stop() can race with http.client's chunked
        # reader after it has cleared its internal file object. In that case it
        # raises AttributeError instead of a normal I/O exception.
        except (AttributeError, OSError, ValueError) as error:
            if not self._stop_requested.is_set():
                raise RuntimeError("TTS audio streaming stopped unexpectedly.") from error
        finally:
            with suppress(BrokenPipeError, OSError, ValueError):
                player.stdin.close()
            if self._stop_requested.is_set() and player.poll() is None:
                player.terminate()

        returncode = player.wait()
        with self._state_lock:
            if self._player is player:
                self._player = None
        if self._stop_requested.is_set():
            return False
        if returncode != 0:
            raise RuntimeError(f"pw-play exited with status {returncode}.")
        return True
