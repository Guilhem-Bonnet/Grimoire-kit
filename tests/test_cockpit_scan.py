"""Tests for ``grimoire cockpit scan`` — bounded project discovery + enrolment."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_cockpit
from grimoire.cli.app import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ck"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


def _tree(tmp_path: Path) -> Path:
    """Scan root: 2 grimoire-managed projects, 1 bare git repo, traps in excluded dirs."""
    root = tmp_path / "dev"
    managed_ctx = root / "alpha"
    managed_ctx.mkdir(parents=True)
    (managed_ctx / "project-context.yaml").write_text("project: alpha\n")
    managed_rt = root / "nested" / "beta"
    (managed_rt / "_grimoire").mkdir(parents=True)
    bare = root / "gamma"
    (bare / ".git").mkdir(parents=True)
    # Trap inside an excluded dir — must never be discovered.
    trap = root / "node_modules" / "trap"
    (trap / ".git").mkdir(parents=True)
    return root


def _registry_paths() -> set[str]:
    return {p["path"] for p in cmd_cockpit._load_registry()}


# ── Crawl helper ──────────────────────────────────────────────────────────────


class TestCrawl:
    def test_finds_projects_and_classifies(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        found = {c.path.name: c.managed for c in cmd_cockpit._crawl_projects(root, 4)}
        assert found == {"alpha": True, "beta": True, "gamma": False}

    def test_project_is_a_leaf(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        inner = root / "gamma" / "sub"
        (inner / ".git").mkdir(parents=True)
        names = {c.path.name for c in cmd_cockpit._crawl_projects(root, 4)}
        assert "sub" not in names

    def test_depth_bound(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        names = {c.path.name for c in cmd_cockpit._crawl_projects(root, 1)}
        assert names == {"alpha", "gamma"}  # nested/beta is at depth 2

    def test_symlinks_not_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        (outside / ".git").mkdir(parents=True)
        root = tmp_path / "dev2"
        root.mkdir()
        (root / "link").symlink_to(outside, target_is_directory=True)
        assert cmd_cockpit._crawl_projects(root, 4) == []

    def test_permission_error_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _tree(tmp_path)
        (root / "locked").mkdir()
        real_iterdir = Path.iterdir

        def fake_iterdir(self: Path) -> Iterator[Path]:
            if self.name == "locked":
                raise PermissionError("denied")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)
        names = {c.path.name for c in cmd_cockpit._crawl_projects(root, 4)}
        assert names == {"alpha", "beta", "gamma"}


# ── CLI command ───────────────────────────────────────────────────────────────


class TestScanCommand:
    def test_yes_enrols_managed_only(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(app, ["cockpit", "scan", str(root), "--yes"])
        assert res.exit_code == 0
        paths = _registry_paths()
        assert str(root / "alpha") in paths
        assert str(root / "nested" / "beta") in paths
        assert str(root / "gamma") not in paths

    def test_uninitialized_listed_with_hint(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(app, ["cockpit", "scan", str(root), "--yes"])
        assert "grimoire up" in res.output
        assert "gamma" in res.output

    def test_dedup_against_registry(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        runner.invoke(app, ["cockpit", "scan", str(root), "--yes"])
        res = runner.invoke(app, ["cockpit", "scan", str(root), "--yes"])
        assert res.exit_code == 0
        assert "Nothing new to enrol" in res.output
        assert len(cmd_cockpit._load_registry()) == 2

    def test_interactive_confirm_declined(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(app, ["cockpit", "scan", str(root)], input="n\n")
        assert res.exit_code == 0
        assert cmd_cockpit._load_registry() == []
        assert "Nothing enrolled" in res.output

    def test_interactive_confirm_accepted(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(app, ["cockpit", "scan", str(root)], input="y\n")
        assert res.exit_code == 0
        assert len(cmd_cockpit._load_registry()) == 2

    def test_single_global_confirmation(self, runner: CliRunner, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(app, ["cockpit", "scan", str(root)], input="y\n")
        assert res.output.count("Enrol 2 project(s)") == 1

    def test_include_uninitialized_enrols_bare_repos(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        root = _tree(tmp_path)
        res = runner.invoke(
            app, ["cockpit", "scan", str(root), "--yes", "--include-uninitialized"]
        )
        assert res.exit_code == 0
        assert str(root / "gamma") in _registry_paths()
        assert "grimoire up" not in res.output

    def test_rejects_missing_root(self, runner: CliRunner, tmp_path: Path) -> None:
        res = runner.invoke(app, ["cockpit", "scan", str(tmp_path / "nope")])
        assert res.exit_code == 1

    def test_no_candidates(self, runner: CliRunner, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        res = runner.invoke(app, ["cockpit", "scan", str(empty)])
        assert res.exit_code == 0
        assert "No candidate project" in res.output

    def test_permission_error_via_cli(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _tree(tmp_path)
        (root / "locked").mkdir()
        real_iterdir = Path.iterdir

        def fake_iterdir(self: Path) -> Iterator[Path]:
            if self.name == "locked":
                raise PermissionError("denied")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)
        res = runner.invoke(app, ["cockpit", "scan", str(root), "--yes"])
        assert res.exit_code == 0
        assert len(cmd_cockpit._load_registry()) == 2
