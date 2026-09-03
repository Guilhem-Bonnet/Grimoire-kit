"""Ouvrir un projet ne l'écrit pas.

L'atelier peut désormais se re-router sur n'importe quel dépôt de la machine.
Une découverte qui laisse un fichier dans chaque projet visité rend `git status`
sale partout où l'on est seulement passé : la sélection et l'enrôlement ne
touchent que l'état hors projet (registre et couche de données sous ``~``).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.tools import serve_data
from grimoire.tools.forge_server import ForgeAPI

ROOT = Path(__file__).resolve().parents[2]


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_selecting_a_project_writes_nothing_inside_it(tmp_path: Path) -> None:
    served = tmp_path / "servi"
    (served / "_grimoire").mkdir(parents=True)
    target = tmp_path / "cible"
    (target / "_grimoire").mkdir(parents=True)
    (target / "fichier.txt").write_text("intact", encoding="utf-8")
    before = _snapshot(target)

    api = ForgeAPI(served, ROOT, None)
    api.project_add(str(target))
    api.select_project(path=str(target))

    assert _snapshot(target) == before, "la sélection a écrit dans le projet cible"
    assert _snapshot(served) == {"_grimoire"}, "la sélection a écrit dans le projet quitté"


def test_the_generated_data_layer_lives_outside_the_project(tmp_path: Path) -> None:
    """La couche générée est un cache machine, pas un artefact du dépôt."""
    project = tmp_path / "projet"
    (project / "_grimoire").mkdir(parents=True)
    layer = serve_data.data_dir(project)
    assert not layer.is_relative_to(project)
    assert layer.is_relative_to(Path.home())
