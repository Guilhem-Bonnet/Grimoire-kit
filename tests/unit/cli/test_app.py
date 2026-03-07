"""Tests for bmad.cli.app — CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bmad.cli.app import app

runner = CliRunner()


# ── Version ───────────────────────────────────────────────────────────────────

class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "bmad-kit" in result.output

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "doctor" in result.output


# ── Init ──────────────────────────────────────────────────────────────────────

class TestInit:
    def test_init_creates_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        cfg = tmp_path / "project-context.yaml"
        assert cfg.is_file()
        content = cfg.read_text()
        assert "project:" in content
        assert tmp_path.name in content

    def test_init_creates_dirs(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        assert (tmp_path / "_bmad" / "_memory").is_dir()
        assert (tmp_path / "_bmad-output").is_dir()

    def test_init_custom_name(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--name", "my-project"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert "my-project" in content

    def test_init_refuses_overwrite(self, tmp_path: Path) -> None:
        # First init
        runner.invoke(app, ["init", str(tmp_path)])
        # Second without --force
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 1

    def test_init_force_overwrite(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(app, ["init", str(tmp_path), "--force"])
        assert result.exit_code == 0

    def test_init_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        result = runner.invoke(app, ["init", str(deep)])
        assert result.exit_code == 0
        assert (deep / "project-context.yaml").is_file()


# ── Doctor ────────────────────────────────────────────────────────────────────

class TestDoctor:
    @pytest.fixture()
    def healthy_project(self, tmp_path: Path) -> Path:
        """Create a project with valid config + structure."""
        runner.invoke(app, ["init", str(tmp_path)])
        return tmp_path

    def test_doctor_healthy(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_doctor_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_doctor_invalid_config(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  description: no name\n")
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1

    def test_doctor_missing_dirs(self, tmp_path: Path) -> None:
        # Config only, no _bmad dirs
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: test\n"
        )
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_doctor_shows_project_name(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert healthy_project.name in result.output

    def test_doctor_shows_version(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert "bmad-kit" in result.output
