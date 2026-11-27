"""Les tours d'une conversation de cadrage : le programme enchaîne, le moteur ne fait que des gestes (épique #1477, lot A).

Chaque fonction publique est UNE opération complète, sous le verrou du fichier de conversation : relire l'état,
vérifier qu'il n'a pas bougé (``rev`` : un autre onglet ou la CLI a écrit entre-temps : 409), appliquer, enregistrer.
Rien n'est enregistré si l'opération échoue (document accepté, rubrique hors cadre, moteur hostile…).

Ce que fait un tour de l'humain (:func:`recevoir`) : classer (geste du moteur, validé), appliquer la classe (pause,
brainstorm, hors-sujet noté « pour plus tard », « je ne sais pas » rangé en question ouverte, éléments PROPOSÉS à partir de
la réponse…), puis laisser la machine à états (:mod:`grimoire.core.cadrage_conversation_flow`) choisir l'étape suivante
et dire la question ou la reformulation. Un élément n'entre dans le document qu'à sa confirmation
(:func:`decider`, ou « oui » en réponse à la reformulation), par :mod:`grimoire.core.cadrage_conversation_document`.

L'indicateur « tour lourd » est calculé AVANT le travail du moteur, et l'accusé de réception (``accuse``) est un texte
du programme : une interface qui sépare les deux temps (lots C et D) n'a rien à changer ici.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from grimoire.core import cadrage_conversation_document as rangement
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import PHASES, cadrage_dir
from grimoire.core.cadrage_conversation import (
    MAX_BOT_CHARS,
    MAX_ELEMENT_CHARS,
    MAX_TURN_CHARS,
    RUBRIQUES,
    Conversation,
    ConversationConflictError,
    ConversationError,
    Element,
    Question,
    Tour,
    angle_pour,
    clean_text,
    rubrique_key,
)
from grimoire.core.cadrage_conversation_engine import (
    EntreeClassement,
    EntreeExtraction,
    EntreePistes,
    EntreeQuestion,
    EntreeReformulation,
    Garde,
)
from grimoire.core.cadrage_conversation_flow import (
    NOMS,
    Cadre,
    Etape,
    cadre_from_view,
    ce_qui_manque,
    prochaine_etape,
    resume,
)
from grimoire.core.cadrage_conversation_hygiene import Reglages, statut
from grimoire.core.cadrage_conversation_hygiene import classer as classer_conv
from grimoire.core.cadrage_conversation_hygiene import rouvrir as rouvrir_conv

__all__ = [
    "Contexte",
    "classer",
    "compris",
    "decider",
    "demarrer",
    "pause",
    "recevoir",
    "rouvrir",
    "terminer",
    "vue",
]

_KEY = {rubrique_key(p, s): (p, s) for p, s in RUBRIQUES}
_TITRES = {p.id: p.title for p in PHASES}
_GESTE_UTILISATEUR = {
    "reponse": "reponse", "confirme": "confirmation", "hors_sujet": "hors-sujet", "ne_sait_pas": "ne-sait-pas",
    "sans_objet": "sans-objet", "contredit": "contradiction", "brainstorm": "brainstorm", "reprise": "reprise",
    "pause": "pause", "question": "reponse",
}
_ACCUSE_MAX = 500
VUE_TOURS = 100


@dataclass(slots=True)
class Contexte:
    """Ce qu'une opération reçoit : le projet, le chantier (dossier retrouvé dans la liste réelle), le moteur, l'horloge."""

    root: Path
    slug: str
    directory: Path
    moteur: Garde
    now: datetime
    reglages: Reglages

    @property
    def name(self) -> str | None:
        return None if self.slug == "principal" else self.slug

    @property
    def date(self) -> str:
        return self.now.isoformat(timespec="seconds")


# ── Lecture de l'avancement et de l'état ───────────────────────────────────


def _view(ctx: Contexte) -> dict[str, Any] | None:
    try:
        return interview.view(ctx.root, ctx.name)
    except ValueError as exc:  # entretien.json abîmé : dit, sans rien écrire
        raise ConversationError(str(exc)) from exc


def _cadre(ctx: Contexte) -> Cadre:
    return cadre_from_view(_view(ctx))


def _ouverte(ctx: Contexte, rev: int, *, vivante: bool = True) -> Conversation:
    """La conversation enregistrée, vérifiée : elle existe, ``rev`` est celui que l'appelant a vu, elle n'est pas classée."""
    conv = store.read(ctx.directory, ctx.slug)
    if conv is None:
        raise FileNotFoundError("aucune conversation pour ce chantier : démarre-en une")
    if conv.rev != rev:
        raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
    if statut(conv, ctx.now, ctx.reglages) == "classee":
        raise ConversationError("cette conversation est classée : rouvre-la d'abord")
    if vivante and conv.mode == "pause":
        raise ConversationConflictError("la conversation est en pause : reprends-la d'abord", "en-pause")
    if vivante and conv.mode == "termine":
        raise ConversationConflictError("la conversation est terminée : rouvre-la pour continuer", "terminee")
    return conv


# ── Dire : la question, la reformulation, l'invitation ─────────────────────


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _dire(ctx: Contexte, conv: Conversation, etape: Etape) -> tuple[str, str]:
    """Le message de la mascotte pour l'étape (et son geste) ; pose ``conv.question``."""
    moteur = ctx.moteur
    kind = etape.kind
    geste = "question"
    if kind == "confirmer":
        group = [e for e in conv.elements if e.id in etape.ids]
        text = moteur.reformuler(EntreeReformulation(
            tuple(e.texte for e in group), rubrique_key(etape.phase, etape.section), any(e.remplace for e in group)))
        conv.question = Question("confirmer", text, etape.pourquoi, etape.angle, 0, etape.phase, etape.section)
        return text, "reformulation"
    if kind == "question":
        recent = tuple(t.texte[:200] for t in conv.tours[-4:])
        text = moteur.formuler_question(EntreeQuestion(
            "amorce", etape.angle, etape.index, etape.seed, etape.pourquoi, etape.obligatoire, derniers_tours=recent))
        conv.question = Question("amorce", text, etape.pourquoi, etape.angle, etape.index, etape.phase, etape.section,
                                 etape.obligatoire)
        return f"{etape.preambule} {text}".strip(), "question"
    text = moteur.formuler_question(EntreeQuestion(kind, fin=etape.fin, pourquoi=etape.pourquoi))
    if kind == "brainstorm":
        pistes = moteur.proposer_pistes(EntreePistes(_besoin(conv), tuple(e.texte for e in conv.elements if e.section == "Pistes")))
        for piste in pistes:
            if len(text) + len(piste) + 3 < MAX_BOT_CHARS - 100:
                text += f" Une piste : {piste}."
        geste = "brainstorm"
    elif kind == "document":
        conv.document = "posee" if conv.document == "" else conv.document
        geste = "fin" if etape.fin else "question"
    elif kind in ("pause", "termine"):
        geste = "pause" if kind == "pause" else "fin"
    else:
        geste = "explication"
    conv.question = Question(kind, text, etape.pourquoi, etape.angle, 0, etape.phase, etape.section, etape.obligatoire)
    return text, geste


