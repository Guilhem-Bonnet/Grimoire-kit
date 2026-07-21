"""Tests for cli/cmd_up.py — ``grimoire up`` one-command bring-up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.cli.cmd_init import (
    _ARCHETYPE_INFO,
    _ARCHETYPE_KEYS,
    ARCHETYPE_CATALOG,
    KNOWN_ARCHETYPES,
)

# ── Archetype catalog (single source of truth) ────────────────────────────────


class TestArchetypeCatalog:
    def test_known_archetypes_derive_from_catalog(self) -> None:
        assert {spec.id for spec in ARCHETYPE_CATALOG} == KNOWN_ARCHETYPES

    def test_catalog_has_no_duplicates(self) -> None:
        ids = [spec.id for spec in ARCHETYPE_CATALOG]
        assert len(ids) == len(set(ids))

    def test_wizard_exposes_every_selectable_archetype(self) -> None:
        selectable = {spec.id for spec in ARCHETYPE_CATALOG if not spec.internal and not spec.base}
        assert set(_ARCHETYPE_INFO) == selectable
        assert set(_ARCHETYPE_KEYS) == selectable

    def test_legacy_ids_still_valid_for_flags(self) -> None:
        # Backward compatibility: every historical id remains accepted.
        assert {
            "minimal", "web-app", "creative-studio", "fix-loop", "infra-ops",
            "meta", "stack", "features", "platform-engineering", "agentic-standard",
        } <= KNOWN_ARCHETYPES

    def test_app_validation_uses_the_catalog(self) -> None:
        from grimoire.cli import app as app_module

        assert app_module._KNOWN_ARCHETYPES is KNOWN_ARCHETYPES


# ── CLI integration ───────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def cli_app():
    from grimoire.cli.app import app

    return app


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `up` runs hermetic: no cockpit registration, no live env probes."""
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


class TestUpExpress:
    def test_happy_path_full_bring_up(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(
            cli_app, ["up", str(target), "--backend", "local", "--name", "demo-app"],
        )
        assert result.exit_code == 0, result.output
        # init
        assert (target / "project-context.yaml").is_file()
        assert "demo-app" in (target / "project-context.yaml").read_text(encoding="utf-8")
        assert (target / "_grimoire" / "_config" / "custom" / "agents").is_dir()
        # identity propagation targets exist (init generates them; step reports sync)
        assert (target / ".github" / "copilot-instructions.md").is_file()
        # standard init
        assert (target / "_grimoire" / "standard" / "standard-profile.yaml").is_file()
        # report
        assert "done" in result.output
        assert "demo-app" in result.output

    def test_happy_path_with_archetype_flag(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(
            cli_app,
            ["up", str(target), "--backend", "local", "-a", "fix-loop"],
        )
        assert result.exit_code == 0, result.output
        assert "fix-loop" in (target / "project-context.yaml").read_text(encoding="utf-8")

    def test_unknown_archetype_rejected(self, runner, cli_app, tmp_path: Path) -> None:
        result = runner.invoke(cli_app, ["up", str(tmp_path), "-a", "not-an-archetype"])
        assert result.exit_code == 1
        assert "Unknown archetype" in result.output

    def test_unknown_backend_rejected(self, runner, cli_app, tmp_path: Path) -> None:
        result = runner.invoke(cli_app, ["up", str(tmp_path), "--backend", "bogus"])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output


class TestUpIdempotent:
    def test_second_run_skips_every_step(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        first = runner.invoke(cli_app, ["up", str(target), "--backend", "local"])
        assert first.exit_code == 0, first.output
        config_before = (target / "project-context.yaml").read_text(encoding="utf-8")

        second = runner.invoke(cli_app, ["up", str(target), "--backend", "local"])
        assert second.exit_code == 0, second.output
        assert "already initialized" in second.output
        assert "already" in second.output  # per-step "déjà en place" signals
        # Nothing rewritten
        assert (target / "project-context.yaml").read_text(encoding="utf-8") == config_before

    def test_up_on_existing_project_does_not_force_reinit(self, runner, cli_app, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "existing"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n',
            encoding="utf-8",
        )
        result = runner.invoke(cli_app, ["up", str(tmp_path), "--no-standard"])
        assert result.exit_code == 0, result.output
        assert "existing" in result.output
        # init skipped — the handcrafted config was preserved
        content = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
        assert 'name: "existing"' in content
        # legacy behavior: required directories reconciled
        assert (tmp_path / "_grimoire" / "_memory").is_dir()
        assert (tmp_path / "_grimoire-output").is_dir()


class TestUpNoStandard:
    def test_no_standard_skips_the_step(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(
            cli_app, ["up", str(target), "--backend", "local", "--no-standard"],
        )
        assert result.exit_code == 0, result.output
        assert not (target / "_grimoire" / "standard" / "standard-profile.yaml").exists()
        assert "--no-standard" in result.output


class TestUpUserFlag:
    def test_user_written_and_propagated(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(
            cli_app,
            ["up", str(target), "--backend", "local", "--user", "Alice Wonder", "--no-standard"],
        )
        assert result.exit_code == 0, result.output
        assert "Alice Wonder" in (target / "project-context.yaml").read_text(encoding="utf-8")
        copilot = target / ".github" / "copilot-instructions.md"
        assert "Alice Wonder" in copilot.read_text(encoding="utf-8")


class TestUpDryRun:
    def test_dry_run_on_fresh_dir_writes_nothing(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(cli_app, ["up", str(target), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "planned" in result.output
        assert not (target / "project-context.yaml").exists()
        assert not (target / "_grimoire" / "standard").exists()

    def test_dry_run_on_existing_project(self, runner, cli_app, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "planned"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n',
            encoding="utf-8",
        )
        result = runner.invoke(cli_app, ["up", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output.lower()


class TestUpJson:
    def test_json_report_structure(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        runner.invoke(cli_app, ["up", str(target), "--backend", "local"])
        result = runner.invoke(cli_app, ["-o", "json", "up", str(target), "--backend", "local"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["dry_run"] is False
        assert isinstance(data["actions"], list)
        assert "agents_count" in data
        steps = {s["step"]: s["status"] for s in data["steps"]}
        assert steps["init"] == "skipped"
        assert steps["standard"] == "skipped"

    def test_json_fresh_init_emits_single_document(self, runner, cli_app, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(
            cli_app, ["-o", "json", "up", str(target), "--backend", "local", "--no-standard"],
        )
        assert result.exit_code == 0, result.output
        # stdout must stay a single JSON document (init report goes to stderr)
        data = json.loads(result.stdout)
        steps = {s["step"]: s["status"] for s in data["steps"]}
        assert steps["init"] == "done"


class TestUpAlias:
    def test_up_registered_in_help(self, runner, cli_app) -> None:
        result = runner.invoke(cli_app, ["--help"])
        assert "up" in result.output

    def test_up_help_shows_new_flags(self, runner, cli_app) -> None:
        result = runner.invoke(cli_app, ["up", "--help"])
        for flag in ("--interactive", "--name", "--user", "--archetype", "--backend", "--no-standard", "--needs"):
            assert flag in result.output
