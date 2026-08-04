"""Launch the GTK popup with the system interpreter that owns its GI bindings."""

from __future__ import annotations

import os
from pathlib import Path


SYSTEM_PYTHON = "/usr/bin/python3"


def main() -> None:
    popup = Path(__file__).with_name("main.py")
    os.execv(SYSTEM_PYTHON, [SYSTEM_PYTHON, str(popup)])


if __name__ == "__main__":
    main()
