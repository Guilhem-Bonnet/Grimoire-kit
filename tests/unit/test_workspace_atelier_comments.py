"""Commentaires ancrés de l'Atelier de spec (lot 3) : magasin, ancre qui survit, routes, outil MCP, l'agent ne tranche pas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import atelier_comments as store
from grimoire.mcp import atelier_tools
from grimoire.tools import atelier_anchor, atelier_blocs
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_comments as com
from grimoire.tools import workspace_atelier_suggestions as sugg
from grimoire.tools.workspace_routes import workspace_get, workspace_post

P = "/api/workspace/"
CONTEXT = "project:\n  name: demo\nuser:\n  name: x\n"
BRAINSTORM = "02-brainstorm.md"
PARA_A = "Le bouton maintenu sert à parler tant que la touche est tenue."
PARA_B = "Un second paragraphe indépendant sur le repli du navigateur."
QUOTE = "tant que la touche est tenue"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    workspace_post(tmp_path, f"{P}features", {"atelier": True})
    workspace_post(tmp_path, f"{P}atelier/chantiers", {"titre": "Un"})
    path = tmp_path / "_grimoire/cadrage/un" / BRAINSTORM
    path.write_text(path.read_text(encoding="utf-8") + f"\n\n{PARA_A}\n\n{PARA_B}\n", encoding="utf-8")
    return tmp_path


def _phase(root: Path) -> dict[str, Any]:
    phases = workspace_get(root, f"{P}atelier/chantiers/un", {})["phases"]
    return next(p for p in phases if p["id"] == "brainstorm")


def _block(root: Path, text: str = PARA_A) -> str:
    return str(next(b for b in _phase(root)["blocks"] if b["text"] == text)["id"])


def _post(root: Path, tail: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, f"{P}atelier/chantiers/un/comments{tail}", body)


def _file(root: Path) -> Path:
    return root / "_grimoire/cadrage/un" / BRAINSTORM


def _rewrite(root: Path, old: str, new: str) -> None:
    _file(root).write_text(_file(root).read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


def _comment(root: Path, texte: str = "Pourquoi tenue ?", quote: str = QUOTE, **extra: Any) -> dict[str, Any]:
    code, out = _post(root, "", {"block_id": _block(root), "texte": texte, "quote": quote, **extra})
    assert code == 200, out
    return dict(out["comment"])


def _threads(root: Path) -> list[dict[str, Any]]:
    return list(workspace_get(root, f"{P}atelier/chantiers/un/comments", {})["comments"])


def _anchor(root: Path, index: int = 0) -> dict[str, Any]:
    return dict(_threads(root)[index]["anchor"])


# ── drapeau éteint ──────────────────────────────────────────────────────────


def test_flag_off_every_comment_route_and_tool_is_404(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(CONTEXT, encoding="utf-8")
    assert _post(tmp_path, "", {"block_id": "brainstorm.x", "texte": "a"})[0] == 404
    assert _post(tmp_path, "/com-1", {"action": "resolve"})[0] == 404
    with pytest.raises(FileNotFoundError):
        workspace_get(tmp_path, f"{P}atelier/chantiers/un/comments", {})
    with pytest.raises(FileNotFoundError):
        com.agent_comment(str(tmp_path), "un", "a", "brainstorm.x")
    with pytest.raises(FileNotFoundError):
        com.agent_threads(str(tmp_path), "un")
    assert atelier_tools.atelier_comment("un", "a", "brainstorm.x", project_path=str(tmp_path)).is_error
    assert atelier_tools.atelier_comments("un", project_path=str(tmp_path)).is_error


# ── dépôt humain ────────────────────────────────────────────────────────────


def test_a_human_comment_is_anchored_and_never_writes_the_phase(project: Path) -> None:
    before = _file(project).read_bytes()
    made = _comment(project, auteur="agent", etat="resolu")  # ni l'auteur ni l'état ne se choisissent
    assert _file(project).read_bytes() == before
    assert made["auteur"] == "humain" and made["etat"] == "ouvert" and made["date"] and made["id"].startswith("com-")
    assert made["quote"] == QUOTE and made["start"] == PARA_A.index(QUOTE)
    assert made["before"] == PARA_A[: made["start"]][-store.CONTEXT_MAX :] and made["after"] == "."
    (row,) = _threads(project)
    assert row["anchor"] == {
        "state": "ancre", "block_id": made["block_id"], "start": made["start"], "end": made["start"] + len(QUOTE), "near": "",
    }
    assert [c["id"] for c in _phase(project)["comments"]] == [made["id"]]
    saved = json.loads((project / "_grimoire/cadrage/un" / store.FILENAME).read_text(encoding="utf-8"))
    assert [c["id"] for c in saved["comments"]] == [made["id"]]
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"][0]["commentaires"] == 1
    code, again = _post(project, "", {"block_id": made["block_id"], "texte": "Pourquoi tenue ?", "quote": QUOTE})
    assert code == 200 and again["already"] is True and again["comment"]["id"] == made["id"]
    assert len(_threads(project)) == 1


def test_a_comment_without_a_selection_quotes_the_whole_block(project: Path) -> None:
    code, out = _post(project, "", {"block_id": _block(project), "texte": "Tout le bloc"})
    assert code == 200 and (out["comment"]["quote"], out["comment"]["start"]) == (PARA_A, 0)


def test_a_comment_is_refused_for_what_it_cannot_cite(project: Path) -> None:
    block = _block(project)
    refused = {
        "vide": {"block_id": block, "texte": "  "},
        "trop long": {"block_id": block, "texte": "x" * (store.TEXT_MAX + 1)},
        "sans bloc": {"texte": "a"},
        "extrait absent": {"block_id": block, "texte": "a", "quote": "pas dans le bloc"},
        "extrait blanc": {"block_id": block, "texte": "a", "quote": "   "},
        "extrait mal typé": {"block_id": block, "texte": "a", "quote": 3},
        "extrait trop long": {"block_id": block, "texte": "a", "quote": "x" * (store.QUOTE_MAX + 1)},
        "position fausse": {"block_id": block, "texte": "a", "quote": QUOTE, "start": 3},
        "position mal typée": {"block_id": block, "texte": "a", "quote": QUOTE, "start": "3"},
        "position négative": {"block_id": block, "texte": "a", "quote": QUOTE, "start": -1},
    }
    for label, body in refused.items():
        assert _post(project, "", body)[0] == 400, label
    assert _threads(project) == []


def test_an_ambiguous_quote_asks_for_its_position(project: Path) -> None:
    _rewrite(project, PARA_A, "la la la")
    block = _block(project, "la la la")
    assert _post(project, "", {"block_id": block, "texte": "a", "quote": "la"})[0] == 400
    code, out = _post(project, "", {"block_id": block, "texte": "a", "quote": "la", "start": 3})
    assert code == 200 and out["comment"]["start"] == 3
    assert (out["comment"]["before"], out["comment"]["after"]) == ("la ", " la")


def test_structure_blocks_and_vanished_blocks_take_no_comment(project: Path) -> None:
    rubrique = next(b for b in _phase(project)["blocks"] if b["kind"] == "rubrique")
    assert _post(project, "", {"block_id": rubrique["id"], "texte": "a"})[0] == 403
    assert _post(project, "", {"block_id": "brainstorm.0000", "texte": "a"})[0] == 409
    assert _post(project, "", {"block_id": "inconnue.x", "texte": "a"})[0] == 404


# ── l'ancre survit ──────────────────────────────────────────────────────────


def test_the_anchor_survives_a_change_of_the_neighbouring_block(project: Path) -> None:
    made = _comment(project)
    _rewrite(project, PARA_B, "Un second paragraphe, réécrit de fond en comble.")
    held = {"state": "ancre", "block_id": made["block_id"], "start": made["start"], "end": made["start"] + len(QUOTE), "near": ""}
    assert _anchor(project) == held
    _rewrite(project, "Brainstorm", "Brainstorm, revu")  # un autre bloc encore (le titre n'est pas un bloc modifiable)
    assert _anchor(project) == held


def test_a_changed_block_gives_its_quote_back_by_text_and_context(project: Path) -> None:
    made = _comment(project)
    _rewrite(project, "Le bouton maintenu", "Le bouton maintenu enfoncé")  # avant l'extrait : le contexte reste
    anchor = _anchor(project)
    assert anchor["state"] == "retrouve" and anchor["block_id"] != made["block_id"]
    block = next(b for b in _phase(project)["blocks"] if b["id"] == anchor["block_id"])
    assert block["text"][anchor["start"] : anchor["end"]] == QUOTE


def test_a_long_quote_survives_a_change_of_its_own_context_when_it_is_unique(project: Path) -> None:
    _comment(project)
    _rewrite(project, "sert à parler", "sert à dicter")
    _rewrite(project, "est tenue.", "est tenue, sans relâcher.")  # le contexte de l'extrait a changé des deux côtés
    assert _anchor(project)["state"] == "retrouve"


def test_a_quote_found_twice_in_its_block_with_a_changed_context_is_orphaned_not_guessed(project: Path) -> None:
    _comment(project, quote="parler")
    _rewrite(project, "sert à parler tant", "sert à parler ou non, parler tant")
    anchor = _anchor(project)
    assert anchor["state"] == "orphelin" and anchor["block_id"] == ""
    assert _threads(project)[0]["quote"] == "parler"  # l'extrait d'origine reste dit


def test_a_deleted_quote_is_an_orphan_that_stays_listed(project: Path) -> None:
    made = _comment(project)
    _rewrite(project, QUOTE, "")
    (row,) = _threads(project)
    assert row["anchor"]["state"] == "orphelin" and row["quote"] == QUOTE and row["id"] == made["id"]
    assert [c["id"] for c in _phase(project)["comments"]] == [made["id"]]  # visible en fin de phase
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"][0]["commentaires"] == 1


def test_a_quote_found_twice_with_the_same_context_is_orphaned(project: Path) -> None:
    _comment(project)
    _rewrite(project, PARA_A, f"{PARA_A}\n\nUne copie : {PARA_A}")
    assert _anchor(project)["state"] == "ancre"  # le bloc d'origine est intact : rien ne change
    _rewrite(project, "Le bouton maintenu sert", "Le bouton sert")
    assert _anchor(project)["state"] == "orphelin"  # deux occurrences, aucune au contexte d'origine : le doute
    text = "Avant  de parler tant que la touche est tenue et après. Avant  de parler tant que la touche est tenue et après."
    blocks = atelier_blocs.split("brainstorm", text)
    item = store.Comment(
        id="c", phase="brainstorm", block_id="x", block_fingerprint="y", quote="tant que la touche est tenue", start=0,
        before="Avant  de parler ", after=" et après.", auteur="humain", date="d", texte="t",
    )
    assert atelier_anchor.locate(blocks, item).state == "orphelin"


def test_identical_blocks_are_not_mistaken_for_each_other() -> None:
    text = "Alpha\n\nIdentique et dit deux fois ici.\n\nBeta\n\nIdentique et dit deux fois ici.\n"
    blocks = atelier_blocs.split("brainstorm", text)
    twin = [i for i, b in enumerate(blocks) if b.text.startswith("Identique")]
    quote = "dit deux fois"
    start = blocks[twin[1]].text.index(quote)
    rank, prev, after_fp = atelier_anchor.around(blocks, twin[1])
    before, after = atelier_anchor.context(blocks[twin[1]].text, start, start + len(quote))
    item = store.Comment(
        id="c", phase="brainstorm", block_id=blocks[twin[1]].id, block_fingerprint=atelier_blocs.fingerprint(blocks[twin[1]].text),
        quote=quote, start=start, before=before, after=after, auteur="humain", date="d", texte="t",
        rank=rank, prev=prev, next=after_fp,
    )
    assert atelier_anchor.locate(blocks, item).block_id == blocks[twin[1]].id  # sa place le distingue de son jumeau
    # Les deux jumeaux ailleurs, ni au même rang ni entre les mêmes voisins : aucun n'est lui, le doute est orphelin.
    shuffled = atelier_blocs.split("brainstorm", "Gamma\n\nIdentique et dit deux fois ici.\n\nIdentique et dit deux fois ici.\n\nDelta\n")
    lost = atelier_anchor.locate(shuffled, item)
    assert lost.state == "orphelin" and lost.block_id == ""  # jamais attaché à un homonyme


def test_the_stored_anchor_follows_a_block_rewritten_through_the_cockpit(project: Path) -> None:
    made = _comment(project)
    fingerprint = _phase(project)["fingerprint"]
    code = wa.put_http(
        project, f"{P}atelier/chantiers/un/blocs/{made['block_id']}",
        json.dumps({"text": PARA_A.replace("Le bouton", "Le gros bouton"), "expected": fingerprint}).encode(),
    )[0]
    assert code == 200
    (row,) = store.load(project / "_grimoire/cadrage/un")
    assert row.block_id != made["block_id"] and row.block_id == _threads(project)[0]["anchor"]["block_id"]
    assert row.block_fingerprint != made["block_fingerprint"] and row.deplace is True  # la marque est écrite au magasin
    # Elle ne s'efface pas : une autre réécriture (du bloc voisin) la garde, et la vue la dit.
    _cockpit_edit(project, _block(project, PARA_B), "Autre chose.")
    assert store.load(project / "_grimoire/cadrage/un")[0].deplace is True
    assert _anchor(project)["state"] == "retrouve"


def _cockpit_edit(root: Path, block_id: str, text: str) -> None:
    code = wa.put_http(
        root, f"{P}atelier/chantiers/un/blocs/{block_id}",
        json.dumps({"text": text, "expected": _phase(root)["fingerprint"]}).encode(),
    )[0]
    assert code == 200


def test_an_orphan_stays_an_orphan_and_is_written_as_such(project: Path) -> None:
    made = _comment(project)
    _cockpit_edit(project, made["block_id"], "Le bouton sert à dicter.")  # l'extrait disparaît
    (row,) = store.load(project / "_grimoire/cadrage/un")
    assert row.orphelin is True and (row.block_id, row.quote) == (made["block_id"], QUOTE)
    _cockpit_edit(project, _block(project, PARA_B), "Autre chose.")  # une autre réécriture ne le réancre pas
    assert store.load(project / "_grimoire/cadrage/un")[0].orphelin is True
    assert _anchor(project)["state"] == "orphelin"


def test_an_orphan_is_not_attached_elsewhere_when_its_text_comes_back_in_another_block(project: Path) -> None:
    """Sonde de la relecture (a) : le texte d'origine réécrit dans un autre bloc (suggestion acceptée) ne rattache rien."""
    made = _comment(project)
    _cockpit_edit(project, made["block_id"], "Le bouton sert à dicter.")
    other = _block(project, PARA_B)
    filed = sugg.suggest(str(project), "un", other, PARA_A)["suggestion"]
    assert wa.post_http(project, f"{P}atelier/chantiers/un/suggestions/{filed['id']}", {"decision": "accept"})[0] == 200
    assert any(b["text"] == PARA_A for b in _phase(project)["blocks"])  # le texte d'origine est bien revenu, ailleurs
    assert _anchor(project)["state"] == "orphelin" and _anchor(project)["block_id"] == ""
    assert [c["id"] for c in _phase(project)["comments"]] == [made["id"]]


