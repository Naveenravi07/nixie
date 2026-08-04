#!/usr/bin/env python3
"""Always-on PipeWire microphone listener with local Whisper transcription."""

from __future__ import annotations

import argparse
import json
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from app.server.config import VoiceConfig, load_config


REPO_ROOT = Path(__file__).resolve().parents[2]
FRAME_MS = 50


def normalize_transcript(text: str) -> str:
    """Normalize punctuation and spacing without changing spoken words."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def extract_wake_command(text: str, wake_phrases: tuple[str, ...]) -> tuple[bool, str]:
    """Return whether a wake phrase occurred and any words spoken after it."""
    normalized = normalize_transcript(text)
    for phrase in sorted(wake_phrases, key=len, reverse=True):
        wake_phrase = normalize_transcript(phrase)
        match = re.search(rf"(?:^|\s){re.escape(wake_phrase)}(?:\s|$)", normalized)
        if match is not None:
            return True, normalized[match.end() :].strip()
    return False, ""


class UtteranceSegmenter:
    """Split PCM frames into utterances using an RMS speech threshold."""

    def __init__(self, config: VoiceConfig, frame_ms: int = FRAME_MS) -> None:
        self.config = config
        self.frame_ms = frame_ms
        self.pre_roll: deque[np.ndarray] = deque(maxlen=max(1, 300 // frame_ms))
        self.active_frames: list[np.ndarray] = []
        self.start_frames = 0
        self.speech_frames = 0
        self.silent_frames = 0
        self.noise_floor = 0.0
        self.calibration_samples: list[float] = []
        self.calibration_frames = max(0, config.calibration_ms // frame_ms)

    @property
    def calibrated(self) -> bool:
        return self.calibration_frames == 0

    @property
    def effective_threshold(self) -> float:
        adaptive = self.noise_floor * self.config.adaptive_noise_ratio
        return max(float(self.config.speech_threshold), adaptive)

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        rms = self._rms(frame)
        if self.calibration_frames:
            self.calibration_samples.append(rms)
            self.calibration_frames -= 1
            if self.calibration_frames == 0:
                self.noise_floor = float(np.median(self.calibration_samples))
                self.pre_roll.clear()
            return None

        is_speech = rms >= self.effective_threshold

        if not self.active_frames:
            self.pre_roll.append(frame)
            if not is_speech:
                self.start_frames = 0
                self._update_noise_floor(rms)
                return None
            self.start_frames += 1
            if self.start_frames * self.frame_ms < self.config.speech_start_ms:
                return None
            self.active_frames = list(self.pre_roll)
            self.pre_roll.clear()
            self.speech_frames = self.start_frames
            self.silent_frames = 0
            return None

        self.active_frames.append(frame)
        if is_speech:
            self.speech_frames += 1
            self.silent_frames = 0
        else:
            self.silent_frames += 1

        long_silence = self.silent_frames * self.frame_ms >= self.config.silence_ms
        too_long = len(self.active_frames) * self.frame_ms >= (
            self.config.max_utterance_seconds * 1000
        )
        if not long_silence and not too_long:
            return None

        enough_speech = self.speech_frames * self.frame_ms >= self.config.min_speech_ms
        utterance = np.concatenate(self.active_frames) if enough_speech else None
        self._reset()
        return utterance

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        samples = frame.astype(np.float32)
        return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0

    def _reset(self) -> None:
        self.active_frames = []
        self.start_frames = 0
        self.speech_frames = 0
        self.silent_frames = 0

    def _update_noise_floor(self, rms: float) -> None:
        if self.noise_floor == 0:
            self.noise_floor = rms
        else:
            self.noise_floor = (self.noise_floor * 0.98) + (rms * 0.02)


class PipeWireRecorder:
    """Continuously read raw mono PCM from pw-record on a background thread."""

    def __init__(self, config: VoiceConfig, frame_ms: int = FRAME_MS) -> None:
        self.config = config
        self.frame_samples = config.sample_rate * frame_ms // 1000
        self.frame_bytes = self.frame_samples * np.dtype(np.int16).itemsize
        self.process: subprocess.Popen[bytes] | None = None
        self.frame_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=300)
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        command = [
            "pw-record",
            "--raw",
            "--format",
            "s16",
            "--rate",
            str(self.config.sample_rate),
            "--channels",
            "1",
            "--latency",
            f"{FRAME_MS}ms",
        ]
        if self.config.microphone_target:
            command.extend(["--target", self.config.microphone_target])
        command.append("-")

        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE)
        except FileNotFoundError as error:
            raise RuntimeError("pw-record is required; install PipeWire audio tools") from error

        self.thread = threading.Thread(target=self._capture, name="nixi-microphone", daemon=True)
        self.thread.start()

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            frame = self.frame_queue.get()
            if frame is None:
                return
            yield frame

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _capture(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        pending = bytearray()
        while self.process.poll() is None:
            chunk = self.process.stdout.read(self.frame_bytes - len(pending))
            if not chunk:
                break
            pending.extend(chunk)
            if len(pending) < self.frame_bytes:
                continue
            frame = np.frombuffer(pending, dtype=np.int16).copy()
            pending.clear()
            self._put_frame(frame)
        self._put_frame(None)

    def _put_frame(self, frame: np.ndarray | None) -> None:
        try:
            self.frame_queue.put_nowait(frame)
        except queue.Full:
            self.frame_queue.get_nowait()
            self.frame_queue.put_nowait(frame)


class WhisperTranscriber:
    def __init__(self, config: VoiceConfig) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "faster-whisper is not installed; run the voice dependency setup from README.md"
            ) from error

        print(f"Loading local Whisper model {config.model!r} (CPU int8)...", flush=True)
        self.language = config.language or None
        self.hotwords = ", ".join(config.wake_phrases[:2])
        self.model = WhisperModel(config.model, device="cpu", compute_type="int8")

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        if sample_rate != 16_000:
            raise ValueError("Whisper input must currently use a 16000 Hz sample rate")
        audio = pcm.astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            hotwords=self.hotwords,
            no_repeat_ngram_size=3,
            repetition_penalty=1.1,
            temperature=0.0,
            without_timestamps=True,
            vad_filter=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class PopupController:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.popup.launcher"],
            cwd=REPO_ROOT,
        )

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


class NixiVoiceDaemon:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config = load_config(config_path)
        self.voice = self.config.voice
        self.segmenter = UtteranceSegmenter(self.voice)
        self.recorder = PipeWireRecorder(self.voice)
        self.popup = PopupController()
        self.transcriber = WhisperTranscriber(self.voice)
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

        woke, inline_command = extract_wake_command(transcript, self.voice.wake_phrases)
        if not self.awaiting_command:
            if not woke:
                return
            self.popup.open()
            if inline_command:
                self._process_command(inline_command)
            else:
                self.awaiting_command = True
                self.command_deadline = time.monotonic() + self.voice.command_timeout_seconds
            return

        command = inline_command if woke and inline_command else normalize_transcript(transcript)
        if command:
            self._process_command(command)

    def _process_command(self, command: str) -> None:
        self.awaiting_command = False
        print(f"Command: {command}", flush=True)
        payload = json.dumps({"message": command}).encode("utf-8")
        request = Request(
            f"http://{self.config.server.host}:{self.config.server.port}/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
            print(f"Nixi: {result.get('response', 'Command processed.')}", flush=True)
        except HTTPError as error:
            print(f"Nixi server rejected the command: HTTP {error.code}", file=sys.stderr)
        except URLError as error:
            print(
                f"Cannot reach the Nixi server at {request.full_url}: {error.reason}",
                file=sys.stderr,
            )
        finally:
            self.popup.close()

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
