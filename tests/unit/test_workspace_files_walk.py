"""La liste des fichiers du cockpit s'arrête à la limite, au lieu de parcourir tout l'arbre.

Constat du 2026-11-12 : ``/api/workspace/files`` mettait 27 à 31 s sur un projet
dont un clone imbriqué porte ~200 worktrees. ``_walk`` faisait
``sorted(root.rglob("*"))`` : il énumérait et triait tout le sous-arbre avant
d'appliquer ``MAX_FILES_PER_TIER``, et la route n'était jamais gardée. Chaque
test dit le défaut qu'il empêche.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_read_cache
from grimoire.tools.workspace_routes import LIVE_ROUTES, PREFIX, workspace_get

FILES = f"{PREFIX}files"


def _count_scandir_entries(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Compte chaque entrée de dossier lue : c'est la mesure du travail réel."""
    visits = [0]
    real = os.scandir

    class _Scan:
        def __init__(self, path: str | os.PathLike[str]) -> None:
            self._scan = real(path)

        def __enter__(self) -> _Scan:
            return self

        def __exit__(self, *exc: object) -> None:
            self._scan.close()

        def __iter__(self) -> _Scan:
            return self

        def __next__(self) -> os.DirEntry[str]:
            entry = next(self._scan)
            visits[0] += 1
            return entry

    monkeypatch.setattr(wa.os, "scandir", _Scan)
    return visits


def _bulk_tree(root: Path, directories: int = 20, per_directory: int = 1000) -> None:
    """20 000 fichiers : assez pour que tout parcourir coûte, pas assez pour le test."""
    for index in range(directories):
        directory = root / f"d{index:02d}"
        directory.mkdir(parents=True)
        for number in range(per_directory):
            (directory / f"f{number:04d}.txt").touch()


# ── Limite d'énumération ────────────────────────────────────────────────────


def test_la_limite_arrete_le_parcours_au_lieu_de_trier_tout_l_arbre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : la limite bornait la réponse, pas le travail — 27 à 31 s
    pour rendre 2000 fichiers d'un arbre à 200 worktrees."""
    _bulk_tree(tmp_path)
    visits = _count_scandir_entries(monkeypatch)

    found, cut = wa._walk(tmp_path, wa.MAX_FILES_PER_TIER)

    assert len(found) == wa.MAX_FILES_PER_TIER
    assert cut is True
    assert visits[0] < 3000, f"{visits[0]} entrées lues pour 2000 fichiers rendus"


def test_les_dossiers_qui_ne_sont_jamais_des_sources_ne_sont_pas_visites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un `.git` ou un `node_modules` plein ne doit pas coûter un parcours ni
    apparaître comme un fichier du projet."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "garde.py").write_text("x", encoding="utf-8")
    for name in (".git", "node_modules", "__pycache__", ".venv", ".mypy_cache"):
        deep = tmp_path / name / "dedans"
        deep.mkdir(parents=True)
        (deep / "bruit.py").write_text("x", encoding="utf-8")
    visits = _count_scandir_entries(monkeypatch)

    found, cut = wa._walk(tmp_path, wa.MAX_FILES_PER_TIER)

    assert [p.name for p in found] == ["garde.py"]
    assert cut is False
    # Seuls la racine (6 entrées) et `src` (1) ont été lues : aucun dossier
    # exclu n'a été ouvert.
    assert visits[0] == 7


def test_un_lien_symbolique_n_est_jamais_suivi(tmp_path: Path) -> None:
    """Suivre un lien sort du projet (ou boucle) et ferait entrer le monde
    entier dans la réponse."""
    outside = tmp_path / "hors"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    project = tmp_path / "projet"
    project.mkdir()
    (project / "vrai.txt").write_text("x", encoding="utf-8")
    (project / "lien").symlink_to(outside, target_is_directory=True)
    (project / "fichier-lien.txt").symlink_to(project / "vrai.txt")

    found, cut = wa._walk(project, wa.MAX_FILES_PER_TIER)

    assert [p.name for p in found] == ["vrai.txt"]
    assert cut is False


# ── Budget de temps ─────────────────────────────────────────────────────────


def test_au_dela_du_budget_le_parcours_s_arrete_et_le_dit(tmp_path: Path) -> None:
    """Comme la vue des preuves : une réponse partielle est dite partielle."""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    # 1er appel : dossier lu ; 2e : a.txt ; 3e : l'échéance est dépassée.
    ticks = iter([0.0, 0.0, 5.0])

    found, cut = wa._walk(tmp_path, wa.MAX_FILES_PER_TIER, deadline=2.0, clock=lambda: next(ticks, 5.0))

    assert cut is True
    assert [p.name for p in found] == ["a.txt"]


def test_files_view_s_arrete_au_budget_et_le_dit(tmp_path: Path) -> None:
    """Le budget couvre aussi l'entrée dans les fichiers (empreinte, catalogue),
    pas seulement l'énumération."""
    kit = tmp_path / "_grimoire" / "kit"
    kit.mkdir(parents=True)
    (kit / "a.md").write_text("x", encoding="utf-8")

    tree = wa.files_view(tmp_path, tier="kit", budget=0)

    tier = tree["tiers"][0]
    assert tier["truncated"] is True
    assert tier["count"] == 0


# ── Même contenu qu'avant ───────────────────────────────────────────────────


def test_un_petit_arbre_rend_la_meme_liste_et_le_meme_ordre_qu_avant(tmp_path: Path) -> None:
    """Cinquième critère : mêmes fichiers, même ordre que ``sorted`` par chemin.

    ``a/b.txt`` précède ``a/b/`` en tri lexicographique complet ('.' < '/') :
    un parcours dossier par dossier sans tri final les inverserait.
    """
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b.txt").write_text("1", encoding="utf-8")
    (tmp_path / "a" / "b" / "c.txt").write_text("2", encoding="utf-8")
    (tmp_path / "a" / "b" / "a.txt").write_text("3", encoding="utf-8")
    (tmp_path / "z.md").write_text("4", encoding="utf-8")
    expected = [p for p in sorted(tmp_path.rglob("*")) if p.is_file() and not p.is_symlink()]

    found, cut = wa._walk(tmp_path, wa.MAX_FILES_PER_TIER)

    assert found == expected
    assert cut is False


# ── Cache de lecture ────────────────────────────────────────────────────────


def test_la_route_files_est_une_lecture_gardee() -> None:
    """Sans cette entrée, chaque ouverture de page repayait le parcours."""
    assert FILES in LIVE_ROUTES


def test_deux_appels_de_suite_a_la_route_files_font_un_seul_parcours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : deux appels de suite prenaient le même temps, rien n'était gardé."""
    kit = tmp_path / "_grimoire" / "kit"
    kit.mkdir(parents=True)
    (kit / "a.md").write_text("x", encoding="utf-8")
    workspace_read_cache.clear()
    calls: list[int] = []
    real = wa._walk

    def spy(*args: object, **kwargs: object) -> tuple[list[Path], bool]:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wa, "_walk", spy)

    first = workspace_get(tmp_path, FILES, {})
    after_first = len(calls)
    second = workspace_get(tmp_path, FILES, {})

    assert after_first > 0
    assert second == first
    assert len(calls) == after_first, "le second appel doit venir du cache"
