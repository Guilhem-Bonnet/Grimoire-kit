"""Le rangement dans le document : la seule porte d'écriture d'une conversation (épique #1477, lot A).

Un élément compris n'entre dans un fichier de phase qu'une fois CONFIRMÉ (ou corrigé par l'humain), et il y entre par
les fonctions EXISTANTES de l'entretien guidé (:func:`grimoire.core.cadrage_interview.answer` et
:func:`~grimoire.core.cadrage_interview.retract`) : même forme de ligne (``- *question* réponse``), même trace dans
``entretien.json``, même refus d'écrire dans un document accepté, jamais un mot du gate touché. Rien ici n'ouvre un
fichier de phase : un moteur, une route, un fichier de conversation hostile ne peuvent donc écrire que ce que
l'entretien sait écrire, dans les rubriques que les angles du kit portent.

Retirer ou corriger un élément déjà rangé suit la pile de l'angle (``retract`` retire la DERNIÈRE réponse) : on retire
ce qui a été rangé après lui, puis lui, et on réécrit ce qui doit rester. Avant de toucher à quoi que ce soit, on
vérifie que les dernières réponses de l'angle sont bien celles que cette conversation a rangées ; sinon (le document
a été retouché ailleurs) on ne touche à rien et on le dit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import cadrage_dir
from grimoire.core.cadrage_conversation import MAX_ELEMENT_CHARS, Conversation, ConversationError, Element, clean_text
from grimoire.core.fsio import locked

__all__ = ["FORMULE_INCONNU", "a_defaire", "corriger", "ecrire", "origin", "reconcilier", "retirer", "traces", "verifier"]


#: Le texte d'un « je ne sais pas » sans rien d'autre : l'entretien guidé n'écrit alors aucune note.
FORMULE_INCONNU = "Je ne sais pas"


def _name(slug: str) -> str | None:
    return None if slug == "principal" else slug


def origin(conv: Conversation, el: Element) -> str:
    """Ce que la trace de l'entretien porte pour une ligne écrite par cette conversation : elle seule peut la retirer."""
    return f"conv:{conv.cree}:{el.id}"


def traces(root: Path, slug: str) -> dict[str, list[str | None]]:
    """Les origines portées par la trace de l'entretien, par angle, dans l'ordre des réponses (``None`` : ligne sans origine).

    La clé ``"skip"`` de chaque angle écarté est rendue sous ``"<angle>#skip"``. Lecture seule.
    """
    out: dict[str, list[str | None]] = {}
    for angle, rec in _angles(root, slug).items():
        out[angle] = [None if line is None else line.get("origin") for line in rec.get("lines", [])]
        if isinstance(rec.get("skip"), dict):
            out[f"{angle}#skip"] = [rec["skip"].get("origin")]
    return out


def _angles(root: Path, slug: str) -> dict[str, Any]:
    try:
        state = interview._load(root, _name(slug))
    except ValueError as exc:
        raise ConversationError(str(exc)) from exc
    return dict((state or {}).get("angles") or {})


def _consigne(angles: dict[str, Any], conv: Conversation, el: Element) -> str | None:
    """La réponse que la trace de l'entretien porte pour la ligne de cet élément (repérée par son origine), ou ``None``."""
    rec = angles.get(el.angle) or {}
    answers, want = rec.get("answers", []), origin(conv, el)
    return next((str(answers[i]) for i, line in enumerate(rec.get("lines", []))
                 if line is not None and line.get("origin") == want and i < len(answers)), None)


