"""Atelier : l'empreinte des suggestions et des commentaires, pour qu'un dépôt de l'agent s'affiche sans reprendre le focus.

Aucune route de plus : la lecture d'entretien (déjà sondée) et le document portent ``annexes`` (une empreinte par magasin,
``suggestions.json`` et ``comments.json``). Ce qui se prouve : absent vaut ``""``, un dépôt la fait bouger, le document et le
pouls disent la même chose, un chantier inconnu est refusé, un lien symbolique n'est jamais suivi, et la lecture n'écrit rien.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_assistant as asst
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
BRAINSTORM = "02-brainstorm.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\nuser:\n  name: x\n", encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + "\n- Une piste\n", encoding="utf-8")
    return tmp_path


def _block(root: Path) -> str:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    phase = next(p for p in phases if p["id"] == "brainstorm")
    return str(next(b for b in phase["blocks"] if b["text"] == "- Une piste")["id"])


def _suggest(root: Path) -> dict[str, Any]:
    code, out = wa.post_http(root, f"{P}atelier/chantiers/un/suggestions", {"block_id": _block(root), "texte": "- Une piste, précisée"})
    assert code == 200, out
    return dict(out["suggestion"])


def _comment(root: Path) -> None:
    code, out = wa.post_http(root, f"{P}atelier/chantiers/un/comments", {"block_id": _block(root), "texte": "Pourquoi ?", "quote": "Une piste", "start": 2})
    assert code == 200, out


def test_no_store_means_empty_fingerprints(project: Path) -> None:
    assert wa.annex_fingerprints(project, "un") == {"suggestions": "", "commentaires": ""}


def test_a_suggestion_moves_only_the_suggestions_fingerprint(project: Path) -> None:
    before = wa.annex_fingerprints(project, "un")
    _suggest(project)
    after = wa.annex_fingerprints(project, "un")
    assert after["suggestions"] and after["suggestions"] != before["suggestions"]
    assert after["commentaires"] == before["commentaires"] == ""


def test_a_comment_moves_only_the_comments_fingerprint(project: Path) -> None:
    before = wa.annex_fingerprints(project, "un")
    _comment(project)
    after = wa.annex_fingerprints(project, "un")
    assert after["commentaires"] and after["commentaires"] != before["commentaires"]
    assert after["suggestions"] == before["suggestions"] == ""


def test_the_document_and_the_interview_say_the_same(project: Path) -> None:
    _suggest(project)
    _comment(project)
    marks = wa.annex_fingerprints(project, "un")
    assert wa.document(project, "un")["annexes"] == marks
    assert asst.entretien_view(project, "un")["annexes"] == marks
    assert wa.get(project, f"{P}atelier/chantiers/un/entretien", {})["annexes"] == marks


def test_reading_writes_nothing(project: Path) -> None:
    _suggest(project)
    snapshot = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    wa.annex_fingerprints(project, "un")
    asst.entretien_view(project, "un")
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == snapshot


def test_an_unknown_chantier_is_refused(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        wa.annex_fingerprints(project, "inconnu")
    with pytest.raises(FileNotFoundError):
        wa.annex_fingerprints(project, "../etc")


def test_the_default_chantier_has_fingerprints_too(project: Path) -> None:
    assert set(wa.annex_fingerprints(project, "principal")) == {"suggestions", "commentaires"}


def test_a_symlinked_store_is_never_followed(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors") / "secret.json"
    outside.write_text("hors du projet", encoding="utf-8")
    (project / "_grimoire/cadrage/un/suggestions.json").symlink_to(outside)
    assert wa.annex_fingerprints(project, "un")["suggestions"] == ""


def test_the_view_survives_a_chantier_without_readable_folder(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(root: Path, slug: str) -> dict[str, str]:
        raise FileNotFoundError(slug)

    monkeypatch.setattr(wa, "annex_fingerprints", _boom)
    assert asst.entretien_view(project, "un")["annexes"] == {}