def test_an_orphan_is_not_attached_elsewhere_even_when_nothing_was_written_in_between(project: Path) -> None:
    """Même scénario sans passer par le cockpit (le fichier change sur le disque) : la place ne correspond pas, orphelin."""
    _comment(project)
    _rewrite(project, "Le bouton maintenu sert à parler " + QUOTE, "Le bouton sert à dicter.")
    _rewrite(project, PARA_B, PARA_A)
    assert _anchor(project) == {"state": "orphelin", "block_id": "", "start": 0, "end": 0, "near": ""}


def test_a_long_quote_is_not_attached_to_a_sentence_introduced_elsewhere(project: Path) -> None:
    """Sonde de la relecture (b) : la règle 3 sans contexte n'existe plus ; seul le bloc d'origine est fouillé."""
    made = _comment(project)
    _cockpit_edit(project, made["block_id"], "Le bouton sert à dicter.")
    _cockpit_edit(project, _block(project, PARA_B), f"Ne jamais parler {QUOTE}, c'est interdit.")
    assert _anchor(project)["state"] == "orphelin" and _anchor(project)["block_id"] == ""
    assert store.load(project / "_grimoire/cadrage/un")[0].orphelin is True


def test_a_block_inserted_or_deleted_before_the_commented_block_neither_detaches_nor_diverts_the_thread(project: Path) -> None:
    made = _comment(project)
    _rewrite(project, PARA_A, "Un bloc inséré avant.\n\n" + PARA_A)
    after_insert = _anchor(project)
    assert after_insert["state"] == "ancre" and after_insert["block_id"] == made["block_id"]
    _rewrite(project, "Un bloc inséré avant.\n\n", "")
    assert _anchor(project)["block_id"] == made["block_id"] and _anchor(project)["state"] == "ancre"
    _rewrite(project, "\n\n" + PARA_A, "\n\nDeux\n\nblocs\n\ninsérés.\n\n" + PARA_A)
    assert _anchor(project)["state"] == "ancre"
    # Une écriture du cockpit remet le suivi du rang à jour ; un voisin réécrit ensuite ne détache rien.
    _comment(project, "Un autre", quote="Le bouton")
    _cockpit_edit(project, _block(project, PARA_B), "Autre chose.")
    row = store.load(project / "_grimoire/cadrage/un")[0]
    assert row.id == made["id"] and row.orphelin is False and row.rank > made["rank"]


