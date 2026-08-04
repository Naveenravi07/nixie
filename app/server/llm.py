"""Gemini conversation through a rotating LiteLLM credential pool."""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from app.event_log import log_event

from .config import LLMConfig


MAX_GOOGLE_KEYS = 10


def discover_google_api_keys() -> list[str]:
    """Read unique numbered Gemini API keys without exposing their values."""
    keys: list[str] = []
    for index in range(1, MAX_GOOGLE_KEYS + 1):
        key = os.environ.get(f"GOOGLE_API_KEY{index}", "").strip()
        if key and key not in keys:
            keys.append(key)
    fallback = os.environ.get("GOOGLE_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys


class GeminiChat:
    def __init__(self, config: LLMConfig) -> None:
        keys = discover_google_api_keys()
        if not keys:
            raise RuntimeError(
                "No Gemini key found. Set GOOGLE_API_KEY1 (and optionally GOOGLE_API_KEY2…10)."
            )

        try:
            from litellm import completion
        except ImportError as error:
            raise RuntimeError("LiteLLM is missing; run `uv sync`.") from error

        self._completion = completion
        self._api_keys = tuple(keys)
        self._next_key_index = 0
        self._cooldown_until: dict[int, float] = {}
        self.config = config
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": config.system_prompt}
        ]
        self.lock = threading.Lock()
        print(
            f"Gemini ready: {config.model} with {len(keys)} rotating API key(s).",
            flush=True,
        )

    def reply(self, user_message: str, request_id: str = "standalone") -> str:
        with self.lock:
            request_messages = [*self.messages, {"role": "user", "content": user_message}]
            response = self._complete_with_rotation(request_messages, request_id)

            content = self._response_text(response)
            if not content:
                raise RuntimeError("Gemini returned an empty response.")

            self.messages.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": content},
                ]
            )
            self._trim_history()
            return content

    def _complete_with_rotation(
        self, messages: list[dict[str, str]], request_id: str
    ) -> Any:
        last_error: Exception | None = None
        now = time.monotonic()
        indexes = [
            (self._next_key_index + offset) % len(self._api_keys)
            for offset in range(len(self._api_keys))
        ]
        available = [
            index for index in indexes if self._cooldown_until.get(index, 0) <= now
        ]
        # If every key is cooling down, retry the pool instead of requiring a
        # restart; a transient provider failure may already have cleared.
        candidates = available or indexes

        for index in candidates:
            try:
                response = self._completion(
                    model=f"gemini/{self.config.model}",
                    api_key=self._api_keys[index],
                    messages=messages,
                    max_tokens=self.config.max_tokens,
                    timeout=self.config.timeout_seconds,
                    reasoning_effort=self.config.thinking_level,
                )
            except Exception as error:
                last_error = error
                self._cooldown_until[index] = now + self.config.cooldown_seconds
                detail = self._safe_error_message(error)
                log_event(
                    "server",
                    "gemini.key_failed",
                    request_id,
                    key_number=index + 1,
                    cooldown_seconds=self.config.cooldown_seconds,
                    error=detail,
                )
                continue

            self._cooldown_until.pop(index, None)
            self._next_key_index = (index + 1) % len(self._api_keys)
            return response

        assert last_error is not None
        detail = self._safe_error_message(last_error)
        raise RuntimeError(
            f"Gemini failed after trying every available API key: {detail}"
        ) from last_error

    def _trim_history(self) -> None:
        message_limit = self.config.history_turns * 2
        self.messages = [self.messages[0], *self.messages[1:][-message_limit:]]

    def _safe_error_message(self, error: Exception) -> str:
        message = str(error)
        for api_key in self._api_keys:
            message = message.replace(api_key, "[REDACTED]")
        message = re.sub(r"\s+", " ", message).strip()
        return f"{type(error).__name__}: {message or 'no provider details'}"[:600]

    @staticmethod
    def _response_text(response: Any) -> str:
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", "")).strip()
                for part in content
                if isinstance(part, dict) and part.get("text")
            ).strip()
        return ""
