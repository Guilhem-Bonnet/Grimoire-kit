"""Le document à la fin : proposer, accepter, écarter, reprendre une rédaction, et dire d'où vient chaque phrase (épique #1477, lot E).

- **Proposer** n'écrit rien dans le document. Le moteur rédige HORS du verrou, à partir des seuls éléments confirmés et rangés de
  la rubrique ; sous le verrou, la conversation et le document doivent être ceux qu'il a vus (sinon 409), puis la proposition
  est enregistrée en ``attente`` avec ses liens vers l'échange et l'empreinte du document.
- **Accepter** est le seul geste qui écrit : l'humain renvoie le TEXTE EXACT affiché ; les éléments confirmés doivent être ceux
  de la proposition et le document celui qu'elle a vu (« le document a changé, relis la proposition ») ; seules les lignes
  que la conversation possède pour la rubrique sont remplacées (:func:`~grimoire.core.cadrage_conversation_document.remplacer_rubrique`),
  jamais une ligne de l'humain.
- **Écarter** ferme la proposition ; **reprendre** la ferme aussi et rouvre la discussion sur la rubrique avec la mascotte
  (la réponse suivante de l'humain y est rangée, proposée, jamais écrite d'office). Aucun des deux n'écrit dans le document.

Le gate de ``cadrage check`` n'est pas touché : les réponses de l'entretien restent ce qu'elles étaient, seule la mise en
forme des lignes de la conversation change.
"""

from __future__ import annotations

import contextlib
from typing import Any

from grimoire.core import cadrage_conversation_document as rangement
from grimoire.core import cadrage_conversation_redaction as red
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_conversation_turn as turn
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import PHASES
from grimoire.core.cadrage_conversation import (
    MAX_TURNS,
    Conversation,
    ConversationConflictError,
    ConversationError,
    Element,
    Question,
    angle_pour,
)
from grimoire.core.cadrage_conversation_hygiene import statut

__all__ = ["accepter", "ecarter", "lire", "lire_une", "proposer", "reprendre"]

_TITRES = {p.id: p.title for p in PHASES}
_TOUR_CHARS = 300


def _nom(phase: str, section: str) -> str:
    return f"{_TITRES.get(phase, phase).split(' — ')[0]}, partie « {section} »"


def _conversation(ctx: turn.Contexte, rev: int) -> Conversation:
    """La conversation enregistrée, à la version vue, non classée ; ce que la trace de l'entretien dit est repris (en mémoire)."""
    conv = store.read(ctx.directory, ctx.slug)
    if conv is None:
        raise FileNotFoundError("aucune conversation pour ce chantier : démarre-en une")
    if conv.reparer:
        raise ConversationError(conv.reparer)
    if conv.rev != rev:
        raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
    if statut(conv, ctx.now, ctx.reglages) == "classee":
        raise ConversationError("cette conversation est classée : rouvre-la d'abord")
    turn._reprendre(ctx, conv)
    return conv


def _propositions(conv: Conversation) -> list[red.Proposition]:
    return conv.redactions


def _base(conv: Conversation, phase: str, section: str) -> list[Element]:
    """Les éléments CONFIRMÉS et rangés de la rubrique : les seuls qu'une rédaction peut porter."""
    return [e for e in conv.ranges() if e.genre == "element" and e.ecrit and (e.phase, e.section) == (phase, section)]


def _empreinte_base(base: list[Element]) -> list[red.Fondement]:
    return [red.Fondement(e.id, e.texte) for e in base]


def _cites(conv: Conversation) -> dict[str, set[str]]:
    return {p.id: p.cites for p in _propositions(conv)}


def _possedees(ctx: turn.Contexte, conv: Conversation, phase: str, section: str, base: list[Element]) -> list[str]:
    return rangement.lignes_possedees(ctx.root, ctx.slug, conv, phase, section, [e.id for e in base], _cites(conv))


