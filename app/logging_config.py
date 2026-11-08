from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings


REDACTED_KEYS = {
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "secret",
    "token",
    "alpaca_market_data_secret",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in REDACTED_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = _redact(getattr(record, "event"))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _file_handler(path: Path) -> logging.Handler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    return handler


def configure_logging() -> None:
    settings.ensure_runtime_directories()
    root = logging.getLogger()
    if getattr(root, "_amw_configured", False):
        return
    root.setLevel(logging.INFO)
    root.addHandler(_file_handler(settings.app_log_path))
    setattr(root, "_amw_configured", True)


def audit_event(event_type: str, **fields: Any) -> None:
    settings.ensure_runtime_directories()
    payload = {
        "data_mode": settings.data_mode,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        **_redact(fields),
    }
    with settings.audit_log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