def _besoin(conv: Conversation) -> str:
    """Ce que la personne a dit vouloir : ses premières réponses confirmées, courtes (le contexte des pistes d'un modèle)."""
    return " ".join(e.texte for e in conv.ranges() if e.genre == "element")[:600]


def _conclure(ctx: Contexte, conv: Conversation, accuse: str, lourd: bool, modifie: bool) -> dict[str, Any]:
    """Choisit l'étape suivante, la dit, enregistre, et rend la réaction (accusé + étape + indicateur « tour lourd »)."""
    cadre = _cadre(ctx)
    etape = prochaine_etape(conv, cadre)
    message, geste = _dire(ctx, conv, etape)
    accuse = _cut(accuse, _ACCUSE_MAX)
    conv.say("mascotte", _cut(f"{accuse} {message}".strip(), MAX_BOT_CHARS), geste, ctx.date)
    conv.active_le = ctx.date
    conv.rev += 1
    conv.moteur = ctx.moteur.id
    store.save(ctx.directory, conv)
    return {
        "accuse": accuse, "message": message, "lourd": lourd, "rev": conv.rev, "document_modifie": modifie,
        "etape": {"kind": etape.kind, "pourquoi": etape.pourquoi, "angle": etape.angle, "manque": list(etape.manque)},
        "question": None if conv.question is None else _question_public(conv.question),
        "moteur": {"id": ctx.moteur.id, "degrade": ctx.moteur.degrade, "notes": list(ctx.moteur.notes)},
    }


