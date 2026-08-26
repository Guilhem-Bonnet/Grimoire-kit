"""Tests de ``grimoire cockpit prune``.

Le registre accumule des entrées mortes à chaque projet supprimé ou déplacé.
La purge doit être franche sur ce qui a disparu, et prudente sur le reste :
retirer une entrée encore valide coûte plus cher que d'en garder une douteuse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_cockpit
from grimoire.cli.app import app

runner = CliRunner()


@pytest.fixture
def cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cockpit-home"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


def _project(root: Path, name: str) -> Path:
    """Répertoire portant un marqueur Grimoire."""
    path = root / name
    (path / "_grimoire").mkdir(parents=True)
    return path


def _bare(root: Path, name: str) -> Path:
    """Répertoire qui existe mais sans marqueur Grimoire."""
    path = root / name
    path.mkdir(parents=True)
    return path


def _write_registry(entries: list[dict[str, str]]) -> None:
    cmd_cockpit._save_registry(entries)


# ── classify_registry ─────────────────────────────────────────────────────────


class TestClassify:
    def test_missing_path_is_dropped(self, tmp_path: Path) -> None:
        entries = [{"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"}]
        keep, drop = cmd_cockpit.classify_registry(entries)
        assert keep == []
        assert drop == entries

    def test_existing_project_is_kept(self, tmp_path: Path) -> None:
        entries = [{"name": "vif", "path": str(_project(tmp_path, "vif")), "slug": "vif"}]
        keep, drop = cmd_cockpit.classify_registry(entries)
        assert keep == entries
        assert drop == []

    def test_existing_but_unmarked_is_kept_by_default(self, tmp_path: Path) -> None:
        """Prudence : un répertoire présent a pu être enrôlé délibérément."""
        entries = [{"name": "nu", "path": str(_bare(tmp_path, "nu")), "slug": "nu"}]
        keep, drop = cmd_cockpit.classify_registry(entries)
        assert keep == entries
        assert drop == []

    def test_stale_widens_to_unmarked(self, tmp_path: Path) -> None:
        entries = [{"name": "nu", "path": str(_bare(tmp_path, "nu")), "slug": "nu"}]
        keep, drop = cmd_cockpit.classify_registry(entries, stale=True)
        assert keep == []
        assert drop == entries

    def test_stale_still_keeps_real_projects(self, tmp_path: Path) -> None:
        entries = [{"name": "vif", "path": str(_project(tmp_path, "vif")), "slug": "vif"}]
        assert cmd_cockpit.classify_registry(entries, stale=True)[0] == entries

    def test_empty_path_is_dropped(self) -> None:
        entries = [{"name": "vide", "path": "", "slug": "vide"}]
        assert cmd_cockpit.classify_registry(entries)[1] == entries

    def test_missing_path_key_is_dropped(self) -> None:
        entries = [{"name": "sans-clé", "slug": "sans-cle"}]
        assert cmd_cockpit.classify_registry(entries)[1] == entries  # type: ignore[list-item]

    def test_empty_registry(self) -> None:
        assert cmd_cockpit.classify_registry([]) == ([], [])


# ── CLI ───────────────────────────────────────────────────────────────────────


class TestPruneCommand:
    def test_clean_registry_reports_nothing_to_do(
        self, tmp_path: Path, cockpit_home: Path
    ) -> None:
        _write_registry([{"name": "vif", "path": str(_project(tmp_path, "vif")), "slug": "vif"}])
        result = runner.invoke(app, ["cockpit", "prune", "--yes"])
        assert result.exit_code == 0
        assert "propre" in result.output

    def test_dry_run_writes_nothing(self, tmp_path: Path, cockpit_home: Path) -> None:
        entries = [
            {"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"},
            {"name": "vif", "path": str(_project(tmp_path, "vif")), "slug": "vif"},
        ]
        _write_registry(entries)
        result = runner.invoke(app, ["cockpit", "prune", "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert cmd_cockpit._load_registry() == entries

    def test_prune_removes_only_the_dead(self, tmp_path: Path, cockpit_home: Path) -> None:
        alive = str(_project(tmp_path, "vif"))
        _write_registry([
            {"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"},
            {"name": "vif", "path": alive, "slug": "vif"},
        ])
        result = runner.invoke(app, ["cockpit", "prune", "--yes"])
        assert result.exit_code == 0
        remaining = cmd_cockpit._load_registry()
        assert [e["path"] for e in remaining] == [alive]

    def test_refusing_the_prompt_changes_nothing(
        self, tmp_path: Path, cockpit_home: Path
    ) -> None:
        entries = [{"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"}]
        _write_registry(entries)
        result = runner.invoke(app, ["cockpit", "prune"], input="n\n")
        assert result.exit_code == 0
        assert "Annulé" in result.output
        assert cmd_cockpit._load_registry() == entries

    def test_accepting_the_prompt_prunes(self, tmp_path: Path, cockpit_home: Path) -> None:
        _write_registry([{"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"}])
        result = runner.invoke(app, ["cockpit", "prune"], input="y\n")
        assert result.exit_code == 0
        assert cmd_cockpit._load_registry() == []

    def test_long_list_is_truncated_in_the_preview(
        self, tmp_path: Path, cockpit_home: Path
    ) -> None:
        _write_registry([
            {"name": f"mort{i}", "path": str(tmp_path / f"disparu{i}"), "slug": f"mort{i}"}
            for i in range(25)
        ])
        result = runner.invoke(app, ["cockpit", "prune", "--dry-run"])
        assert "et 15 autre(s)" in result.output

    def test_stale_flag_reaches_the_cli(self, tmp_path: Path, cockpit_home: Path) -> None:
        _write_registry([{"name": "nu", "path": str(_bare(tmp_path, "nu")), "slug": "nu"}])
        assert "propre" in runner.invoke(app, ["cockpit", "prune", "--yes"]).output
        result = runner.invoke(app, ["cockpit", "prune", "--yes", "--stale"])
        assert cmd_cockpit._load_registry() == []
        assert "1 entrée(s) retirée(s)" in result.output


class TestPruneJson:
    def test_json_reports_counts_and_candidates(
        self, tmp_path: Path, cockpit_home: Path
    ) -> None:
        _write_registry([
            {"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"},
            {"name": "vif", "path": str(_project(tmp_path, "vif")), "slug": "vif"},
        ])
        result = runner.invoke(app, ["-o", "json", "cockpit", "prune"])
        payload = json.loads(result.output)
        assert payload["total"] == 2
        assert payload["kept"] == 1
        assert payload["removed"] == 1
        assert payload["candidates"][0]["name"] == "mort"
        assert len(cmd_cockpit._load_registry()) == 1

    def test_json_dry_run_writes_nothing(self, tmp_path: Path, cockpit_home: Path) -> None:
        entries = [{"name": "mort", "path": str(tmp_path / "disparu"), "slug": "mort"}]
        _write_registry(entries)
        result = runner.invoke(app, ["-o", "json", "cockpit", "prune", "--dry-run"])
        payload = json.loads(result.output)
        assert payload["dryRun"] is True
        assert payload["removed"] == 0
        assert cmd_cockpit._load_registry() == entries
