"""Structured JSON logging with PII redaction.

The redaction is not decorative. This corpus contains email addresses, invoice
references, a VAT number (T-009) — and T-021 is a GDPR erasure request. Logging
full ticket bodies at INFO would be a data-protection incident in a system whose
own test data is asking to be forgotten (PRD §10.3).

The rule this module enforces: logs carry ticket IDs, not ticket text.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

# Populated by the correlation-ID middleware so every line of a request's work
# can be stitched together without threading a parameter through every call.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_BEARER = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

# Any log record field whose NAME looks like a credential is replaced wholesale.
# Matching on the key rather than the value is what catches a secret that does
# not happen to look like one.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "llm_api_key",
        "api_token",
        "token",
        "secret",
        "password",
        "authorization",
        "auth",
    }
)

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def redact(text: str) -> str:
    """Scrub the patterns that show up in this corpus."""
    text = _EMAIL.sub("[email]", text)
    text = _CARD.sub("[number]", text)
    return _BEARER.sub(r"\1[redacted]", text)


class RedactionFilter(logging.Filter):
    """Applies redaction to the message and to every structured extra."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED:
                continue
            if key.lower() in _SECRET_KEYS:
                record.__dict__[key] = "[redacted]"
            elif isinstance(value, str):
                record.__dict__[key] = redact(value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Greppable, and parseable by anything."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent: safe to call from both the app factory and the eval CLI."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # httpx logs the full request URL at INFO on every call, which is noise at
    # 30 tickets and would drown the telemetry this system actually reports.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
