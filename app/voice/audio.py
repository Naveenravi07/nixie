"""Audio segmentation and continuous PipeWire capture for Nixi voice."""

from __future__ import annotations

import queue
import subprocess
import threading
from collections import deque
from collections.abc import Iterator

import numpy as np

from app.config import VoiceConfig


FRAME_MS = 50


class UtteranceSegmenter:
    """Split PCM frames into utterances using an adaptive RMS threshold."""

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

    def push(
        self,
        frame: np.ndarray,
        *,
        threshold: float | None = None,
        speech_start_ms: int | None = None,
        update_noise_floor: bool = True,
    ) -> np.ndarray | None:
        rms = self._rms(frame)
        if self.calibration_frames:
            self.calibration_samples.append(rms)
            self.calibration_frames -= 1
            if self.calibration_frames == 0:
                self.noise_floor = float(np.median(self.calibration_samples))
                self.pre_roll.clear()
            return None

        speech_threshold = threshold if threshold is not None else self.effective_threshold
        is_speech = rms >= speech_threshold
        required_start_ms = (
            speech_start_ms if speech_start_ms is not None else self.config.speech_start_ms
        )

        if not self.active_frames:
            self.pre_roll.append(frame)
            if not is_speech:
                self.start_frames = 0
                if update_noise_floor:
                    self._update_noise_floor(rms)
                return None
            self.start_frames += 1
            if self.start_frames * self.frame_ms < required_start_ms:
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
        self._reset_active()
        return utterance

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        samples = frame.astype(np.float32)
        return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0

    def _reset_active(self) -> None:
        self.active_frames = []
        self.start_frames = 0
        self.speech_frames = 0
        self.silent_frames = 0

    def reset(self) -> None:
        """Forget buffered speech while preserving the calibrated noise floor."""
        self._reset_active()
        self.pre_roll.clear()

    def _update_noise_floor(self, rms: float) -> None:
        if self.noise_floor == 0:
            self.noise_floor = rms
        else:
            self.noise_floor = (self.noise_floor * 0.98) + (rms * 0.02)


class PipeWireRecorder:
    """Continuously read raw mono PCM from ``pw-record`` in the background."""

    def __init__(self, config: VoiceConfig, frame_ms: int = FRAME_MS) -> None:
        self.config = config
        self.frame_ms = frame_ms
        self.frame_samples = config.sample_rate * frame_ms // 1000
        self.frame_bytes = self.frame_samples * np.dtype(np.int16).itemsize
        self.process: subprocess.Popen[bytes] | None = None
        self.frame_queue: queue.Queue[np.ndarray | None] = queue.Queue()
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
            f"{self.frame_ms}ms",
        ]
        if self.config.microphone_target:
            command.extend(["--target", self.config.microphone_target])
        command.append("-")

        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE)
        except FileNotFoundError as error:
            raise RuntimeError("pw-record is required; install PipeWire audio tools") from error

        self.thread = threading.Thread(
            target=self._capture,
            name="nixi-microphone",
            daemon=True,
        )
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

    def discard_pending(self) -> None:
        """Discard audio captured while Nixi was thinking or speaking."""
        while True:
            try:
                frame = self.frame_queue.get_nowait()
            except queue.Empty:
                return
            if frame is None:
                self.frame_queue.put_nowait(None)
                return

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