def _dire(ctx: turn.Contexte, conv: Conversation, texte: str, geste: str) -> int:
    """Une parole de la mascotte (sauf conversation pleine) ; rend son numéro de tour (0 si rien n'est dit)."""
    if len(conv.tours) >= MAX_TURNS:
        return 0
    return conv.say("mascotte", texte, geste, ctx.date).n


def _enregistrer(ctx: turn.Contexte, conv: Conversation) -> None:
    red.borner(conv.redactions)
    conv.active_le = ctx.date
    conv.rev += 1
    store.save(ctx.directory, conv)


def _attente(conv: Conversation, pid: Any) -> red.Proposition:
    prop = next((p for p in _propositions(conv) if p.id == pid), None)
    if prop is None:
        raise FileNotFoundError(f"rédaction inconnue : {str(pid)[:20]!r}")
    if prop.statut != "attente":
        raise ConversationConflictError("cette rédaction n'est plus en attente : relis les propositions", "deja-tranchee")
    return prop


# ── Proposer : rien n'est écrit ────────────────────────────────────────────


def proposer(ctx: turn.Contexte, rev: int, rubrique: Any) -> dict[str, Any]:
    """Propose une rédaction pour la rubrique (``phase/section`` de la liste fermée), à partir de ses éléments confirmés."""
    phase, section = red.cle_rubrique(rubrique)
    conv = _conversation(ctx, rev)
    base = _base(conv, phase, section)
    if not base:
        raise ConversationError("rien de confirmé dans cette rubrique : il n'y a rien à rédiger")
    _possedees(ctx, conv, phase, section, base)  # un document retouché à la main se refuse avant d'appeler le moteur
    vue = rangement.empreinte(ctx.root, ctx.slug, phase)
    entree = red.EntreeRedaction(f"{phase}/{section}", _nom(phase, section), tuple(_empreinte_base(base)))
    redaction, auteur = red.rediger(ctx.moteur, entree)  # hors du verrou : un moteur lent ne retient pas le fichier
    with store.locked_store(ctx.directory):
        ctx.alertes.clear()
        conv = _conversation(ctx, rev)
        base = _base(conv, phase, section)
        if _empreinte_base(base) != list(entree.elements) or rangement.empreinte(ctx.root, ctx.slug, phase) != vue:
            raise ConversationConflictError("la conversation ou le document a changé pendant la rédaction : recommence",
                                            "conversation-changee")
        _possedees(ctx, conv, phase, section, base)
        sources = {e.id: e.source for e in base}
        for old in _propositions(conv):
            if old.statut == "attente" and (old.phase, old.section) == (phase, section):
                old.statut, old.tranchee_le = "remplacee", ctx.date
        prop = red.Proposition(
            red.nouvel_id(_propositions(conv)), phase, section, "attente", auteur, ctx.date, vue,
            [red.Passage(p.texte, list(p.fondements), sorted({sources[c] for c in p.fondements})) for p in redaction.phrases],
            list(entree.elements))
        prop.tour = _dire(ctx, conv, f"Voici une rédaction pour « {section} ». Relis-la : rien n'est écrit dans le document "
                          "sans ton accord. Tu peux l'accepter telle quelle, l'écarter, ou me demander d'y revenir.", "explication")
        conv.redactions.append(prop)
        _enregistrer(ctx, conv)
        return {
            "rev": conv.rev, "document_modifie": False, "proposition": _publique(ctx, conv, prop), "alertes": list(ctx.alertes),
            "moteur": {"id": auteur, "degrade": ctx.moteur.degrade, "notes": list(ctx.moteur.notes)},
        }


# ── Accepter : le seul geste qui écrit ─────────────────────────────────────


