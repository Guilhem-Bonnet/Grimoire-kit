"""Tests de la visibilité de l'étape d'enregistrement dans le cockpit (#341).

Le cockpit ne scanne jamais le disque : il lit ``~/.grimoire/cockpit/registry.json``.
Un projet Grimoire valide mais jamais enregistré disparaissait donc en silence,
et l'utilisateur en concluait — à tort — que le cockpit ne détectait pas ses
projets. Ces tests couvrent le critère d'arrêt de l'issue : le message apparaît
quand il faut, se tait quand il ne faut pas.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_cockpit
from grimoire.cli.app import app
from grimoire.tools import project_registry

runner = CliRunner()


@pytest.fixture
def cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Registre isolé : ces tests ne doivent jamais toucher le vrai registre utilisateur."""
    home = tmp_path / "cockpit-home"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


def _grimoire_project(root: Path, name: str) -> Path:
    """Répertoire portant un marqueur Grimoire, tel que ``looks_grimoire()`` le validerait."""
    path = root / name
    (path / "_grimoire").mkdir(parents=True)
    return path


class TestUnregisteredCwdNotice:
    """``_unregistered_cwd_notice`` — la fonction pure derrière le message."""

    def test_none_when_cwd_is_not_grimoire(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bare = tmp_path / "pas-grimoire"
        bare.mkdir()
        monkeypatch.chdir(bare)
        assert cmd_cockpit._unregistered_cwd_notice([]) is None

    def test_names_the_project_and_the_add_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        proot = _grimoire_project(tmp_path, "grimoire-kit")
        monkeypatch.chdir(proot)
        notice = cmd_cockpit._unregistered_cwd_notice([])
        assert notice is not None
        assert str(proot) in notice
        assert f"grimoire cockpit add {proot}" in notice

    def test_none_when_already_registered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        proot = _grimoire_project(tmp_path, "deja-la")
        monkeypatch.chdir(proot)
        registry = [{"name": "deja-la", "path": str(proot), "slug": "deja-la"}]
        assert cmd_cockpit._unregistered_cwd_notice(registry) is None


class TestListCommand:
    """``grimoire cockpit list`` — critère d'arrêt de l'issue #341."""

    def test_unregistered_project_is_named(
        self, cockpit_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proot = _grimoire_project(tmp_path, "grimoire-kit")
        monkeypatch.chdir(proot)
        result = runner.invoke(app, ["cockpit", "list"])
        assert result.exit_code == 0
        # Rich réenveloppe la ligne selon la largeur du terminal de test : on
        # normalise les espaces (y compris les sauts de ligne insérés) avant
        # de chercher la commande complète, plutôt que de dépendre du wrap.
        collapsed = " ".join(result.output.split())
        assert str(proot) in collapsed
        assert f"grimoire cockpit add {proot}" in collapsed

    def test_empty_registry_names_scan(
        self, cockpit_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare = tmp_path / "pas-grimoire"
        bare.mkdir()
        monkeypatch.chdir(bare)
        result = runner.invoke(app, ["cockpit", "list"])
        assert result.exit_code == 0
        assert "grimoire cockpit scan" in result.output

    def test_no_parasite_message_when_already_registered(
        self, cockpit_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Critère d'arrêt explicite de l'issue : rien de plus depuis un projet déjà enregistré."""
        proot = _grimoire_project(tmp_path, "deja-la")
        project_registry.save_registry([{"name": "deja-la", "path": str(proot), "slug": "deja-la"}])
        monkeypatch.chdir(proot)
        result = runner.invoke(app, ["cockpit", "list"])
        assert result.exit_code == 0
        assert "n'est pas enregistré" not in result.output
        assert "cockpit add" not in result.output
