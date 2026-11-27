"""Un fil suit son bloc quand le bloc est réécrit (Atelier de spec, lot 3) : « deux jours » → « trois jours » ne l'orpheline pas.

Route d'édition et suggestion acceptée : ré-ancrage dans la même opération. Fichier modifié hors du cockpit :
voisinage et similarité, sans jamais choisir entre des blocs ambigus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import atelier_comments as store
from grimoire.tools import atelier_anchor, atelier_blocs
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_comments as com
from grimoire.tools import workspace_atelier_suggestions as sugg
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "project:\n  name: demo\nuser:\n  name: x\n"
BRAINSTORM = "02-brainstorm.md"
PARA = "Le délai de livraison est de deux jours ouvrés après le paiement de la commande."
NEXT = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "deux jours"
FOLDER = "_grimoire/cadrage/un"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / FOLDER / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + f"\n\n{PARA}\n\n{NEXT}\n", encoding="utf-8")
    return tmp_path


def _file(root: Path) -> Path:
    return root / FOLDER / BRAINSTORM


def _blocks(root: Path) -> list[dict[str, Any]]:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    return list(next(p for p in phases if p["id"] == "brainstorm")["blocks"])


def _block(root: Path, text: str) -> dict[str, Any]:
    return next(b for b in _blocks(root) if b["text"] == text)


def _comment(root: Path, quote: str | None = QUOTE) -> dict[str, Any]:
    body: dict[str, Any] = {"block_id": _block(root, PARA)["id"], "texte": "Pourquoi deux ?"}
    if quote is not None:
        body["quote"] = quote
    code, out = wa.post_http(root, f"{P}atelier/chantiers/un/comments", body)
    assert code == 200, out
    return dict(out["comment"])


def _thread(root: Path) -> dict[str, Any]:
    return dict(workspace_get(root, f"{P}atelier/chantiers/un/comments", {})["comments"][0])


def _edit(root: Path, block: dict[str, Any], text: str) -> int:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    fp = next(p for p in phases if p["id"] == "brainstorm")["fingerprint"]
    body = {"text": text, "expected": fp}
    return wa.put_http(root, f"{P}atelier/chantiers/un/blocs/{block['id']}", json.dumps(body).encode())[0]


def _rewrite(root: Path, old: str, new: str) -> None:
    _file(root).write_text(_file(root).read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


def test_a_thread_follows_its_block_edited_through_the_route(project: Path) -> None:
    made = _comment(project)
    assert _edit(project, _block(project, PARA), PARA.replace("deux jours", "trois jours")) == 200
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["orphelin"] is False
    assert row["quote"] == "trois jours" and row["modifie_depuis"] == QUOTE
    block = _block(project, PARA.replace("deux jours", "trois jours"))
    assert row["anchor"]["block_id"] == block["id"] and row["block_id"] == block["id"] and row["id"] == made["id"]
    assert block["text"][row["anchor"]["start"] : row["anchor"]["end"]] == "trois jours"
    (trace,) = row["historique"]
    assert trace["avant"]["quote"] == QUOTE and trace["apres"]["quote"] == "trois jours" and trace["auto"] is True
    saved = store.load(project / FOLDER)[0]
    assert saved.orphelin is False and saved.deplace is True  # écrit au magasin, pas seulement à la lecture


def test_a_thread_on_a_whole_block_follows_its_rewrite(project: Path) -> None:
    _comment(project, quote=None)
    assert _edit(project, _block(project, PARA), "Un texte complètement réécrit, sans rapport.") == 200
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == "Un texte complètement réécrit, sans rapport."
    assert row["modifie_depuis"] == PARA


def test_an_edit_elsewhere_in_the_block_keeps_the_quote(project: Path) -> None:
    _comment(project)
    assert _edit(project, _block(project, PARA), PARA.replace("Le délai", "Le délai maximal")) == 200
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == QUOTE and row["modifie_depuis"] == ""
    assert row["historique"] == []  # le passage n'a pas changé : rien à garder


def test_a_resolved_thread_follows_and_stays_resolved(project: Path) -> None:
    made = _comment(project)
    assert wa.post_http(project, f"{P}atelier/chantiers/un/comments/{made['id']}", {"action": "resolve"})[0] == 200
    assert _edit(project, _block(project, PARA), PARA.replace("deux jours", "trois jours")) == 200
    row = _thread(project)
    assert row["etat"] == "resolu" and row["orphelin"] is False and row["anchor"]["state"] == "retrouve"
    assert row["quote"] == "trois jours"


def test_a_block_split_in_two_by_the_edit_keeps_the_thread_on_the_right_half(project: Path) -> None:
    _comment(project)
    assert _edit(project, _block(project, PARA), "Un premier paragraphe neuf.\n\nLe délai est de deux jours ouvrés.") == 200
    row = _thread(project)
    block = next(b for b in _blocks(project) if b["id"] == row["anchor"]["block_id"])
    assert block["text"] == "Le délai est de deux jours ouvrés." and row["quote"] == QUOTE and row["anchor"]["state"] == "retrouve"


def test_a_thread_follows_a_block_rewritten_by_an_accepted_suggestion(project: Path) -> None:
    _comment(project)
    new = PARA.replace("deux jours", "trois jours")
    filed = sugg.suggest(str(project), "un", _block(project, PARA)["id"], new)["suggestion"]
    assert wa.post_http(project, f"{P}atelier/chantiers/un/suggestions/{filed['id']}", {"decision": "accept"})[0] == 200
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == "trois jours" and row["modifie_depuis"] == QUOTE


def test_a_thread_follows_a_crlf_file_edit(project: Path) -> None:
    _comment(project)
    raw = _file(project).read_bytes().replace(b"\n", b"\r\n")
    _file(project).write_bytes(raw)
    block = next(b for b in _blocks(project) if PARA in b["text"])
    assert _edit(project, block, PARA.replace("deux jours", "trois jours")) == 200
    row = _thread(project)
    assert row["quote"] == "trois jours" and row["anchor"]["state"] == "retrouve"


def test_an_ambiguous_homonym_is_not_followed_through_the_route(project: Path) -> None:
    """Deux blocs identiques : le fil ne sait pas lequel il est, la route ne le déplace sur aucun."""
    _rewrite(project, NEXT, PARA)
    code, out = wa.post_http(
        project, f"{P}atelier/chantiers/un/comments", {"block_id": _blocks(project)[-1]["id"], "texte": "Lequel ?", "quote": QUOTE}
    )
    assert code == 200, out
    first = next(b for b in _blocks(project) if b["text"] == PARA)
    assert _edit(project, first, PARA.replace("deux jours", "trois jours")) == 200
    row = _thread(project)
    assert row["quote"] == QUOTE  # intact : la route n'a rien suivi


def test_a_file_edited_by_hand_in_place_is_followed_by_its_neighbours(project: Path) -> None:
    _comment(project)
    _rewrite(project, "deux jours", "trois jours")
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == "trois jours" and row["modifie_depuis"] == QUOTE
    assert row["orphelin"] is False


def test_a_file_edited_by_hand_with_changed_neighbours_is_followed_by_similarity(project: Path) -> None:
    _comment(project)
    _rewrite(project, "deux jours", "trois jours")
    _rewrite(project, NEXT, "Un tout autre paragraphe, sans lien.")  # le voisin change : plus de « même place »
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == "trois jours"


def test_two_equally_similar_blocks_are_never_chosen(project: Path) -> None:
    _comment(project)
    _rewrite(project, PARA, PARA.replace("deux jours", "trois jours") + "\n\n" + PARA.replace("deux jours", "quatre jours"))
    _rewrite(project, NEXT, "Autre sujet.")
    row = _thread(project)
    assert row["anchor"]["state"] == "orphelin" and row["quote"] == QUOTE


def test_a_stored_follow_is_written_by_the_next_cockpit_write(project: Path) -> None:
    _comment(project)
    _rewrite(project, "deux jours", "trois jours")
    assert store.load(project / FOLDER)[0].quote == QUOTE  # la lecture n'écrit rien
    assert _edit(project, _block(project, NEXT), "Autre chose.") == 200
    saved = store.load(project / FOLDER)[0]
    assert saved.quote == "trois jours" and saved.deplace is True and saved.historique[-1]["avant"]["quote"] == QUOTE


def test_a_resolved_thread_whose_block_vanished_stays_resolved_in_the_listing(project: Path) -> None:
    made = _comment(project)
    wa.post_http(project, f"{P}atelier/chantiers/un/comments/{made['id']}", {"action": "resolve"})
    _rewrite(project, PARA, "Rien à voir.")
    _rewrite(project, NEXT, "Autre sujet.")
    row = _thread(project)
    assert row["etat"] == "resolu" and row["anchor"]["state"] == "orphelin"


# ── l'unité : follow() ──────────────────────────────────────────────────────


def _item(old: str, quote: str) -> store.Comment:
    start = old.index(quote)
    before, after = atelier_anchor.context(old, start, start + len(quote))
    return store.Comment(
        id="c", phase="brainstorm", block_id="x", block_fingerprint="y", quote=quote, start=start, before=before,
        after=after, auteur="humain", date="d", texte="t",
    )


def _followed(old: str, new: str, quote: str) -> str | None:
    placed = atelier_anchor.follow(atelier_anchor.Rewrite(old, new), _item(old, quote), new)
    return None if placed is None else new[placed[0] : placed[1]]


def test_follow_maps_a_span_through_a_rewrite_on_word_boundaries() -> None:
    assert _followed("Le délai est de deux jours ouvrés.", "Le délai est de trois jours ouvrés.", "deux jours") == "trois jours"
    # Une sélection qui coupe un mot est suivie sur des mots entiers, jamais au milieu.
    assert _followed("Le délai est de deux jours ouvrés.", "Le délai est de trois jours ouvrés.", "eux jou") == "trois jours"


def test_follow_falls_back_to_the_whole_text_when_nothing_remains() -> None:
    assert _followed("abc def", "xyz uvw", "abc") == "xyz uvw"
    old = "Début. Le passage cité. Fin."
    assert _followed(old, "Début. Fin.", "Le passage cité") == "Début. Fin."


def test_follow_keeps_a_whole_block_comment_on_the_whole_block() -> None:
    old = "Un bloc entier commenté."
    assert _followed(old, "Le bloc a été réécrit.", old) == "Le bloc a été réécrit."


def test_a_long_whole_text_is_clipped_on_a_word_boundary() -> None:
    new = "mot " * 200
    placed = atelier_anchor.follow(atelier_anchor.Rewrite("abc", new), _item("abc def", "abc"), new)
    assert placed is not None and placed[1] <= store.QUOTE_MAX and not new[placed[0] : placed[1]].endswith("mo")


def test_a_block_too_big_for_a_diff_keeps_its_verbatim_quote_or_is_orphaned() -> None:
    old = "Le délai de deux jours ouvrés. " + "mot " * 5000
    rewrite = atelier_anchor.Rewrite(old, old.replace("deux", "trois"))
    assert rewrite.ops is None  # pas de diff : trop gros
    item = _item(old, "deux jours")
    assert atelier_anchor.follow(rewrite, item, old) is not None  # l'extrait mot pour mot est retrouvé
    bracketed = atelier_anchor.follow(rewrite, item, old.replace("deux jours", "trois jours"))
    assert bracketed is not None and old.replace("deux jours", "trois jours")[bracketed[0] : bracketed[1]] == "trois jours"
    assert atelier_anchor.follow(rewrite, item, old.replace("Le délai de deux jours ouvrés.", "Un texte sans rapport.")) is None


def test_one_diff_serves_every_thread_of_a_big_block_quickly(project: Path) -> None:
    """Un bloc de 20 000 caractères et 100 fils : l'édition (suivi compris) reste sous quelques secondes."""
    import time

    big = ("Le délai de livraison est de deux jours ouvrés. " + "mot suivant du paragraphe. " * 700).rstrip()
    _rewrite(project, PARA, big)
    block = _block(project, big)
    folder = project / FOLDER
    blocks = atelier_blocs.split("brainstorm", _file(project).read_text(encoding="utf-8"))
    index = next(i for i, b in enumerate(blocks) if b.text == big)
    rank, prev, after_fp = atelier_anchor.around(blocks, index)
    rows = []
    for n in range(100):
        quote = "deux jours" if n % 2 else "Le délai de livraison"
        start = big.index(quote)
        before, after = atelier_anchor.context(big, start, start + len(quote))
        rows.append(store.new_comment(
            phase="brainstorm", block_id=block["id"], block_fingerprint=atelier_blocs.fingerprint(big), quote=quote,
            start=start, before=before, after=after, auteur="humain", texte=f"fil {n}", rank=rank, prev=prev, next=after_fp,
        ))
    with store.locked_store(folder):
        store.save(folder, rows)
    began = time.monotonic()
    assert _edit(project, block, big.replace("deux jours", "trois jours", 1)) == 200
    assert time.monotonic() - began < 6  # large marge CI : mesuré sous la seconde ; 2,65 s pour UN fil avant le diff par mots
    assert len(store.load(folder)) == 100 and not any(r.orphelin for r in store.load(folder))
    listed = time.monotonic()
    _thread(project)
    assert time.monotonic() - listed < 6  # la lecture ne recalcule pas, n'écrit rien


