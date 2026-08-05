from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.server.config import LLMConfig, TTSConfig
from app.server.llm import VertexChat
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
