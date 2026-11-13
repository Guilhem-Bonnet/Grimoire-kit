"""Routes de l'Atelier de spec (lot 1) : drapeau, bibliothèque, document, édition d'un bloc, 409."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.tools import workspace_atelier as wa
from grimoire.tools.workspace_routes import WORKSPACE_UNHANDLED, workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "# réglages du projet\nproject:\n  name: demo   # le nom\nuser:\n  name: x\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    return tmp_path


def _on(root: Path) -> Path:
    workspace_post(root, f"{P}features", {"atelier": True})
    return root


def _put(root: Path, slug: str, block: str, body: dict) -> tuple[int, dict]:
    return wa.put_http(root, f"{P}atelier/chantiers/{slug}/blocs/{block}", json.dumps(body).encode())


def test_flag_off_by_default_and_atelier_routes_are_404(project: Path) -> None:
    assert workspace_get(project, f"{P}features", {}) == {"atelier": False}
    for path in ("atelier/chantiers", "atelier/chantiers/x"):
        with pytest.raises(FileNotFoundError):
            workspace_get(project, P + path, {})
    with pytest.raises(FileNotFoundError):
        workspace_post(project, f"{P}atelier/chantiers", {"titre": "x"})
    assert _put(project, "x", "brief.abc", {"text": "a", "expected": "b"})[0] == 404


def test_flag_write_keeps_comments_and_round_trips(project: Path) -> None:
    assert workspace_post(project, f"{P}features", {"atelier": True}) == {"atelier": True}
    text = (project / "project-context.yaml").read_text(encoding="utf-8")
    assert "# réglages du projet" in text and "# le nom" in text
    assert "cockpit:" in text and "atelier: true" in text
    assert workspace_post(project, f"{P}features", {"atelier": False}) == {"atelier": False}
    with pytest.raises(ValueError):
        workspace_post(project, f"{P}features", {"atelier": "oui"})


def test_flag_must_be_exactly_true(project: Path) -> None:
    (project / "project-context.yaml").write_text(CONTEXT + "cockpit:\n  test_features:\n    atelier: \"true\"\n")
    assert wa.enabled(project) is False
    (project / "project-context.yaml").write_text("cockpit: 3\n")
    assert wa.enabled(project) is False


def test_library_create_and_errors(project: Path) -> None:
    _on(project)
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"] == []
    made = workspace_post(project, f"{P}atelier/chantiers", {"titre": "Dicter une idée"})["chantier"]
    assert made["slug"] == "dicter-une-idee" and made["etat"] == "idee" and made["phases"] == ["vide"] * 5
    with pytest.raises(ValueError, match="existe déjà"):
        workspace_post(project, f"{P}atelier/chantiers", {"titre": "Dicter une idée"})
    with pytest.raises(ValueError, match="réservé"):
        workspace_post(project, f"{P}atelier/chantiers", {"titre": "x", "slug": "principal"})
    (project / "_grimoire/cadrage/cassé").mkdir()
    (project / "_grimoire/cadrage/cassé/chantier.yaml").write_text("[pas un dict", encoding="utf-8")
    rows = {r["slug"]: r for r in workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"]}
    assert rows["cassé"]["error"] and rows["cassé"]["chemin"] == "_grimoire/cadrage/cassé/chantier.yaml"
    assert rows["dicter-une-idee"]["titre"] == "Dicter une idée"


def test_document_has_five_phases_with_blocks(project: Path) -> None:
    _on(project)
    workspace_post(project, f"{P}atelier/chantiers", {"titre": "Un"})
    doc = workspace_get(project, f"{P}atelier/chantiers/un", {})
    assert [p["id"] for p in doc["phases"]] == ["brief", "brainstorm", "comprehension", "exigences", "cahier-des-charges"]
    brief = doc["phases"][0]
    assert brief["fingerprint"] and any(b["kind"] == "rubrique" for b in brief["blocks"])
    with pytest.raises(FileNotFoundError):
        workspace_get(project, f"{P}atelier/chantiers/inconnu", {})
    with pytest.raises(FileNotFoundError):
        workspace_get(project, f"{P}atelier/chantiers/..%2Fx", {})


def test_principal_opens_like_the_others(project: Path) -> None:
    from grimoire.core.cadrage import scaffold

    _on(project)
    scaffold(project, project_name="demo")
    doc = workspace_get(project, f"{P}atelier/chantiers/principal", {})
    assert doc["chantier"]["slug"] == "principal" and doc["phases"][0]["blocks"]


def test_block_edit_changes_only_its_range_and_stale_write_is_409(project: Path) -> None:
    _on(project)
    workspace_post(project, f"{P}atelier/chantiers", {"titre": "Un"})
    path = project / "_grimoire/cadrage/un/02-brainstorm.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n- Une piste\n- Une autre\r\n", encoding="utf-8", newline="")
    before = path.read_bytes()
    phase = workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"][1]
    block = next(b for b in phase["blocks"] if b["text"] == "- Une piste")
    code, out = _put(project, "un", block["id"], {"text": "- Une piste, précisée", "expected": phase["fingerprint"]})
    assert code == 200, out
    assert path.read_bytes() == before.replace(b"- Une piste\n", "- Une piste, précisée\n".encode(), 1)
    assert out["phase"]["fingerprint"] != phase["fingerprint"]
    # Avec la même empreinte périmée : refusé, fichier intact.
    snapshot = path.read_bytes()
    code, out = _put(project, "un", block["id"], {"text": "- Écrasé", "expected": phase["fingerprint"]})
    assert code == 409 and out["conflict"] is True and path.read_bytes() == snapshot


def test_block_edit_refusals(project: Path) -> None:
    _on(project)
    workspace_post(project, f"{P}atelier/chantiers", {"titre": "Un"})
    phase = workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"][0]
    rubrique = next(b for b in phase["blocks"] if b["kind"] == "rubrique")
    editable = next(b for b in phase["blocks"] if b["editable"])
    fp = phase["fingerprint"]
    assert _put(project, "un", rubrique["id"], {"text": "## X", "expected": fp})[0] == 403
    assert _put(project, "un", editable["id"], {"text": "  ", "expected": fp})[0] == 400
    assert _put(project, "un", editable["id"], {"text": "x"})[0] == 400
    assert _put(project, "un", "brief.inconnu", {"text": "x", "expected": fp})[0] == 409
    assert _put(project, "un", "zzz.inconnu", {"text": "x", "expected": fp})[0] == 404
    assert _put(project, "un", editable["id"], {"text": "x" * (wa.MAX_BLOCK_CHARS + 1), "expected": fp})[0] == 400
    assert wa.put_http(project, f"{P}atelier/chantiers/un/blocs/x", b"{pas du json")[0] == 400
    assert wa.put_http(project, f"{P}atelier/autre", b"{}")[0] == 404


def test_unhandled_paths_stay_unhandled(project: Path) -> None:
    assert workspace_get(project, "/api/autre", {}) is WORKSPACE_UNHANDLED
