"""Suggestions de l'Atelier (lot 2), relecture adverse de #1397 : canal bouché, invisibles, magasin abîmé, homonymes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import atelier_suggestions as store
from grimoire.tools import workspace_atelier as wa
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
BRAINSTORM = "02-brainstorm.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + "\n- Une piste\n- Une autre\n", encoding="utf-8")
    return tmp_path


def _phase(root: Path) -> dict[str, Any]:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    return next(p for p in phases if p["id"] == "brainstorm")


def _block(root: Path, text: str = "- Une piste") -> str:
    return str(next(b for b in _phase(root)["blocks"] if b["text"] == text)["id"])


def _post(root: Path, tail: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, f"{P}atelier/chantiers/un/suggestions{tail}", body)


def _path(root: Path) -> Path:
    return root / "_grimoire/cadrage/un" / BRAINSTORM


def _dir(root: Path) -> Path:
    return root / "_grimoire/cadrage/un"


def _edit(root: Path, block_id: str, text: str) -> None:
    current = _path(root).read_text(encoding="utf-8")
    wa.edit_block(root, "un", block_id, {"text": text, "expected": hashlib.sha256(current.encode()).hexdigest()})


def _deposit(root: Path, texte: str = "- Une piste, précisée") -> dict[str, Any]:
    code, out = _post(root, "", {"block_id": _block(root), "texte": texte})
    assert code == 200, out
    return dict(out["suggestion"])


def test_stale_suggestions_do_not_jam_the_channel(project: Path) -> None:
    """100 dépôts sur un bloc, l'humain le modifie : les périmées sont enregistrées et le canal se rouvre."""
    block = _block(project)
    for i in range(store.MAX_PENDING):
        assert _post(project, "", {"block_id": block, "texte": f"- v{i}"})[0] == 200
    _edit(project, block, "- Une piste revue")
    assert _phase(project)["suggestions"] == []
    code, out = _post(project, "", {"block_id": _block(project, "- Une autre"), "texte": "- Z"})
    assert code == 200, out
    etats = [s.etat for s in store.load(_dir(project))]
    assert etats.count("perimee") == store.MAX_PENDING and etats[-1] == "proposee"
    assert _post(project, f"/{out['suggestion']['id']}", {"decision": "accept"})[0] == 200


def test_accepting_one_suggestion_stales_the_others_on_that_block(project: Path) -> None:
    first = _deposit(project, "- A")
    _deposit(project, "- B")
    assert _post(project, f"/{first['id']}", {"decision": "accept"})[0] == 200
    assert sorted(s.etat for s in store.load(_dir(project))) == ["acceptee", "perimee"]


def test_total_entries_are_capped_and_oldest_settled_go_first(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 4)
    ids = []
    for i in range(3):
        made = _deposit(project, f"- t{i}")
        ids.append(made["id"])
        _post(project, f"/{made['id']}", {"decision": "reject"})
    keep = _deposit(project, "- garde")
    code, out = _post(project, "", {"block_id": _block(project), "texte": "- encore"})
    assert code == 200, out
    kept = [s.id for s in store.load(_dir(project))]
    assert ids[0] not in kept and keep["id"] in kept and out["suggestion"]["id"] in kept and len(kept) <= 4


@pytest.mark.parametrize("char", ["‮", "⁦", "⁩", "​", "﻿", "\U000e0041"])
def test_invisible_characters_are_refused_at_deposit(project: Path, char: str) -> None:
    block = _block(project)
    code, out = _post(project, "", {"block_id": block, "texte": f"- Budget {char}0001"})
    assert code == 400 and "invisible" in out["error"]
    code, out = _post(project, "", {"block_id": block, "texte": "- Budget", "motif": f"vu{char}"})
    assert code == 400 and "invisible" in out["error"]
    assert not (_dir(project) / store.FILENAME).exists()