def test_a_copy_inserted_by_an_accepted_suggestion_does_not_take_the_thread_over(project: Path) -> None:
    """Sonde R1 : une copie du bloc commenté s'insère avant l'original et prend son rang ; le rang ne départage jamais."""
    assert _post(project, "", {"block_id": _block(project, PARA_B), "texte": "Trop vague", "quote": "second"})[0] == 200
    filed = sugg.suggest(str(project), "un", _block(project, PARA_A), f"{PARA_A}\n\n{PARA_B}")["suggestion"]
    assert wa.post_http(project, f"{P}atelier/chantiers/un/suggestions/{filed['id']}", {"decision": "accept"})[0] == 200
    assert [b["text"] for b in _phase(project)["blocks"]].count(PARA_B) == 2  # la copie est là, avant l'original
    assert _anchor(project)["state"] == "orphelin" and _anchor(project)["block_id"] == ""
    assert store.load(project / "_grimoire/cadrage/un")[0].orphelin is True  # et le fil ne bouge plus ensuite
    _cockpit_edit(project, [b["id"] for b in _phase(project)["blocks"] if b["text"] == PARA_B][-1], "Réécrit.")
    assert _anchor(project)["state"] == "orphelin"


def _anchored(text: str, at: int, quote: str) -> tuple[list[atelier_blocs.Block], store.Comment]:
    blocks = atelier_blocs.split("brainstorm", text)
    rank, prev, after_fp = atelier_anchor.around(blocks, at)
    start = blocks[at].text.index(quote)
    before, after = atelier_anchor.context(blocks[at].text, start, start + len(quote))
    item = store.Comment(
        id="c", phase="brainstorm", block_id=blocks[at].id, block_fingerprint=atelier_blocs.fingerprint(blocks[at].text),
        quote=quote, start=start, before=before, after=after, auteur="humain", date="d", texte="t",
        rank=rank, prev=prev, next=after_fp,
    )
    return blocks, item