def test_a_comment_store_that_cannot_be_read_does_not_break_the_edit(project: Path) -> None:
    _comment(project)
    (project / FOLDER / store.FILENAME).write_text("pas du json", encoding="utf-8")
    assert _edit(project, _block(project, PARA), PARA.replace("deux jours", "trois jours")) == 200
    assert com.listing(project, "un")["damaged"] is True


def test_a_failing_follow_never_breaks_the_edit(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _comment(project)
    phases = workspace_get(project, f"{P}atelier/chantiers/un", {})["phases"]
    phase = next(p for p in phases if p["id"] == "brainstorm")
    block = next(b for b in phase["blocks"] if b["text"] == PARA)

    def put(text: str, fingerprint: str) -> int:
        body = {"text": text, "expected": fingerprint}
        return wa.put_http(project, f"{P}atelier/chantiers/un/blocs/{block['id']}", json.dumps(body).encode())[0]

    def broken(*_: Any) -> Any:
        raise OSError("disque")

    monkeypatch.setattr(com, "_phase_blocks", broken)
    assert put(PARA.replace("deux jours", "trois jours"), phase["fingerprint"]) == 200
    assert store.load(project / FOLDER)[0].quote == QUOTE  # rien suivi, rien cassé
    fingerprint = atelier_blocs.fingerprint(_file(project).read_text(encoding="utf-8"))
    monkeypatch.setattr(com, "_phase_blocks", lambda *_: {})  # phase illisible : on ne conclut rien
    block = {"id": next(b.id for b in atelier_blocs.split("brainstorm", _file(project).read_text(encoding="utf-8")) if "trois" in b.text)}
    assert put(PARA, fingerprint) == 200


def test_adopting_a_rewrite_without_a_place_or_with_an_empty_span_is_safe(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _comment(project)
    item = store.load(project / FOLDER)[0]
    block = next(b for b in atelier_blocs.split("brainstorm", _file(project).read_text(encoding="utf-8")) if b.text == PARA)
    held = item.to_dict()
    com._adopt(item, [], block, "autre", None)  # aucun bloc où se poser
    assert item.to_dict() == held
    fresh = [b for b in atelier_blocs.split("brainstorm", _file(project).read_text(encoding="utf-8")) if b.editable]
    monkeypatch.setattr(atelier_anchor, "follow", lambda *_: None)  # rien de sûr : le fil n'est pas touché
    com._adopt(item, fresh, block, PARA, None)
    assert item.to_dict() == held
    monkeypatch.setattr(atelier_anchor, "follow", lambda *_: (5, 5))  # un intervalle vide
    com._adopt(item, fresh, block, PARA, None)
    assert item.quote and item.start == 0  # le bloc entier plutôt qu'un extrait vide


def test_a_quote_kept_verbatim_in_a_block_whose_neighbours_changed_is_found_by_similarity(project: Path) -> None:
    _comment(project)
    _rewrite(project, "Le délai de livraison", "Le délai de livraison indicatif")
    _rewrite(project, NEXT, "Un tout autre voisin.")
    row = _thread(project)
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == QUOTE and row["modifie_depuis"] == ""


def test_similarity_is_zero_without_text_words_or_with_a_block_too_long() -> None:
    assert atelier_anchor._fit("", 0, 0, "texte")[0] == 0.0 and atelier_anchor._fit("texte", 0, 5, "")[0] == 0.0
    assert atelier_anchor._fit("!!! ???", 0, 3, "texte")[0] == 0.0  # aucun mot dans l'ancre
    assert atelier_anchor._fit("abc def", 0, 3, "zzz " * 2000)[0] == 0.0  # bloc au-delà du plafond
    assert atelier_anchor._fit("abc def ghi", 0, 3, "abc def ghi")[0] == 1.0
    assert atelier_anchor._fit("abc def ghi", 0, 3, "abc def ghi") is atelier_anchor._fit("abc def ghi", 0, 3, "abc def ghi")  # mémorisé


def _external_replace(project: Path, new: str, next_block: str | None = None) -> dict[str, Any]:
    _comment(project)
    _rewrite(project, PARA, new)
    if next_block is not None:
        _rewrite(project, NEXT, next_block)
    return _thread(project)


def test_an_unrelated_replacement_between_the_same_neighbours_is_orphaned_not_followed(project: Path) -> None:
    """Sonde du relecteur : un bloc supprimé puis remplacé entre les mêmes voisins par un texte sans rapport."""
    row = _external_replace(project, "Thème sombre pour l'éditeur de texte, avec une palette dédiée.")
    assert row["anchor"]["state"] == "orphelin" and row["quote"] == QUOTE
    assert store.load(project / FOLDER)[0].orphelin is False  # la lecture n'écrit rien
    assert _edit(project, _block(project, NEXT), "Autre chose.") == 200  # une écriture le fige, comme sur main
    saved = store.load(project / FOLDER)[0]
    assert saved.orphelin is True and saved.quote == QUOTE and saved.historique == []


def test_a_light_retouch_between_the_same_neighbours_is_followed_on_whole_words(project: Path) -> None:
    row = _external_replace(project, PARA.replace("deux jours ouvrés", "trois jours ouvrés"))
    assert row["anchor"]["state"] == "retrouve" and row["quote"] == "trois jours"
    assert row["quote"] in PARA.replace("deux", "trois")


def test_a_following_never_cuts_a_word(project: Path) -> None:
    row = _external_replace(project, PARA.replace("livraison est de deux jours", "livraison est de trois semaines"))
    quote = row["quote"]
    block = next(b for b in _blocks(project) if quote in b["text"])
    at = block["text"].index(quote)
    assert not block["text"][at - 1 : at].isalnum() and not block["text"][at + len(quote) : at + len(quote) + 1].isalnum()
