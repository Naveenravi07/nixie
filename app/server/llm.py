"""Gemini conversation through Vertex AI Express Mode with 429 Retry & Search Fallback."""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from .config import LLMConfig


_CURRENT_INFORMATION_PATTERN = re.compile(
    r"\b(?:"
    r"weather|forecast|temperature|rain|storm|cyclone|humidity|air quality|"
    r"news|headlines?|latest|recent|currently|right now|today|tomorrow|yesterday|"
    r"live|score|match result|standings|schedule|traffic|road closure|"
    r"holiday|school closure|college closure|district collector|alert|warning|"
    r"price|stock|share price|market|crypto|bitcoin|exchange rate|"
    r"election|poll results?|current president|current prime minister|current ceo|"
    r"who is|what is the status|when is|where is"
    r")\b",
    re.IGNORECASE,
)


def _is_rate_limit_error(exception: BaseException) -> bool:
    """Check if the error is a 429 / RESOURCE_EXHAUSTED error."""
    error_str = str(exception)
    return "429" in error_str or "RESOURCE_EXHAUSTED" in error_str


class VertexChat:
    def __init__(self, config: LLMConfig) -> None:
        api_key = os.environ.get("GOOGLE_CLOUD_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "No Vertex AI Express Mode key configured. "
                "Set GOOGLE_CLOUD_API_KEY in .env."
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise RuntimeError("Google Gen AI SDK is missing; run `uv sync`.") from error

        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"

        self._api_key = api_key
        self._types = types
        client_options: dict[str, Any] = {
            "vertexai": True,
            "api_key": api_key,
            "http_options": types.HttpOptions(
                timeout=round(config.timeout_seconds * 1_000),
            ),
        }
        # An API key by itself selects Vertex Express Mode. Supplying a location
        # without a project instead builds requests for `projects/None`.
        if project:
            client_options.update(project=project, location=location)
        self._client = genai.Client(**client_options)
        self.config = config
        self.messages: list[dict[str, str]] = []
        self.lock = threading.Lock()
        print(
            f"Vertex AI Express ready: {config.model}.",
            flush=True,
        )

    def reply(self, user_message: str, request_id: str = "standalone") -> str:
        with self.lock:
            request_messages = [*self.messages, {"role": "user", "content": user_message}]
            response = self._complete(
                request_messages,
                use_google_search=self.should_use_google_search(user_message),
            )

            content = self._response_text(response)
            if not content:
                raise RuntimeError("Gemini returned an empty response.")

            self.messages.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "model", "content": content},
                ]
            )
            self._trim_history()
            return content

    def should_use_google_search(self, user_message: str) -> bool:
        """Use paid grounding only for prompts that need fresh world information."""
        if not self.config.google_search_enabled or not user_message:
            return False
        return bool(_CURRENT_INFORMATION_PATTERN.search(user_message))

    @retry(
        retry=retry_if_exception(_is_rate_limit_error),
        wait=wait_random_exponential(min=1, max=16),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _complete_with_retry(
        self,
        messages: list[dict[str, str]],
        config_options: dict[str, Any],
    ) -> Any:
        return self._client.models.generate_content(
            model=self.config.model,
            contents=[
                self._types.Content(
                    role=message["role"],
                    parts=[self._types.Part.from_text(text=message["content"])],
                )
                for message in messages
            ],
            config=self._types.GenerateContentConfig(**config_options),
        )

    def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        use_google_search: bool = False,
    ) -> Any:
        config_options: dict[str, Any] = {
            "system_instruction": self.config.system_prompt,
            "max_output_tokens": self.config.max_tokens,
        }

        # Safely parse thinking_level
        thinking = (self.config.thinking_level or "").strip().lower()
        if thinking and thinking != "off":
            config_options["thinking_config"] = self._types.ThinkingConfig(
                thinking_level=thinking.upper(),
            )

        if use_google_search:
            config_options["tools"] = [
                self._types.Tool(google_search=self._types.GoogleSearch())
            ]

        try:
            return self._complete_with_retry(messages, config_options)
        except Exception as error:
            # Fallback: If 429 rate limit is triggered by search grounding, strip tools & retry plain completion
            if use_google_search and _is_rate_limit_error(error):
                config_options.pop("tools", None)
                try:
                    return self._complete_with_retry(messages, config_options)
                except Exception as fallback_error:
                    error = fallback_error

            detail = self._safe_error_message(error)
            raise RuntimeError(f"Vertex AI request failed: {detail}") from error

    def _trim_history(self) -> None:
        message_limit = self.config.history_turns * 2
        self.messages = self.messages[-message_limit:]

    def _safe_error_message(self, error: Exception) -> str:
        message = str(error).replace(self._api_key, "[REDACTED]")
        message = re.sub(r"\s+", " ", message).strip()
        return f"{type(error).__name__}: {message or 'no provider details'}"[:600]

    @staticmethod
    def _response_text(response: Any) -> str:
        content = getattr(response, "text", None)
        return content.strip() if isinstance(content, str) else ""