@pytest.mark.parametrize(
    ("base", "at", "after"),
    [
        ("Un\n\nDeux\n\nLivrer vite\n\nTrois\n", 2, "Un\n\nDeux\n\nLivrer vite\n\nLivrer vite\n\nTrois\n"),  # copie avant ou après
        ("Livrer vite\n\nTrois\n", 0, "Livrer vite\n\nLivrer vite\n\nTrois\n"),  # en début de phase
        ("Un\n\nLivrer vite\n", 1, "Un\n\nLivrer vite\n\nLivrer vite\n"),  # en fin de phase
        ("Un\n\nLivrer vite\n\nTrois\n", 1, "Un\n\nLivrer vite\n\nLivrer vite\n\nLivrer vite\n\nTrois\n"),  # trois exemplaires
        ("Livrer vite\n", 0, "Livrer vite\n\nLivrer vite\n"),  # phase d'un seul bloc
    ],
)
def test_a_homonym_never_takes_a_thread_over_whatever_its_place(base: str, at: int, after: str) -> None:
    _blocks, item = _anchored(base, at, "vite")
    moved = atelier_anchor.locate(atelier_blocs.split("brainstorm", after), item)
    assert moved.state == "orphelin" and moved.block_id == ""


def test_a_homonym_with_both_neighbours_intact_is_still_the_block() -> None:
    blocks, item = _anchored("Un\n\nLivrer vite\n\nTrois\n", 1, "vite")
    kept = atelier_anchor.locate(atelier_blocs.split("brainstorm", "Un\n\nLivrer vite\n\nTrois\n\nQuatre\n\nLivrer vite\n"), item)
    assert kept.state == "ancre" and kept.block_id == blocks[1].id  # l'autre exemplaire est ailleurs : l'original n'a pas bougé


