from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from app.utils.time import utcnow


@dataclass
class ErrorRecord:
    at: datetime
    where: str
    summary: str


# Small in-memory ring buffer for /errors (the full traces are in the structured logs).
RECENT_ERRORS: deque[ErrorRecord] = deque(maxlen=50)


def record_error(where: str, exc: BaseException) -> None:
    RECENT_ERRORS.append(ErrorRecord(utcnow(), where, f"{type(exc).__name__}: {str(exc)[:200]}"))