def test_rows_with_unknown_phase_or_state_are_ignored_not_fatal(project: Path) -> None:
    good = _deposit(project)
    path = _dir(project) / store.FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["suggestions"] += [{**good, "id": "sug-p", "phase": "zzz"}, {**good, "id": "sug-e", "etat": "zz"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert [s["id"] for s in _phase(project)["suggestions"]] == [good["id"]]
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"][0]["suggestions"] == 1
    listing = workspace_get(project, f"{P}atelier/chantiers/un/suggestions", {})
    assert listing["ignored"] == 2 and len(listing["suggestions"]) == 1
    assert _post(project, "/sug-e", {"decision": "accept"})[0] == 404
    code, _out = _post(project, f"/{good['id']}", {"decision": "accept"})
    assert code == 200 and len(list(_dir(project).glob(store.FILENAME + ".abime-*"))) == 1  # lignes ignorées gardées


def test_a_damaged_store_is_set_aside_before_being_rewritten(project: Path) -> None:
    made = _deposit(project, "- A")
    path = _dir(project) / store.FILENAME
    path.write_bytes(path.read_bytes()[:-5])
    damaged = path.read_bytes()
    code, out = _post(project, "", {"block_id": made["block_id"], "texte": "- B"})
    assert code == 200 and out["ecarte"].startswith(store.FILENAME + ".abime-")
    assert (path.parent / out["ecarte"]).read_bytes() == damaged
    assert [s.texte for s in store.load(path.parent)] == ["- B"]


def test_an_unreadable_store_is_never_overwritten(project: Path) -> None:
    made = _deposit(project, "- A")
    path = _dir(project) / store.FILENAME
    before = path.read_bytes()
    path.chmod(0)
    try:
        code, out = _post(project, "", {"block_id": made["block_id"], "texte": "- B"})
        assert code == 400 and "illisible" in out["error"]
        assert workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"]  # le document ne tombe pas
    finally:
        path.chmod(0o644)
    assert path.read_bytes() == before


def test_a_lock_timeout_is_a_clean_503(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = _deposit(project)

    @contextmanager
    def never(_path: Any, **_k: Any) -> Iterator[None]:
        raise TimeoutError("verrou")
        yield

    monkeypatch.setattr(store, "locked", never)
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 503 and "verrouillé" in out["error"]
    from grimoire.mcp import atelier_tools

    refused = atelier_tools.atelier_suggest("un", made["block_id"], "- Autre", project_path=str(project))
    assert refused.is_error is True


def test_duplicate_blocks_are_not_confused_after_the_first_is_edited(project: Path) -> None:
    """Deux blocs au texte identique : l'id du second devient celui du premier quand celui-ci change."""
    path = _path(project)
    path.write_text(path.read_text(encoding="utf-8") + "\n- Doublon\n\n- Doublon\n", encoding="utf-8")
    ids = [b["id"] for b in _phase(project)["blocks"] if b["text"] == "- Doublon"]
    assert len(ids) == 2 and ids[0] != ids[1]
    made = _post(project, "", {"block_id": ids[0], "texte": "- Proposé pour le premier"})[1]["suggestion"]
    _edit(project, ids[0], "- Premier revu")
    before = path.read_bytes()
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 409 and out["reason"] == "perimee" and path.read_bytes() == before
    assert store.load(_dir(project))[0].etat == "perimee"


def test_conflict_reasons_tell_decided_from_stale(project: Path) -> None:
    made = _deposit(project)
    assert _post(project, f"/{made['id']}", {"decision": "accept"})[0] == 200
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 409 and out["reason"] == "deja-tranchee"


def test_ordinary_combining_marks_stay_allowed(project: Path) -> None:
    assert (
        _post(project, "", {"block_id": _block(project), "texte": "- Une piste, e\u0301te\u0301", "motif": "a\u0300"})[
            0
        ]
        == 200
    )


def test_a_homonym_appearing_before_a_unique_block_stales_the_suggestion(project: Path) -> None:
    made = _deposit(project)
    assert store.load(_dir(project))[0].homonymes == 1
    path = _path(project)
    path.write_text(
        path.read_text(encoding="utf-8").replace("- Une piste\n", "- Une piste\n\n- Une piste\n", 1), encoding="utf-8"
    )
    before = path.read_bytes()
    code, out = _post(project, f"/{made['id']}", {"decision": "accept"})
    assert code == 409 and out["reason"] == "perimee" and path.read_bytes() == before


def test_purge_drops_stale_first_then_decided_and_refuses_when_nothing_is_purgeable(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 8)
    other = _block(project, "- Une autre")
    decided = []
    for i in range(3):  # décisions humaines
        made = _post(project, "", {"block_id": other, "texte": f"- r{i}"})[1]["suggestion"]
        _post(project, f"/{made['id']}", {"decision": "reject", "motif": f"non {i}"})
        decided.append(made["id"])
    flooded = _block(project)
    for i in range(4):  # l'agent inonde un bloc que l'humain retouche ensuite
        _post(project, "", {"block_id": flooded, "texte": f"- f{i}"})
    _edit(project, flooded, "- Une piste retouchée")
    for i in range(5):  # de nouveaux dépôts forcent la purge : les périmées partent, les décisions restent
        code, out = _post(project, "", {"block_id": other, "texte": f"- g{i}"})
        assert code == 200, out
    kept = {s.id: s.etat for s in store.load(_dir(project))}
    assert all(kept.get(d) == "refusee" for d in decided)
    assert "perimee" not in kept.values() and len(kept) <= 8
    # Que des décisions et de l'attendu valide : décisions purgées en dernier ; tout en attente : refus net.
    monkeypatch.setattr(store, "MAX_ENTRIES", 6)
    monkeypatch.setattr(store, "MAX_PENDING", 99)
    for i in range(10):
        code, out = _post(project, "", {"block_id": other, "texte": f"- h{i}"})
        if code != 200:
            break
    assert code == 400 and "entrées" in out["error"]
    assert all(s.etat in ("proposee", "refusee") for s in store.load(_dir(project)))
