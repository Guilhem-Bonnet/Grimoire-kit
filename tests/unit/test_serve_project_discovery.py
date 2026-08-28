"""L'atelier découvre les projets de la machine et se re-route dessus.

``grimoire serve`` n'exposait aucune surface projet : l'UI retombait sur
l'index de la vitrine, et le bouton « projet » de la barre latérale ne faisait
que recharger la page. La découverte existait, mais uniquement dans la CLI du
cockpit. Ces tests couvrent les trois entrées désormais partagées — parcourir,
scanner, désigner — et le re-routage du serveur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import project_registry as reg
from grimoire.tools.forge_server import ForgeAPI

ROOT = Path(__file__).resolve().parents[2]


def _make_project(root: Path, name: str, *, managed: bool = True) -> Path:
    proot = root / name
    (proot / (".git" if not managed else "_grimoire")).mkdir(parents=True)
    return proot


@pytest.fixture
def api(tmp_path: Path) -> ForgeAPI:
    return ForgeAPI(_make_project(tmp_path, "servi"), ROOT, None)


# ── Registre ─────────────────────────────────────────────────────────────────


def test_the_served_project_is_always_listed(api: ForgeAPI, tmp_path: Path) -> None:
    """Une découverte qui oublie le projet sous les yeux n'en est pas une."""
    view = api.projects_view()
    entry = next(p for p in view["projects"] if p["path"] == str(api.project_root))
    assert entry["unregistered"] is True
    assert view["served"] == str(api.project_root)


def test_registering_is_idempotent(api: ForgeAPI) -> None:
    first = api.project_add(str(api.project_root))
    assert first["slug"]
    again = api.project_add(str(api.project_root))
    assert again["slug"] == first["slug"]
    paths = [p["path"] for p in reg.load_registry()]
    assert paths.count(str(api.project_root)) == 1


def test_adding_an_unknown_path_is_refused(api: ForgeAPI, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        api.project_add(str(tmp_path / "nulle-part"))


# ── Scan ─────────────────────────────────────────────────────────────────────


def test_scan_finds_projects_without_enrolling_them(api: ForgeAPI, tmp_path: Path) -> None:
    """Le scan propose, l'utilisateur dispose.

    Un scan qui enrôle tout seul remplit le registre de dossiers jetables —
    c'est exactement ce qu'ont produit les campagnes d'évals sur ce poste.
    """
    workspace = tmp_path / "espace"
    _make_project(workspace, "avec-grimoire", managed=True)
    _make_project(workspace, "juste-un-depot", managed=False)
    (workspace / "sans-rien").mkdir(parents=True)
    before = [p["path"] for p in reg.load_registry()]

    result = api.project_scan(str(workspace), 3)
    found = {c["name"]: c for c in result["candidates"]}
    assert set(found) == {"avec-grimoire", "juste-un-depot"}
    assert found["avec-grimoire"]["managed"] is True
    assert found["juste-un-depot"]["managed"] is False
    assert all(c["registered"] is False for c in result["candidates"])
    assert [p["path"] for p in reg.load_registry()] == before


def test_scan_does_not_descend_into_a_detected_project(api: ForgeAPI, tmp_path: Path) -> None:
    outer = _make_project(tmp_path / "espace", "depot", managed=True)
    _make_project(outer, "vendored", managed=True)
    result = api.project_scan(str(tmp_path / "espace"), 4)
    assert [c["path"] for c in result["candidates"]] == [str(outer)]


def test_scan_refuses_a_missing_root(api: ForgeAPI, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        api.project_scan(str(tmp_path / "absent"), 2)


# ── Navigation manuelle ──────────────────────────────────────────────────────


def test_browse_lists_subdirectories_and_flags_projects(api: ForgeAPI, tmp_path: Path) -> None:
    workspace = tmp_path / "espace"
    _make_project(workspace, "un-projet", managed=True)
    (workspace / "ordinaire").mkdir(parents=True)
    (workspace / ".cache").mkdir()
    (workspace / "note.txt").write_text("x", encoding="utf-8")

    view = api.browse_view(str(workspace))
    names = {e["name"]: e for e in view["entries"]}
    assert set(names) == {"un-projet", "ordinaire"}, "fichiers et dossiers cachés écartés"
    assert names["un-projet"]["isProject"] is True
    assert names["ordinaire"]["isProject"] is False
    assert view["parent"] == str(tmp_path)


def test_browse_refuses_a_file(api: ForgeAPI, tmp_path: Path) -> None:
    target = tmp_path / "fichier.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        api.browse_view(str(target))


# ── Re-routage ───────────────────────────────────────────────────────────────


def test_selecting_a_project_reroots_the_server(api: ForgeAPI, tmp_path: Path) -> None:
    other = _make_project(tmp_path, "autre", managed=True)
    status = api.select_project(path=str(other))

    assert status["projectRoot"] == str(other)
    assert api.project_root == other
    assert api.data.project_root == other
    assert reg.selected_slug() == status["slug"]


def test_selecting_by_slug_uses_the_registry(api: ForgeAPI, tmp_path: Path) -> None:
    other = _make_project(tmp_path, "par-slug", managed=True)
    slug = api.project_add(str(other))["slug"]
    assert api.select_project(slug=slug)["projectRoot"] == str(other)


def test_selecting_an_unknown_target_is_refused(api: ForgeAPI, tmp_path: Path) -> None:
    served = api.project_root
    for payload in ({"path": str(tmp_path / "nulle-part")}, {"slug": "inconnu"}, {}):
        with pytest.raises(FileNotFoundError):
            api.select_project(**payload)
    assert api.project_root == served, "un refus ne doit pas déplacer la racine servie"


def test_selection_survives_a_registry_entry_going_away(api: ForgeAPI, tmp_path: Path) -> None:
    """Un slug pointant un chemin disparu ne doit pas re-router dans le vide."""
    other = _make_project(tmp_path, "ephemere", managed=True)
    slug = api.project_add(str(other))["slug"]
    (other / "_grimoire").rmdir()
    other.rmdir()
    with pytest.raises(FileNotFoundError):
        api.select_project(slug=slug)