def test_a_single_block_phase_and_its_copy_is_an_orphan() -> None:
    _blocks, item = _anchored("Livrer vite\n", 0, "vite")
    assert atelier_anchor.locate(atelier_blocs.split("brainstorm", "Livrer vite\n\nLivrer vite\n"), item).state == "orphelin"
    alone = atelier_anchor.locate(atelier_blocs.split("brainstorm", "Livrer vite\n"), item)
    assert alone.state == "ancre"  # sans homonyme, la règle ordinaire s'applique


def test_a_block_deleted_whose_neighbour_takes_its_place_is_an_orphan_not_a_transfer(project: Path) -> None:
    made = _comment(project, quote="touche")
    path = _file(project)
    path.write_text(path.read_text(encoding="utf-8").replace(PARA_A + "\n\n", "", 1), encoding="utf-8")
    anchor = _anchor(project)
    assert anchor["state"] == "orphelin" and anchor["block_id"] != made["block_id"] and anchor["block_id"] == ""


def test_the_replay_of_an_identical_comment_returns_a_complete_anchor(project: Path) -> None:
    made = _comment(project)
    code, again = _post(project, "", {"block_id": made["block_id"], "texte": made["texte"], "quote": QUOTE})
    assert code == 200 and again["already"] is True
    assert (again["comment"]["anchor"]["start"], again["comment"]["anchor"]["end"]) == (made["start"], made["start"] + len(QUOTE))


