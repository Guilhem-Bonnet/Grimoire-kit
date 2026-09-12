"""Tests for grimoire.tools._common — helpers and base class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from grimoire.tools._common import (
    GrimoireTool,
    _get_yaml_loader,
    estimate_tokens,
    find_project_root,
    load_yaml,
    load_yaml_roundtrip,
    save_yaml,
)

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
        with pytest.raises(FileNotFoundError, match=r"No project-context\.yaml"):
            find_project_root(sub)

    def test_defaults_to_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  name: cwd\n")
        with patch("grimoire.tools._common.Path.cwd", return_value=tmp_path):
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
        with pytest.raises(OSError):
            load_yaml(tmp_path / "nope.yaml")


class TestSaveYaml:
    def test_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "out.yaml"
        data = CommentedMap({"project": {"name": "test"}, "items": [1, 2, 3]})
        save_yaml(data, f)
        loaded = load_yaml(f)
        assert loaded["project"]["name"] == "test"
        assert loaded["items"] == [1, 2, 3]

    def test_unicode(self, tmp_path: Path) -> None:
        f = tmp_path / "unicode.yaml"
        save_yaml(CommentedMap({"langue": "Français"}), f)
        text = f.read_text(encoding="utf-8")
        assert "Fran" in text

    def test_brand_new_file_from_a_commented_map(self, tmp_path: Path) -> None:
        """A freshly-built CommentedMap (no source file to round-trip) is
        the documented way to generate a new file from scratch."""
        f = tmp_path / "new.yaml"
        data = CommentedMap({"project": {"name": "test"}})
        save_yaml(data, f)
        assert load_yaml(f) == {"project": {"name": "test"}}


class TestSaveYamlRoundtripGuard:
    """save_yaml() must refuse a plain dict/list on the ruamel backend: it
    has no comment metadata to round-trip, so accepting it silently
    reproduces the exact bug class of grimoire-kit#430 (grimoire upgrade
    stripping every comment from project-context.yaml). See also
    load_yaml()'s docstring, which tells callers to use
    load_yaml_roundtrip() instead when a file will be rewritten."""

    def test_refuses_a_plain_dict(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="plain_dict_would_lose_comments"):
            save_yaml({"key": "value"}, tmp_path / "out.yaml")

    def test_refuses_a_plain_list(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="plain_list_would_lose_comments"):
            save_yaml(["a", "b"], tmp_path / "out.yaml")

    def test_error_message_names_the_issue(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="430"):
            save_yaml({"key": "value"}, tmp_path / "out.yaml")

    def test_accepts_a_commented_map(self, tmp_path: Path) -> None:
        f = tmp_path / "out.yaml"
        save_yaml(CommentedMap({"key": "value"}), f)
        assert load_yaml(f) == {"key": "value"}

    def test_accepts_a_commented_seq_as_top_level_value(self, tmp_path: Path) -> None:
        f = tmp_path / "out.yaml"
        top = CommentedMap({"items": CommentedSeq(["a", "b"])})
        save_yaml(top, f)
        assert load_yaml(f) == {"items": ["a", "b"]}

    def test_a_scalar_or_none_is_not_refused(self, tmp_path: Path) -> None:
        """The guard only targets dict/list — it must not get in the way of
        callers writing e.g. a bare document."""
        f = tmp_path / "out.yaml"
        save_yaml(None, f)
        assert load_yaml(f) is None


class TestLoadYamlRoundtrip:
    def test_preserves_comments_quotes_and_inline_collections(self, tmp_path: Path) -> None:
        f = tmp_path / "rich.yaml"
        f.write_text(
            "# En-tête\n"
            'project: "MonProjet"  # nom affiché\n'
            "\n"
            "# Langue\n"
            'communication_language: "français"\n'
            "tags: [alpha, beta, gamma]  # liste inline\n"
            "notes: |\n"
            "  Une note\n"
            "  multi-lignes.\n",
            encoding="utf-8",
        )
        data = load_yaml_roundtrip(f)
        assert isinstance(data, CommentedMap)

        out = tmp_path / "rewritten.yaml"
        save_yaml(data, out)
        text = out.read_text(encoding="utf-8")
        assert "# En-tête" in text
        assert "# nom affiché" in text
        assert "# Langue" in text
        assert '"MonProjet"' in text
        assert '"français"' in text
        assert "[alpha, beta, gamma]" in text
        assert "# liste inline" in text
        assert "notes: |" in text
        # A pure load→save round-trip with no mutation is byte-identical.
        assert text == f.read_text(encoding="utf-8")

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            load_yaml_roundtrip(tmp_path / "nope.yaml")

    def test_requires_ruamel(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "ruamel.yaml" or name.startswith("ruamel."):
                raise ImportError("simulated: no ruamel.yaml")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match=r"requires ruamel\.yaml"):
            load_yaml_roundtrip(f)


# ── _get_yaml_loader fallback ─────────────────────────────────────────────────

class TestGetYamlLoader:
    def test_returns_ruamel_by_default(self) -> None:
        _loader, backend = _get_yaml_loader()
        assert backend == "ruamel"


class TestLoadYamlPyYamlFallback:
    def test_load_via_pyyaml_backend(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercise load_yaml through PyYAML backend."""
        from unittest.mock import MagicMock

        import grimoire.tools._common as mod

        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")

        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = {"key": "value"}
        monkeypatch.setattr(mod, "_get_yaml_loader", lambda: (mock_yaml, "pyyaml"))
        data = mod.load_yaml(f)
        assert data["key"] == "value"
        mock_yaml.safe_load.assert_called_once()

    def test_save_via_pyyaml_backend(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercise save_yaml through PyYAML backend."""
        from unittest.mock import MagicMock

        import grimoire.tools._common as mod

        f = tmp_path / "rt.yaml"
        mock_yaml = MagicMock()
        monkeypatch.setattr(mod, "_get_yaml_loader", lambda: (mock_yaml, "pyyaml"))
        mod.save_yaml({"items": [1, 2, 3]}, f)
        mock_yaml.dump.assert_called_once()

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text(":\n  :\n    - [\n")
        with pytest.raises(OSError, match="Cannot parse YAML"):
            load_yaml(f)


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


# ── GrimoireTool ABC ──────────────────────────────────────────────────────────────

class TestGrimoireTool:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            GrimoireTool(Path())  # type: ignore[abstract]

    def test_subclass_works(self, tmp_path: Path) -> None:
        class DummyTool(GrimoireTool):
            def run(self, **kwargs: object) -> str:
                return f"ran in {self.project_root.name}"

        tool = DummyTool(tmp_path)
        assert tool.project_root == tmp_path.resolve()
        assert "ran in" in tool.run()

    def test_project_root_is_resolved(self) -> None:
        class DummyTool(GrimoireTool):
            def run(self, **kwargs: object) -> None:
                pass

        tool = DummyTool(Path())
        assert tool.project_root.is_absolute()
