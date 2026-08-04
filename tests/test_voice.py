from __future__ import annotations

import unittest
import threading
from types import SimpleNamespace

import numpy as np

from app.server.config import VoiceConfig
from app.voice.main import (
    NixiVoiceDaemon,
    UtteranceSegmenter,
    extract_wake_command,
    normalize_transcript,
)


class WakePhraseTests(unittest.TestCase):
    def test_normalizes_whisper_punctuation(self) -> None:
        self.assertEqual(normalize_transcript("  Hey, Nixi!  "), "hey nixi")

    def test_extracts_inline_command(self) -> None:
        woke, command = extract_wake_command(
            "Hey, Nixie! Take a screenshot.",
            ("hey nixi", "hey nixie"),
        )
        self.assertTrue(woke)
        self.assertEqual(command, "take a screenshot")

    def test_rejects_phrase_inside_another_word(self) -> None:
        woke, _command = extract_wake_command("they hey nixies often", ("hey nixi",))
        self.assertFalse(woke)

    def test_accepts_observed_whisper_phonetic_variant(self) -> None:
        woke, command = extract_wake_command(
            "Hey, Nick see. Hello.",
            VoiceConfig.wake_phrases,
        )
        self.assertTrue(woke)
        self.assertEqual(command, "hello")


class SegmenterTests(unittest.TestCase):
    def test_emits_speech_after_silence(self) -> None:
        config = VoiceConfig(
            speech_threshold=100,
            silence_ms=200,
            speech_start_ms=100,
            min_speech_ms=200,
            calibration_ms=0,
        )
        segmenter = UtteranceSegmenter(config, frame_ms=100)
        silence = np.zeros(1600, dtype=np.int16)
        speech = np.full(1600, 1000, dtype=np.int16)

        frames = [silence, speech, speech, silence, silence]
        results = [segmenter.push(frame) for frame in frames]

        self.assertIsNone(results[-2])
        self.assertIsNotNone(results[-1])
        assert results[-1] is not None
        self.assertGreaterEqual(results[-1].size, 4 * 1600)

    def test_ignores_short_noise(self) -> None:
        config = VoiceConfig(
            speech_threshold=100,
            silence_ms=200,
            speech_start_ms=100,
            min_speech_ms=300,
            calibration_ms=0,
        )
        segmenter = UtteranceSegmenter(config, frame_ms=100)
        speech = np.full(1600, 1000, dtype=np.int16)
        silence = np.zeros(1600, dtype=np.int16)

        self.assertIsNone(segmenter.push(speech))
        self.assertIsNone(segmenter.push(silence))
        self.assertIsNone(segmenter.push(silence))


class ConversationTests(unittest.TestCase):
    def test_barge_in_stops_speaker_and_returns_followup(self) -> None:
        class FakeSpeaker:
            def __init__(self) -> None:
                self.stopped = False
                self.released = threading.Event()

            def speak(self, _text: str, request_id: str, started_event: object) -> bool:
                started_event.set()
                self.released.wait(timeout=1)
                return False

            def stop(self) -> None:
                self.stopped = True
                self.released.set()

        class FakeTranscriber:
            def transcribe_pcm(self, _audio: object, _request_id: str) -> str:
                return "Here is my follow-up"

        daemon = object.__new__(NixiVoiceDaemon)
        daemon.voice = SimpleNamespace(command_timeout_seconds=0.01)
        daemon.speaker = FakeSpeaker()
        daemon.sarvam_transcriber = FakeTranscriber()
        daemon.recorder = SimpleNamespace(frames=lambda: iter(()))
        daemon._capture_utterance = lambda **options: (
            options["on_speech_start"](),
            np.ones(1600, dtype=np.int16),
        )[1]

        command, request_id = daemon._speak_and_listen("A long answer", "response-id")

        self.assertEqual(command, "Here is my follow-up")
        self.assertTrue(request_id)
        self.assertTrue(daemon.speaker.stopped)


if __name__ == "__main__":
    unittest.main()
