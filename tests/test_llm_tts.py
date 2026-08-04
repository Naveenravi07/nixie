from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.server.config import LLMConfig, TTSConfig
from app.server.llm import GeminiChat, discover_google_api_keys
from app.voice.tts import SarvamSpeaker


class GoogleKeyTests(unittest.TestCase):
    def test_discovers_numbered_keys_in_order_and_removes_duplicates(self) -> None:
        environment = {
            "GOOGLE_API_KEY1": "first",
            "GOOGLE_API_KEY3": "third",
            "GOOGLE_API_KEY5": "first",
            "GOOGLE_API_KEY": "fallback",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(discover_google_api_keys(), ["first", "third", "fallback"])

    def test_rotates_to_next_key_after_any_error(self) -> None:
        completion = MagicMock(
            side_effect=[
                RuntimeError("first key failed"),
                SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Hello there."))]
                ),
            ]
        )
        fake_litellm = SimpleNamespace(completion=completion)

        with (
            patch.dict(
                os.environ,
                {"GOOGLE_API_KEY1": "one", "GOOGLE_API_KEY2": "two"},
                clear=True,
            ),
            patch.dict(sys.modules, {"litellm": fake_litellm}),
        ):
            chat = GeminiChat(LLMConfig())
            self.assertEqual(chat.reply("Hello"), "Hello there.")

        self.assertEqual(completion.call_count, 2)
        self.assertEqual(completion.call_args_list[0].kwargs["api_key"], "one")
        self.assertEqual(completion.call_args_list[1].kwargs["api_key"], "two")

    def test_provider_errors_include_details_without_exposing_keys(self) -> None:
        completion = MagicMock(side_effect=RuntimeError("quota failure for secret-key"))
        fake_litellm = SimpleNamespace(completion=completion)

        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY1": "secret-key"}, clear=True),
            patch.dict(sys.modules, {"litellm": fake_litellm}),
        ):
            chat = GeminiChat(LLMConfig())
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


if __name__ == "__main__":
    unittest.main()
