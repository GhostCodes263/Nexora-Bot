from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_URL_PW_RE = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")


def redact(text: str) -> str:
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    return _URL_PW_RE.sub(r"\1[REDACTED]\2", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
