"""Tests for grimoire.core.log — configure_logging and JSONFormatter."""

from __future__ import annotations

import json
import logging

import pytest

from grimoire.core.log import JSONFormatter, configure_logging


@pytest.fixture(autouse=True)
def _reset_grimoire_logger() -> None:
    """Remove handlers between tests to avoid leaking state."""
    root = logging.getLogger("grimoire")
    root.handlers.clear()
    root.setLevel(logging.WARNING)


# ── configure_logging ─────────────────────────────────────────────────────────


class TestConfigureLogging:
    """Verify level resolution and handler setup."""

    def test_default_level_is_warning(self) -> None:
        configure_logging()
        assert logging.getLogger("grimoire").level == logging.WARNING

    def test_explicit_debug(self) -> None:
        configure_logging("DEBUG")
        assert logging.getLogger("grimoire").level == logging.DEBUG

    def test_explicit_info(self) -> None:
        configure_logging("INFO")
        assert logging.getLogger("grimoire").level == logging.INFO

    def test_case_insensitive(self) -> None:
        configure_logging("debug")
        assert logging.getLogger("grimoire").level == logging.DEBUG

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_LOG_LEVEL", "ERROR")
        configure_logging()
        assert logging.getLogger("grimoire").level == logging.ERROR

    def test_explicit_level_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_LOG_LEVEL", "ERROR")
        configure_logging("DEBUG")
        assert logging.getLogger("grimoire").level == logging.DEBUG

    def test_handler_added(self) -> None:
        configure_logging()
        assert len(logging.getLogger("grimoire").handlers) == 1

    def test_handler_not_duplicated(self) -> None:
        configure_logging()
        configure_logging()
        assert len(logging.getLogger("grimoire").handlers) == 1

    def test_text_format_default(self) -> None:
        configure_logging()
        handler = logging.getLogger("grimoire").handlers[0]
        assert not isinstance(handler.formatter, JSONFormatter)

    def test_json_format(self) -> None:
        configure_logging(fmt="json")
        handler = logging.getLogger("grimoire").handlers[0]
        assert isinstance(handler.formatter, JSONFormatter)


# ── JSONFormatter ─────────────────────────────────────────────────────────────


class TestJSONFormatter:
    """Verify JSON output structure."""

    def _make_record(self, msg: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name="grimoire.test",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_output_is_valid_json(self) -> None:
        fmt = JSONFormatter()
        out = fmt.format(self._make_record())
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_fields_present(self) -> None:
        fmt = JSONFormatter()
        data = json.loads(fmt.format(self._make_record()))
        assert set(data.keys()) >= {"ts", "level", "logger", "msg"}

    def test_level_value(self) -> None:
        fmt = JSONFormatter()
        data = json.loads(fmt.format(self._make_record(level=logging.WARNING)))
        assert data["level"] == "WARNING"

    def test_message_value(self) -> None:
        fmt = JSONFormatter()
        data = json.loads(fmt.format(self._make_record(msg="test 42")))
        assert data["msg"] == "test 42"

    def test_logger_name(self) -> None:
        fmt = JSONFormatter()
        data = json.loads(fmt.format(self._make_record()))
        assert data["logger"] == "grimoire.test"

    def test_exception_captured(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="grimoire.test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="failure",
                args=(),
                exc_info=sys.exc_info(),
            )
        data = json.loads(fmt.format(record))
        assert "exception" in data
        assert "boom" in data["exception"]
