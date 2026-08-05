from __future__ import annotations

import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.config import ServerConfig
from app.voice.server_client import NixiServerClient, ServerRequestError


class NixiServerClientTests(unittest.TestCase):
    def test_sends_message_and_returns_response(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"response":"Hello there."}')

        with patch("app.voice.server_client.urlopen", return_value=response) as urlopen:
            result = NixiServerClient(ServerConfig()).send_message("Hello", "request-id")

        self.assertEqual(result, "Hello there.")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            json.loads(request.data),
            {"message": "Hello", "request_id": "request-id"},
        )

    def test_preserves_server_error_detail(self) -> None:
        error = HTTPError(
            "http://127.0.0.1:8765/message",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"error":"Vertex unavailable"}'),
        )

        with patch("app.voice.server_client.urlopen", side_effect=error):
            with self.assertRaisesRegex(ServerRequestError, "Vertex unavailable") as raised:
                NixiServerClient(ServerConfig()).send_message("Hello", "request-id")

        self.assertEqual(raised.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