def accepter(ctx: turn.Contexte, rev: int, pid: Any, texte: Any) -> dict[str, Any]:
    """Écrit la rédaction, si ``texte`` est EXACTEMENT celui de la proposition affichée et que rien n'a bougé depuis."""
    if not isinstance(texte, str) or not texte:
        raise ConversationError("texte requis : le texte exact de la rédaction affichée")
    with store.locked_store(ctx.directory):
        conv = _conversation(ctx, rev)
        prop = _attente(conv, pid)
        if texte != prop.texte:
            raise ConversationConflictError("ce texte n'est pas celui de la rédaction proposée : relis-la", "proposition-differente")
        base = _base(conv, prop.phase, prop.section)
        if _empreinte_base(base) != prop.base:
            raise ConversationConflictError("ce qui est confirmé dans cette rubrique a changé depuis la proposition : relis la "
                                            "proposition", "base-changee")
        store.check_room(conv)
        rangement.remplacer_rubrique(
            ctx.root, ctx.slug, conv, prop.phase, prop.section, [e.id for e in base], _cites(conv), prop.lignes,
            prop.empreinte, f"conv:{conv.cree}:{prop.id}")
        for old in _propositions(conv):
            if old.statut == "acceptee" and old.cle == prop.cle:
                old.statut = "remplacee"
        prop.statut, prop.tranchee_le = "acceptee", ctx.date
        message = (f"C'est écrit dans le document, partie « {prop.section} », à la place de ce que j'y avais noté. "
                   "Les lignes que tu as écrites toi-même n'ont pas bougé.")
        _dire(ctx, conv, message, "note")
        _enregistrer(ctx, conv)
        return {"rev": conv.rev, "document_modifie": True, "message": message, "proposition": _publique(ctx, conv, prop),
                "alertes": list(ctx.alertes)}


# ── Écarter, reprendre : rien n'est écrit ──────────────────────────────────


def ecarter(ctx: turn.Contexte, rev: int, pid: Any) -> dict[str, Any]:
    """Écarte la rédaction proposée : le document reste tel quel."""
    with store.locked_store(ctx.directory):
        conv = _conversation(ctx, rev)
        prop = _attente(conv, pid)
        prop.statut, prop.tranchee_le = "ecartee", ctx.date
        message = "D'accord, je laisse le document tel quel."
        _dire(ctx, conv, message, "note")
        _enregistrer(ctx, conv)
        return {"rev": conv.rev, "document_modifie": False, "message": message, "alertes": list(ctx.alertes)}


def _index(angle: str, phase: str, section: str) -> int:
    meta = next(a for a in interview.ANGLES if a.id == angle)
    return next((i for i in range(len(meta.seeds)) if interview.question_target(meta, i) == (phase, section)), 0)


def reprendre(ctx: turn.Contexte, rev: int, pid: Any) -> dict[str, Any]:
    """Ferme la proposition et rouvre la discussion sur sa rubrique : la prochaine réponse de l'humain y est rangée (proposée)."""
    with store.locked_store(ctx.directory):
        conv = _conversation(ctx, rev)
        prop = _attente(conv, pid)
        prop.statut, prop.tranchee_le = "reprise", ctx.date
        angle = angle_pour(prop.phase, prop.section)
        message = (f"D'accord, on reprend « {prop.section} ». Dis-moi ce que tu veux changer ou ajouter : je te le "
                   "reproposerai, et rien n'est écrit sans ton accord.")
        conv.question = Question("amorce", message, "Tu as demandé d'y revenir avant que la rédaction entre dans le document.",
                                 angle, _index(angle, prop.phase, prop.section), prop.phase, prop.section, False)
        conv.mode = "cadrage"
        _dire(ctx, conv, message, "reprise")
        _enregistrer(ctx, conv)
        return {"rev": conv.rev, "document_modifie": False, "message": message, "alertes": list(ctx.alertes)}


# ── Lire : d'où vient chaque phrase (rien n'est écrit) ─────────────────────