def _question_public(q: Question) -> dict[str, Any]:
    return {"kind": q.kind, "texte": q.texte, "pourquoi": q.pourquoi, "angle": q.angle, "obligatoire": q.obligatoire,
            "phase": q.phase, "section": q.section}


# ── Ranger dans le document (confirmation) ─────────────────────────────────


def _confirmer(ctx: Contexte, conv: Conversation, items: list[Element]) -> bool:
    """Confirme ces éléments (ils entrent dans le document) ; si le premier en corrige un autre, celui-ci en sort."""
    old = conv.element(items[0].remplace) if items[0].remplace else None
    if old is not None and old.statut not in ("confirme", "corrige"):
        old = None
    rangement.ecrire(ctx.root, ctx.slug, conv, items, remplace=old)
    for item in items:
        item.statut = "confirme"
    if old is not None:
        old.statut = "ecarte"
    return any(i.ecrit for i in items) or old is not None


def _proposer(ctx: Contexte, conv: Conversation, tour: Tour, texte: str, cible: tuple[str, str], angle: str,
              label: str, remplace: str = "") -> list[Element]:
    """Les éléments que le moteur tire du texte, en liste fermée, rangés en ``propose`` (rien n'est écrit au document)."""
    entree = EntreeExtraction(texte, rubrique_key(*cible), mode=conv.mode)
    made: list[Element] = []
    for x in ctx.moteur.extraire_elements(entree):
        phase, section = _KEY[x.rubrique]
        made.append(conv.new_element("element", x.texte, phase, section, angle_pour(phase, section, angle), tour.n,
                                     question=label, remplace=remplace if not made else ""))
    return made


def _cible(conv: Conversation) -> tuple[tuple[str, str], str, str] | None:
    """Où ranger une réponse libre : la rubrique de la question en cours, ou « Pistes » en brainstorm ; sinon nulle part."""
    q = conv.question
    if q is not None and q.kind == "amorce" and q.phase:
        return (q.phase, q.section), q.angle, q.texte
    if conv.mode == "brainstorm":
        # L'entretien met en italique la question devant la réponse ; une piste ne répond à aucune amorce : on le dit.
        return ("brainstorm", "Pistes"), "alternatives", "Piste du brainstorm"
    return None


def _appliquer(ctx: Contexte, conv: Conversation, classe: str, tour: Tour, texte: str) -> tuple[str, bool]:
    """Applique la classe du tour ; rend l'accusé (texte du programme) et si le document a changé."""
    kind = conv.question.kind if conv.question else ""
    group = _groupe(conv)
    if classe == "pause":
        conv.mode_avant_pause, conv.mode = conv.mode, "pause"
        return "", False  # le message de pause du moteur suffit
    if classe == "brainstorm":
        conv.mode = "brainstorm"
        return "D'accord, on brainstorme.", False
    if classe == "reprise":
        conv.mode = "cadrage"
        return "Très bien, on reprend le cadrage.", False
    if classe == "hors_sujet":
        conv.note(conv.plus_tard, texte, tour.n)
        return "C'est noté dans « pour plus tard » : on y reviendra.", False
    if classe == "question":
        pourquoi = conv.question.pourquoi if conv.question else ""
        said = ctx.moteur.repondre_question(texte, pourquoi)
        if said != pourquoi:
            conv.note(conv.ouvertes, texte, tour.n)
        return said, False
    if classe in ("ne_sait_pas", "sans_objet") and kind == "amorce" and conv.question is not None:
        q = conv.question
        unknown = classe == "ne_sait_pas"
        phase, section = ("brainstorm", "Questions ouvertes") if unknown else _angle_rubrique(q.angle)
        # Un « je ne sais pas » ou un « sans objet » dit explicitement par l'humain vaut confirmation : rangé aussitôt.
        el = conv.new_element("inconnu" if unknown else "sansobjet", q.texte if unknown else "sans objet", phase, section,
                              q.angle, tour.n, statut="confirme", question=q.texte)
        rangement.ecrire(ctx.root, ctx.slug, conv, [el])
        if unknown:
            conv.note(conv.ouvertes, q.texte, tour.n)
            return "Pas de souci, je le note comme un point à clarifier.", el.ecrit
        return "D'accord, je le note comme sans objet.", el.ecrit
    if classe in ("ne_sait_pas", "sans_objet"):
        return "Pas de souci, on y reviendra.", False
    if kind == "document":
        if classe == "confirme":
            conv.mode = "termine"
            return "", False
        conv.document = "refusee"
        if classe != "contredit" or not _est_vide_apres_negation(ctx, texte):
            conv.note(conv.plus_tard, texte, tour.n)
        return "D'accord, on continue.", False
    if kind == "confirmer" and group:
        if classe == "confirme":
            return "C'est noté.", _confirmer(ctx, conv, group)
        new = _proposer(ctx, conv, tour, texte, (group[0].phase, group[0].section), group[0].angle, group[0].question,
                        remplace=group[0].remplace)
        if not new:
            return "Dis-moi ce qui ne va pas, avec tes mots.", False
        for e in group:
            e.statut = "ecarte"
        return "D'accord, je corrige.", False
    written = [e for e in conv.ranges() if e.genre == "element"]
    if classe == "contredit" and written:
        last = written[-1]
        new = _proposer(ctx, conv, tour, texte, (last.phase, last.section), last.angle, last.question, remplace=last.id)
        if new:
            return "D'accord, je corrige ce que j'avais compris.", False
    cible = _cible(conv)
    if cible is None:
        conv.note(conv.plus_tard, texte, tour.n)
        return "Je n'ai pas de rubrique où ranger ça sans question en cours : je le garde pour plus tard.", False
    new = _proposer(ctx, conv, tour, texte, cible[0], cible[1], cible[2])
    return ("Merci." if new else "Je n'ai rien retenu de précis : tu peux le redire autrement ?"), False


