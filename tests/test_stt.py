from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace

import aiohttp
import numpy as np

from app.voice.stt import SarvamRealtimeTranscriber


class FakeWebSocket:
    def __init__(self, messages: list[object] | None = None) -> None:
        self.messages = iter(messages or [])
        self.sent: list[dict[str, object]] = []

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> object:
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration from None

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)

    def exception(self) -> None:
        return None


class SarvamRealtimeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_final_transcript(self) -> None:
        websocket = FakeWebSocket(
            [
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"event": "transcript.partial", "text": "tell me"}),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"event": "transcript.final", "text": "tell me a joke"}
                    ),
                ),
            ]
        )

        transcript = await SarvamRealtimeTranscriber._receive_transcript(websocket)

        self.assertEqual(transcript, "tell me a joke")

    async def test_sends_raw_linear16_audio_and_end_event(self) -> None:
        websocket = FakeWebSocket()
        frame = np.array([1, -2, 3], dtype=np.int16)

        await SarvamRealtimeTranscriber._send_frames(
            websocket,
            iter([frame]),
            finite=True,
        )

        self.assertEqual(websocket.sent[0]["event"], "audio_input")
        encoded = str(websocket.sent[0]["audio"])
        self.assertEqual(base64.b64decode(encoded), frame.astype("<i2").tobytes())
        self.assertEqual(websocket.sent[1], {"event": "end"})


if __name__ == "__main__":
    unittest.main()
