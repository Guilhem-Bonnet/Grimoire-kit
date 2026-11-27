"""Atelier de spec : contrôle de concurrence PAR BLOC (une édition ne se perd plus parce qu'un autre bloc a bougé)."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import atelier_blocs as ab
from grimoire.tools import atelier_conflict as ac
from grimoire.tools import workspace_atelier as wa
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "# réglages\nproject:\n  name: demo\nuser:\n  name: x\n"
FILE = "_grimoire/cadrage/un/02-brainstorm.md"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    return tmp_path


def _write(root: Path, tail: bytes) -> Path:
    path = root / FILE
    path.write_bytes(path.read_bytes().rstrip(b"\n") + b"\n\n" + tail)
    return path


def _phase(root: Path) -> dict[str, Any]:
    return workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"][1]


def _block(root: Path, text: str) -> dict[str, Any]:
    return next(b for b in _phase(root)["blocks"] if b["text"] == text)


def _body(block: dict[str, Any], text: str, **extra: Any) -> dict[str, Any]:
    return {
        "text": text,
        "block_fp": block["fp"],
        "prev": block["prev"],
        "next": block["next"],
        "twins": block["twins"],
        "base": block["text"],
        **extra,
    }


def _put(root: Path, block_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.put_http(root, f"{P}atelier/chantiers/un/blocs/{block_id}", json.dumps(body).encode())


def test_view_carries_block_fingerprints_and_neighbours(project: Path) -> None:
    _write(project, b"Un.\n\nDeux.\n")
    blocks = _phase(project)["blocks"]
    assert blocks[0]["prev"] == "" and blocks[-1]["next"] == ""
    assert all(a["next"] == b["fp"] and b["prev"] == a["fp"] for a, b in pairwise(blocks))
    assert blocks[0]["fp"] == ab.fingerprint(blocks[0]["text"])


def test_other_block_changed_meanwhile_is_accepted_and_both_edits_survive(project: Path) -> None:
    path = _write(project, b"Premier.\n\nSecond.\n\nTroisieme.\n")
    mine = _block(project, "Premier.")
    body = _body(mine, "Premier, précisé.")
    # Un agent réécrit un AUTRE bloc pendant la saisie : l'empreinte du fichier change, pas celle du bloc.
    path.write_bytes(path.read_bytes().replace(b"Troisieme.", b"Troisieme, par l'agent."))
    code, out = _put(project, mine["id"], body)
    assert code == 200, out
    text = path.read_text(encoding="utf-8")
    assert "Premier, précisé." in text and "Troisieme, par l'agent." in text and "Second." in text


def test_blocks_added_or_removed_before_are_tolerated(project: Path) -> None:
    path = _write(project, b"Cible.\n\nSuite.\n")
    mine = _block(project, "Cible.")
    body = _body(mine, "Cible, éditée.")
    data = path.read_bytes()
    path.write_bytes(data.replace(b"Cible.", b"Ajoute avant.\n\nCible.", 1))
    code, out = _put(project, mine["id"], body)
    assert code == 200, out
    now = path.read_text(encoding="utf-8")
    assert "Ajoute avant." in now and "Cible, éditée." in now and "Suite." in now
    # Et un bloc retiré avant : la cible se retrouve toujours.
    again = _block(project, "Cible, éditée.")
    body = _body(again, "Cible, rééditée.")
    path.write_bytes(path.read_bytes().replace(b"Ajoute avant.\n\n", b"", 1))
    assert _put(project, again["id"], body)[0] == 200
    assert "Cible, rééditée." in path.read_text(encoding="utf-8")


def test_same_block_changed_is_409_with_both_versions_and_nothing_written(project: Path) -> None:
    path = _write(project, b"Cible.\n\nSuite.\n")
    mine = _block(project, "Cible.")
    body = _body(mine, "Ma version.")
    path.write_bytes(path.read_bytes().replace(b"Cible.", b"Version de l'agent.", 1))
    snapshot = path.read_bytes()
    code, out = _put(project, mine["id"], body)
    assert code == 409 and out["conflict"] is True and out["reason"] == "bloc-modifie"
    assert out["submitted"] == "Ma version." and out["base"] == "Cible."
    assert out["current"]["text"] == "Version de l'agent." and out["current"]["fp"] == ab.fingerprint(
        "Version de l'agent."
    )
    assert out["merged"] is None  # les deux ont réécrit la même ligne : pas de fusion triviale
    assert path.read_bytes() == snapshot
    # « Garder la mienne » : le client rejoue avec la version courante comme base ; c'est un geste explicite.
    cur = out["current"]
    retry = {
        "text": "Ma version.",
        "block_fp": cur["fp"],
        "prev": cur["prev"],
        "next": cur["next"],
        "twins": cur["twins"],
        "base": cur["text"],
    }
    assert _put(project, cur["id"], retry)[0] == 200
    assert "Ma version." in path.read_text(encoding="utf-8")


def test_conflict_proposes_a_trivial_three_way_merge(project: Path) -> None:
    path = _write(project, b"ligne un\nligne deux\nligne trois\nligne quatre\n")
    block = _block(project, "ligne un\nligne deux\nligne trois\nligne quatre")
    body = _body(block, "LIGNE UN\nligne deux\nligne trois\nligne quatre")
    path.write_bytes(path.read_bytes().replace(b"ligne quatre", b"ligne quatre (agent)"))
    code, out = _put(project, block["id"], body)
    assert code == 409 and out["merged"] == "LIGNE UN\nligne deux\nligne trois\nligne quatre (agent)"


def test_twin_blocks_are_refused_not_guessed(project: Path) -> None:
    path = _write(project, b"Jumeau.\n\nJumeau.\n")
    twins = [b for b in _phase(project)["blocks"] if b["text"] == "Jumeau."]
    assert len(twins) == 2 and twins[0]["fp"] == twins[1]["fp"]
    # Voisins identiques (bord pour l'un, jumeau pour l'autre) : le premier a prev=titre, le second next="" ;
    # on fabrique l'ambiguïté en laissant le client sans voisins connus.
    body = {"text": "Choisi.", "block_fp": twins[0]["fp"], "prev": "zzz", "next": "yyy", "base": "Jumeau."}
    snapshot = path.read_bytes()
    code, out = _put(project, twins[0]["id"], body)
    assert code == 409 and out["reason"] == "bloc-ambigu" and out["submitted"] == "Choisi."
    assert path.read_bytes() == snapshot
    # Avec leurs vrais voisins, ils se distinguent : le second est le dernier du fichier.
    assert _put(project, twins[1]["id"], _body(twins[1], "Second choisi."))[0] == 200
    now = path.read_text(encoding="utf-8")
    assert now.count("Jumeau.") == 1 and now.rstrip().endswith("Second choisi.")


def _twins_in_two_sections(project: Path) -> tuple[Path, dict[str, Any]]:
    path = _write(project, b"## Section A\n\nA preciser.\n\n## Section B\n\nA preciser.\n")
    first = next(b for b in _phase(project)["blocks"] if b["text"] == "A preciser.")
    assert first["twins"] == 2
    return path, first


def test_modified_twin_is_a_conflict_not_a_write_into_the_other_twin(project: Path) -> None:
    path, mine = _twins_in_two_sections(project)
    body = _body(mine, "Ma saisie.")
    # L'agent réécrit le jumeau de la section A ; il ne reste qu'un bloc « A preciser. », celui de la section B.
    path.write_bytes(path.read_bytes().replace(b"A preciser.", b"Rempli par l'agent.", 1))
    snapshot = path.read_bytes()
    code, out = _put(project, mine["id"], body)
    assert code == 409 and out["reason"] == "bloc-modifie"
    assert out["current"]["text"] == "Rempli par l'agent." and out["submitted"] == "Ma saisie."
    assert path.read_bytes() == snapshot


def test_deleted_twin_is_refused_and_the_other_twin_is_untouched(project: Path) -> None:
    path, mine = _twins_in_two_sections(project)
    body = _body(mine, "Ma saisie.")
    path.write_bytes(path.read_bytes().replace(b"A preciser.\n\n## Section B", b"## Section B", 1))
    snapshot = path.read_bytes()
    code, out = _put(project, mine["id"], body)
    assert code == 409 and out["reason"] == "bloc-ambigu" and out["submitted"] == "Ma saisie."
    assert path.read_bytes() == snapshot


def test_surviving_twin_with_matching_neighbours_is_edited(project: Path) -> None:
    path, _first = _twins_in_two_sections(project)
    second = [b for b in _phase(project)["blocks"] if b["text"] == "A preciser."][1]
    # Le jumeau de la section A est rempli ailleurs : celui qu'on édite (B) garde ses voisins, il est bien le bon.
    path.write_bytes(path.read_bytes().replace(b"A preciser.", b"Rempli.", 1))
    assert _put(project, second["id"], _body(second, "Section B remplie."))[0] == 200
    text = path.read_text(encoding="utf-8")
    assert "Rempli." in text and "Section B remplie." in text and "A preciser." not in text


def test_block_id_must_match_the_fingerprint(project: Path) -> None:
    _write(project, b"Un.\n")
    block = _block(project, "Un.")
    code, out = _put(project, "brainstorm.0000000000", _body(block, "Deux."))
    assert code == 400 and "incohérent" in out["error"]
    code, _ = _put(project, "brainstorm", _body(block, "Deux."))
    assert code == 400
    assert _put(project, "zzz.abc", _body(block, "Deux."))[0] == 404  # phase inconnue
    assert _put(project, block["id"], {**_body(block, "Deux."), "twins": 0})[0] == 400
    assert _put(project, block["id"], {**_body(block, "Deux."), "twins": True})[0] == 400


def test_pathological_merge_is_bounded_and_computed_outside_the_lock(project: Path) -> None:
    import time

    base = "\n".join(["a"] * 9000)
    path = _write(project, base.encode() + b"\n")
    block = _block(project, base)
    path.write_bytes(path.read_bytes().replace(b"a\n", b"b\n", 1))
    started = time.monotonic()
    code, out = _put(project, block["id"], _body(block, "\n".join(["c"] * 9000)))
    assert code == 409 and out["merged"] is None
    assert time.monotonic() - started < 3


def test_vanished_block_is_refused_with_the_submitted_text(project: Path) -> None:
    path = _write(project, b"Eph.\n\nReste.\n")
    mine = _block(project, "Eph.")
    body = _body(mine, "Saisie.")
    path.write_bytes(path.read_bytes().replace(b"Eph.\n\nReste.", b"Autre chose entierement.\n\nEt un autre."))
    code, out = _put(project, mine["id"], body)
    assert code == 409 and out["reason"] == "bloc-introuvable" and out["submitted"] == "Saisie."


def test_old_client_with_file_fingerprint_keeps_the_old_behaviour(project: Path) -> None:
    path = _write(project, b"Un.\n\nDeux.\n")
    phase = _phase(project)
    block = _block(project, "Un.")
    code, _ = _put(project, block["id"], {"text": "Un bis.", "expected": phase["fingerprint"]})
    assert code == 200
    snapshot = path.read_bytes()
    code, out = _put(project, block["id"], {"text": "Perdu.", "expected": phase["fingerprint"]})
    assert code == 409 and out["conflict"] is True and path.read_bytes() == snapshot
    assert _put(project, block["id"], {"text": "x"})[0] == 400  # ni `expected` ni `block_fp`


def test_bytes_outside_the_block_are_preserved(project: Path) -> None:
    path = _write(project, b"Cible.\r\n\r\nSuite.\r\n\r\n\r\nFin sans saut")
    before = path.read_bytes()
    mine = _block(project, "Cible.")
    code, out = _put(project, mine["id"], _body(mine, "Cible\nmodifiee."))
    assert code == 200, out
    assert path.read_bytes() == before.replace(b"Cible.", b"Cible\r\nmodifiee.", 1)
    assert not path.read_bytes().endswith(b"\n")


@pytest.mark.parametrize(
    "patch",
    [{"block_fp": ""}, {"block_fp": 3}, {"prev": 1}, {"base": "x" * (wa.MAX_BLOCK_CHARS + 1)}],
)
def test_malformed_block_fields_are_400(project: Path, patch: dict[str, Any]) -> None:
    _write(project, b"Un.\n")
    block = _block(project, "Un.")
    code, _ = _put(project, block["id"], {**_body(block, "Deux."), **patch})
    assert code == 400


def test_structure_and_locked_blocks_are_still_refused(project: Path) -> None:
    _write(project, b"Un.\n")
    block = _block(project, "Un.")
    assert _put(project, block["id"], _body(block, "## Titre"))[0] == 400
    locked = next(b for b in _phase(project)["blocks"] if not b["editable"])
    assert _put(project, locked["id"], _body(locked, "x"))[0] == 403


def test_unchanged_text_writes_nothing(project: Path) -> None:
    path = _write(project, b"Un.\n")
    block = _block(project, "Un.")
    before = path.stat().st_mtime_ns
    assert _put(project, block["id"], _body(block, "Un."))[0] == 200
    assert path.stat().st_mtime_ns == before


# ── Unités : locate et merge3 ────────────────────────────────────────────────


def _blocks(text: str) -> list[ab.Block]:
    return ab.split("brief", text)


def test_locate_kinds() -> None:
    blocks = _blocks("Un.\n\nDeux.\n\nTrois.\n")
    fps = [ab.fingerprint(b.text) for b in blocks]
    assert ac.locate(blocks, fps[1], fps[0], fps[2]) == ac.Located(ac.SAME, 1)
    assert ac.locate(blocks, fps[1], "x", "y", twins=1) == ac.Located(
        ac.SAME, 1
    )  # unique à la lecture : voisins libres
    assert ac.locate(blocks, fps[1], "x", "y").kind == ac.AMBIGUOUS  # twins inconnu et voisins qui ne concordent pas
    assert ac.locate(blocks, fps[1], "x", "y", twins=2).kind == ac.AMBIGUOUS
    assert ac.locate(blocks, "autre", fps[0], fps[2]) == ac.Located(ac.CHANGED, 1)
    assert ac.locate(blocks, "autre", "", fps[1]) == ac.Located(ac.CHANGED, 0)
    assert ac.locate(blocks, "autre", fps[1], "") == ac.Located(ac.CHANGED, 2)
    assert ac.locate(blocks, "autre", "zzz", "yyy").kind == ac.MISSING
    twins = _blocks("A.\n\nB.\n\nA.\n\nC.\n")
    t = [ab.fingerprint(b.text) for b in twins]
    assert ac.locate(twins, t[0], "", t[1]) == ac.Located(ac.SAME, 0)
    assert ac.locate(twins, t[0], t[1], t[3]) == ac.Located(ac.SAME, 2)
    assert ac.locate(twins, t[0], "q", "q").kind == ac.AMBIGUOUS


def test_locate_ambiguous_places() -> None:
    blocks = _blocks("P.\n\nQ.\n\nN.\n\nP.\n\nQ.\n\nN.\n")
    fp = {t: ab.fingerprint(t) for t in ("P.", "N.")}
    assert ac.locate(blocks, "autre", fp["P."], fp["N."]).kind == ac.AMBIGUOUS


def test_merge3() -> None:
    base = "a\nb\nc\nd"
    assert ac.merge3(base, "A\nb\nc\nd", "a\nb\nc\nD") == "A\nb\nc\nD"
    assert ac.merge3(base, "a\nb\nc\nd\ne", "a\nb\nc\nd") == "a\nb\nc\nd\ne"  # un seul côté change
    assert ac.merge3(base, "a\nB\nc\nd", "a\nB\nc\nd") == "a\nB\nc\nd"  # même changement des deux côtés
    assert ac.merge3(base, "a\nX\nc\nd", "a\nY\nc\nd") is None  # chevauchement
    assert ac.merge3(base, "a\nX\nc\nd", "a\nb\nY\nd") is None  # plages adjacentes : refus prudent
    assert ac.merge3("a\r\nb", "a\r\nB", "a\r\nb") == "a\nB"  # fins de ligne ramenées à \n
