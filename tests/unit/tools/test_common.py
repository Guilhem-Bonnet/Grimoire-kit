"""Tests for bmad.tools._common — helpers and base class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bmad.tools._common import BmadTool, estimate_tokens, find_project_root, load_yaml, save_yaml


# ── find_project_root ─────────────────────────────────────────────────────────

class TestFindProjectRoot:
    def test_finds_root_in_current_dir(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
        assert find_project_root(tmp_path) == tmp_path

    def test_finds_root_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        assert find_project_root(sub) == tmp_path

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        sub = tmp_path / "no" / "config"
        sub.mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="No project-context.yaml"):
            find_project_root(sub)

    def test_defaults_to_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  name: cwd\n")
        with patch("bmad.tools._common.Path.cwd", return_value=tmp_path):
            assert find_project_root() == tmp_path


# ── load_yaml / save_yaml ─────────────────────────────────────────────────────

class TestLoadYaml:
    def test_load_simple(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nlist:\n  - a\n  - b\n")
        data = load_yaml(f)
        assert data["key"] == "value"
        assert data["list"] == ["a", "b"]

    def test_load_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert load_yaml(f) is None

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            load_yaml(tmp_path / "nope.yaml")


class TestSaveYaml:
    def test_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "out.yaml"
        data = {"project": {"name": "test"}, "items": [1, 2, 3]}
        save_yaml(data, f)
        loaded = load_yaml(f)
        assert loaded["project"]["name"] == "test"
        assert loaded["items"] == [1, 2, 3]

    def test_unicode(self, tmp_path: Path) -> None:
        f = tmp_path / "unicode.yaml"
        save_yaml({"langue": "Français"}, f)
        text = f.read_text(encoding="utf-8")
        assert "Fran" in text


# ── estimate_tokens ───────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty(self) -> None:
        assert estimate_tokens("") == 1

    def test_short(self) -> None:
        assert estimate_tokens("hi") == 1

    def test_typical(self) -> None:
        text = "Hello, this is a medium-length sentence for testing."
        tokens = estimate_tokens(text)
        assert 10 <= tokens <= 20

    def test_scales_linearly(self) -> None:
        short = estimate_tokens("a" * 100)
        long = estimate_tokens("a" * 1000)
        assert long == short * 10


# ── BmadTool ABC ──────────────────────────────────────────────────────────────

class TestBmadTool:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BmadTool(Path("."))  # type: ignore[abstract]

    def test_subclass_works(self, tmp_path: Path) -> None:
        class DummyTool(BmadTool):
            def run(self, **kwargs: object) -> str:
                return f"ran in {self.project_root.name}"

        tool = DummyTool(tmp_path)
        assert tool.project_root == tmp_path.resolve()
        assert "ran in" in tool.run()

    def test_project_root_is_resolved(self) -> None:
        class DummyTool(BmadTool):
            def run(self, **kwargs: object) -> None:
                pass

        tool = DummyTool(Path("."))
        assert tool.project_root.is_absolute()