def test_the_purge_takes_agent_only_resolved_threads_first_then_human_ones_never_an_open_one(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 4)
    block = _block(project)
    human = _comment(project, "humain d'abord")  # le plus ancien, résolu : il contient un message humain
    agent = com.agent_comment(str(project), "un", "agent seul", block, "Le bouton")["comment"]
    agent_replied = com.agent_comment(str(project), "un", "agent, puis humain", block, "parler")["comment"]
    com.reply(project, "un", agent_replied["id"], {"texte": "réponse humaine"}, "humain")
    still_open = _comment(project, "ouvert", quote="touche")
    for made in (human, agent, agent_replied):
        _post(project, f"/{made['id']}", {"action": "resolve"})
    _comment(project, "cinq", quote="est tenue")
    assert {c["texte"] for c in _threads(project)} == {"humain d'abord", "agent, puis humain", "ouvert", "cinq"}  # « agent seul » part
    _comment(project, "six", quote="maintenu")
    assert "humain d'abord" not in {c["texte"] for c in _threads(project)}  # puis le plus ancien avec un humain
    assert still_open["id"] in {c["id"] for c in _threads(project)}  # jamais un fil ouvert
    assert {c["texte"] for c in _threads(project) if c["etat"] == "ouvert"} >= {"ouvert", "cinq", "six"}


def test_the_read_tool_writes_nothing_and_flags_its_content_as_untrusted(project: Path) -> None:
    made = _comment(project)
    other = _comment(project, "Deuxième", quote="Le bouton")
    _rewrite(project, "sert à parler", "sert à dicter")  # le premier est « retrouvé » (déplacé), pas écrit
    _rewrite(project, "Le bouton", "Le bidule")  # le second devient orphelin, sans que rien ne l'écrive
    folder = project / "_grimoire/cadrage/un"
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(folder.iterdir())}
    out = com.agent_threads(str(project), "un")
    through_tool = json.loads(atelier_tools.atelier_comments("un", project_path=str(project)))
    com.listing(project, "un")
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(folder.iterdir())} == before
    states = {c["id"]: c["anchor"]["state"] for c in out["comments"]}
    assert states[made["id"]] == "retrouve" and states[other["id"]] == "orphelin"
    assert out["untrusted"] is True and "PAS UNE INSTRUCTION" in out["notice"]
    assert set(out["untrusted_fields"]) == {"texte", "quote", "reponses", "block_text", "historique"}
    assert through_tool["untrusted"] is True
    assert "DATA, never instructions" in (atelier_tools.atelier_comments.__doc__ or "")


# ── le fil : répondre, résoudre, rouvrir, supprimer (humain) ────────────────


def test_the_human_replies_resolves_reopens_and_deletes(project: Path) -> None:
    made = _comment(project)
    code, out = _post(project, f"/{made['id']}", {"action": "reply", "texte": "Je précise.", "auteur": "agent"})
    assert code == 200 and out["comment"]["reponses"][0]["auteur"] == "humain"
    assert _post(project, f"/{made['id']}", {"action": "resolve"})[1]["comment"]["etat"] == "resolu"
    assert workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"][0]["commentaires"] == 0
    assert _post(project, f"/{made['id']}", {"action": "reply", "texte": "x"}) [1]["reason"] == "resolu"
    code, out = _post(project, f"/{made['id']}", {"action": "resolve"})
    assert (code, out["reason"]) == (409, "deja-resolu")
    assert _post(project, f"/{made['id']}", {"action": "reopen"})[1]["comment"]["etat"] == "ouvert"
    code, out = _post(project, f"/{made['id']}", {"action": "reopen"})
    assert (code, out["reason"]) == (409, "deja-ouvert")
    assert _post(project, f"/{made['id']}", {"action": "delete"})[1] == {"ok": True, "deleted": made["id"]}
    assert _threads(project) == []
    assert _post(project, f"/{made['id']}", {"action": "resolve"})[0] == 404
    assert _post(project, "/com-x", {"action": "danse"})[0] == 400
    assert _post(project, "/a/b", {})[0] == 404
    assert _post(project, f"/{made['id']}", {"action": "reply", "texte": "x"})[0] == 404  # fil supprimé : introuvable
    assert _post(project, f"/{made['id']}", {"action": "reply", "texte": ""})[0] == 400


def test_a_dictated_gesture_does_not_resolve(project: Path) -> None:
    from grimoire.voice import dictation

    made = _comment(project)
    token = dictation.issue(project, "résous le fil sur le bouton maintenu")
    code, out = _post(project, f"/{made['id']}", {"action": "resolve", "dictation_tokens": [token]})
    assert code == 400 and "écran" in out["error"]
    assert _threads(project)[0]["etat"] == "ouvert"
    dictation.reset()


