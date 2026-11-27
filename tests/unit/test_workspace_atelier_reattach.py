"""« Rattacher ici » (Atelier de spec, lot 3) : un fil orphelin s'ancre à la sélection ou au bloc que l'humain désigne.

Geste humain seulement : mêmes refus que résoudre (dictée), identifiants validés, bornes de taille, aucun outil MCP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import atelier_comments as store
from grimoire.mcp import atelier_tools
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_comments as com
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "project:\n  name: demo\nuser:\n  name: x\n"
BRAINSTORM = "02-brainstorm.md"
PARA_A = "Le bouton maintenu sert à parler tant que la touche est tenue."
PARA_B = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "tant que la touche est tenue"
NEW_A = "Le bouton maintenu sert à parler jusqu'au relâchement de la touche."


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + f"\n\n{PARA_A}\n\n{PARA_B}\n", encoding="utf-8")
    return tmp_path


def _block(root: Path, text: str, phase: str = "brainstorm") -> str:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    found = next(p for p in phases if p["id"] == phase)
    return str(next(b for b in found["blocks"] if b["text"] == text)["id"])


def _post(root: Path, cid: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, f"{P}atelier/chantiers/un/comments/{cid}", body)


def _orphan(root: Path) -> str:
    code, out = wa.post_http(
        root, f"{P}atelier/chantiers/un/comments", {"block_id": _block(root, PARA_A), "texte": "Pourquoi ?", "quote": QUOTE}
    )
    assert code == 200, out
    file = root / "_grimoire/cadrage/un" / BRAINSTORM
    # Hors cockpit, le passage ET ses voisins changent : rien ne le suit (même place perdue, texte trop différent).
    file.write_text(file.read_text(encoding="utf-8").replace(PARA_A, NEW_A).replace(PARA_B, "Autre sujet."), encoding="utf-8")
    thread = workspace_get(root, f"{P}atelier/chantiers/un/comments", {})["comments"][0]
    assert thread["anchor"]["state"] == "orphelin"
    return str(out["comment"]["id"])


def _thread(root: Path) -> dict[str, Any]:
    return dict(workspace_get(root, f"{P}atelier/chantiers/un/comments", {})["comments"][0])


def test_an_orphan_is_attached_to_the_selected_passage_and_keeps_its_trace(project: Path) -> None:
    cid = _orphan(project)
    before = _thread(project)
    code, out = _post(project, cid, {"action": "attach", "block_id": _block(project, NEW_A), "quote": "jusqu'au relâchement"})
    assert code == 200, out
    assert out["comment"]["anchor"]["state"] == "ancre" and out["comment"]["quote"] == "jusqu'au relâchement"
    again = _thread(project)
    assert again["anchor"]["state"] == "ancre" and again["orphelin"] is False and again["deplace"] is False
    (trace,) = again["historique"]
    assert trace["avant"] == {"block_id": before["block_id"], "quote": QUOTE, "start": before["start"]}
    assert trace["apres"]["quote"] == "jusqu'au relâchement" and trace["apres"]["start"] == NEW_A.index("jusqu'au")
    assert trace["date"]
    assert store.load(project / "_grimoire/cadrage/un")[0].historique == [trace]


def test_without_a_selection_the_orphan_is_attached_to_the_whole_block(project: Path) -> None:
    cid = _orphan(project)
    code, out = _post(project, cid, {"action": "attach", "block_id": _block(project, NEW_A)})
    assert code == 200, out
    assert out["comment"]["quote"] == NEW_A and out["comment"]["start"] == 0
    assert _thread(project)["anchor"]["state"] == "ancre"


def test_a_thread_that_is_not_orphan_or_is_resolved_is_not_attached(project: Path) -> None:
    code, made = wa.post_http(
        project, f"{P}atelier/chantiers/un/comments", {"block_id": _block(project, PARA_B), "texte": "ok", "quote": "second"}
    )
    cid = str(made["comment"]["id"])
    code, out = _post(project, cid, {"action": "attach", "block_id": _block(project, PARA_A)})
    assert code == 409 and out["reason"] == "pas-orphelin"
    assert _thread(project)["historique"] == []
    other = _orphan_after(project)
    _post(project, other, {"action": "resolve"})
    code, out = _post(project, other, {"action": "attach", "block_id": _block(project, NEW_A)})
    assert code == 409 and out["reason"] == "resolu"


def _orphan_after(root: Path) -> str:
    code, out = wa.post_http(
        root, f"{P}atelier/chantiers/un/comments", {"block_id": _block(root, PARA_A), "texte": "Deux", "quote": QUOTE}
    )
    assert code == 200, out
    file = root / "_grimoire/cadrage/un" / BRAINSTORM
    file.write_text(file.read_text(encoding="utf-8").replace(PARA_A, NEW_A).replace(PARA_B, "Autre sujet."), encoding="utf-8")
    return str(out["comment"]["id"])


def test_a_dictated_gesture_does_not_attach(project: Path) -> None:
    from grimoire.voice import dictation

    cid = _orphan(project)
    token = dictation.issue(project, "rattache le fil au bloc")
    code, out = _post(project, cid, {"action": "attach", "block_id": _block(project, NEW_A), "dictation_tokens": [token]})
    assert code == 400 and "écran" in out["error"]
    assert _thread(project)["anchor"]["state"] == "orphelin" and _thread(project)["historique"] == []
    dictation.reset()


def test_forged_identifiers_sizes_and_passages_are_refused_and_write_nothing(project: Path) -> None:
    cid = _orphan(project)
    file = project / "_grimoire/cadrage/un" / store.FILENAME
    saved = file.read_bytes()
    good = _block(project, NEW_A)
    cases: list[tuple[dict[str, Any], int]] = [
        ({"action": "attach"}, 400),  # block_id requis
        ({"action": "attach", "block_id": 12}, 400),
        ({"action": "attach", "block_id": "../../etc/passwd"}, 404),  # jamais lu comme un chemin : un id de bloc inconnu
        ({"action": "attach", "block_id": "x" * 500}, 400),
        ({"action": "attach", "block_id": "brainstorm.inconnu"}, 409),  # bloc disparu
        ({"action": "attach", "block_id": good, "quote": "x" * (store.QUOTE_MAX + 1)}, 400),
        ({"action": "attach", "block_id": good, "quote": "absent du bloc"}, 400),
        ({"action": "attach", "block_id": good, "quote": "jusqu'au", "start": -1}, 400),
        ({"action": "attach", "block_id": good, "quote": "jusqu'au", "start": True}, 400),
        ({"action": "attach", "block_id": good, "quote": "jusqu'au", "start": 0}, 400),
        ({"action": "attach", "block_id": _block(project, "Autre sujet."), "quote": ["x"]}, 400),
    ]
    for body, wanted in cases:
        code, out = _post(project, cid, body)
        assert code == wanted, (body, code, out)
    assert _post(project, "com-00000000", {"action": "attach", "block_id": good})[0] == 404
    assert file.read_bytes() == saved


def test_a_passage_of_another_phase_or_a_structural_block_is_refused(project: Path) -> None:
    cid = _orphan(project)
    phases = workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"]
    other = next(p for p in phases if p["id"] != "brainstorm" and any(b["editable"] for b in p["blocks"]))
    target = next(b for b in other["blocks"] if b["editable"])
    code, out = _post(project, cid, {"action": "attach", "block_id": target["id"]})
    assert code == 400 and "autre phase" in out["error"]
    structural = next((b for p in phases for b in p["blocks"] if not b["editable"] and p["id"] == "brainstorm"), None)
    if structural is not None:
        assert _post(project, cid, {"action": "attach", "block_id": structural["id"]})[0] == 403


def test_the_history_keeps_the_latest_attachments_only(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_HISTORY", 2)
    cid = _orphan(project)
    for word in ("Le bouton", "maintenu", "relâchement"):
        # chaque rattachement, puis le passage disparaît : le fil redevient orphelin pour le suivant
        phases = workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"]
        block = next(b["id"] for p in phases for b in p["blocks"] if word in b["text"] and p["id"] == "brainstorm")
        assert _post(project, cid, {"action": "attach", "block_id": block, "quote": word})[0] == 200
        folder = project / "_grimoire/cadrage/un"
        with store.locked_store(folder):
            rows = store.load(folder)
            rows[0].orphelin = True  # « un orphelin le reste » : l'état écrit au magasin
            store.save(folder, rows)
        assert _thread(project)["anchor"]["state"] == "orphelin"
    assert [h["apres"]["quote"] for h in _thread(project)["historique"]] == ["maintenu", "relâchement"]


def test_no_agent_tool_can_attach(project: Path) -> None:
    import inspect

    names = [n for n in dir(atelier_tools) if "atelier" in n]
    assert not any("rattach" in n or "attach" in n for n in names)
    source = inspect.getsource(atelier_tools)
    assert "reattach" not in source and "comments.act" not in source and "com.act" not in source
    cid = _orphan(project)
    out = com.agent_comment(str(project), "un", "je rattache", _block(project, NEW_A), reply_to=cid)
    assert out["comment"]["id"] == cid and _thread(project)["historique"] == []
    assert json.dumps(com.agent_threads(str(project), "un"))  # lecture seule


def test_a_malformed_history_makes_the_line_ignored_not_the_store(project: Path) -> None:
    cid = _orphan(project)
    folder = project / "_grimoire/cadrage/un"
    data = json.loads((folder / store.FILENAME).read_text(encoding="utf-8"))
    data["comments"].append({**data["comments"][0], "id": "com-bad", "historique": [{"date": 1}]})
    (folder / store.FILENAME).write_text(json.dumps(data), encoding="utf-8")
    items, ignored, damaged = store.read(folder)
    assert [i.id for i in items] == [cid] and ignored == 1 and damaged is None


def test_an_oversized_history_is_bounded_when_read(project: Path) -> None:
    cid = _orphan(project)
    folder = project / "_grimoire/cadrage/un"
    data = json.loads((folder / store.FILENAME).read_text(encoding="utf-8"))
    anchor = {"block_id": "b" * 900, "quote": "q" * 5000, "start": 0}
    data["comments"][0]["historique"] = [{"date": "d" * 500, "avant": anchor, "apres": anchor}] * 500
    (folder / store.FILENAME).write_text(json.dumps(data), encoding="utf-8")
    (item,) = store.read(folder)[0]
    assert item.id == cid and len(item.historique) == store.MAX_HISTORY
    assert all(len(h["avant"]["quote"]) <= store.QUOTE_MAX and len(h["avant"]["block_id"]) <= 200 and len(h["date"]) <= 64 for h in item.historique)
