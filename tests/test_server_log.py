from __future__ import annotations

import io
import unittest

from rich.console import Console

from app.server.server_log import ServerConsole


class ServerConsoleTests(unittest.TestCase):
    def test_endpoint_line_renders_body_with_credentials_redacted(self) -> None:
        output = io.StringIO()
        logger = ServerConsole(
            Console(file=output, color_system=None, force_terminal=False, width=100)
        )

        logger.response(
            request_id="request-123",
            method="POST",
            path="/message",
            status=200,
            duration_ms=12,
            body={
                "message": "hello",
                "api_key": "do-not-print-this",
                "nested": {"access_token": "also-secret"},
            },
        )

        rendered = output.getvalue()
        self.assertIn("POST", rendered)
        self.assertIn("/message", rendered)
        self.assertIn("hello", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("do-not-print-this", rendered)
        self.assertNotIn("also-secret", rendered)


if __name__ == "__main__":
    unittest.main()