def reconcilier(root: Path, slug: str, conv: Conversation) -> list[str]:
    """Un élément « écrit » dont la trace de l'entretien ne porte pas son origine n'a pas été écrit par cette conversation
    (fichier forgé, ligne de l'entretien guidé, trace perdue) : il repasse « non écrit », et on le dit. Rien n'est retiré.

    Un élément écrit dont la ligne du document porte un AUTRE texte que celui consigné (panne pendant la réécriture de ce
    qui suit un élément corrigé : le document a le texte corrigé, la conversation l'ancien) repasse « à confirmer » avec le
    texte EXACT du document, à confirmer seul : une alerte le dit. Rien n'est écrit ni retiré ; l'élément reste le propriétaire
    de sa ligne, donc le confirmer, le corriger ou l'écarter agit sur ce que le document contient vraiment.
    """
    angles = _angles(root, slug)
    seen = {o for rows in traces(root, slug).values() for o in rows if o}
    alertes = []
    for el in conv.elements:
        if el.ecrit and origin(conv, el) not in seen:
            el.ecrit = False
            alertes.append(f"« {el.texte[:60]} » n'est pas dans le document de cette conversation : je ne le retirerai pas")
    for el in conv.elements:
        actuel = _consigne(angles, conv, el) if el.ecrit and el.genre == "element" else None
        if actuel is None or actuel == interview._fold(el.texte):
            continue
        try:
            texte = clean_text(actuel, MAX_ELEMENT_CHARS, what="texte du document")
        except ConversationError:  # un texte que la conversation ne saurait pas porter : dit, laissé tel quel
            alertes.append(f"« {el.texte[:60]} » a une autre ligne dans le document que celle consignée : je n'y touche pas")
            continue
        alertes.append(f"« {el.texte[:60]} » : le document porte « {texte[:60]} » (panne pendant une correction) : "
                       "il attend ta confirmation sur ce texte exact")
        el.texte, el.statut, el.individuel = texte, "propose", True
    return alertes


def verifier(root: Path, slug: str, items: list[Element]) -> None:
    """Refuse AVANT toute écriture si un fichier visé est au statut accepté."""
    _refuse_accepted(root, _name(slug), items)


def _answers(root: Path, name: str | None, angle: str) -> tuple[list[str], str, str]:
    """Les réponses consignées de l'angle, son état et sa raison, lus dans l'entretien (rien n'est écrit)."""
    shown = interview.view(root, name)
    rec = next((a for a in (shown or {}).get("angles", []) if a["id"] == angle), None)
    if rec is None:
        return [], "ouvert", ""
    return [str(a) for a in rec.get("answers", [])], str(rec.get("state", "ouvert")), str(rec.get("reason", ""))


def _expected(el: Element) -> str:
    """Ce que l'entretien a consigné comme réponse pour cet élément."""
    if el.genre == "inconnu":
        title = next(a.title for a in interview.ANGLES if a.id == el.angle)
        return interview.UNKNOWN_PREFIX + (interview._label(el.question) or title)
    return interview._fold(el.texte)


def _pile(conv: Conversation, angle: str) -> list[Element]:
    """Les éléments déjà rangés par cette conversation sur l'angle, dans l'ordre où ils l'ont été."""
    return sorted((e for e in conv.elements if e.ecrit and e.angle == angle and e.genre != "sansobjet"), key=lambda e: e.rang)


def a_defaire(conv: Conversation, el: Element) -> list[Element]:
    """Ce qu'un retrait de ``el`` défait puis réécrit : lui et ce qui a été rangé après lui sur son angle (rien s'il n'est pas rangé).

    L'appelant les inscrit au journal d'intention AVANT de toucher au document : après une panne en route, ce que la trace de
    l'entretien ne porte plus redevient « à confirmer », jamais perdu en silence.
    """
    if not el.ecrit:
        return []
    if el.genre == "sansobjet":
        return [el]
    stack = _pile(conv, el.angle)
    return stack[stack.index(el):] if el in stack else [el]


def _retract(root: Path, name: str | None, angle: str) -> None:
    try:
        interview.retract(root, name, angle)
    except ValueError as exc:  # rien à retirer, chantier inconnu, document accepté…
        raise ConversationError(str(exc)) from exc


def _write_one(root: Path, name: str | None, conv: Conversation, el: Element) -> None:
    """Range UN élément par ``answer`` ; ``ecrit`` dit s'il est vraiment entré (un doublon exact n'écrit rien).

    Un élément déjà rangé dont la ligne du document porte exactement son texte (repris après une panne) n'est pas réécrit :
    il garde sa ligne et son rang.
    """
    if el.ecrit and el.genre == "element" and _consigne(_angles(root, "principal" if name is None else name), conv, el) == interview._fold(el.texte):
        return
    before, _state, _ = _answers(root, name, el.angle)
    origine = origin(conv, el)
    try:
        if el.genre == "element":
            interview.answer(root, name, el.angle, el.texte, question=el.question or None, rubrique=(el.phase, el.section),
                             origine=origine)
        elif el.genre == "inconnu":  # ce que l'humain a dit en plus est gardé comme note de la question ouverte
            note = "" if el.texte == FORMULE_INCONNU else el.texte
            interview.answer(root, name, el.angle, note, unknown=True, question=el.question or None, origine=origine)
        else:
            interview.answer(root, name, el.angle, "sans objet", skip=True, origine=origine)
    except ValueError as exc:  # document accepté, chantier inconnu, rubrique que l'angle ne porte pas…
        raise ConversationError(str(exc)) from exc
    after, state, reason = _answers(root, name, el.angle)
    el.ecrit = state == "ecarte" and bool(reason.strip()) if el.genre == "sansobjet" else len(after) > len(before)
    conv.rang += 1
    el.rang = conv.rang


