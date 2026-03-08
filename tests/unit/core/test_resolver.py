"""Tests for bmad.core.resolver — path and template resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.core.exceptions import BmadConfigError
from bmad.core.resolver import PathResolver


@pytest.fixture()
def resolver(tmp_path: Path) -> PathResolver:
    return PathResolver(tmp_path)


class TestResolvePath:
    def test_absolute_passthrough(self, resolver: PathResolver, tmp_path: Path) -> None:
        p = resolver.resolve_path(str(tmp_path / "foo.md"))
        assert p == tmp_path / "foo.md"

    def test_project_root_substitution(self, resolver: PathResolver, tmp_path: Path) -> None:
        p = resolver.resolve_path("{project-root}/agents/my-agent.md")
        assert p == tmp_path / "agents" / "my-agent.md"

    def test_relative_path(self, resolver: PathResolver, tmp_path: Path) -> None:
        p = resolver.resolve_path("./agents/my-agent.md")
        assert p == tmp_path / "agents" / "my-agent.md"

    def test_must_exist_ok(self, resolver: PathResolver, tmp_path: Path) -> None:
        f = tmp_path / "exists.txt"
        f.write_text("hello")
        p = resolver.resolve_path("exists.txt", must_exist=True)
        assert p == f

    def test_must_exist_missing(self, resolver: PathResolver) -> None:
        with pytest.raises(BmadConfigError, match="Path does not exist"):
            resolver.resolve_path("no-such-file.txt", must_exist=True)

    def test_root_property(self, resolver: PathResolver, tmp_path: Path) -> None:
        assert resolver.root == tmp_path


class TestResolveTemplate:
    def test_simple_variable(self, resolver: PathResolver) -> None:
        result = resolver.resolve_template("Hello {user_name}!", {"user_name": "Guilhem"})
        assert result == "Hello Guilhem!"

    def test_project_root_always_available(self, resolver: PathResolver, tmp_path: Path) -> None:
        result = resolver.resolve_template("{project-root}/agents", {})
        assert result == f"{tmp_path}/agents"

    def test_multiple_variables(self, resolver: PathResolver) -> None:
        ctx = {"user_name": "Guilhem", "communication_language": "Français"}
        result = resolver.resolve_template(
            "{user_name} speaks {communication_language}", ctx
        )
        assert result == "Guilhem speaks Français"

    def test_unknown_variable_raises(self, resolver: PathResolver) -> None:
        with pytest.raises(BmadConfigError, match="Unknown variable.*unknown"):
            resolver.resolve_template("{unknown}", {})

    def test_no_variables(self, resolver: PathResolver) -> None:
        result = resolver.resolve_template("plain text", {})
        assert result == "plain text"

    def test_repeated_variable(self, resolver: PathResolver) -> None:
        result = resolver.resolve_template("{x} and {x}", {"x": "a"})
        assert result == "a and a"

    def test_empty_template(self, resolver: PathResolver) -> None:
        result = resolver.resolve_template("", {})
        assert result == ""

    def test_context_overrides_project_root(self, resolver: PathResolver) -> None:
        """User-provided {project-root} overrides the built-in one."""
        result = resolver.resolve_template("{project-root}", {"project-root": "/custom"})
        assert result == "/custom"
