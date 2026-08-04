"""Small structured event logger shared by Nixi processes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def log_event(component: str, event: str, request_id: str, **details: Any) -> None:
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "component": component,
        "request_id": request_id,
        "event": event,
        **details,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
