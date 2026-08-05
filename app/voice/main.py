#!/usr/bin/env python3
"""Always-on PipeWire microphone listener with local Whisper transcription."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from app.environment import load_environment
from app.event_log import log_event
from app.config import load_config
from app.voice import audio as voice_audio
from app.voice import recognition as voice_recognition
from app.voice.popup_control import PopupController as VoicePopupController
from app.voice.server_client import NixiServerClient, ServerRequestError
from app.voice.stt import SarvamRealtimeTranscriber
from app.voice.tts import SarvamSpeaker

class NixiVoiceDaemon:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config = load_config(config_path)
        self.voice = self.config.voice
        self.segmenter = voice_audio.UtteranceSegmenter(self.voice)
        self.recorder = voice_audio.PipeWireRecorder(self.voice)
        self.popup = VoicePopupController()
        self.transcriber = voice_recognition.WhisperTranscriber(self.voice)
        self.sarvam_transcriber = SarvamRealtimeTranscriber(
            self.config.stt,
            self.voice.sample_rate,
        )
        self.speaker = SarvamSpeaker(self.config.tts)
        self.server_client = NixiServerClient(self.config.server)
        self.awaiting_command = False
        self.command_deadline = 0.0
        self.running = True

    def run(self) -> None:
        print(
            f"Starting microphone calibration ({self.voice.calibration_ms / 1000:g}s); "
            "keep quiet...",
            flush=True,
        )
        self.recorder.start()
        announced_ready = False
        try:
            for frame in self.recorder.frames():
                if not self.running:
                    break
                if self.segmenter.calibrated and not announced_ready:
                    print(
                        f"Nixi is ready. Say {self.voice.wake_phrases[0]!r}. "
                        f"Speech threshold: {self.segmenter.effective_threshold:.0f}",
                        flush=True,
                    )
                    announced_ready = True
                self._expire_command_window()
                utterance = self.segmenter.push(frame)
                if utterance is not None:
                    self._handle_utterance(utterance)
        finally:
            self.popup.close()
            self.recorder.stop()

    def stop(self, *_args: object) -> None:
        self.running = False
        self.recorder.stop()

    def _handle_utterance(self, utterance: np.ndarray) -> None:
        started = time.perf_counter()
        transcript = self.transcriber.transcribe(utterance, self.voice.sample_rate)
        inference_seconds = time.perf_counter() - started
        if not transcript:
            return
        audio_seconds = utterance.size / self.voice.sample_rate
        print(
            f"Heard [{audio_seconds:.1f}s audio, {inference_seconds:.2f}s STT]: {transcript}",
            flush=True,
        )

        woke, inline_command = voice_recognition.extract_wake_command(
            transcript,
            self.voice.wake_phrases,
        )
        if not self.awaiting_command:
            if not woke:
                return
            self.popup.open()
            if self.config.stt.enabled:
                self._handle_sarvam_command(utterance, inline_command)
                return
            if inline_command:
                self._process_command(inline_command)
            else:
                self.awaiting_command = True
                self.command_deadline = time.monotonic() + self.voice.command_timeout_seconds
            return

        command = (
            inline_command
            if woke and inline_command
            else voice_recognition.normalize_transcript(transcript)
        )
        if command:
            self._process_command(command)

    def _handle_sarvam_command(
        self,
        wake_utterance: np.ndarray,
        whisper_inline_command: str,
    ) -> None:
        request_id = uuid.uuid4().hex[:12]
        try:
            if whisper_inline_command:
                transcript = self.sarvam_transcriber.transcribe_pcm(
                    wake_utterance,
                    request_id,
                )
                woke, sarvam_command = voice_recognition.extract_wake_command(
                    transcript,
                    self.voice.wake_phrases,
                )
                command = sarvam_command if woke and sarvam_command else whisper_inline_command
            else:
                print("Listening for command...", flush=True)
                command_audio = self._capture_utterance(
                    timeout_seconds=self.config.stt.timeout_seconds,
                )
                if command_audio is None:
                    print("Listening timed out.", flush=True)
                    self.popup.close()
                    return
                command = self.sarvam_transcriber.transcribe_pcm(
                    command_audio,
                    request_id,
                )
        except RuntimeError as error:
            log_event("voice", "sarvam_stt.failed", request_id, error=str(error))
            print("Sarvam STT failed; falling back to local Whisper.", file=sys.stderr)
            self.awaiting_command = True
            self.command_deadline = time.monotonic() + self.voice.command_timeout_seconds
            return

        if command.strip():
            self._process_command(command.strip(), request_id=request_id)
            return

        print("Sarvam did not detect a command.", flush=True)
        self.popup.close()

    def _capture_utterance(
        self,
        *,
        timeout_seconds: float,
        stop_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        barge_in_until: threading.Event | None = None,
    ) -> np.ndarray | None:
        """Capture one locally segmented utterance from the live recorder."""
        deadline = time.monotonic() + timeout_seconds
        notified_start = False
        was_barge_in_mode = barge_in_until is not None and not barge_in_until.is_set()
        for frame in self.recorder.frames():
            if not self.running or (stop_event is not None and stop_event.is_set()):
                return None
            barge_in_mode = (
                barge_in_until is not None
                and (not barge_in_until.is_set() or notified_start)
            )
            if was_barge_in_mode and not barge_in_mode:
                # Drop audio buffered from Nixi's own voice before opening the
                # normal-sensitivity follow-up window.
                self.segmenter.reset()
            was_barge_in_mode = barge_in_mode
            was_active = bool(self.segmenter.active_frames)
            utterance = self.segmenter.push(
                frame,
                threshold=(
                    self.segmenter.effective_threshold
                    * self.voice.barge_in_threshold_multiplier
                    if barge_in_mode
                    else None
                ),
                speech_start_ms=(
                    self.voice.barge_in_speech_start_ms if barge_in_mode else None
                ),
                update_noise_floor=not barge_in_mode,
            )
            if not notified_start and not was_active and self.segmenter.active_frames:
                notified_start = True
                if on_speech_start is not None:
                    on_speech_start()
            if utterance is not None:
                return utterance
            if time.monotonic() >= deadline:
                self.segmenter.reset()
                return None
        return None

    def _process_command(self, command: str, request_id: str | None = None) -> None:
        self.awaiting_command = False
        request_id = request_id or uuid.uuid4().hex[:12]
        try:
            while command and self.running:
                response_text = self._request_response(command, request_id)
                if response_text is None:
                    return
                command, request_id = self._speak_and_listen(response_text, request_id)
                self.recorder.discard_pending()
                self.segmenter.reset()
        finally:
            self.awaiting_command = False
            self.recorder.discard_pending()
            self.segmenter.reset()
            self.popup.close()

    def _request_response(self, command: str, request_id: str) -> str | None:
        server_started = time.perf_counter()
        try:
            return self.server_client.send_message(command, request_id)
        except ServerRequestError as error:
            print(str(error), file=sys.stderr)
            log_event(
                "voice",
                "server_request.failed",
                request_id,
                duration_ms=round((time.perf_counter() - server_started) * 1000),
                status=error.status,
                error=str(error),
            )
            return None

    def _speak_and_listen(
        self,
        response_text: str,
        response_request_id: str,
    ) -> tuple[str, str]:
        """Speak a response while listening for barge-in or a follow-up turn."""
        listen_request_id = uuid.uuid4().hex[:12]
        stop_listening = threading.Event()
        tts_ready = threading.Event()
        tts_finished = threading.Event()
        speech_started = threading.Event()
        interrupted_tts = threading.Event()

        def finish_followup_window() -> None:
            timer = threading.Timer(
                self.voice.command_timeout_seconds,
                stop_listening.set,
            )
            timer.daemon = True
            timer.start()

        def speak_response() -> None:
            try:
                self.speaker.speak(
                    response_text,
                    request_id=response_request_id,
                    started_event=tts_ready,
                )
            except RuntimeError as error:
                log_event("voice", "sarvam.failed", response_request_id, error=str(error))
            finally:
                tts_finished.set()
                finish_followup_window()

        def handle_speech_start() -> None:
            if speech_started.is_set():
                return
            speech_started.set()
            if not tts_finished.is_set():
                interrupted_tts.set()
            log_event(
                "voice",
                "followup.detected" if tts_finished.is_set() else "barge_in.detected",
                listen_request_id,
            )
            self.speaker.stop()

        speaker_thread = threading.Thread(
            target=speak_response,
            name="nixi-tts",
            daemon=True,
        )
        speaker_thread.start()
        tts_ready.wait(timeout=1)

        local_command = ""
        try:
            followup_audio = self._capture_utterance(
                timeout_seconds=300,
                stop_event=stop_listening,
                on_speech_start=handle_speech_start,
                barge_in_until=tts_finished,
            )
            local_command = self._local_transcript(followup_audio)
            if (
                followup_audio is not None
                and interrupted_tts.is_set()
                and voice_recognition.resembles_spoken_text(local_command, response_text)
            ):
                log_event("voice", "barge_in.echo_ignored", listen_request_id)
                print("Ignored speaker echo; listening for your command...", flush=True)
                self.segmenter.reset()
                followup_audio = self._capture_utterance(
                    timeout_seconds=self.voice.command_timeout_seconds,
                    stop_event=stop_listening,
                )
                local_command = self._local_transcript(followup_audio)
            next_command = (
                self.sarvam_transcriber.transcribe_pcm(
                    followup_audio,
                    listen_request_id,
                )
                if followup_audio is not None
                else ""
            )
        except RuntimeError as error:
            log_event("voice", "sarvam_stt.failed", listen_request_id, error=str(error))
            next_command = local_command
        finally:
            stop_listening.set()
            if speech_started.is_set():
                self.speaker.stop()
            speaker_thread.join(timeout=5)

        return next_command.strip(), listen_request_id

    def _local_transcript(self, audio: np.ndarray | None) -> str:
        """Produce a local fallback transcript without failing the conversation."""
        if audio is None:
            return ""
        try:
            return self.transcriber.transcribe(audio, self.voice.sample_rate).strip()
        except (RuntimeError, ValueError):
            return ""

    def _expire_command_window(self) -> None:
        if self.awaiting_command and time.monotonic() >= self.command_deadline:
            print("Listening timed out.", flush=True)
            self.awaiting_command = False
            self.popup.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the always-on local Nixi voice daemon.")
    parser.add_argument("--config", type=Path, help="path to nixi.toml")
    return parser.parse_args()


def main() -> None:
    load_environment()
    args = parse_args()
    try:
        daemon = NixiVoiceDaemon(args.config)
    except RuntimeError as error:
        raise SystemExit(f"nixi-voice: {error}") from error

    signal.signal(signal.SIGINT, daemon.stop)
    signal.signal(signal.SIGTERM, daemon.stop)
    daemon.run()


if __name__ == "__main__":
    main()