def _angle_rubrique(angle_id: str) -> tuple[str, str]:
    angle = next(a for a in interview.ANGLES if a.id == angle_id)
    return angle.phase, angle.section


def _groupe(conv: Conversation) -> list[Element]:
    pending = conv.en_attente()
    return [e for e in pending if e.source == pending[0].source] if pending else []


def _est_vide_apres_negation(ctx: Contexte, texte: str) -> bool:
    """Le refus ne porte-t-il que la négation (« non », « pas tout de suite ») ? Alors rien à noter pour plus tard."""
    return not ctx.moteur.extraire_elements(EntreeExtraction(texte, "brief/Le problème", mode="cadrage"))


# ── Opérations publiques ───────────────────────────────────────────────────


def demarrer(ctx: Contexte) -> dict[str, Any]:
    """Démarre la conversation du chantier, ou la reprend (pause levée). Sans effet si elle est déjà en cours."""
    from grimoire.core.fsio import locked

    with store.locked_store(ctx.directory):
        conv = store.read(ctx.directory, ctx.slug)
        if conv is None:
            with locked(cadrage_dir(ctx.root, ctx.name) / interview.STATE_FILE):
                try:
                    interview.interview_plan(ctx.root, ctx.name)
                except ValueError as exc:
                    raise ConversationError(str(exc)) from exc
            conv = Conversation(ctx.slug, ctx.date, ctx.date, moteur=ctx.moteur.id)
            accueil = ("Salut ! On va cadrer ta demande ensemble, une question à la fois. "
                       "Rien n'est écrit dans le document sans ton accord.")
            if ctx.moteur.id == "sans-modele":
                accueil += " Je fonctionne sans modèle d'IA : mes questions sont guidées, et je reprends tes mots tels quels."
            conv.say("mascotte", accueil, "accueil", ctx.date)
            out = _conclure(ctx, conv, "", False, False)
            out.update(cree=True, resume=resume(conv, _cadre(ctx)))
            return out
        if statut(conv, ctx.now, ctx.reglages) == "classee":
            raise ConversationError("cette conversation est classée : rouvre-la d'abord")
        cadre = _cadre(ctx)
        if conv.mode == "pause":
            conv.mode = conv.mode_avant_pause
            summary = resume(conv, cadre)
            out = _conclure(ctx, conv, summary.replace("\n", " "), False, False)
            out.update(cree=False, resume=summary)
            return out
        return {"cree": False, "resume": resume(conv, cadre), "rev": conv.rev, "document_modifie": False,
                "etape": {"kind": prochaine_etape(conv, cadre).kind}, "moteur": {"id": ctx.moteur.id, "degrade": False, "notes": []},
                "question": None if conv.question is None else _question_public(conv.question), "accuse": "", "message": "",
                "lourd": False}


