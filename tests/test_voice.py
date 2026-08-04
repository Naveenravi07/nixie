from __future__ import annotations

import unittest

import numpy as np

from app.server.config import VoiceConfig
from app.voice.main import (
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


if __name__ == "__main__":
    unittest.main()
