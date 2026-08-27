"""Gemini conversation through Vertex AI Express Mode with 429 Retry & Search Fallback."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import ActionConfig, LLMConfig
from app.server.web_search import WebSearcher


@dataclass
class ToolCall:
    """Returned by reply_with_tools() when the LLM wants to run an action."""
    name: str
    args: dict[str, Any]


_CURRENT_INFORMATION_PATTERN = re.compile(
    r"\b(?:"
    r"weather|forecast|temperature|rain|storm|cyclone|humidity|air quality|"
    r"news|headlines?|latest|recent|currently|right now|today|tomorrow|yesterday|"
    r"live|score|match result|standings|schedule|traffic|road closure|"
    r"holiday|school closure|college closure|district collector|alert|warning|"
    r"price|stock|share price|market|crypto|bitcoin|exchange rate|"
    r"election|poll results?|current president|current prime minister|current ceo|"
    r"who (?:is|was|are|leaked|hacked|breached|stole|claimed)|"
    r"what (?:happened|is going on)|"
    r"what is the status|when is|where is|"
    r"what(?:'s| is) (?:the )?(?:date|time|day)|date today|current time|what time|"
    r"leaked|hacked|breach|"
    r"chief minister|prime minister|president|governor|"
    r"search(?: the)? web|search online|search it|web search|browse(?: the)? web|"
    r"look it up|look up online|find online"
    r")\b",
    re.IGNORECASE,
)

_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")


def _is_rate_limit_error(exception: BaseException) -> bool:
    """Check if the error is a 429 / RESOURCE_EXHAUSTED error."""
    error_str = str(exception)
    return "429" in error_str or "RESOURCE_EXHAUSTED" in error_str


class VertexChat:
    def __init__(
        self,
        config: LLMConfig,
        searcher: WebSearcher | None = None,
    ) -> None:
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
        self._searcher = searcher
        self._search_active = False
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

    def reply_with_tools(
        self,
        user_message: str,
        actions: dict[str, ActionConfig],
        request_id: str = "standalone",
    ) -> tuple[str, ToolCall | None]:
        """
        Send user_message to the LLM with action function declarations.
        Returns (spoken_response, tool_call_or_None).

        If the LLM decides an action is needed it returns a ToolCall.
        The spoken_response is always a natural language string suitable for TTS.
        """
        tools = _build_function_declarations(actions)
        # Current-information questions get a live DDGS search. Function calls
        # and paid Google Search grounding cannot coexist, so the raw results
        # are injected as context instead and the model answers from them.
        search_context = self._search_context(user_message)

        with self.lock:
            content = user_message
            if search_context:
                content = f"{user_message}\n\n{search_context}"
            request_messages = [*self.messages, {"role": "user", "content": content}]

            response = self._complete(
                request_messages,
                use_google_search=False,   # tool calls and search grounding cannot coexist
                tools=tools if tools else None,
            )

            # Check if the LLM returned a function call
            tool_call = _extract_tool_call(response)

            if tool_call is not None:
                # LLM wants to run an action — build a short spoken confirmation
                action = actions.get(tool_call.name)
                spoken = f"Sure, running {tool_call.name.replace('_', ' ')}."
                if action and action.description:
                    spoken = action.description.split(".")[0] + "."
                self.messages.extend([
                    {"role": "user", "content": user_message},
                    {"role": "model", "content": spoken},
                ])
            else:
                spoken = self._response_text(response)
                if not spoken:
                    raise RuntimeError("Gemini returned an empty response.")
                self.messages.extend([
                    {"role": "user", "content": user_message},
                    {"role": "model", "content": spoken},
                ])

            self._trim_history()
            return spoken, tool_call

    def should_use_google_search(self, user_message: str) -> bool:
        """Detect prompts that need fresh world information.

        Drives both paid Vertex grounding (reply path) and the no-key live
        web-search context (reply_with_tools path). Only runs when enabled.
        """
        if not self.config.google_search_enabled or not user_message:
            return False
        if _CURRENT_INFORMATION_PATTERN.search(user_message):
            return True
        recent_year = date.today().year - 1
        return any(int(year) >= recent_year for year in _YEAR_PATTERN.findall(user_message))

    def _search_context(self, user_message: str) -> str:
        """Run a live no-key web search and format a grounded context block."""
        if not self.config.google_search_enabled or not user_message:
            return ""
        if not self._search_active and not self.should_use_google_search(user_message):
            return ""
        self._search_active = True
        # If the user is asking to search (e.g. "search online", "look it up"),
        # use the last real question from history as the search query instead.
        query = self._resolve_search_query(user_message)
        now = datetime.now(timezone.utc)
        date_line = now.strftime("Today's date: %A, %B %d, %Y. Current time: %H:%M UTC.")
        try:
            searcher = self._searcher or WebSearcher()
            results = searcher.search(query)
        except Exception as error:
            print(f"web search skipped: {type(error).__name__}", flush=True)
            return f"{date_line}\n"
        if not results:
            return f"{date_line}\n"
        lines = [
            "CRITICAL: The following web search results are the source of truth. "
            "Answer the user's question ONLY using these results, not your training data.",
            date_line,
            "",
        ]
        for index, result in enumerate(results, 1):
            lines.append(f"{index}. {result.title}")
            lines.append(f"   Source: {result.url}")
            lines.append(f"   {result.snippet}")
            lines.append("")
        return "\n".join(lines)

    _SEARCH_REQUEST_PATTERN = re.compile(
        r"\bsearch(?:\s+(?:online|the\s+web|it))?|look\s+(?:it\s+)?up|browse(?:\s+the)?\s+web\b",
        re.IGNORECASE,
    )

    def _resolve_search_query(self, user_message: str) -> str:
        """Build a good DDGS query from the user message and conversation history.

        If the message is a 'search online' request, find the real question.
        If the query contains pronouns or is too short, enrich it with the
        last model response for better search results.
        """
        if not self._SEARCH_REQUEST_PATTERN.search(user_message):
            base = user_message
        else:
            # User said "search online" — find the real question in history
            base = user_message
            for msg in reversed(self.messages):
                if msg["role"] != "user":
                    continue
                candidate = msg["content"]
                if not self._SEARCH_REQUEST_PATTERN.search(candidate):
                    base = candidate
                    break

        # If the query is short or pronoun-heavy, try to enrich it with the
        # last model response for better DDGS results.
        pronouns = re.compile(r"\b(he|she|it|they|his|her|its|their|this|that|him|them)\b", re.IGNORECASE)
        if pronouns.search(base):
            for msg in reversed(self.messages):
                if msg["role"] == "model" and msg["content"]:
                    snippet = msg["content"][:200]
                    base = f"{base} {snippet}"
                    break

        return base

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
        tools: list[dict[str, Any]] | None = None,
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
        elif tools:
            # Add tool definitions to the LLM config
            config_options["tools"] = [{"function_declarations": tools}]

        try:
            return self._complete_with_retry(messages, config_options)
        except Exception as error:
            # Fallback: If 429 rate limit is triggered by search grounding, strip tools & retry plain completion
            if (use_google_search or tools) and _is_rate_limit_error(error):
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_function_declarations(actions: dict[str, ActionConfig]) -> list[dict[str, Any]]:
    """
    Convert ActionConfig entries into Gemini function declarations.
    Inspects the command template for {path} and {amount} placeholders
    and exposes them as typed parameters so the LLM can extract them
    from the user's natural language message.
    """
    declarations = []
    for name, action in actions.items():
        if not action.command:
            continue

        description = action.description or f"Runs the {name.replace('_', ' ')} action."
        properties: dict[str, Any] = {}
        required: list[str] = []

        if "{path}" in action.command:
            properties["path"] = {
                "type": "string",
                "description": "Full filesystem path required for this action.",
            }
            required.append("path")

        if "{amount}" in action.command:
            properties["amount"] = {
                "type": "string",
                "description": (
                    "The amount for this action, e.g. '5%+', '20%-', '30%+'. "
                    "Read this from the user's message. "
                    "If not specified, use '5%+' for increase actions and '5%-' for decrease actions."
                ),
            }
            required.append("amount")

        declarations.append({
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })

    return declarations


def _extract_tool_call(response: Any) -> ToolCall | None:
    """
    Inspect a Gemini API response object for a function call.
    Returns a ToolCall if found, None otherwise.
    """
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    name = getattr(fc, "name", None)
                    args = dict(getattr(fc, "args", {}) or {})
                    if name:
                        return ToolCall(name=name, args=args)
    except Exception:
        pass
    return None
