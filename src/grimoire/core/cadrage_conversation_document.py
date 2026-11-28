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

Lot E (le document à la fin) : :func:`remplacer_rubrique` remplace, sur le seul geste d'acceptation de l'humain, les lignes
qu'une conversation POSSÈDE dans une rubrique (selon la trace de l'entretien) par la rédaction acceptée ; les réponses de
l'entretien, donc le gate, ne bougent pas, et aucune ligne de l'humain n'est touchée.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import cadrage_dir
from grimoire.core.cadrage_conversation import (
    MAX_ELEMENT_CHARS,
    MAX_FILE_BYTES,
    Conversation,
    ConversationConflictError,
    ConversationError,
    Element,
    clean_text,
    rubrique_key,
)
from grimoire.core.fsio import atomic_write, locked
from grimoire.core.regular_file import open_regular

__all__ = [
    "FORMULE_INCONNU", "a_defaire", "corriger", "ecrire", "empreinte", "lignes_possedees", "lignes_presentes", "origin",
    "reconcilier", "redactions_tracees", "remplacer_rubrique", "retirer", "traces", "verifier",
]


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


def _consignes(angles: dict[str, Any], conv: Conversation, el: Element) -> list[tuple[str, dict[str, Any]]]:
    """Les (réponse, ligne de trace) que la trace de l'entretien porte pour cet élément, repérées par son origine."""
    rec = angles.get(el.angle) or {}
    answers, want = rec.get("answers", []), origin(conv, el)
    return [(str(answers[i]), line) for i, line in enumerate(rec.get("lines", []))
            if isinstance(line, dict) and line.get("origin") == want and i < len(answers)]


def _relue(root: Path, slug: str, reponse: str, ligne: dict[str, Any]) -> str | None:
    """Le texte de la ligne RÉELLE du fichier de phase, si la trace et le fichier concordent ; sinon ``None`` (fail-closed).

    La ligne consignée doit exister UNE fois dans son fichier de phase et porter exactement la réponse consignée ; le texte
    doit être identique à lui-même après nettoyage (``clean_text``) : ni caractère de contrôle, ni retour à la ligne.
    """
    text = str(ligne.get("line", ""))
    try:
        reel = interview._phase_file(root, _name(slug), str(ligne.get("phase"))).read_text(encoding="utf-8").splitlines()
        propre = clean_text(reponse, MAX_ELEMENT_CHARS, what="texte du document")
    except (ValueError, OSError):  # ConversationError est une ValueError : fichier illisible ou texte que la conversation ne porte pas
        return None
    forme = text == f"- {reponse}" or (text.startswith("- *") and text.endswith(f"* {reponse}"))
    return propre if reel.count(text) == 1 and forme and propre == reponse else None


def reconcilier(root: Path, slug: str, conv: Conversation) -> list[str]:
    """Un élément « écrit » dont la trace de l'entretien ne porte pas son origine n'a pas été écrit par cette conversation
    (fichier forgé, ligne de l'entretien guidé, trace perdue) : il repasse « non écrit », et on le dit. Rien n'est retiré.

    Un élément écrit dont la trace porte un AUTRE texte que celui de la conversation (panne pendant la réécriture de ce
    qui suit un élément corrigé : le document a le texte corrigé, la conversation l'ancien) repasse « à confirmer », avec le
    texte EXACT relu dans la LIGNE RÉELLE du fichier de phase (origine ``document``, à confirmer seul) : une alerte le dit.
    Fail-closed : si la trace et le fichier de phase divergent entre eux, si le texte n'est pas identique à lui-même après
    nettoyage, ou s'il y a plusieurs lignes de même origine, l'alerte est seule : rien n'est proposé, écrit ni retiré.
    Rien n'est jamais écrit ni retiré ici ; l'élément reste propriétaire de sa ligne, donc le confirmer ne la réécrit pas.
    """
    angles = _angles(root, slug)
    seen = {o for rows in traces(root, slug).values() for o in rows if o}
    alertes = []
    for el in conv.elements:
        if el.ecrit and origin(conv, el) not in seen:
            el.ecrit = False
            alertes.append(f"« {el.texte[:60]} » n'est pas dans le document de cette conversation : je ne le retirerai pas")
    for el in conv.elements:
        rows = _consignes(angles, conv, el) if el.ecrit and el.genre == "element" else []
        if len(rows) == 1 and rows[0][0] == interview._fold(el.texte):
            continue
        if not rows:
            continue
        texte = _relue(root, slug, *rows[0]) if len(rows) == 1 else None
        if texte is None:
            alertes.append(f"« {el.texte[:60]} » : la trace et le fichier de phase ne concordent pas (ou plusieurs lignes de "
                           "même origine) : je n'y touche pas")
            continue
        alertes.append(f"« {el.texte[:60]} » : le document porte « {texte[:60]} » (panne pendant une correction) : "
                       "il attend ta confirmation sur ce texte exact")
        el.texte, el.statut, el.individuel, el.origine = texte, "propose", True, "document"
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


