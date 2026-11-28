"""Les tours d'une conversation de cadrage : le programme enchaîne, le moteur ne fait que des gestes (épique #1477, lot A).

Chaque opération est UNE unité complète : relire l'état, vérifier qu'il n'a pas bougé (``rev`` : un autre onglet ou la CLI
a écrit entre-temps : 409), appliquer, enregistrer. Rien n'est enregistré si l'opération échoue.

**Le moteur propose, il ne décide jamais.** Rien n'entre dans le document, et aucun angle n'est marqué couvert ou écarté,
sans un geste EXPLICITE de l'humain reconnu par le PROGRAMME :

- le « oui » est reconnu par une règle lexicale stricte du programme (:func:`~grimoire.core.cadrage_conversation_engine.est_un_oui`),
  jamais par un moteur, et seulement quand une confirmation est en attente ;
- ce que l'humain confirme est la LISTE EXACTE des textes qui seront écrits, avec leur rubrique en mots simples, construite
  ici à partir des éléments réels (``a_confirmer``) ; la reformulation du moteur l'accompagne, ne la remplace pas ;
- un « je ne sais pas » ou un « sans objet » (reconnu seulement quand la réponse n'est QUE cela) produit une PROPOSITION
  qui n'est appliquée que par le « oui » de l'humain ;
- un élément d'une autre rubrique que celle de la question, ou dont le texte n'est pas dans ce que l'humain a dit
  (``origine`` = ``moteur``), se confirme SEUL, jamais par le « oui » du groupe.

**Le moteur travaille hors du verrou.** Un calcul à blanc, sur une copie jetable des fichiers du chantier, exécute
l'opération avec le vrai moteur et note ses sorties validées ; sous le verrou, l'opération est rejouée avec ces sorties,
sans rappeler le moteur. Un moteur lent ne retient donc jamais le fichier.

**Écrire dans le bon ordre.** Avant la première écriture du document, tout ce qui peut échouer est vérifié (document accepté,
place dans le fichier) ; l'intention d'écrire est enregistrée dans la conversation ; le document est écrit ; l'intention est
levée. Une panne entre les deux est reprise à l'opération suivante par la trace de l'entretien : terminée si la ligne y est,
annulée sinon.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from grimoire.core import cadrage_conversation_document as rangement
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import PHASES, cadrage_dir
from grimoire.core.cadrage_conversation import (
    ANGLE_IDS,
    MAX_BOT_CHARS,
    MAX_ELEMENT_CHARS,
    MAX_FILE_BYTES,
    MAX_QUESTION_CHARS,
    MAX_TURN_CHARS,
    MAX_TURNS,
    PLEINE_A,
    RUBRIQUES,
    Conversation,
    ConversationConflictError,
    ConversationError,
    Element,
    Question,
    Tour,
    angle_pour,
    clean_text,
    confirmable_en_groupe,
    mots_de_l_humain,
    rubrique_key,
)
from grimoire.core.cadrage_conversation_engine import (
    EntreeClassement,
    EntreeExtraction,
    EntreePistes,
    EntreeQuestion,
    EntreeReformulation,
    Garde,
    est_un_oui,
)
from grimoire.core.cadrage_conversation_flow import (
    NOMS,
    Cadre,
    Etape,
    cadre_from_view,
    ce_qui_manque,
    groupe_a_confirmer,
    prochaine_etape,
    resume,
)
from grimoire.core.cadrage_conversation_hygiene import Reglages, statut
from grimoire.core.cadrage_conversation_hygiene import classer as classer_conv
from grimoire.core.cadrage_conversation_hygiene import rouvrir as rouvrir_conv
from grimoire.core.cadrage_conversation_moteur_vue import vue_moteur
from grimoire.core.regular_file import open_regular

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
LABEL_PISTE = "Piste du brainstorm"
_FICHIERS_COPIES = (*(p.filename for p in PHASES), interview.STATE_FILE, "chantier.yaml", store.FILENAME)
_ACTIONS = {"element": "noter dans le document", "inconnu": "noter comme question ouverte (à préciser)",
            "sansobjet": "écarter cette question comme sans objet"}


@dataclass(slots=True)
class Contexte:
    """Ce qu'une opération reçoit : le projet, le chantier (dossier retrouvé dans la liste réelle), le moteur, l'horloge."""

    root: Path
    slug: str
    directory: Path
    moteur: Garde
    now: datetime
    reglages: Reglages
    #: Ce que l'opération a corrigé ou refusé de croire en route (ex. une ligne « écrite » que la trace ne confirme pas).
    alertes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str | None:
        return None if self.slug == "principal" else self.slug

    @property
    def date(self) -> str:
        return self.now.isoformat(timespec="seconds")


# ── Le moteur hors du verrou : calcul à blanc, puis rejeu ──────────────────


def _bac_a_sable(ctx: Contexte) -> tuple[Contexte, Path]:
    """Une copie jetable des seuls fichiers du chantier (ordinaires, bornés) : l'opération s'y joue à blanc."""
    tmp = Path(tempfile.mkdtemp(prefix="grimoire-conversation-"))
    dest = cadrage_dir(tmp, ctx.name)
    dest.mkdir(parents=True)
    for name in _FICHIERS_COPIES:
        with contextlib.suppress(OSError, ValueError):
            with open_regular(ctx.directory / name) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) <= MAX_FILE_BYTES:
                (dest / name).write_bytes(data)
    return replace(ctx, root=tmp, directory=dest, moteur=ctx.moteur.enregistreur(), alertes=[]), tmp


def _operer(ctx: Contexte, fonction: Callable[[Contexte], dict[str, Any]]) -> dict[str, Any]:
    """Joue ``fonction`` : à blanc avec le vrai moteur, hors du verrou ; puis sous le verrou, avec ce que le moteur a rendu."""
    blanc, tmp = _bac_a_sable(ctx)
    try:
        with contextlib.suppress(Exception):  # l'échec éventuel est rejoué, et levé, sous le verrou
            fonction(blanc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with store.locked_store(ctx.directory):
        reel = replace(ctx, moteur=ctx.moteur.rejouer(blanc.moteur), alertes=[])
        out = fonction(reel)
        out["alertes"] = list(reel.alertes)
        return out


# ── Lecture de l'avancement et de l'état ───────────────────────────────────


def _view(ctx: Contexte) -> dict[str, Any] | None:
    try:
        return interview.view(ctx.root, ctx.name)
    except ValueError as exc:  # entretien.json abîmé : dit, sans rien écrire
        raise ConversationError(str(exc)) from exc


def _cadre(ctx: Contexte) -> Cadre:
    return cadre_from_view(_view(ctx))


def _reprendre(ctx: Contexte, conv: Conversation) -> bool:
    """Termine ou annule ce qu'une panne a laissé à mi-chemin, puis ne croit que la trace de l'entretien.

    Rend vrai si la conversation a changé (l'appelant qui n'enregistre pas autrement doit alors l'enregistrer).
    """
    seen = {o for rows in rangement.traces(ctx.root, ctx.slug).values() for o in rows if o}
    changed = False
    for el in conv.elements:
        if el.intention == "ecrire":
            changed = True
            el.intention = ""
            if rangement.origin(conv, el) in seen:
                el.ecrit = True
            elif any(o.remplace == el.id and o is not el and rangement.origin(conv, o) in seen for o in conv.elements):
                el.statut, el.ecrit = "ecarte", False  # il était retiré pour être remplacé, et son remplaçant est écrit : c'est fait
            else:
                # Rien n'a été écrit pour lui, ou son ancienne ligne a été retirée avant la panne : l'intention est annulée et
                # l'humain peut reconfirmer. Jamais « confirmé » sans que la trace de l'entretien le porte.
                el.statut, el.ecrit = "propose", False
                ctx.alertes.append(f"« {el.texte[:60]} » n'avait pas été écrit (panne) : il attend de nouveau ta confirmation")
    lost = rangement.reconcilier(ctx.root, ctx.slug, conv)
    ctx.alertes.extend(lost)
    return changed or bool(lost)


def _ouverte(ctx: Contexte, rev: int, *, vivante: bool = True) -> Conversation:
    """La conversation enregistrée, vérifiée : elle existe, ``rev`` est celui que l'appelant a vu, elle n'est pas classée."""
    conv = store.read(ctx.directory, ctx.slug)
    if conv is None:
        raise FileNotFoundError("aucune conversation pour ce chantier : démarre-en une")
    if conv.reparer:
        raise ConversationError(conv.reparer)
    if conv.rev != rev:
        raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
    if statut(conv, ctx.now, ctx.reglages) == "classee":
        raise ConversationError("cette conversation est classée : rouvre-la d'abord")
    if vivante and conv.mode == "pause":
        raise ConversationConflictError("la conversation est en pause : reprends-la d'abord", "en-pause")
    if vivante and conv.mode == "termine":
        raise ConversationConflictError("la conversation est terminée : rouvre-la pour continuer", "terminee")
    _reprendre(ctx, conv)
    return conv


# ── Dire : la question, la reformulation exacte, l'invitation ──────────────


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _rubrique_simple(e: Element) -> str:
    if e.genre == "inconnu":
        return "Questions ouvertes (à préciser)"
    if e.genre == "sansobjet":
        return f"Écart : {e.section}"
    return f"{_TITRES.get(e.phase, e.phase).split(' — ')[0]}, partie « {e.section} »"


def _a_confirmer(conv: Conversation, ids: tuple[str, ...]) -> list[dict[str, Any]]:
    """La liste EXACTE de ce qui serait écrit : texte, rubrique en mots simples, geste, origine. Construite d'ici, pas du moteur."""
    out = []
    for e in conv.elements:
        if e.id in ids and e.statut == "propose":
            shown = e.question if e.genre != "element" else e.texte
            out.append({
                "id": e.id, "genre": e.genre, "texte": e.texte if e.genre == "element" else shown, "action": _ACTIONS[e.genre],
                "rubrique": {"phase": e.phase, "section": e.section, "nom": _rubrique_simple(e)}, "origine": e.origine,
                "individuel": e.individuel, "remplace": e.remplace,
                "note": e.texte if e.genre == "inconnu" and e.texte != rangement.FORMULE_INCONNU else "",
            })
    return out


def _a_confirmer_vue(conv: Conversation, cadre: Cadre, etape: Etape) -> list[dict[str, Any]]:
    """``a_confirmer`` recalculé à l'identique depuis l'état, TEXTE ENTIER : le tour enregistré (1500 caractères) et la question
    en cours (600) sont coupés, pas cette liste. En pause aussi : elle suspend les gestes, pas la relecture de ce qui attend."""
    if etape.kind == "confirmer":
        return _a_confirmer(conv, etape.ids)
    if etape.kind == "pause" and conv.en_attente() and cadre.interview and cadre.blocking:
        return _a_confirmer(conv, groupe_a_confirmer(conv))
    return []


def _liste_exacte(items: list[dict[str, Any]]) -> str:
    shown = [f"« {i['texte']} » ({i['action']} : {i['rubrique']['nom']}"
             + {"moteur": ", reformulé par le moteur", "document": ", texte relu dans le document"}.get(i["origine"], "") + ")"
             for i in items]
    return "; ".join(shown)  # tout le groupe, jamais « et N autres » : le groupe est borné à ce qui s'affiche


def _q(kind: str, text: str, pourquoi: str, etape: Etape) -> Question:
    return Question(kind, _cut(text, MAX_QUESTION_CHARS), _cut(pourquoi, MAX_QUESTION_CHARS), etape.angle, etape.index,
                    etape.phase, etape.section, etape.obligatoire)


def _dire(ctx: Contexte, conv: Conversation, etape: Etape) -> tuple[str, str]:
    """Le message de la mascotte pour l'étape (et son geste) ; pose ``conv.question``."""
    moteur = ctx.moteur
    kind = etape.kind
    geste = "question"
    if kind == "confirmer":
        group = [e for e in conv.elements if e.id in etape.ids]
        items = _a_confirmer(conv, etape.ids)
        special = group[0].genre != "element"
        if special:  # « je ne sais pas » / « sans objet » : la proposition est dite par le programme
            if group[0].genre == "inconnu":
                text = f"Tu veux qu'on note que tu ne sais pas encore répondre à : « {group[0].question} » ?"
            else:
                text = f"On met cette question de côté comme sans objet : « {group[0].question} » ?"
        else:
            text = _cut(moteur.reformuler(EntreeReformulation(
                tuple(e.texte for e in group), rubrique_key(etape.phase, etape.section), any(e.remplace for e in group))), 350)
            text += f" Je noterais exactement : {_liste_exacte(items)}. Tu confirmes ?"
        conv.question = _q("confirmer", text, etape.pourquoi, etape)
        return text, "reformulation"
    if kind == "question":
        recent = tuple(t.texte[:200] for t in conv.tours[-4:])
        text = moteur.formuler_question(EntreeQuestion(
            "amorce", etape.angle, etape.index, etape.seed, etape.pourquoi, etape.obligatoire, derniers_tours=recent))
        conv.question = _q("amorce", text, etape.pourquoi, etape)
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
    conv.question = _q(kind, text, etape.pourquoi, etape)
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
    if len(conv.tours) < MAX_TURNS:  # à la limite, les gestes de correction restent possibles : ils ne parlent plus
        conv.say("mascotte", _cut(f"{accuse} {message}".strip(), MAX_BOT_CHARS), geste, ctx.date)
    conv.active_le = ctx.date
    conv.rev += 1
    conv.moteur = ctx.moteur.id
    store.save(ctx.directory, conv)
    return {
        "accuse": accuse, "message": message, "lourd": lourd, "rev": conv.rev, "document_modifie": modifie,
        "etape": {"kind": etape.kind, "pourquoi": etape.pourquoi, "angle": etape.angle, "manque": list(etape.manque)},
        "question": None if conv.question is None else _question_public(conv.question),
        "a_confirmer": _a_confirmer_vue(conv, cadre, etape),
        "moteur": {**_vue_moteur(ctx), "degrade": ctx.moteur.degrade, "notes": list(ctx.moteur.notes)},
    }


def _question_public(q: Question) -> dict[str, Any]:
    return {"kind": q.kind, "texte": q.texte, "pourquoi": q.pourquoi, "angle": q.angle, "obligatoire": q.obligatoire,
            "phase": q.phase, "section": q.section}


# ── Ranger dans le document (confirmation explicite de l'humain) ───────────


def _journaliser(ctx: Contexte, conv: Conversation, retire: Element | None) -> list[Element]:
    """Inscrit au journal d'intention, et enregistre, ce qu'un retrait de ``retire`` va défaire puis réécrire (lui et ce qui le suit).

    Sans cela, une panne entre le retrait et la réécriture laisserait des éléments « confirmés » que la trace de l'entretien ne
    porte plus : des lignes de l'humain perdues en silence. Avec cela, la reprise les remet à confirmer. Rend ces éléments.
    """
    defaits = [] if retire is None else rangement.a_defaire(conv, retire)
    for el in defaits:
        el.intention = "ecrire"
    store.save(ctx.directory, conv)
    return defaits


def _confirmer(ctx: Contexte, conv: Conversation, items: list[Element]) -> bool:
    """Confirme ces éléments (ils entrent dans le document) ; si le premier en corrige un autre, celui-ci en sort.

    Tout ce qui peut échouer est vérifié AVANT la première écriture ; l'intention est enregistrée avant le document.
    """
    if not items:
        return False
    old = conv.element(items[0].remplace) if items[0].remplace else None
    # Un ancien élément redevenu « à confirmer » par une reprise (panne en plein remplacement) n'est pas dans le document :
    # son remplaçant confirmé, il est écarté sans nouvelle question.
    depose = old if old is not None and old.statut == "propose" and not old.ecrit else None
    if old is not None and old.statut not in ("confirme", "corrige"):
        old = None
    store.check_room(conv)
    rangement.verifier(ctx.root, ctx.slug, [*items, *([old] if old is not None else [])])
    for item in items:
        item.statut, item.intention = "confirme", "ecrire"
    defaits = _journaliser(ctx, conv, old)  # le journal d'intention : une panne en route est reprise par la trace de l'entretien
    ecrit = rangement.ecrire(ctx.root, ctx.slug, conv, items, remplace=old)  # vrai seulement si un fichier a changé
    for item in [*items, *defaits]:
        item.intention = ""
        if item.genre == "inconnu":
            conv.note(conv.ouvertes, item.question, item.source)
    if old is not None:
        old.statut = "ecarte"
    if depose is not None:
        depose.statut = "ecarte"
    return ecrit


def _proposer(conv: Conversation, ctx: Contexte, tour: Tour, texte: str, cible: tuple[str, str], angle: str, label: str,
              remplace: str = "") -> list[Element]:
    """Les éléments que le moteur tire du texte, en liste fermée, en ``propose`` : rien n'est écrit au document.

    Ceux qui visent une autre rubrique que la question, ou dont le texte n'est pas dans les mots de l'humain, se
    confirment seuls (``individuel``) ; aucun ne marque un angle couvert tant qu'il n'est pas confirmé.
    """
    made: list[Element] = []
    extraits = ctx.moteur.extraire_elements(EntreeExtraction(texte, rubrique_key(*cible), mode=conv.mode))
    # Une autre proposition du tour hors du groupe peut retirer ou restreindre ce qu'il accorde : confirmation individuelle.
    groupable = confirmable_en_groupe([(x.texte, x.tronque) for x in extraits], texte)
    for x in extraits:
        phase, section = _KEY[x.rubrique]
        origine = "humain" if mots_de_l_humain(x.texte, texte, tronque=x.tronque) else "moteur"
        seul = (phase, section) != cible or origine == "moteur" or not groupable
        made.append(conv.new_element(
            "element", x.texte, phase, section, angle_pour(phase, section, angle), tour.n, question=label,
            remplace=remplace if not made else "", origine=origine, individuel=seul))
    return made


def _proposer_special(conv: Conversation, tour: Tour, genre: str, angle: str, label: str, note: str = "") -> Element:
    """La PROPOSITION de noter « je ne sais pas » ou d'écarter comme « sans objet » : rien n'est écrit, aucun angle ne bouge."""
    if genre == "inconnu":
        phase, section, texte = "brainstorm", "Questions ouvertes", note or rangement.FORMULE_INCONNU
    else:
        meta = next(a for a in interview.ANGLES if a.id == angle)
        phase, section, texte = meta.phase, meta.section, "Sans objet"
    return conv.new_element(genre, texte, phase, section, angle, tour.n, question=label)


def _cible(conv: Conversation) -> tuple[tuple[str, str], str, str] | None:
    """Où ranger une réponse libre : la rubrique de la question en cours, ou « Pistes » en brainstorm ; sinon nulle part."""
    q = conv.question
    if q is not None and q.kind == "amorce" and q.phase:
        return (q.phase, q.section), q.angle, q.texte
    if conv.mode == "brainstorm":
        # L'entretien met en italique la question devant la réponse ; une piste ne répond à aucune amorce : on le dit.
        return ("brainstorm", "Pistes"), "alternatives", LABEL_PISTE
    return None


def _appliquer(ctx: Contexte, conv: Conversation, classe: str, tour: Tour, texte: str) -> tuple[str, bool]:
    """Applique la classe du tour ; rend l'accusé (texte du programme) et si le document a changé."""
    kind = conv.question.kind if conv.question else ""
    ids = groupe_a_confirmer(conv) if conv.en_attente() else ()
    group = [e for e in conv.elements if e.id in ids]
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
    if classe == "confirme":  # reconnu par le programme seul, en réponse à CE qui est affiché
        if kind == "document":
            conv.mode = "termine"
            return "", False
        if not group:  # une question « confirmer » sans rien en attente (fichier forgé ou édité) : rien à écrire
            return "Je n'ai rien en attente de confirmation : je reprends où on en est.", False
        return "C'est noté.", _confirmer(ctx, conv, group)
    if classe in ("ne_sait_pas", "sans_objet"):
        genre = "inconnu" if classe == "ne_sait_pas" else "sansobjet"
        if kind == "amorce" and conv.question is not None and conv.question.angle in ANGLE_IDS:
            q = conv.question
            _proposer_special(conv, tour, genre, q.angle, q.texte)
            return "D'accord : je te le propose, et je ne note rien sans ton accord.", False
        if kind == "confirmer" and group:  # ce que l'humain avait dit n'est pas jeté : il devient la note de la question ouverte
            kept = _cut(" ; ".join(e.texte for e in group if e.genre == "element"), MAX_ELEMENT_CHARS)
            _proposer_special(conv, tour, genre, group[0].angle, group[0].question, kept if genre == "inconnu" else "")
            for e in group:
                e.statut = "ecarte"
            return "D'accord : je te le propose, et je ne note rien sans ton accord.", False
        return "Pas de souci, on y reviendra.", False
    if kind == "document":
        conv.document = "refusee"
        if classe != "contredit" or not _est_vide_apres_negation(ctx, texte):
            conv.note(conv.plus_tard, texte, tour.n)
        return "D'accord, on continue.", False
    if kind == "confirmer" and group:
        new = _proposer(conv, ctx, tour, texte, (group[0].phase, group[0].section), group[0].angle, group[0].question,
                        remplace=group[0].remplace)
        if not new:
            return "Dis-moi ce qui ne va pas, avec tes mots.", False
        for e in group:
            e.statut = "ecarte"
        return "D'accord, je corrige.", False
    written = [e for e in conv.ranges() if e.genre == "element"]
    if classe == "contredit" and written:
        last = written[-1]
        new = _proposer(conv, ctx, tour, texte, (last.phase, last.section), last.angle, last.question, remplace=last.id)
        if new:
            return "D'accord, je corrige ce que j'avais compris.", False
    cible = _cible(conv)
    if cible is None:
        conv.note(conv.plus_tard, texte, tour.n)
        return "Je n'ai pas de rubrique où ranger ça sans question en cours : je le garde pour plus tard.", False
    new = _proposer(conv, ctx, tour, texte, cible[0], cible[1], cible[2])
    return ("Merci." if new else "Je n'ai rien retenu de précis : tu peux le redire autrement ?"), False


def _est_vide_apres_negation(ctx: Contexte, texte: str) -> bool:
    """Le refus ne porte-t-il que la négation (« non », « pas tout de suite ») ? Alors rien à noter pour plus tard."""
    return not ctx.moteur.extraire_elements(EntreeExtraction(texte, "brief/Le problème", mode="cadrage"))


# ── Opérations publiques ───────────────────────────────────────────────────


def demarrer(ctx: Contexte) -> dict[str, Any]:
    """Démarre la conversation du chantier, ou la reprend (pause levée). Sans effet si elle est déjà en cours."""
    return _operer(ctx, _demarrer)


def _demarrer(ctx: Contexte) -> dict[str, Any]:
    from grimoire.core.fsio import locked

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
    if conv.reparer:
        raise ConversationError(conv.reparer)
    if statut(conv, ctx.now, ctx.reglages) == "classee":
        raise ConversationError("cette conversation est classée : rouvre-la d'abord")
    changed = _reprendre(ctx, conv)
    cadre = _cadre(ctx)
    if conv.mode == "pause":
        conv.mode = conv.mode_avant_pause
        summary = resume(conv, cadre)
        out = _conclure(ctx, conv, summary.replace("\n", " "), False, False)
        out.update(cree=False, resume=summary)
        return out
    if changed:  # une reprise après panne : ce qu'elle a réglé est enregistré, et la question est recalculée (un « oui » doit viser le bon texte)
        out = _conclure(ctx, conv, "", False, False)
        out.update(cree=False, resume=resume(conv, _cadre(ctx)))
        return out
    etape = prochaine_etape(conv, cadre)
    return {"cree": False, "resume": resume(conv, cadre), "rev": conv.rev, "document_modifie": False,
            "etape": {"kind": etape.kind}, "moteur": {**_vue_moteur(ctx), "degrade": False, "notes": []},
            "question": None if conv.question is None else _question_public(conv.question), "accuse": "", "message": "",
            "lourd": False, "a_confirmer": _a_confirmer_vue(conv, cadre, etape)}  # la même liste que la vue


def recevoir(ctx: Contexte, rev: int, texte: Any) -> dict[str, Any]:
    """Un tour de l'humain : classé, appliqué, puis la réaction (accusé + étape suivante + indicateur « tour lourd »)."""
    if not isinstance(texte, str) or not texte.strip():
        raise ConversationError("texte requis : le tour ne peut pas être vide")
    if len(texte) > MAX_TURN_CHARS:
        raise ConversationError(f"tour trop long ({MAX_TURN_CHARS} caractères au plus)")
    return _operer(ctx, lambda c: _recevoir(c, rev, texte))


def _recevoir(ctx: Contexte, rev: int, texte: str) -> dict[str, Any]:
    conv = _ouverte(ctx, rev)
    if len(conv.tours) >= PLEINE_A:  # la limite de tours : la conversation se termine proprement, en le disant
        conv.mode = "termine"
        return _conclure(ctx, conv, "Cette conversation est pleine : elle se termine ici. Tu peux la classer et en ouvrir une "
                         "nouvelle ; corriger ce qui est compris reste possible.", False, False)
    avant = prochaine_etape(conv, _cadre(ctx))
    lourd = ctx.moteur.est_tour_lourd(texte, avant.kind)
    question = conv.question
    kind = question.kind if question else ""
    if kind in ("confirmer", "document") and est_un_oui(texte):
        classe = "confirme"  # le programme seul reconnaît un oui : un moteur n'en décide jamais
    else:
        classe = ctx.moteur.classer_reponse(EntreeClassement(texte, kind, conv.mode, question.texte if question else ""))
    tour = conv.say("utilisateur", texte, _GESTE_UTILISATEUR[classe], ctx.date)
    accuse, modifie = _appliquer(ctx, conv, classe, tour, texte)
    return _conclure(ctx, conv, accuse, lourd, modifie)


def decider(ctx: Contexte, rev: int, element_id: str, decision: str, texte: Any = None) -> dict[str, Any]:
    """Confirmer, corriger ou écarter un élément compris. Le document ne change que par ``rangement``."""
    return _operer(ctx, lambda c: _decider(c, rev, element_id, decision, texte))


def _decider(ctx: Contexte, rev: int, element_id: str, decision: str, texte: Any) -> dict[str, Any]:
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
        old = el.texte
        if el.statut in ("confirme", "corrige") or el.ecrit:  # « ecrit » : sa ligne est dans le document, même à reconfirmer (reprise)
            store.check_room(conv)
            defaits = _journaliser(ctx, conv, el)
            rangement.corriger(ctx.root, ctx.slug, conv, el, new)
            for d in defaits:
                d.intention = ""
            modifie = True
            el.origine = "humain"  # c'est la frappe de l'humain, même si le texte relu venait du document
        else:
            el.texte, el.origine, el.individuel = new, "humain", False  # c'est la frappe de l'humain : ses mots exacts
            modifie = _confirmer(ctx, conv, [el])
        el.avant = [*el.avant, old][-10:]
        el.statut = "corrige"
        return _conclure(ctx, conv, "C'est corrigé.", False, modifie)
    if decision == "ecarter":
        if el.statut == "ecarte":
            raise ConversationConflictError("cet élément est déjà écarté", "deja-tranche")
        modifie = el.ecrit
        if el.ecrit:
            store.check_room(conv)
            defaits = _journaliser(ctx, conv, el)
            rangement.retirer(ctx.root, ctx.slug, conv, el)
            for d in defaits:
                d.intention = ""
        el.statut = "ecarte"
        return _conclure(ctx, conv, "D'accord, je l'écarte.", False, modifie)
    raise ConversationError(f"décision inconnue : {decision!r} (confirmer, corriger, ecarter)")


def pause(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Met la conversation en pause : tout est gardé, la reprise redit où on en est."""
    return _operer(ctx, lambda c: _changer_mode(c, rev, "pause"))


def terminer(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Termine la conversation (elle reste lisible). Les éléments encore en attente restent non rangés."""
    return _operer(ctx, lambda c: _changer_mode(c, rev, "termine"))


def _changer_mode(ctx: Contexte, rev: int, mode: str) -> dict[str, Any]:
    conv = _ouverte(ctx, rev)
    if mode == "pause":
        conv.mode_avant_pause = conv.mode
    conv.mode = mode
    return _conclure(ctx, conv, "", False, False)


def _stockee(ctx: Contexte, rev: int) -> Conversation:
    conv = store.read(ctx.directory, ctx.slug)
    if conv is None:
        raise FileNotFoundError("aucune conversation pour ce chantier")
    if conv.reparer:
        raise ConversationError(conv.reparer)
    if conv.rev != rev:
        raise ConversationConflictError("la conversation a changé depuis l'affichage : recharge-la", "conversation-changee")
    return conv


def classer(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Classe la conversation (conservée, retrouvable, hors de la liste active)."""
    with store.locked_store(ctx.directory):
        conv = _stockee(ctx, rev)
        classer_conv(conv, ctx.now)
        store.save(ctx.directory, conv)
        return {"rev": conv.rev, "statut": "classee"}


def rouvrir(ctx: Contexte, rev: int) -> dict[str, Any]:
    """Rouvre une conversation classée (explicitement, ou par le délai)."""
    with store.locked_store(ctx.directory):
        conv = _stockee(ctx, rev)
        if statut(conv, ctx.now, ctx.reglages) != "classee":
            raise ConversationError("cette conversation n'est pas classée")
        rouvrir_conv(conv, ctx.now)
        store.save(ctx.directory, conv)
        return {"rev": conv.rev, "statut": statut(conv, ctx.now, ctx.reglages)}


# ── Lire (rien n'est écrit) ────────────────────────────────────────────────


def _element_public(e: Element) -> dict[str, Any]:
    return {"id": e.id, "genre": e.genre, "texte": e.texte, "statut": e.statut, "source": e.source, "ecrit": e.ecrit,
            "phase": e.phase, "section": e.section, "phase_titre": _TITRES.get(e.phase, e.phase), "question": e.question,
            "remplace": e.remplace, "avant": list(e.avant), "origine": e.origine, "individuel": e.individuel,
            "rubrique_nom": _rubrique_simple(e), "action": _ACTIONS[e.genre]}


def _lue(ctx: Contexte) -> tuple[Conversation | None, list[str]]:
    """La conversation lue, avec ce que la trace de l'entretien dit de ce qui est vraiment écrit (en mémoire seulement)."""
    conv = store.read(ctx.directory, ctx.slug)
    alertes: list[str] = []
    if conv is not None and not conv.reparer:
        with contextlib.suppress(ConversationError):
            alertes = rangement.reconcilier(ctx.root, ctx.slug, conv)
    return conv, alertes


def vue(ctx: Contexte) -> dict[str, Any]:
    """L'état de la conversation pour la vue Conversation. ``{"existe": False}`` s'il n'y en a pas. Lecture seule."""
    from grimoire.core.cadrage_progress import progress

    try:
        conv, alertes = _lue(ctx)
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
        "reparer": conv.reparer, "alertes": alertes,
        "tours": [{"n": t.n, "auteur": t.auteur, "texte": t.texte, "date": t.date, "geste": t.geste} for t in conv.tours[-VUE_TOURS:]],
        "tours_total": len(conv.tours),
        "question": None if conv.question is None else _question_public(conv.question),
        "a_confirmer": _a_confirmer_vue(conv, cadre, etape),
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
    """Qui parle et ce qu'il ne sait pas faire ; ``limites`` est borné à une fraction de seconde (la lecture ne bloque jamais)."""
    return {**_vue_moteur(ctx), "sans_modele": ctx.moteur.id == "sans-modele", "limites": list(ctx.moteur.limites())}


def _vue_moteur(ctx: Contexte) -> dict[str, Any]:
    """Qui parle, si du texte sort de la machine, pourquoi, et si la mascotte réfléchit : dit par le programme, jamais par le moteur."""
    libelle = getattr(ctx.moteur.moteur, "libelle", "")
    return vue_moteur(ctx.moteur.presentation, ctx.moteur.id, libelle if isinstance(libelle, str) else "")


def compris(ctx: Contexte) -> dict[str, Any]:
    """« Ce qui est compris », rangé par rubrique, pour la vue Document. Les éléments écartés sont comptés, pas listés."""
    try:
        conv, alertes = _lue(ctx)
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
            "en_attente": len(conv.en_attente()), "alertes": alertes}
