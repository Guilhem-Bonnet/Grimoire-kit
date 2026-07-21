"""Tests for StackScanner.scan_tree — bounded recursive monorepo detection."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from grimoire.core.scanner import StackScanner, TreeScanResult


def _monorepo(tmp_path: Path) -> Path:
    """Root without markers + two subprojects + excluded dirs with markers."""
    root = tmp_path / "mono"
    api = root / "api"
    api.mkdir(parents=True)
    (api / "pyproject.toml").write_text("[build-system]\n")
    front = root / "frontend"
    front.mkdir()
    (front / "package.json").write_text("{}")
    # Excluded directories carrying markers — must never be visited.
    for excluded in ("node_modules", ".venv", "_grimoire-runtime-output", ".git"):
        d = root / excluded / "trap"
        d.mkdir(parents=True)
        (d / "pyproject.toml").write_text("")
        (root / excluded / "package.json").write_text("{}")
    return root


# ── Backward compatibility ────────────────────────────────────────────────────


class TestScanUnchanged:
    def test_scan_signature_and_result_on_simple_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[build-system]\n")
        result = StackScanner(tmp_path).scan()
        assert [s.name for s in result.stacks] == ["python"]
        assert result.root == tmp_path

    def test_scan_does_not_recurse(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "go.mod").write_text("module m\n")
        result = StackScanner(tmp_path).scan()
        assert result.stacks == ()


# ── Monorepo detection ────────────────────────────────────────────────────────


class TestScanTreeMonorepo:
    def test_detects_subprojects(self, tmp_path: Path) -> None:
        root = _monorepo(tmp_path)
        tree = StackScanner(root).scan_tree()
        assert isinstance(tree, TreeScanResult)
        assert tree.root == root
        sub_names = {r.root.name for r in tree.subprojects}
        assert sub_names == {"api", "frontend"}

    def test_subproject_stacks(self, tmp_path: Path) -> None:
        root = _monorepo(tmp_path)
        tree = StackScanner(root).scan_tree()
        by_dir = {r.root.name: [s.name for s in r.stacks] for r in tree.subprojects}
        assert "python" in by_dir["api"]
        assert "javascript" in by_dir["frontend"]

    def test_monorepo_flag_exposed(self, tmp_path: Path) -> None:
        root = _monorepo(tmp_path)
        tree = StackScanner(root).scan_tree()
        assert tree.root_result.stacks == ()
        assert tree.is_monorepo is True

    def test_not_monorepo_when_root_has_markers(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("")
        tree = StackScanner(tmp_path).scan_tree()
        assert tree.is_monorepo is False
        assert [s.name for s in tree.root_result.stacks] == ["python"]

    def test_excluded_dirs_not_reported(self, tmp_path: Path) -> None:
        root = _monorepo(tmp_path)
        tree = StackScanner(root).scan_tree(max_depth=4)
        for sub in tree.subprojects:
            parts = sub.root.relative_to(root).parts
            assert not any(
                p in {"node_modules", ".venv", ".git"} or p.startswith("_grimoire")
                for p in parts
            )


# ── Depth bound, leaves, symlinks ─────────────────────────────────────────────


class TestScanTreeBounds:
    def test_depth_limit(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "Cargo.toml").write_text("[package]\n")
        shallow = StackScanner(tmp_path).scan_tree(max_depth=2)
        assert shallow.subprojects == ()
        deeper = StackScanner(tmp_path).scan_tree(max_depth=3)
        assert {r.root.name for r in deeper.subprojects} == {"c"}

    def test_detected_subproject_is_a_leaf(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / "package.json").write_text("{}")
        (inner / "go.mod").write_text("module m\n")
        tree = StackScanner(tmp_path).scan_tree(max_depth=3)
        assert {r.root.name for r in tree.subprojects} == {"outer"}

    def test_symlinks_not_followed(self, tmp_path: Path) -> None:
        real = tmp_path / "elsewhere"
        real.mkdir()
        (real / "go.mod").write_text("module m\n")
        root = tmp_path / "root"
        root.mkdir()
        (root / "link").symlink_to(real, target_is_directory=True)
        tree = StackScanner(root).scan_tree()
        assert tree.subprojects == ()

    def test_unreadable_dir_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ok = tmp_path / "ok"
        ok.mkdir()
        (ok / "pyproject.toml").write_text("")
        locked = tmp_path / "locked"
        locked.mkdir()
        real_iterdir = Path.iterdir

        def fake_iterdir(self: Path) -> Iterator[Path]:
            if self.name == "locked":
                raise PermissionError("denied")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)
        tree = StackScanner(tmp_path).scan_tree()
        assert {r.root.name for r in tree.subprojects} == {"ok"}
