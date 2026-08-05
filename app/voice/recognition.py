"""Wake-phrase normalization and local Whisper transcription."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np

from app.config import VoiceConfig


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


def resembles_spoken_text(captured: str, spoken: str) -> bool:
    """Return whether a microphone transcript is probably speaker echo."""
    captured_words = normalize_transcript(captured).split()
    spoken_words = normalize_transcript(spoken).split()
    if not captured_words or not spoken_words:
        return not captured_words

    captured_text = " ".join(captured_words)
    spoken_text = " ".join(spoken_words)
    if captured_text in spoken_text:
        return True

    window_size = len(captured_words)
    windows = (
        " ".join(spoken_words[index : index + window_size])
        for index in range(max(1, len(spoken_words) - window_size + 1))
    )
    return any(
        SequenceMatcher(None, captured_text, window).ratio() >= 0.70
        for window in windows
    )


class WhisperTranscriber:
    """Small local Whisper adapter used for wake-phrase recognition."""

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
