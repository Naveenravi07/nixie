from __future__ import annotations

import unittest

from app.voice.recognition import normalize_transcript


class TranscriptTests(unittest.TestCase):
    def test_normalizes_whisper_punctuation(self) -> None:
        self.assertEqual(normalize_transcript("  Hey, Nixi!  "), "hey nixi")

    def test_normalizes_mixed_case(self) -> None:
        self.assertEqual(normalize_transcript("Set Volume to 50%"), "set volume to 50")


if __name__ == "__main__":
    unittest.main()