def _write_one(root: Path, name: str | None, conv: Conversation, el: Element) -> bool:
    """Range UN élément par ``answer`` ; ``ecrit`` dit s'il est vraiment entré (un doublon exact n'écrit rien). Rend vrai
    si le fichier a réellement changé.

    Un élément déjà rangé dont la ligne du document porte exactement son texte (repris après une panne) n'est pas réécrit :
    il garde sa ligne et son rang. Une ligne de même origine qui existe déjà sans concorder refuse d'écrire : jamais deux
    lignes de la même origine.
    """
    own = _consignes(_angles(root, "principal" if name is None else name), conv, el)
    if own and el.ecrit and el.genre == "element" and len(own) == 1 and own[0][0] == interview._fold(el.texte):
        return False
    if own:
        raise ConversationError("une ligne de cette conversation existe déjà pour cet élément : je n'en écris pas une seconde")
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
    return el.ecrit


def _refuse_accepted(root: Path, name: str | None, items: list[Element]) -> None:
    """Un document au statut accepté n'est jamais touché : refus AVANT la première écriture."""
    from grimoire.core.cadrage import PHASES

    for phase_id in {e.phase for e in items} | {PHASES[0].id}:
        try:
            interview._refuse_accepted(interview._phase_file(root, name, phase_id))
        except ValueError as exc:
            raise ConversationError(str(exc)) from exc


def ecrire(root: Path, slug: str, conv: Conversation, items: list[Element], *, remplace: Element | None = None) -> bool:
    """Range les éléments confirmés dans le document, tous ou aucun si le document est accepté (``ConversationError``).

    ``remplace`` : un élément déjà rangé que ceux-ci corrigent ; il est retiré d'abord, ce qui le suivait est réécrit.
    Rend vrai si un fichier a réellement changé.
    """
    name = _name(slug)
    with locked(cadrage_dir(root, name) / interview.STATE_FILE):
        _refuse_accepted(root, name, [*items, *([remplace] if remplace is not None else [])])
        changed = False
        if remplace is not None and remplace.ecrit:
            changed = True
            for later in _unwrite(root, name, conv, remplace)[1:]:
                _write_one(root, name, conv, later)
        for el in items:
            changed = _write_one(root, name, conv, el) or changed
        return changed


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


# ── Le document à la fin (lot E) : remplacer les SEULES lignes que la conversation possède ─────────────────────────────

#: La clé de ``entretien.json`` qui dit, par rubrique, quelles lignes une rédaction acceptée a écrites et pour quelle
#: conversation : comme ``origin`` sur les lignes de l'entretien, c'est la trace qui fait foi, jamais ``conversation.json``.
REDACTIONS_KEY = "redactions"
_ORIGINE_OK = re.compile(r"[A-Za-z0-9:_.+-]{1,120}")


def empreinte(root: Path, slug: str, phase: str) -> str:
    """L'empreinte du fichier de phase ET de la trace de l'entretien : une rédaction ne s'accepte que sur le document qu'elle a vu."""
    name = _name(slug)
    try:
        paths = (interview._phase_file(root, name, phase), cadrage_dir(root, name) / interview.STATE_FILE)
    except ValueError as exc:
        raise ConversationError(str(exc)) from exc
    digest = hashlib.sha256()
    for path in paths:
        try:
            with open_regular(path) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            data = b""
        except ValueError as exc:
            raise ConversationError(f"{path.name} illisible : je ne rédige rien") from exc
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def redactions_tracees(root: Path, slug: str) -> dict[str, dict[str, Any]]:
    """Les rédactions acceptées que la trace de l'entretien attribue à une conversation : ``{rubrique: {"origin", "lines"}}``.

    Une entrée mal formée est ignorée : sans trace lisible, la conversation ne possède aucune ligne, donc n'en retire aucune.
    """
    try:
        state = interview._load(root, _name(slug)) or {}
    except ValueError:
        return {}
    raw = state.get(REDACTIONS_KEY)
    out: dict[str, dict[str, Any]] = {}
    for key, rec in (raw.items() if isinstance(raw, dict) else ()):
        if not isinstance(rec, dict) or set(rec) != {"origin", "lines"}:
            continue
        mark, lines = rec["origin"], rec["lines"]
        if not isinstance(mark, str) or not _ORIGINE_OK.fullmatch(mark) or not isinstance(lines, list) or not 0 < len(lines) <= 20:
            continue
        if all(isinstance(ln, str) and ln.startswith("- ") and "\n" not in ln and "\r" not in ln and len(ln) <= 5000 for ln in lines):
            out[str(key)] = {"origin": mark, "lines": list(lines)}
    return out


