from __future__ import annotations

import json
import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import LLMConfig, TTSConfig
from app.server.llm import VertexChat
from app.server.web_search import SearchResult
from app.voice.tts import SarvamSpeaker


class VertexChatTests(unittest.TestCase):
    def test_calls_vertex_express_with_configured_api_key(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="Hello there.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client) as client_class,
        ):
            chat = VertexChat(LLMConfig())
            self.assertEqual(chat.reply("Hello"), "Hello there.")

        self.assertEqual(client_class.call_args.kwargs["vertexai"], True)
        self.assertEqual(client_class.call_args.kwargs["api_key"], "vertex-key")
        self.assertNotIn("location", client_class.call_args.kwargs)
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.5-flash-lite")
        self.assertEqual(request["contents"][0].role, "user")
        self.assertEqual(request["contents"][0].parts[0].text, "Hello")
        self.assertIsNone(request["config"].tools)

    def test_enables_google_search_only_for_current_information(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="It may rain.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig())
            chat.reply("Will it rain in Ernakulam tomorrow?")

        request_config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(len(request_config.tools), 1)
        self.assertIsNotNone(request_config.tools[0].google_search)

    def test_explicit_web_search_request_enables_grounding(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="Search result.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig())
            chat.reply("Please search it online.")

        request_config = client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNotNone(request_config.tools[0].google_search)

    def test_recent_release_year_enables_grounding(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="Search result.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig())
            chat.reply(f"Tell me about the movie released in {date.today().year}.")

        request_config = client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNotNone(request_config.tools[0].google_search)

    def test_uses_project_and_location_together_when_configured(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="Hello.")

        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_CLOUD_API_KEY": "vertex-key",
                    "GOOGLE_CLOUD_PROJECT": "nixi-project",
                    "GOOGLE_CLOUD_LOCATION": "global",
                },
                clear=True,
            ),
            patch("google.genai.Client", return_value=client) as client_class,
        ):
            VertexChat(LLMConfig()).reply("Hello")

        self.assertEqual(client_class.call_args.kwargs["project"], "nixi-project")
        self.assertEqual(client_class.call_args.kwargs["location"], "global")

    def test_google_search_can_be_disabled(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="I cannot check.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(google_search_enabled=False))
            chat.reply("What is the latest news?")

        request_config = client.models.generate_content.call_args.kwargs["config"]
        self.assertIsNone(request_config.tools)

    def test_requires_a_vertex_express_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GOOGLE_CLOUD_API_KEY"):
                VertexChat(LLMConfig())

    def test_provider_errors_include_details_without_exposing_key(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError(
            "quota failure for vertex-key"
        )

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig())
            with self.assertRaisesRegex(RuntimeError, "quota failure for \\[REDACTED\\]"):
                chat.reply("Hello")

    def test_reply_with_tools_injects_live_web_search_context_for_current_information(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(
            text="Pinarayi Vijayan is the Chief Minister of Kerala as of 2026."
        )
        searcher = MagicMock()
        searcher.search.return_value = [
            SearchResult(
                title="Kerala Chief Minister latest",
                url="https://example.com/kerala",
                snippet="Live snippet naming the current Chief Minister of Kerala.",
            )
        ]

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(), searcher=searcher)
            spoken, tool_call = chat.reply_with_tools(
                "Who is the current Chief Minister of Kerala?", {}
            )

        searcher.search.assert_called_once_with(
            "Who is the current Chief Minister of Kerala?"
        )
        request = client.models.generate_content.call_args.kwargs
        parts_text = " ".join(
            part.text for content in request["contents"] for part in content.parts
        )
        self.assertIn("CRITICAL", parts_text)
        self.assertIn("source of truth", parts_text)
        self.assertIn("Today's date:", parts_text)
        self.assertIn("Kerala Chief Minister latest", parts_text)
        self.assertIn("https://example.com/kerala", parts_text)
        self.assertIsNone(request["config"].tools)
        self.assertIsNone(tool_call)
        self.assertIn("Chief Minister", spoken)

    def test_reply_with_tools_skips_search_for_ordinary_commands(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="Sure.")
        searcher = MagicMock()

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(), searcher=searcher)
            chat.reply_with_tools("Switch on the desk light.", {})

        searcher.search.assert_not_called()

    def test_search_active_persists_in_session(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="OK.")
        searcher = MagicMock()
        searcher.search.return_value = [
            SearchResult(title="Result", url="https://x.com", snippet="info.")
        ]

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(), searcher=searcher)
            # First call triggers search
            chat.reply_with_tools("Who is the CM?", {})
            self.assertTrue(chat._search_active)
            searcher.search.assert_called_once()

            # Second call: not a heuristic match, but _search_active is True
            chat.reply_with_tools("How old is he?", {})
            self.assertEqual(searcher.search.call_count, 2)

    def test_search_active_resets_per_session(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="OK.")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig())
            # No search triggered, _search_active stays False
            chat.reply_with_tools("Switch on the desk light.", {})
            self.assertFalse(chat._search_active)

    def test_reply_with_tools_falls_back_gracefully_when_search_fails(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(
            text="I could not look that up."
        )
        searcher = MagicMock()
        searcher.search.side_effect = RuntimeError("ddgs unavailable")

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(), searcher=searcher)
            spoken, tool_call = chat.reply_with_tools("What is the latest news?", {})

        self.assertEqual(spoken, "I could not look that up.")
        self.assertIsNone(tool_call)

    def test_reply_with_tools_persists_search_context_in_history(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(
            text="V.D. Satheesan is the Chief Minister."
        )
        searcher = MagicMock()
        searcher.search.return_value = [
            SearchResult(
                title="Kerala CM 2026",
                url="https://example.com/cm",
                snippet="V.D. Satheesan is the new CM.",
            )
        ]

        with (
            patch.dict(os.environ, {"GOOGLE_CLOUD_API_KEY": "vertex-key"}, clear=True),
            patch("google.genai.Client", return_value=client),
        ):
            chat = VertexChat(LLMConfig(), searcher=searcher)
            chat.reply_with_tools("Who is the CM of Kerala?", {})

        # History should contain the original user message, not the augmented content
        self.assertEqual(len(chat.messages), 2)
        self.assertEqual(chat.messages[0]["content"], "Who is the CM of Kerala?")


class SarvamTTSTests(unittest.TestCase):
    def test_sends_bulbul_request_without_exposing_key_in_payload(self) -> None:
        response = MagicMock()
        urlopen_result = MagicMock()
        urlopen_result.__enter__.return_value = response

        with (
            patch.dict(os.environ, {"SARVAM_API_KEY": "sarvam-secret"}, clear=True),
            patch("app.voice.tts.urlopen", return_value=urlopen_result) as mocked_urlopen,
            patch.object(SarvamSpeaker, "_play_stream") as mocked_play,
        ):
            speaker = SarvamSpeaker(TTSConfig())
            speaker.speak("Hello from Nixi")

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "bulbul:v3")
        self.assertEqual(payload["target_language_code"], "en-IN")
        self.assertNotIn("sarvam-secret", request.data.decode())
        mocked_play.assert_called_once_with(response)

    def test_stopping_during_http_read_is_a_clean_interruption(self) -> None:
        speaker = SarvamSpeaker(TTSConfig(enabled=False))
        response = MagicMock()
        player = MagicMock()
        player.stdin = MagicMock()
        player.poll.return_value = None
        player.wait.return_value = -15

        def interrupted_read(_size: int) -> bytes:
            speaker._stop_requested.set()
            raise AttributeError("'NoneType' object has no attribute 'read'")

        response.read.side_effect = interrupted_read
        with patch("app.voice.tts.subprocess.Popen", return_value=player):
            completed = speaker._play_stream(response)

        self.assertFalse(completed)
        player.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
