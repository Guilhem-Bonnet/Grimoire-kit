"""Atelier : l'empreinte des fichiers de phase, pour recharger le document quand un fichier change hors du cockpit.

Aucune route de plus : la lecture d'entretien (déjà sondée par l'assistant) porte ``fichiers`` (une empreinte par phase). Ce qui se
prouve : elle est celle que ``GET`` du document donne à chaque phase, elle bouge quand le fichier change (même d'un octet), un
fichier absent vaut ``""``, un chantier inconnu est refusé, un lien symbolique n'est jamais suivi, et la lecture n'écrit rien.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.cadrage import PHASES
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_assistant as asst
from grimoire.tools.workspace_routes import workspace_post

P = "/api/workspace/"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    return tmp_path


def _brainstorm(root: Path) -> Path:
    return root / "_grimoire/cadrage/un/02-brainstorm.md"


def test_fingerprints_are_the_ones_the_document_serves(project: Path) -> None:
    doc = wa.document(project, "un")
    marks = wa.phase_fingerprints(project, "un")
    assert set(marks) == {p.id for p in PHASES}
    assert marks == {p["id"]: p["fingerprint"] for p in doc["phases"]}
    assert all(len(v) == 64 for v in marks.values() if v)


def test_a_one_byte_change_moves_only_that_phase(project: Path) -> None:
    before = wa.phase_fingerprints(project, "un")
    path = _brainstorm(project)
    path.write_text(path.read_text(encoding="utf-8") + "x", encoding="utf-8")
    after = wa.phase_fingerprints(project, "un")
    changed = [k for k in before if before[k] != after[k]]
    assert changed == ["brainstorm"]
    assert after == {p["id"]: p["fingerprint"] for p in wa.document(project, "un")["phases"]}


def test_a_missing_file_is_an_empty_fingerprint(project: Path) -> None:
    path = _brainstorm(project)
    path.unlink()
    assert wa.phase_fingerprints(project, "un")["brainstorm"] == ""


def test_a_file_that_is_not_utf8_still_has_the_documents_fingerprint(project: Path) -> None:
    _brainstorm(project).write_bytes(b"# Brainstorm\n\n\xff\xfe pas de l'utf-8\n")
    doc = wa.document(project, "un")
    assert wa.phase_fingerprints(project, "un")["brainstorm"] == next(p for p in doc["phases"] if p["id"] == "brainstorm")["fingerprint"]


def test_an_unknown_chantier_is_refused(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        wa.phase_fingerprints(project, "inconnu")
    with pytest.raises(FileNotFoundError):
        wa.phase_fingerprints(project, "../etc")


def test_a_symlinked_phase_file_is_never_followed(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors") / "secret.md"
    outside.write_text("hors du projet", encoding="utf-8")
    path = _brainstorm(project)
    path.unlink()
    path.symlink_to(outside)
    assert wa.phase_fingerprints(project, "un")["brainstorm"] == ""


def test_the_default_chantier_has_fingerprints_too(project: Path) -> None:
    marks = wa.phase_fingerprints(project, "principal")
    assert set(marks) == {p.id for p in PHASES}


def test_the_interview_view_carries_them_and_writes_nothing(project: Path) -> None:
    snapshot = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    view = asst.entretien_view(project, "un")
    assert view["fichiers"] == wa.phase_fingerprints(project, "un")
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == snapshot


def test_the_get_route_serves_them(project: Path) -> None:
    body = wa.get(project, f"{P}atelier/chantiers/un/entretien", {})
    assert body["fichiers"] == wa.phase_fingerprints(project, "un")


def test_the_view_survives_a_chantier_without_readable_folder(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(root: Path, slug: str) -> dict[str, str]:
        raise FileNotFoundError(slug)

    monkeypatch.setattr(wa, "phase_fingerprints", _boom)
    assert asst.entretien_view(project, "un")["fichiers"] == {}
