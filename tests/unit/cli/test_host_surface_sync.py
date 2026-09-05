"""Le rendu des surfaces hôtes dit quand il échoue.

Trois commandes régénéraient les surfaces (agents, skills, hooks) derrière un
`except Exception: return []`, avec pour justification que `host status`
rapporterait la dérive. Un projet dont les surfaces ne se rendent pas sortait
donc d'`init` avec un rapport vert et aucun hook — et l'utilisateur ne l'apprenait
qu'en cherchant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.hosts import collect

runner = CliRunner()


def _boum(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("boum : frontmatter d'agent invalide")


@pytest.fixture
def broken_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collect, "build_surface", _boum)


def test_init_says_when_host_surfaces_could_not_be_rendered(tmp_path: Path, broken_surface: None) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--name", "p"])
    assert result.exit_code == 0, result.output  # l'installation reste un succès…
    assert "surfaces hôtes" in result.output  # …mais on ne prétend pas qu'elle est complète
    assert "boum" in result.output
    assert "grimoire host sync" in result.output


def test_standard_init_reports_the_host_surface_failure_in_json(tmp_path: Path, broken_surface: None) -> None:
    result = runner.invoke(app, ["-o", "json", "standard", "init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "boum" in payload["host_surfaces"]["error"]
    assert "boum" in result.output


def test_doctor_fix_says_when_host_surfaces_could_not_be_rendered(cli_project: Path, broken_surface: None) -> None:
    result = runner.invoke(app, ["doctor", str(cli_project), "--fix"])
    assert "surfaces hôtes" in result.output
    assert "boum" in result.output


def test_a_healthy_project_syncs_without_error(cli_project: Path) -> None:
    from grimoire.hosts.sync import sync_host_surfaces

    outcome = sync_host_surfaces(cli_project)
    assert outcome.error == ""
    assert outcome.ok
