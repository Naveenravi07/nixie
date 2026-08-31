"""Transcript normalization."""

from __future__ import annotations

import re


def normalize_transcript(text: str) -> str:
    """Normalize punctuation and spacing without changing spoken words."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())