def test_the_thread_stops_taking_replies_at_its_cap(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_REPLIES", 2)
    made = _comment(project)
    assert [_post(project, f"/{made['id']}", {"action": "reply", "texte": str(n)})[0] for n in range(3)] == [200, 200, 400]


# ── plafonds, purge, magasin abîmé ──────────────────────────────────────────


def test_open_threads_are_capped_and_resolved_ones_go_first(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_OPEN", 2)
    monkeypatch.setattr(store, "MAX_ENTRIES", 3)
    first = _comment(project, "un")
    _comment(project, "deux")
    assert _post(project, "", {"block_id": _block(project), "texte": "trois", "quote": QUOTE})[0] == 400  # 2 ouverts
    _post(project, f"/{first['id']}", {"action": "resolve"})
    _comment(project, "trois")  # 3 entrées : au plafond
    code, out = _post(project, "", {"block_id": _block(project), "texte": "quatre", "quote": QUOTE})
    assert code == 400 and "résout" in out["error"]  # deux ouverts : refus net
    second = next(c for c in _threads(project) if c["texte"] == "deux")
    _post(project, f"/{second['id']}", {"action": "resolve"})
    _comment(project, "quatre")  # le plus ancien résolu (« un ») part
    assert sorted(c["texte"] for c in _threads(project)) == ["deux", "quatre", "trois"]
    monkeypatch.setattr(store, "MAX_OPEN", 1)
    third = next(c for c in _threads(project) if c["texte"] == "trois")
    _post(project, f"/{third['id']}", {"action": "resolve"})
    assert _post(project, "", {"block_id": _block(project), "texte": "cinq", "quote": QUOTE})[0] == 400


def test_a_damaged_store_is_set_aside_and_rows_without_meaning_are_ignored(project: Path) -> None:
    folder = project / "_grimoire/cadrage/un"
    (folder / store.FILENAME).write_text('{"comments": [', encoding="utf-8")  # JSON tronqué
    assert _threads(project) == []
    made = _comment(project)
    aside = [p for p in folder.iterdir() if p.name.startswith(f"{store.FILENAME}.abime-")]
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == '{"comments": ['
    rows = json.loads((folder / store.FILENAME).read_text(encoding="utf-8"))["comments"]
    rows += ["pas un objet", {"id": "x"}, {**rows[0], "etat": "inconnu"}, {**rows[0], "auteur": "robot"}, {**rows[0], "start": "0"}]
    rows.append({**rows[0], "replies": [{"id": "r"}]})
    (folder / store.FILENAME).write_text(json.dumps({"version": 1, "comments": rows}), encoding="utf-8")
    listed = workspace_get(project, f"{P}atelier/chantiers/un/comments", {})
    assert [c["id"] for c in listed["comments"]] == [made["id"]] and listed["ignored"] == 6
    _comment(project, "encore", quote="Le bouton")
    assert len([p for p in folder.iterdir() if p.name.startswith(f"{store.FILENAME}.abime-")]) == 2  # l'avant-dernier gardé
    assert len(store.load(folder)) == 2


def test_a_store_that_is_not_a_regular_file_is_not_overwritten(project: Path) -> None:
    folder = project / "_grimoire/cadrage/un"
    (folder / "ailleurs.json").write_text("{}", encoding="utf-8")
    (folder / store.FILENAME).symlink_to(folder / "ailleurs.json")
    assert _threads(project) == []
    assert _post(project, "", {"block_id": _block(project), "texte": "a", "quote": QUOTE})[0] == 400
    assert (folder / "ailleurs.json").read_text(encoding="utf-8") == "{}"
    with pytest.raises(store.StoreError):
        store.read(folder)
    assert store.load(folder) == []


def test_a_non_list_store_is_damaged(project: Path) -> None:
    folder = project / "_grimoire/cadrage/un"
    (folder / store.FILENAME).write_text('{"comments": 3}', encoding="utf-8")
    assert store.read(folder)[2] == b'{"comments": 3}'


# ── l'agent : commente, répond, lit ; ne tranche pas ────────────────────────


def test_the_agent_comments_replies_and_reads_the_open_threads(project: Path) -> None:
    made = com.agent_comment(str(project), "un", "Et si la touche est lâchée ?", _block(project), QUOTE)["comment"]
    assert made["auteur"] == "agent" and made["quote"] == QUOTE
    asked = _comment(project, "Une question humaine", quote="Le bouton maintenu")
    reply = com.agent_comment(str(project), "un", "Voici ma réponse.", reply_to=asked["id"])["comment"]
    assert [(r["auteur"], r["texte"]) for r in reply["reponses"]] == [("agent", "Voici ma réponse.")]
    _post(project, f"/{made['id']}", {"action": "resolve"})
    seen = com.agent_threads(str(project), "un")["comments"]
    assert [c["id"] for c in seen] == [asked["id"]]  # résolu : plus à répondre
    assert seen[0]["block_text"] == PARA_A and seen[0]["quote"] == "Le bouton maintenu" and seen[0]["anchor"]["state"] == "ancre"
    _rewrite(project, "Le bouton maintenu", "Le bouton")
    (orphan,) = com.agent_threads(str(project), "un")["comments"]
    assert orphan["anchor"]["state"] == "orphelin" and orphan["quote"] == "Le bouton maintenu" and orphan["block_text"] == ""


def test_the_agent_text_is_refused_when_the_human_could_not_see_it(project: Path) -> None:
    for invisible in ("a​b", "a‮b", "a︀b", "a⠀b"):
        with pytest.raises(ValueError, match="invisible"):
            com.agent_comment(str(project), "un", invisible, _block(project), QUOTE)
    asked = _comment(project)
    with pytest.raises(ValueError, match="invisible"):
        com.agent_comment(str(project), "un", "a​b", reply_to=asked["id"])
    assert _threads(project)[0]["reponses"] == []


def test_an_agent_comment_can_point_at_the_suggestion_it_just_filed(project: Path) -> None:
    filed = sugg.suggest(str(project), "un", _block(project, PARA_B), PARA_B + " Plus court.")["suggestion"]
    made = com.agent_comment(str(project), "un", "Voir ma suggestion.", _block(project, PARA_B), suggestion=filed["id"])["comment"]
    assert made["suggestion"] == filed["id"]
    with pytest.raises(FileNotFoundError, match="suggestion inconnue"):
        com.agent_comment(str(project), "un", "x", _block(project, PARA_B), suggestion="sug-0000")
    with pytest.raises(ValueError, match="identifiant"):
        com.agent_comment(str(project), "un", "x", _block(project, PARA_B), suggestion=3)  # type: ignore[arg-type]
    human = _post(project, "", {"block_id": _block(project, PARA_B), "texte": "à moi", "suggestion": filed["id"]})[1]["comment"]
    assert human["suggestion"] == ""  # l'humain ne dépose pas de suggestion : le lien est celui de l'agent


def test_the_agent_cannot_resolve_reopen_or_delete_a_thread(project: Path) -> None:
    """Geste humain : ni outil, ni paramètre, ni route côté agent ; un fil résolu ne se rouvre pas en y répondant."""
    import inspect

    made = _comment(project)
    names: list[str] = []
    atelier_tools.register(lambda _a: lambda f: names.append(f.__name__) or f, lambda **_: None)
    assert names == ["atelier_suggest", "atelier_comment", "atelier_comments"]
    for function in (atelier_tools.atelier_comment, atelier_tools.atelier_comments, com.agent_comment, com.agent_threads):
        params = set(inspect.signature(function).parameters)
        assert not params & {"action", "etat", "state", "resolve", "resolved", "reopen", "delete", "auteur", "author"}, function
    assert not hasattr(atelier_tools, "act") and not hasattr(atelier_tools, "resolve")
    # Répondre ne change pas l'état ; un fil résolu refuse la réponse de l'agent.
    com.agent_comment(str(project), "un", "Je réponds.", reply_to=made["id"])
    assert _threads(project)[0]["etat"] == "ouvert"
    _post(project, f"/{made['id']}", {"action": "resolve"})
    with pytest.raises(wa.ConflictError):
        com.agent_comment(str(project), "un", "Je rouvre ?", reply_to=made["id"])
    out = atelier_tools.atelier_comment("un", "Je rouvre ?", reply_to=made["id"], project_path=str(project))
    assert out.is_error and "résolu" in out.content[0].text
    assert _threads(project)[0]["etat"] == "resolu"
    # La seule fonction qui tranche est celle de la route humaine, que l'outil n'importe pas.
    assert "workspace_atelier_comments import agent_comment" in inspect.getsource(atelier_tools.atelier_comment)
    assert "import agent_threads" in inspect.getsource(atelier_tools.atelier_comments)


def test_the_mcp_tools_answer_in_json(project: Path) -> None:
    out = json.loads(atelier_tools.atelier_comment("un", "Une remarque", _block(project), QUOTE, project_path=str(project)))
    assert out["ok"] and out["comment"]["auteur"] == "agent"
    again = json.loads(atelier_tools.atelier_comments("un", project_path=str(project)))
    assert [c["id"] for c in again["comments"]] == [out["comment"]["id"]]
    missing = atelier_tools.atelier_comment("un", "x", "brainstorm.0000", project_path=str(project))
    assert missing.is_error and "relire" in missing.content[0].text


def test_the_library_counts_open_threads_only_and_tolerates_a_missing_store(project: Path) -> None:
    rows = workspace_get(project, f"{P}atelier/chantiers", {})["chantiers"]
    assert rows[0]["commentaires"] == 0
    made = _comment(project)
    _comment(project, "Deux", quote="Le bouton")
    assert com.open_count(project, "un") == 2
    _post(project, f"/{made['id']}", {"action": "resolve"})
    assert com.open_count(project, "un") == 1