def recevoir(ctx: Contexte, rev: int, texte: Any) -> dict[str, Any]:
    """Un tour de l'humain : classé, appliqué, puis la réaction (accusé + étape suivante + indicateur « tour lourd »)."""
    if not isinstance(texte, str) or not texte.strip():
        raise ConversationError("texte requis : le tour ne peut pas être vide")
    if len(texte) > MAX_TURN_CHARS:
        raise ConversationError(f"tour trop long ({MAX_TURN_CHARS} caractères au plus)")
    with store.locked_store(ctx.directory):
        conv = _ouverte(ctx, rev)
        avant = prochaine_etape(conv, _cadre(ctx))
        lourd = ctx.moteur.est_tour_lourd(texte, avant.kind)
        question = conv.question
        classe = ctx.moteur.classer_reponse(EntreeClassement(
            texte, question.kind if question else "", conv.mode, question.texte if question else ""))
        tour = conv.say("utilisateur", texte, _GESTE_UTILISATEUR[classe], ctx.date)
        accuse, modifie = _appliquer(ctx, conv, classe, tour, texte)
        return _conclure(ctx, conv, accuse, lourd, modifie)


def decider(ctx: Contexte, rev: int, element_id: str, decision: str, texte: Any = None) -> dict[str, Any]:
    """Confirmer, corriger ou écarter un élément compris. Le document ne change que par ``rangement``."""
    with store.locked_store(ctx.directory):
        conv = _ouverte(ctx, rev, vivante=False)
        el = conv.element(element_id)
        if el is None:
            raise FileNotFoundError(f"élément inconnu : {element_id!r}")
        if decision == "confirmer":
            if el.statut != "propose":
                raise ConversationConflictError("cet élément est déjà tranché", "deja-tranche")
            modifie = _confirmer(ctx, conv, [el])
            return _conclure(ctx, conv, "C'est noté dans le document." if modifie else "C'est noté.", False, modifie)
        if decision == "corriger":
            if el.statut == "ecarte":
                raise ConversationConflictError("cet élément est déjà écarté", "deja-tranche")
            if el.genre != "element":
                raise ConversationError("ce point n'a pas de texte à corriger : écarte-le, ou réponds à nouveau")
            new = clean_text(texte, MAX_ELEMENT_CHARS, what="texte corrigé")
            written = el.statut in ("confirme", "corrige")
            old = el.texte
            if written:
                rangement.corriger(ctx.root, ctx.slug, conv, el, new)
            else:
                el.texte = new
                _confirmer(ctx, conv, [el])
            el.avant = [*el.avant, old][-10:]
            el.statut = "corrige"
            return _conclure(ctx, conv, "C'est corrigé.", False, True)
        if decision == "ecarter":
            if el.statut == "ecarte":
                raise ConversationConflictError("cet élément est déjà écarté", "deja-tranche")
            modifie = el.ecrit
            if el.ecrit:
                rangement.retirer(ctx.root, ctx.slug, conv, el)
            el.statut = "ecarte"
            return _conclure(ctx, conv, "D'accord, je l'écarte.", False, modifie)
        raise ConversationError(f"décision inconnue : {decision!r} (confirmer, corriger, ecarter)")


