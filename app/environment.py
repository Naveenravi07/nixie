"""Load local secrets for interactive and systemd launches."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load simple KEY=VALUE secrets without overriding the process environment."""
    from app.config import ensure_config_exists, USER_CONFIG_PATH
    ensure_config_exists()

    # Helper to parse and set non-empty environment variables from a file
    def load_from_file(path: Path) -> None:
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if name and value:
                os.environ.setdefault(name, value)

    # 1. Load from ~/.config/nixii/.env first (user overrides)
    load_from_file(USER_CONFIG_PATH.parent / ".env")

    # 2. Fall back to REPO_ROOT / ".env" (local development)
    if REPO_ROOT != USER_CONFIG_PATH.parent:
        load_from_file(REPO_ROOT / ".env")
