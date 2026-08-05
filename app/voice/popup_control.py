"""Lifecycle wrapper for the standalone GTK popup process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class PopupController:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.popup.launcher"],
            cwd=REPO_ROOT,
        )

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
