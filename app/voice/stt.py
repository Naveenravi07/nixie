"""Sarvam Saaras realtime command transcription."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Iterator
from collections.abc import Callable
from typing import Any

import aiohttp
import numpy as np

from app.event_log import log_event
from app.config import STTConfig


SARVAM_REALTIME_STT_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"


class SarvamRealtimeTranscriber:
    def __init__(self, config: STTConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.api_key = os.environ.get("SARVAM_API_KEY", "").strip()
        if config.enabled and not self.api_key:
            raise RuntimeError("SARVAM_API_KEY is required while Sarvam STT is enabled.")
        if config.enabled and sample_rate != 16_000:
            raise RuntimeError("Sarvam realtime STT currently requires Nixi audio at 16000 Hz.")

    def transcribe_live(
        self,
        frames: Iterator[np.ndarray],
        request_id: str,
        *,
        stop_event: Any | None = None,
        on_speech_start: Callable[[], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        return asyncio.run(
            self._transcribe(
                frames,
                request_id,
                finite=False,
                stop_event=stop_event,
                on_speech_start=on_speech_start,
                timeout_seconds=timeout_seconds,
            )
        )

    def transcribe_pcm(self, pcm: np.ndarray, request_id: str) -> str:
        if not self.config.enabled:
            return ""

        import io
        import wave

        started = time.perf_counter()
        log_event(
            "voice",
            "sarvam_stt.started",
            request_id,
            model="saaras:v3",
            language=self.config.language,
        )

        # REST API has a 30-second limit. Split long audio into chunks.
        max_samples = self.sample_rate * 30
        chunks: list[np.ndarray] = []
        for offset in range(0, pcm.size, max_samples):
            chunks.append(pcm[offset : offset + max_samples])

        transcripts: list[str] = []
        for chunk in chunks:
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(chunk.astype(np.int16).tobytes())
            wav_buf.seek(0)

            try:
                from sarvamai import SarvamAI

                client = SarvamAI(api_subscription_key=self.api_key)
                response = client.speech_to_text.transcribe(
                    file=wav_buf,
                    model="saaras:v3",
                    language_code=self.config.language,
                )
                text = str(response.transcript or "").strip()
                if text:
                    transcripts.append(text)
            except Exception as error:
                raise RuntimeError(f"Sarvam STT failed: {error}") from error

        transcript = " ".join(transcripts)
        log_event(
            "voice",
            "sarvam_stt.completed",
            request_id,
            duration_ms=round((time.perf_counter() - started) * 1000),
            transcript=transcript,
        )
        return transcript

    async def _transcribe(
        self,
        frames: Iterator[np.ndarray],
        request_id: str,
        *,
        finite: bool,
        stop_event: Any | None = None,
        on_speech_start: Callable[[], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        if not self.config.enabled:
            return ""

        started = time.perf_counter()
        log_event(
            "voice",
            "sarvam_stt.started",
            request_id,
            model=self.config.model,
            language=self.config.language,
        )
        params = {
            "language_code": self.config.language,
            "model": self.config.model,
            "stream_type": self.config.stream_type,
            "mode": self.config.mode,
            "endpointing": "manual",
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "threshold": str(self.config.threshold),
            "silence_duration_ms": str(self.config.silence_ms),
            "min_speech_duration_ms": str(self.config.min_speech_ms),
        }
        headers = {"Api-Subscription-Key": self.api_key}
        timeout = aiohttp.ClientTimeout(total=None, connect=5)

        try:
            async with asyncio.timeout(timeout_seconds or self.config.timeout_seconds):
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        SARVAM_REALTIME_STT_URL,
                        params=params,
                        headers=headers,
                        heartbeat=20,
                    ) as websocket:
                        producer = asyncio.create_task(
                            self._send_frames(websocket, frames, finite=finite)
                        )
                        receiver = asyncio.create_task(
                            self._receive_transcript(
                                websocket,
                                on_speech_start=on_speech_start,
                            )
                        )
                        stopper = (
                            asyncio.create_task(self._wait_for_stop(stop_event))
                            if stop_event is not None
                            else None
                        )
                        try:
                            if stopper is None:
                                transcript = await receiver
                            else:
                                done, _pending = await asyncio.wait(
                                    {receiver, stopper},
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                transcript = await receiver if receiver in done else ""
                        finally:
                            producer.cancel()
                            receiver.cancel()
                            if stopper is not None:
                                stopper.cancel()
                            await asyncio.gather(
                                producer,
                                receiver,
                                *([stopper] if stopper is not None else []),
                                return_exceptions=True,
                            )
        except TimeoutError as error:
            raise RuntimeError("Sarvam STT timed out waiting for speech.") from error
        except aiohttp.ClientError as error:
            raise RuntimeError(f"Sarvam STT connection failed: {error}.") from error

        transcript = transcript.strip()
        log_event(
            "voice",
            "sarvam_stt.completed",
            request_id,
            duration_ms=round((time.perf_counter() - started) * 1000),
            transcript=transcript,
        )
        return transcript

    @staticmethod
    async def _wait_for_stop(stop_event: Any) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.05)

    @staticmethod
    async def _send_frames(
        websocket: aiohttp.ClientWebSocketResponse,
        frames: Iterator[np.ndarray],
        *,
        finite: bool,
    ) -> None:
        while True:
            if finite:
                has_frame, frame = SarvamRealtimeTranscriber._next_frame(frames)
            else:
                has_frame, frame = await asyncio.to_thread(
                    SarvamRealtimeTranscriber._next_frame,
                    frames,
                )
            if not has_frame:
                if finite:
                    await websocket.send_json({"event": "end"})
                return
            assert frame is not None
            audio = frame.astype("<i2", copy=False).tobytes()
            await websocket.send_json(
                {
                    "event": "audio_input",
                    "audio": base64.b64encode(audio).decode("ascii"),
                }
            )

    @staticmethod
    def _next_frame(frames: Iterator[np.ndarray]) -> tuple[bool, np.ndarray | None]:
        try:
            return True, next(frames)
        except StopIteration:
            return False, None

    @staticmethod
    async def _receive_transcript(
        websocket: aiohttp.ClientWebSocketResponse,
        *,
        on_speech_start: Callable[[], None] | None = None,
    ) -> str:
        async for message in websocket:
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload: dict[str, Any] = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                event = str(payload.get("event", ""))
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                if event == "vad.speech_start" and on_speech_start is not None:
                    on_speech_start()
                if event == "transcript.final":
                    return str(
                        payload.get("text")
                        or payload.get("transcript")
                        or data.get("text")
                        or data.get("transcript")
                        or ""
                    )
                if event == "error":
                    detail = payload.get("message") or data.get("message") or "unknown error"
                    raise RuntimeError(f"Sarvam STT rejected the stream: {detail}")
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(f"Sarvam STT WebSocket failed: {websocket.exception()}")
            elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}:
                break
        raise RuntimeError("Sarvam STT closed before returning a transcript.")
