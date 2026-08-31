"""Continuous PipeWire capture for Nixi voice."""

from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Iterator

import numpy as np

from app.config import VoiceConfig


FRAME_MS = 50


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
