"""Centralised logging configuration for Grimoire Kit.

Usage::

    from grimoire.core.log import configure_logging

    configure_logging()              # reads GRIMOIRE_LOG_LEVEL env var
    configure_logging("DEBUG")       # explicit override
    configure_logging(fmt="json")    # machine-readable JSON output
"""

from __future__ import annotations

import json
import logging
import os

__all__ = ["JSONFormatter", "configure_logging"]

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_ENV_VAR = "GRIMOIRE_LOG_LEVEL"
_DEFAULT_LEVEL = "WARNING"


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for machine-readable output."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": self.formatTime(record, datefmt=_DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(
    level: str | None = None,
    *,
    fmt: str = "text",
) -> None:
    """Set up the ``grimoire.*`` logger hierarchy.

    Parameters
    ----------
    level:
        Log level name (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
        Falls back to :envvar:`GRIMOIRE_LOG_LEVEL`, then ``WARNING``.
    fmt:
        ``"text"`` (default) for human-readable output,
        ``"json"`` for structured JSON lines.
    """
    resolved = (level or os.environ.get(_ENV_VAR) or _DEFAULT_LEVEL).upper()
    root = logging.getLogger("grimoire")
    root.setLevel(resolved)

    if not root.handlers:
        handler = logging.StreamHandler()
        if fmt == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
