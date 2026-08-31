"""Screen capture with user approval via desktop notification."""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from app.config import VisionConfig


_APPROVAL_DIR = Path(tempfile.gettempdir()) / "nixi-vision"
_APPROVAL_FILE = _APPROVAL_DIR / "approved"


def request_and_capture(config: VisionConfig) -> bytes | None:
    """Ask user for permission, wait for approval, then capture screen.

    Returns PNG bytes on success, or None if denied/timed out.
    """
    if not config.enabled:
        return None

    _APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    _APPROVAL_FILE.unlink(missing_ok=True)

    _send_notification(config)
    approved = _wait_for_approval(config.approval_timeout_seconds)

    if not approved:
        return None

    return _take_screenshot(config.screenshot_command)


def _send_notification(config: VisionConfig) -> None:
    title = "Nixi wants to see your screen"
    body = "Run  uv run nixi-approve  to approve."
    cmd = config.notify_command.format(title=title, body=body)
    try:
        subprocess.run(cmd, shell=True, check=False, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _wait_for_approval(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _APPROVAL_FILE.exists():
            _APPROVAL_FILE.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def approve() -> None:
    """Write the approval file so the waiting request proceeds."""
    _APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    _APPROVAL_FILE.write_text("ok")


def _take_screenshot(command: str) -> bytes | None:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            timeout=10,
        )
        if completed.returncode != 0:
            return None
        data = completed.stdout
        return data if data else None
    except (subprocess.TimeoutExpired, OSError):
        return None