def _tour(conv: Conversation, n: int) -> dict[str, Any] | None:
    t = next((t for t in conv.tours if t.n == n), None)
    if t is None:
        return None
    texte = t.texte if len(t.texte) <= _TOUR_CHARS else t.texte[: _TOUR_CHARS - 1] + "…"
    return {"n": t.n, "auteur": t.auteur, "texte": texte, "date": t.date}


def _phrase(conv: Conversation, prop: red.Proposition, passage: red.Passage) -> dict[str, Any]:
    textes = {f.id: f.texte for f in prop.base}
    fondements = []
    for ident in passage.fondements:
        el = conv.element(ident)
        fondements.append({"id": ident, "texte": textes.get(ident, el.texte if el else ""), "statut": el.statut if el else "",
                           "source": el.source if el else 0})
    tours = [t for t in (_tour(conv, n) for n in passage.tours) if t is not None]
    return {"texte": passage.texte, "ligne": passage.ligne, "fondements": fondements, "tours": tours}


def _publique(ctx: turn.Contexte, conv: Conversation, prop: red.Proposition) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": prop.id, "statut": prop.statut, "texte": prop.texte, "moteur": prop.moteur, "date": prop.date,
        "tranchee_le": prop.tranchee_le, "tour": prop.tour,
        "rubrique": {"cle": prop.cle, "phase": prop.phase, "section": prop.section, "nom": _nom(prop.phase, prop.section)},
        "phrases": [_phrase(conv, prop, p) for p in prop.passages],
    }
    if prop.statut == "acceptee":
        out["perimee"] = _empreinte_base(_base(conv, prop.phase, prop.section)) != prop.base
        out["dans_le_document"] = rangement.lignes_presentes(ctx.root, ctx.slug, prop.phase, prop.section, prop.lignes)
    return out


def _lue(ctx: turn.Contexte) -> Conversation | None:
    conv = store.read(ctx.directory, ctx.slug)
    if conv is not None and not conv.reparer:
        with contextlib.suppress(ConversationError):  # en mémoire seulement : ne croire que la trace de l'entretien
            rangement.reconcilier(ctx.root, ctx.slug, conv)
    return conv


def lire(ctx: turn.Contexte) -> dict[str, Any]:
    """Les rubriques rédigeables, les propositions en attente, et chaque passage écrit avec ses éléments et tours d'origine."""
    conv = _lue(ctx)
    if conv is None:
        return {"existe": False}
    rubriques = []
    for phase, section in red.RUBRIQUES_REDIGEABLES:
        n = len(_base(conv, phase, section))
        if n:
            rubriques.append({"cle": f"{phase}/{section}", "phase": phase, "section": section, "nom": _nom(phase, section),
                              "confirmes": n})
    passages = []
    for prop in _propositions(conv):
        if prop.statut != "acceptee":
            continue
        shown = _publique(ctx, conv, prop)
        for phrase in shown["phrases"]:
            passages.append({**phrase, "proposition": prop.id, "rubrique": shown["rubrique"], "perimee": shown["perimee"],
                             "dans_le_document": shown["dans_le_document"]})
    return {
        "existe": True, "rev": conv.rev, "rubriques": rubriques, "passages": passages,
        "propositions": [_publique(ctx, conv, p) for p in _propositions(conv) if p.statut == "attente"],
        "historique": [{"id": p.id, "rubrique": p.cle, "statut": p.statut, "moteur": p.moteur, "date": p.date,
                        "tranchee_le": p.tranchee_le} for p in _propositions(conv)],
    }


def lire_une(ctx: turn.Contexte, pid: str) -> dict[str, Any]:
    """Une rédaction (tout statut), avec la trace de chacune de ses phrases."""
    conv = _lue(ctx)
    prop = None if conv is None else next((p for p in _propositions(conv) if p.id == pid), None)
    if conv is None or prop is None:
        raise FileNotFoundError(f"rédaction inconnue : {pid[:20]!r}")
    return _publique(ctx, conv, prop)
