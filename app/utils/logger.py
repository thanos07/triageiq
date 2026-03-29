"""
app/utils/logger.py

Structured logging setup for the Incident Triage Copilot.

- In development: human-readable colored output
- In production: JSON-structured output (ready for log aggregators)

Usage:
    from app.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Processing incident", extra={"incident_id": "abc-123"})
"""

import logging
import json
import sys
from datetime import datetime, timezone
from app.config import settings


class JSONFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.
    This is the format used in production — easy to ingest into
    Datadog, Grafana Loki, CloudWatch, etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include any extra fields passed via extra={...}
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info", "exc_text",
                "stack_info", "lineno", "funcName", "created", "msecs",
                "relativeCreated", "thread", "threadName", "processName",
                "process", "message", "taskName",
            ):
                log_entry[key] = value

        # Append exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class DevFormatter(logging.Formatter):
    """
    Human-readable formatter for development.
    Format: [LEVEL]  logger_name — message  {extra_fields}
    """

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:<8}{self.RESET}"
        base = f"{level} {record.name} — {record.getMessage()}"

        # Collect extra fields (anything not standard)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in (
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info", "exc_text",
                "stack_info", "lineno", "funcName", "created", "msecs",
                "relativeCreated", "thread", "threadName", "processName",
                "process", "message", "taskName",
            )
        }
        if extras:
            base += f"  {extras}"

        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)

        return base


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured logger for the given module name.

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        A Python logger instance with the appropriate formatter attached.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(settings.log_level.upper())

    if settings.is_production:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(DevFormatter())

    logger.addHandler(handler)
    logger.propagate = False  # Don't double-log via root logger

    return logger