def pause(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Met la conversation en pause : tout est gardé, la reprise redit où on en est."""
    with store.locked_store(ctx.directory):
        conv = _ouverte(ctx, rev)
        conv.mode_avant_pause, conv.mode = conv.mode, "pause"
        return _conclure(ctx, conv, "", False, False)


def terminer(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Termine la conversation (elle reste lisible). Les éléments encore en attente restent non rangés."""
    with store.locked_store(ctx.directory):
        conv = _ouverte(ctx, rev)
        conv.mode = "termine"
        return _conclure(ctx, conv, "", False, False)


def classer(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Classe la conversation (conservée, retrouvable, hors de la liste active)."""
    with store.locked_store(ctx.directory):
        conv = store.read(ctx.directory, ctx.slug)
        if conv is None:
            raise FileNotFoundError("aucune conversation pour ce chantier")
        if conv.rev != rev:
            raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
        classer_conv(conv, ctx.now)
        store.save(ctx.directory, conv)
        return {"rev": conv.rev, "statut": "classee"}


def rouvrir(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Rouvre une conversation classée (explicitement, ou par le délai)."""
    with store.locked_store(ctx.directory):
        conv = store.read(ctx.directory, ctx.slug)
        if conv is None:
            raise FileNotFoundError("aucune conversation pour ce chantier")
        if conv.rev != rev:
            raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
        if statut(conv, ctx.now, ctx.reglages) != "classee":
            raise ConversationError("cette conversation n'est pas classée")
        rouvrir_conv(conv, ctx.now)
        store.save(ctx.directory, conv)
        return {"rev": conv.rev, "statut": statut(conv, ctx.now, ctx.reglages)}


# ── Lire (rien n'est écrit) ────────────────────────────────────────────────


def _element_public(e: Element) -> dict[str, Any]:
    return {"id": e.id, "genre": e.genre, "texte": e.texte, "statut": e.statut, "source": e.source, "ecrit": e.ecrit,
            "phase": e.phase, "section": e.section, "phase_titre": _TITRES.get(e.phase, e.phase), "question": e.question,
            "remplace": e.remplace, "avant": list(e.avant)}


def vue(ctx: Contexte) -> dict[str, Any]:
    """L'état de la conversation pour la vue Conversation. ``{"existe": False}`` s'il n'y en a pas. Lecture seule."""
    from grimoire.core.cadrage_progress import progress

    try:
        conv = store.read(ctx.directory, ctx.slug)
        if conv is None:
            return {"existe": False, "moteur": _moteur_public(ctx)}
        cadre = _cadre(ctx)
    except ConversationError as exc:  # fichier hors cadre : dit, sans rien écrire ni casser la page
        return {"existe": True, "erreur": str(exc), "moteur": _moteur_public(ctx)}
    etape = prochaine_etape(conv, cadre)
    avancement = progress(ctx.root, ctx.name)
    return {
        "existe": True, "chantier": ctx.slug, "rev": conv.rev, "mode": conv.mode, "statut": statut(conv, ctx.now, ctx.reglages),
        "classee": bool(conv.classee_le), "document": conv.document, "active_le": conv.active_le, "cree": conv.cree,
        "tours": [{"n": t.n, "auteur": t.auteur, "texte": t.texte, "date": t.date, "geste": t.geste} for t in conv.tours[-VUE_TOURS:]],
        "tours_total": len(conv.tours),
        "question": None if conv.question is None else _question_public(conv.question),
        "elements": [_element_public(e) for e in conv.elements],
        "ouvertes": [{"texte": n.texte, "tour": n.tour} for n in conv.ouvertes],
        "plus_tard": [{"texte": n.texte, "tour": n.tour} for n in conv.plus_tard],
        "etape": {"kind": etape.kind, "pourquoi": etape.pourquoi, "angle": etape.angle},
        "manque": [{"id": a.id, "nom": NOMS.get(a.id, a.titre)} for a in ce_qui_manque(cadre)] if cadre.interview else [],
        "resume": resume(conv, cadre),
        "avancement": {k: avancement[k] for k in ("etat", "resume", "a_preciser", "a_confirmer", "angles")},
        "moteur": _moteur_public(ctx),
    }


def _moteur_public(ctx: Contexte) -> dict[str, Any]:
    libelle = getattr(ctx.moteur.moteur, "libelle", "")
    return {"id": ctx.moteur.id, "libelle": libelle if isinstance(libelle, str) else "", "sans_modele": ctx.moteur.id == "sans-modele",
            "limites": list(ctx.moteur.limites())}


def compris(ctx: Contexte) -> dict[str, Any]:
    """« Ce qui est compris », rangé par rubrique, pour la vue Document. Les éléments écartés sont comptés, pas listés."""
    try:
        conv = store.read(ctx.directory, ctx.slug)
    except ConversationError as exc:
        return {"existe": True, "erreur": str(exc), "rubriques": [], "ecartes": 0}
    if conv is None:
        return {"existe": False, "rubriques": [], "ecartes": 0}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in conv.elements:
        if e.statut != "ecarte":
            groups.setdefault((e.phase, e.section), []).append(_element_public(e))
    order = {pair: i for i, pair in enumerate(RUBRIQUES)}
    rows = [{"phase": p, "phase_titre": _TITRES.get(p, p), "section": s, "elements": items}
            for (p, s), items in sorted(groups.items(), key=lambda kv: order[kv[0]])]
    return {"existe": True, "rev": conv.rev, "rubriques": rows, "ecartes": sum(1 for e in conv.elements if e.statut == "ecarte"),
            "en_attente": len(conv.en_attente())}
