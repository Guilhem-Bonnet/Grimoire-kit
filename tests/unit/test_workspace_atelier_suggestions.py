"""Suggestions de l'Atelier de spec (lot 2) : magasin, dépôt, décision humaine, périmée = 409, outil MCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import atelier_suggestions as store
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_suggestions as sugg
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "project:\n  name: demo\nuser:\n  name: x\n"
BRAINSTORM = "02-brainstorm.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + "\n- Une piste\n- Une autre\n", encoding="utf-8")
    return tmp_path


def _phase(root: Path, slug: str = "un") -> dict[str, Any]:
    phases = workspace_get(root, f"{P}atelier/chantiers/{slug}", {})["phases"]
    return next(p for p in phases if p["id"] == "brainstorm")


def _block(root: Path, text: str = "- Une piste") -> str:
    return str(next(b for b in _phase(root)["blocks"] if b["text"] == text)["id"])


def _post(root: Path, tail: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, f"{P}atelier/chantiers/un/suggestions{tail}", body)


def _file(root: Path) -> bytes:
    return (root / "_grimoire/cadrage/un" / BRAINSTORM).read_bytes()


def _deposit(root: Path, texte: str = "- Une piste, précisée", **extra: Any) -> dict[str, Any]:
    code, out = _post(root, "", {"block_id": _block(root), "texte": texte, "motif": "plus net", **extra})
    assert code == 200, out
    return dict(out["suggestion"])


def test_flag_off_every_suggestion_route_is_404(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    assert _post(tmp_path, "", {"block_id": "brainstorm.x", "texte": "a"})[0] == 404
    assert _post(tmp_path, "/sug-1", {"decision": "accept"})[0] == 404
    with pytest.raises(FileNotFoundError):
        workspace_get(tmp_path, f"{P}atelier/chantiers/un/suggestions", {})
    with pytest.raises(FileNotFoundError):
        sugg.suggest(str(tmp_path), "un", "brainstorm.x", "a")


def test_deposit_never_writes_the_phase_file(project: Path) -> None:
    before = _file(project)
    made = _deposit(project, posture="sceptique", auteur="claude")  # l'auteur envoyé est ignoré
    assert _file(project) == before
    assert made["etat"] == "proposee" and made["posture"] == "sceptique" and made["auteur"] == "agent"
    assert made["avant"] == "- Une piste" and made["texte"] == "- Une piste, précisée" and made["date"]
    saved = json.loads((project / "_grimoire/cadrage/un" / store.FILENAME).read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["suggestions"]] == [made["id"]]
    # Le document l'affiche sur sa phase ; la bibliothèque la compte.
    assert [s["id"] for s in _phase(project)["suggestions"]] == [made["id"]]
    rows = workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"]
    assert rows[0]["suggestions"] == 1
    # Redéposer la même chose ne crée rien de plus.
    code, again = _post(project, "", {"block_id": made["block_id"], "texte": made["texte"]})
    assert code == 200 and again["already"] is True and again["suggestion"]["id"] == made["id"]
    assert len(workspace_get(project, f"{P}atelier/chantiers/un/suggestions", {})["suggestions"]) == 1


def test_deposit_refusals(project: Path) -> None:
    block = _block(project)
    assert _post(project, "", {"block_id": block, "texte": "  "})[0] == 400
    assert _post(project, "", {"block_id": block})[0] == 400
    assert _post(project, "", {"texte": "a"})[0] == 400
    assert _post(project, "", {"block_id": block, "texte": "- Une piste"})[0] == 400  # identique
    assert _post(project, "", {"block_id": block, "texte": "## Titre"})[0] == 400  # structure
    assert _post(project, "", {"block_id": block, "texte": "a", "posture": "autre"})[0] == 400
    assert _post(project, "", {"block_id": block, "texte": "a", "motif": "m" * 301})[0] == 400
    assert _post(project, "", {"block_id": block, "texte": "a", "motif": 3})[0] == 400
    assert _post(project, "", {"block_id": block, "texte": "x" * (wa.MAX_BLOCK_CHARS + 1)})[0] == 400
    assert _post(project, "", {"block_id": "brainstorm.inconnu", "texte": "a"})[0] == 409
    assert _post(project, "", {"block_id": "zzz.x", "texte": "a"})[0] == 404
    rubrique = next(b for b in _phase(project)["blocks"] if b["kind"] == "rubrique")
    assert _post(project, "", {"block_id": rubrique["id"], "texte": "x"})[0] == 403
    assert wa.post_http(project, f"{P}atelier/chantiers/inconnu/suggestions", {"block_id": block, "texte": "a"})[0] == 404
    assert wa.post_http(project, f"{P}atelier/chantiers/un/suggestions/a/b", {})[0] == 404
    assert not (project / "_grimoire/cadrage/un" / store.FILENAME).exists()


def test_pending_cap(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_PENDING", 2)
    _deposit(project, "- A")
    _deposit(project, "- B")
    assert _post(project, "", {"block_id": _block(project), "texte": "- C"})[0] == 400


def test_accept_rewrites_only_the_block_range(project: Path) -> None:
    made = _deposit(project)
    before = _file(project)
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 200, out
    assert _file(project) == before.replace(b"- Une piste\n", "- Une piste, précisée\n".encode(), 1)
    assert out["suggestion"]["etat"] == "acceptee" and out["suggestion"]["decide_le"]
    assert _phase(project)["suggestions"] == []
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"][0]["suggestions"] == 0
    # Une décision ne se rejoue pas.
    assert _post(project, f"/{made['id']}", {"decision": "reject"})[0] == 409


def test_reject_keeps_the_file_and_records_the_reason(project: Path) -> None:
    made = _deposit(project)
    before = _file(project)
    code, out = _post(project, f"/{made['id']}", {"decision": "reject", "motif": "trop vague"})
    assert code == 200 and _file(project) == before
    assert out["suggestion"]["etat"] == "refusee" and out["suggestion"]["decide_motif"] == "trop vague"
    stored = workspace_get(project, f"{P}atelier/chantiers/un/suggestions", {})["suggestions"]
    assert stored[0]["etat"] == "refusee" and stored[0]["decide_motif"] == "trop vague"
    assert _phase(project)["suggestions"] == []


def test_block_changed_since_deposit_is_409_and_perimee(project: Path) -> None:
    made = _deposit(project)
    path = project / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("- Une piste\n", "- Une piste modifiée ailleurs\n"))
    changed = path.read_bytes()
    # La lecture le dit déjà, sans rien écrire.
    assert workspace_get(project, f"{P}atelier/chantiers/un/suggestions", {})["suggestions"][0]["etat"] == "perimee"
    assert _phase(project)["suggestions"] == []
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 409 and out["conflict"] is True and path.read_bytes() == changed
    assert store.load(path.parent)[0].etat == "perimee"
    assert _post(project, f"/{made['id']}", {"decision": "accept"})[0] == 409


def test_another_block_changing_does_not_stale_the_suggestion(project: Path) -> None:
    made = _deposit(project)
    path = project / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8").replace("- Une autre\n", "- Une autre, changée\n"))
    assert _post(project, f"/{made['id']}", {"decision": "accept"})[0] == 200


def test_race_between_check_and_write_is_perimee(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = _deposit(project)

    def conflict(*_a: Any, **_k: Any) -> Any:
        raise wa.ConflictError("a changé")

    monkeypatch.setattr(wa, "edit_block", conflict)
    assert _post(project, f"/{made['id']}", {"decision": "accept"})[0] == 409
    assert store.load(project / "_grimoire/cadrage/un")[0].etat == "perimee"


def test_decision_refusals(project: Path) -> None:
    made = _deposit(project)
    assert _post(project, f"/{made['id']}", {})[0] == 400
    assert _post(project, f"/{made['id']}", {"decision": "peut-être"})[0] == 400
    assert _post(project, f"/{made['id']}", {"decision": "accept", "motif": "m" * 301})[0] == 400
    assert _post(project, "/sug-inconnue", {"decision": "accept"})[0] == 404
    assert store.load(project / "_grimoire/cadrage/un")[0].etat == "proposee"


def test_a_dictated_decision_is_refused(project: Path) -> None:
    """Comme `chat/permission` : un corps issu d'une dictée ne tranche pas, seul le clic à l'écran le fait."""
    made = _deposit(project)
    before = _file(project)
    code, out = _post(project, f"/{made['id']}", {"decision": "accept", "source": "voice"})
    assert code == 400 and "clic à l'écran" in out["error"]
    assert _file(project) == before and store.load(project / "_grimoire/cadrage/un")[0].etat == "proposee"


def test_principal_chantier_takes_suggestions(project: Path) -> None:
    from grimoire.core.cadrage import scaffold

    scaffold(project, project_name="demo")
    block = next(b for b in workspace_get(project, f"{P}atelier/chantiers/principal", {})["phases"][0]["blocks"] if b["editable"])
    code, out = wa.post_http(project, f"{P}atelier/chantiers/principal/suggestions", {"block_id": block["id"], "texte": "Autre"})
    assert code == 200 and (project / "_grimoire/cadrage" / store.FILENAME).is_file()
    rows = {r["slug"]: r for r in workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"]}
    assert rows["principal"]["suggestions"] == 1 and rows["un"]["suggestions"] == 0
    assert workspace_get(project, f"{P}atelier/chantiers/principal", {})["phases"][0]["suggestions"][0]["id"] == out["suggestion"]["id"]


def test_store_load_tolerates_damage(tmp_path: Path) -> None:
    assert store.load(tmp_path) == []
    (tmp_path / store.FILENAME).write_text("{pas du json", encoding="utf-8")
    assert store.load(tmp_path) == []
    (tmp_path / store.FILENAME).write_text(json.dumps({"suggestions": "non"}), encoding="utf-8")
    assert store.load(tmp_path) == []
    good = {"id": "a", "phase": "brief", "block_id": "brief.x", "block_fingerprint": "f", "avant": "", "texte": "t",
            "motif": "", "posture": "explorer", "auteur": "agent", "date": "d", "extra": 1}
    (tmp_path / store.FILENAME).write_text(json.dumps({"suggestions": [good, {"id": "b"}, 3, {**good, "id": "c", "date": None}]}))
    assert [s.id for s in store.load(tmp_path)] == ["a"]  # champ mal typé, absent, pas un objet : ignorés
    incomplete = {k: v for k, v in good.items() if k != "phase"}
    (tmp_path / store.FILENAME).write_text(json.dumps({"suggestions": [incomplete]}))
    assert store.load(tmp_path) == []


# ── L'agent n'a aucun moyen de trancher ─────────────────────────────────────


def test_mcp_tool_only_deposits_and_cannot_accept_its_own_suggestion(project: Path) -> None:
    import inspect

    from grimoire.mcp import atelier_tools
    from grimoire.mcp.server import mcp

    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "atelier_suggest" in names
    assert not [n for n in names if n.startswith("atelier") and n != "atelier_suggest"]
    # Aucun paramètre de décision, aucune fonction de décision exposée par le module de l'outil.
    params = set(inspect.signature(atelier_tools.atelier_suggest).parameters)
    assert not params & {"decision", "accept", "reject", "suggestion_id", "id", "state", "etat"}
    assert not [n for n in vars(atelier_tools) if "decid" in n.lower() or "accept" in n.lower()]

    before = _file(project)
    raw = atelier_tools.atelier_suggest("un", _block(project), "- Une piste, précisée", "plus net", "simplificateur", str(project))
    made = json.loads(raw)["suggestion"]
    assert made["etat"] == "proposee" and made["auteur"] == "agent" and made["posture"] == "simplificateur"
    assert _file(project) == before
    # Rappeler l'outil avec le même texte rend la même suggestion, toujours en attente : il n'accepte rien.
    again = json.loads(atelier_tools.atelier_suggest("un", _block(project), "- Une piste, précisée", project_path=str(project)))
    assert again["suggestion"]["etat"] == "proposee" and again["already"] is True
    assert _file(project) == before and store.load(project / "_grimoire/cadrage/un")[0].etat == "proposee"


def test_mcp_tool_refusals_are_tool_errors(project: Path) -> None:
    from grimoire.mcp import atelier_tools

    result = atelier_tools.atelier_suggest("un", "brainstorm.inconnu", "x", project_path=str(project))
    assert result.is_error is True and "existe plus" in result.content[0].text
    bad = atelier_tools.atelier_suggest("un", _block(project), "x", posture="zz", project_path=str(project))
    assert bad.is_error is True and "posture" in bad.content[0].text
    assert not (project / "_grimoire/cadrage/un" / store.FILENAME).exists()
