"""``grimoire host status`` affiche les notes de la surface (issue #373).

``ProjectSurface.notes`` (collisions de faisceau entre agents du kit) n'était
affiché nulle part avant ce lot — une dette du kit restait invisible pour
l'utilisateur qui synchronise un projet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_host
from grimoire.hosts.surface import ProjectSurface

runner = CliRunner()


def _surface_with_notes(project_root: Path, *, project_name: str | None = None) -> ProjectSurface:
    return ProjectSurface(
        project_name=project_name or project_root.name,
        notes=("Agents livrés par le kit au faisceau identique : demo-a == demo-b.",),
    )


def test_les_notes_de_surface_apparaissent_en_texte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_host, "build_surface", _surface_with_notes)

    from grimoire.cli.app import app

    result = runner.invoke(app, ["host", "status", "--host", "claude", "--project-root", str(tmp_path)])

    assert "faisceau identique" in result.stdout


def test_aucune_note_ne_produit_aucune_ligne(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _surface_sans_notes(project_root: Path, *, project_name: str | None = None) -> ProjectSurface:
        return ProjectSurface(project_name=project_name or project_root.name)

    monkeypatch.setattr(cmd_host, "build_surface", _surface_sans_notes)

    from grimoire.cli.app import app

    result = runner.invoke(app, ["host", "status", "--host", "claude", "--project-root", str(tmp_path)])

    assert "[!]" not in result.stdout
