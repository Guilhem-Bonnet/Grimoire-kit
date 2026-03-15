"""Tests for ``grimoire merge`` CLI command and cmd_merge module."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_merge import run_merge, run_undo
from grimoire.core.exceptions import GrimoireMergeError

runner = CliRunner()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    d = tmp_path / "src"
    d.mkdir()
    (d / "_grimoire").mkdir()
    (d / "_grimoire" / "config.yaml").write_text("version: 3\n")
    (d / "hello.txt").write_text("hello\n")
    return d


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    d = tmp_path / "tgt"
    d.mkdir()
    return d


# ── cmd_merge functions ───────────────────────────────────────────────────────


class TestRunMerge:
    def test_basic_merge(self, source: Path, target: Path) -> None:
        _plan, result = run_merge(source, target)
        assert "hello.txt" in result.files_created
        assert (target / "hello.txt").is_file()

    def test_dry_run(self, source: Path, target: Path) -> None:
        _plan, result = run_merge(source, target, dry_run=True)
        assert len(result.files_created) >= 1
        assert not (target / "hello.txt").exists()

    def test_force(self, source: Path, target: Path) -> None:
        (target / "hello.txt").write_text("old\n")
        _plan, result = run_merge(source, target, force=True)
        assert "hello.txt" in result.files_created
        assert (target / "hello.txt").read_text() == "hello\n"

    def test_skip_conflicts(self, source: Path, target: Path) -> None:
        (target / "hello.txt").write_text("keep\n")
        _plan, result = run_merge(source, target)
        assert "hello.txt" in result.files_skipped
        assert (target / "hello.txt").read_text() == "keep\n"


class TestRunUndo:
    def test_undo(self, source: Path, target: Path) -> None:
        run_merge(source, target)
        assert (target / "hello.txt").is_file()
        deleted = run_undo(target)
        assert "hello.txt" in deleted
        assert not (target / "hello.txt").exists()

    def test_undo_no_log(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireMergeError, match="not found"):
            run_undo(tmp_path)


# ── CLI integration ──────────────────────────────────────────────────────────


class TestMergeCLI:
    def test_merge_creates_files(self, source: Path, target: Path) -> None:
        result = runner.invoke(app, ["merge", str(source), "--target", str(target)])
        assert result.exit_code == 0
        assert "created" in result.output
        assert (target / "hello.txt").is_file()

    def test_merge_dry_run(self, source: Path, target: Path) -> None:
        result = runner.invoke(app, ["merge", str(source), "--target", str(target), "--dry-run"])
        assert result.exit_code == 0
        assert "plan" in result.output
        assert not (target / "hello.txt").exists()

    def test_merge_force(self, source: Path, target: Path) -> None:
        (target / "hello.txt").write_text("old\n")
        result = runner.invoke(app, ["merge", str(source), "--target", str(target), "--force"])
        assert result.exit_code == 0
        assert (target / "hello.txt").read_text() == "hello\n"

    def test_merge_undo(self, source: Path, target: Path) -> None:
        runner.invoke(app, ["merge", str(source), "--target", str(target)])
        assert (target / "hello.txt").is_file()
        result = runner.invoke(app, ["-y", "merge", str(source), "--target", str(target), "--undo"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower() or "removed" in result.output.lower()

    def test_merge_bad_source(self, tmp_path: Path, target: Path) -> None:
        result = runner.invoke(app, ["merge", str(tmp_path / "nope"), "--target", str(target)])
        assert result.exit_code == 1

    def test_merge_warnings(self, source: Path, target: Path) -> None:
        # Create sensitive conflict
        gh = target / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("existing\n")
        (source / ".github").mkdir()
        (source / ".github" / "copilot-instructions.md").write_text("new\n")
        result = runner.invoke(app, ["merge", str(source), "--target", str(target)])
        assert result.exit_code == 0
        assert "skipped" in result.output

    def test_undo_no_log(self, source: Path, target: Path) -> None:
        result = runner.invoke(app, ["-y", "merge", str(source), "--target", str(target), "--undo"])
        assert result.exit_code == 1