def _refuse_accepted(root: Path, name: str | None, items: list[Element]) -> None:
    """Un document au statut accepté n'est jamais touché : refus AVANT la première écriture."""
    from grimoire.core.cadrage import PHASES

    for phase_id in {e.phase for e in items} | {PHASES[0].id}:
        try:
            interview._refuse_accepted(interview._phase_file(root, name, phase_id))
        except ValueError as exc:
            raise ConversationError(str(exc)) from exc


def ecrire(root: Path, slug: str, conv: Conversation, items: list[Element], *, remplace: Element | None = None) -> None:
    """Range les éléments confirmés dans le document, tous ou aucun si le document est accepté (``ConversationError``).

    ``remplace`` : un élément déjà rangé que ceux-ci corrigent ; il est retiré d'abord, ce qui le suivait est réécrit.
    """
    name = _name(slug)
    with locked(cadrage_dir(root, name) / interview.STATE_FILE):
        _refuse_accepted(root, name, [*items, *([remplace] if remplace is not None else [])])
        if remplace is not None and remplace.ecrit:
            for later in _unwrite(root, name, conv, remplace)[1:]:
                _write_one(root, name, conv, later)
        for el in items:
            _write_one(root, name, conv, el)


def _unwrite(root: Path, name: str | None, conv: Conversation, el: Element) -> list[Element]:
    """Retire du document ``el`` et ce qui a été rangé après lui sur son angle ; rend ces éléments, ``el`` en tête."""
    seen = traces(root, name or "principal")
    if el.genre == "sansobjet":
        _, state, reason = _answers(root, name, el.angle)
        if state != "ecarte" or not reason.strip():
            raise ConversationError("l'angle n'est plus écarté dans l'entretien : je ne touche à rien")
        if seen.get(f"{el.angle}#skip") != [origin(conv, el)]:
            raise ConversationError("cet écart n'a pas été fait par cette conversation : je ne touche à rien")
        _refuse_accepted(root, name, [el])
        _retract(root, name, el.angle)
        el.ecrit = False
        return [el]
    stack = _pile(conv, el.angle)
    if el not in stack:
        raise ConversationError("cet élément n'est pas rangé dans le document")
    tail = stack[stack.index(el):]
    answers, state, _ = _answers(root, name, el.angle)
    wanted = [_expected(e) for e in tail]
    if state == "ecarte" or answers[-len(wanted):] != wanted:
        raise ConversationError("le document ne correspond plus à la conversation (réponses modifiées ailleurs) : je ne touche à rien")
    if seen.get(el.angle, [])[-len(wanted):] != [origin(conv, e) for e in tail]:
        raise ConversationError("ces lignes n'ont pas été écrites par cette conversation : je ne touche à rien")
    _refuse_accepted(root, name, tail)
    for _ in tail:
        _retract(root, name, el.angle)
    for e in tail:
        e.ecrit = False
    return tail


def retirer(root: Path, slug: str, conv: Conversation, el: Element) -> None:
    """Retire ``el`` du document (il est écarté) et réécrit ce qui avait été rangé après lui sur son angle."""
    name = _name(slug)
    with locked(cadrage_dir(root, name) / interview.STATE_FILE):
        tail = _unwrite(root, name, conv, el)
        for later in tail[1:]:
            _write_one(root, name, conv, later)


def corriger(root: Path, slug: str, conv: Conversation, el: Element, texte: str) -> None:
    """Retire puis réécrit : ``el`` reprend la place de la fin avec son texte corrigé, ce qui le suivait est réécrit après."""
    name = _name(slug)
    with locked(cadrage_dir(root, name) / interview.STATE_FILE):
        tail = _unwrite(root, name, conv, el)
        el.texte = texte
        for item in tail:
            _write_one(root, name, conv, item)
