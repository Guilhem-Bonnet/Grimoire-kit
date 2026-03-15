"""Tests for grimoire.registry.discovery — plugin entry point loading."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from grimoire.registry.discovery import (
    _GROUP_BACKENDS,
    _GROUP_TOOLS,
    discover_backends,
    discover_tools,
)


def _make_ep(name: str, obj: object) -> SimpleNamespace:
    """Create a fake entry-point object."""
    return SimpleNamespace(name=name, load=lambda: obj)


def _make_bad_ep(name: str) -> SimpleNamespace:
    def _boom() -> None:
        raise ImportError("missing")
    return SimpleNamespace(name=name, load=_boom)


class TestDiscoverTools:
    def test_returns_empty_when_no_plugins(self) -> None:
        with patch("grimoire.registry.discovery.entry_points", return_value=[]):
            assert discover_tools() == {}

    def test_loads_registered_tool(self) -> None:
        class FakeTool:
            pass

        eps = [_make_ep("my_tool", FakeTool)]
        with patch("grimoire.registry.discovery.entry_points", return_value=eps) as mock_ep:
            result = discover_tools()
            mock_ep.assert_called_once_with(group=_GROUP_TOOLS)
        assert "my_tool" in result
        assert result["my_tool"] is FakeTool

    def test_skips_broken_plugin(self) -> None:
        eps = [_make_bad_ep("broken"), _make_ep("good", 42)]
        with patch("grimoire.registry.discovery.entry_points", return_value=eps):
            result = discover_tools()
        assert "good" in result
        assert "broken" not in result

    def test_multiple_tools(self) -> None:
        eps = [_make_ep("a", 1), _make_ep("b", 2)]
        with patch("grimoire.registry.discovery.entry_points", return_value=eps):
            result = discover_tools()
        assert result == {"a": 1, "b": 2}


class TestDiscoverBackends:
    def test_returns_empty_when_no_plugins(self) -> None:
        with patch("grimoire.registry.discovery.entry_points", return_value=[]):
            assert discover_backends() == {}

    def test_loads_registered_backend(self) -> None:
        class FakeBackend:
            pass

        eps = [_make_ep("mem_backend", FakeBackend)]
        with patch("grimoire.registry.discovery.entry_points", return_value=eps) as mock_ep:
            result = discover_backends()
            mock_ep.assert_called_once_with(group=_GROUP_BACKENDS)
        assert result["mem_backend"] is FakeBackend
