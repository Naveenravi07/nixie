"""Load local secrets for interactive and systemd launches."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load simple KEY=VALUE secrets without overriding the process environment."""
    environment_file = REPO_ROOT / ".env"
    if not environment_file.exists():
        return

    for raw_line in environment_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            os.environ.setdefault(name, value)