def _rubrique(root: Path, slug: str, phase: str, section: str) -> tuple[Path, list[str], int, int]:
    """Le fichier de phase, ses lignes et les bornes (titre, fin) de la rubrique ; ``ConversationError`` si elle n'y est pas."""
    try:
        path = interview._phase_file(root, _name(slug), phase)
        text = interview.read_regular(path, limit=MAX_FILE_BYTES)
    except (ValueError, OSError) as exc:
        raise ConversationError(str(exc)) from exc
    lines = (text or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {section}"), None)
    if start is None:
        raise ConversationError(f"la rubrique « {section} » n'est plus dans le document : je n'y touche pas")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return path, lines, start, end


def lignes_possedees(root: Path, slug: str, conv: Conversation, phase: str, section: str, base: list[str],
                     cites: Mapping[str, set[str]]) -> list[str]:
    """Les lignes de la rubrique que CETTE conversation possède, dans l'ordre du fichier : celles que la trace de l'entretien lui
    attribue (``origin`` d'un élément), plus celles de sa dernière rédaction acceptée (``redactions`` de la trace).

    Fail-closed : ``ConversationError`` si une ligne possédée est plusieurs fois dans la rubrique, si la rédaction acceptée a
    été retouchée, ou si un élément de ``base`` n'y est plus (sa ligne a été retouchée à la main : elle est à l'humain).
    ``cites`` : pour chaque rédaction (son identifiant), les éléments qu'elle porte. Rien n'est écrit.
    """
    _path, lines, start, end = _rubrique(root, slug, phase, section)
    body = [ln.strip() for ln in lines[start + 1:end]]
    prefix = f"conv:{conv.cree}:"
    owned: list[str] = []
    covered: set[str] = set()
    for rec in _angles(root, slug).values():
        for line in rec.get("lines", []):
            mark = str(line.get("origin") or "") if isinstance(line, dict) else ""
            if not mark.startswith(prefix) or (line.get("phase"), line.get("section")) != (phase, section):
                continue
            text = str(line.get("line", "")).strip()
            if body.count(text) > 1:
                raise ConversationError("une ligne de cette conversation est plusieurs fois dans la rubrique : je ne choisis pas")
            if body.count(text) == 1:
                owned.append(text)
                covered.add(mark[len(prefix):])
    rec = redactions_tracees(root, slug).get(rubrique_key(phase, section))
    if rec is not None and rec["origin"].startswith(prefix):
        present = [body.count(ln.strip()) for ln in rec["lines"]]
        if all(n == 1 for n in present):
            owned += [ln.strip() for ln in rec["lines"]]
            covered |= cites.get(rec["origin"][len(prefix):], set())
        elif any(present):
            raise ConversationError("la rédaction acceptée a été retouchée à la main dans cette rubrique : je ne la réécris pas")
    if len(set(owned)) != len(owned):
        raise ConversationError("deux lignes de cette conversation sont identiques dans la rubrique : je ne choisis pas")
    if any(i not in covered for i in base):
        raise ConversationError("le document a été retouché à la main dans cette rubrique (une ligne de la conversation n'y est "
                                "plus telle quelle) : je ne le réécris pas")
    return sorted(owned, key=body.index)


def lignes_presentes(root: Path, slug: str, phase: str, section: str, lignes: list[str]) -> bool:
    """Ces lignes sont-elles toutes, une fois chacune, dans la rubrique ? Lecture seule ; faux si la rubrique est introuvable."""
    try:
        _path, lines, start, end = _rubrique(root, slug, phase, section)
    except ConversationError:
        return False
    body = [ln.strip() for ln in lines[start + 1:end]]
    return all(body.count(ln.strip()) == 1 for ln in lignes)


def remplacer_rubrique(root: Path, slug: str, conv: Conversation, phase: str, section: str, base: list[str],
                       cites: Mapping[str, set[str]], nouvelles: list[str], attendue: str, marque: str) -> None:
    """Remplace, dans la rubrique, les SEULES lignes que la conversation possède par ``nouvelles`` (à la place de la première).

    Les lignes de l'humain restent où elles sont. Sous le verrou de l'entretien : l'empreinte doit être celle de la
    proposition (``ConversationConflictError``, raison ``document-change``), le document ne doit pas être accepté ; puis la
    trace ``redactions`` de l'entretien attribue les nouvelles lignes à ``marque`` (``conv:<création>:<rédaction>``).
    """
    name = _name(slug)
    with locked(cadrage_dir(root, name) / interview.STATE_FILE):
        if empreinte(root, slug, phase) != attendue:
            raise ConversationConflictError("le document a changé depuis la proposition : relis la proposition", "document-change")
        path, lines, start, end = _rubrique(root, slug, phase, section)
        try:
            interview._refuse_accepted(path)
        except ValueError as exc:
            raise ConversationError(str(exc)) from exc
        owned = set(lignes_possedees(root, slug, conv, phase, section, base, cites))
        at = [i for i in range(start + 1, end) if lines[i].strip() in owned]
        if not at:
            raise ConversationError("aucune ligne de cette conversation dans la rubrique : rien à remplacer")
        gone = set(at)
        kept = [ln for i, ln in enumerate(lines[at[0]:end], at[0]) if i not in gone]
        atomic_write(path, "\n".join([*lines[:at[0]], *nouvelles, *kept, *lines[end:]]).rstrip("\n") + "\n")
        state = interview._load(root, name) or {}
        found = state.get(REDACTIONS_KEY)
        recs: dict[str, Any] = found if isinstance(found, dict) else {}
        recs[rubrique_key(phase, section)] = {"origin": marque, "lines": list(nouvelles)}
        state[REDACTIONS_KEY] = recs
        interview._save(root, name, state)
