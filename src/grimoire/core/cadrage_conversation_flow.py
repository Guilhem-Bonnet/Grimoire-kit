"""La machine à états de la conversation de cadrage : le programme tient le cadre (épique #1477, lot A).

Fonction PURE de l'état de la conversation et de l'avancement réel de l'entretien (:class:`Cadre`, tiré de
:func:`grimoire.core.cadrage_interview.view`) : :func:`prochaine_etape` décide quoi faire ensuite, avec des règles
simples et explicables, dans CET ordre :

1. ``termine`` / ``pause`` : le mode l'emporte ;
2. ``rien`` (la demande n'appelle pas d'entretien) ou ``reporte`` (action immédiate : l'entretien vient au post-mortem) ;
3. ``confirmer`` : tant qu'un élément compris attend un oui, on reformule avant toute autre question ;
4. ``brainstorm`` : en mode brainstorm on invite à lister des pistes ;
5. ``question`` essentielle : un angle OBLIGATOIRE à cette profondeur n'est pas couvert (ordre des angles du kit) ;
6. ``document`` : l'essentiel (ce qui compte pour le gate de l'entretien) est couvert, on propose de passer au document ;
7. après un refus : ``question`` pour affiner (la seconde question des angles obligatoires), puis facultative
   (angles non obligatoires, une question chacun), puis ``document`` encore (fin possible).

Elle ne pose jamais une question d'un angle non obligatoire avant que l'essentiel soit là, et jamais deux fois la
même (l'index vient du nombre de réponses déjà consignées). Chaque étape porte POURQUOI elle est posée.
Aucune E/S, aucun modèle : les tests la jouent sur des :class:`Cadre` construits à la main.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.core.cadrage_conversation import Conversation

__all__ = [
    "INTENTIONS",
    "NOMS",
    "AngleCadre",
    "Cadre",
    "Etape",
    "cadre_from_view",
    "ce_qui_manque",
    "groupe_a_confirmer",
    "prochaine_etape",
    "resume",
]

#: Chaque angle, en mots simples (sans jargon : « pré-mortem », « MoSCoW » n'apparaissent jamais à l'écran).
NOMS: dict[str, str] = {
    "utilisateurs": "les personnes qui s'en serviront",
    "existant": "ce qui existe déjà",
    "criteres": "ce qui dira que c'est réussi",
    "hors-perimetre": "ce que tu ne veux pas traiter",
    "contraintes": "les limites à respecter",
    "risques": "ce qui pourrait mal tourner",
    "alternatives": "les versions plus simples",
}
#: Pourquoi on pose les questions d'un angle (la fin de « Je te demande ça … »).
INTENTIONS: dict[str, str] = {
    "utilisateurs": "pour savoir à qui ça sert vraiment : sans ça, on risque de construire à côté.",
    "existant": "pour ne pas refaire ce qui existe déjà.",
    "criteres": "pour qu'on puisse vérifier, à la fin, que c'est réussi.",
    "hors-perimetre": "pour fixer des bords : ce qu'on ne fera pas évite que le chantier grossisse sans fin.",
    "contraintes": "pour connaître les limites avant de choisir comment faire.",
    "risques": "pour repérer tôt ce qui pourrait échouer.",
    "alternatives": "pour vérifier qu'une version plus simple ne suffirait pas.",
}


#: Les genres de question dont le texte stocké est une vraie question en attente de réponse (pas une pause ni une fin).
_POSEES = ("amorce", "confirmer", "brainstorm", "document")


@dataclass(frozen=True, slots=True)
class AngleCadre:
    """Un angle de l'entretien, tel que l'avancement réel le montre."""

    id: str
    titre: str
    obligatoire: bool
    etat: str
    questions: tuple[str, ...]
    reponses: int
    raison: str = ""
    inconnu: bool = False

    @property
    def couvert(self) -> bool:
        """Compte pour le gate de l'entretien : couvert, ou écarté avec une raison."""
        return self.etat == "couvert" or (self.etat == "ecarte" and bool(self.raison.strip()))

    @property
    def nombre(self) -> int:
        """Combien de questions on pose sur cet angle : toutes si obligatoire, une seule sinon."""
        return len(self.questions) if self.obligatoire else min(1, len(self.questions))

    @property
    def ferme(self) -> bool:
        """Plus rien à demander : écarté, « je ne sais pas » consigné, ou toutes les questions répondues."""
        return self.couvert and (self.etat == "ecarte" or self.inconnu or self.reponses >= self.nombre)


@dataclass(frozen=True, slots=True)
class Cadre:
    """Ce que le programme sait de l'avancement réel : les angles, la profondeur, la nature de la demande."""

    angles: tuple[AngleCadre, ...]
    interview: bool = True
    blocking: bool = True
    profondeur: str = "standard"


def cadre_from_view(view: dict[str, Any] | None) -> Cadre:
    """Le cadre d'une vue d'entretien (:func:`grimoire.core.cadrage_interview.view`) ; ``None`` : aucun entretien entamé."""
    from grimoire.core.cadrage_interview import UNKNOWN_PREFIX

    if view is None:
        return Cadre((), interview=False)
    angles = []
    for rec in view.get("angles", []):
        answers = [str(a) for a in rec.get("answers", [])]
        angles.append(AngleCadre(
            str(rec["id"]), str(rec.get("title", rec["id"])), bool(rec.get("required")), str(rec.get("state", "ouvert")),
            tuple(str(q) for q in rec.get("questions", [])), len(answers), str(rec.get("reason", "")),
            any(a.startswith(UNKNOWN_PREFIX) for a in answers),
        ))
    return Cadre(tuple(angles), bool(view.get("interview", True)), bool(view.get("blocking", True)),
                 str(view.get("depth", "standard")))


@dataclass(frozen=True, slots=True)
class Etape:
    """Ce que la machine décide : le genre d'étape, la question visée, POURQUOI, et ce qu'il manque."""

    kind: str
    pourquoi: str = ""
    angle: str = ""
    index: int = 0
    phase: str = ""
    section: str = ""
    obligatoire: bool = True
    seed: str = ""
    preambule: str = ""
    manque: tuple[str, ...] = ()
    ids: tuple[str, ...] = field(default_factory=tuple)
    fin: bool = False


def ce_qui_manque(cadre: Cadre) -> list[AngleCadre]:
    """Les angles obligatoires pas encore couverts (ce qui compte pour le gate), dans l'ordre des angles du kit."""
    return [a for a in cadre.angles if a.obligatoire and not a.couvert]


def _affinage(cadre: Cadre) -> list[AngleCadre]:
    return [a for a in cadre.angles if a.obligatoire and a.couvert and not a.ferme]


def _facultatifs(cadre: Cadre) -> list[AngleCadre]:
    return [a for a in cadre.angles if not a.obligatoire and not a.couvert and a.questions]


def _simple(angle: AngleCadre) -> str:
    return NOMS.get(angle.id, angle.titre)


def _liste(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} et {items[-1]}"


def _pourquoi(angle: AngleCadre, *, essentiel: bool, affiner: bool = False) -> str:
    base = f"Je te demande ça {INTENTIONS.get(angle.id, 'pour bien cadrer ton besoin.')}"
    if affiner:
        return f"{base} L'essentiel est déjà là : c'est pour affiner, tu peux passer."
    return base + (" C'est indispensable pour ce cadrage." if essentiel else " C'est facultatif : tu peux passer.")


def _question(angle: AngleCadre, index: int, *, essentiel: bool, manque: tuple[str, ...], done: int, affiner: bool = False) -> Etape:
    from grimoire.core.cadrage_interview import ANGLES, question_target

    meta = next(a for a in ANGLES if a.id == angle.id)
    phase, section = question_target(meta, index)
    preambule = ""
    if essentiel and done and len(manque) == 1:
        preambule = f"Il ne me manque plus que ça : {manque[0]}."
    elif essentiel and done and 2 <= len(manque) <= 3:
        preambule = f"Il me manque encore : {_liste(list(manque))}."
    return Etape("question", _pourquoi(angle, essentiel=essentiel, affiner=affiner), angle.id, index, phase, section,
                 essentiel, angle.questions[index] if index < len(angle.questions) else meta.seeds[0],
                 preambule, manque)


def groupe_a_confirmer(conv: Conversation) -> tuple[str, ...]:
    """Les éléments qu'un seul « oui » confirme : ceux du même tour qui sont de l'humain et de la rubrique de la question.

    Sans groupe, le premier élément à confirmer SEUL (autre rubrique, ou texte du moteur) : le « oui » ne porte alors que
    sur lui, dont le texte exact est montré.
    """
    pending = conv.en_attente()
    together = [e for e in pending if not e.individuel]
    if together:
        return tuple(e.id for e in together if e.source == together[0].source)
    return (pending[0].id,)


def prochaine_etape(conv: Conversation, cadre: Cadre) -> Etape:
    """L'étape suivante, décidée par le seul état de la conversation et l'avancement réel (voir le module)."""
    if conv.mode == "termine":
        return Etape("termine", "La conversation est terminée : elle reste lisible, et le document est à toi.")
    if conv.mode == "pause":
        return Etape("pause", "Tu as demandé une pause : on reprend quand tu veux, exactement là où on s'est arrêtés.")
    if not cadre.interview:
        return Etape("rien", "Ta demande n'appelle pas de cadrage : une question simple se règle directement.")
    if not cadre.blocking:
        return Etape("reporte", "C'est une action à rétablir d'abord : le cadrage se fera après, au retour d'expérience.")
    pending = conv.en_attente()
    if pending:
        ids = groupe_a_confirmer(conv)
        first = conv.element(ids[0])
        assert first is not None  # ids vient des éléments de la conversation
        return Etape("confirmer", "Je te redis ce que j'ai compris avant de le ranger dans le document : rien n'y entre sans ton accord.",
                     first.angle, 0, first.phase, first.section, ids=ids)
    if conv.mode == "brainstorm":
        return Etape("brainstorm", "Tu as demandé à brainstormer : on cherche des pistes, sans les juger.",
                     "alternatives", 0, "brainstorm", "Pistes", obligatoire=False)
    missing = ce_qui_manque(cadre)
    names = tuple(_simple(a) for a in missing)
    done = sum(1 for a in cadre.angles if a.obligatoire and a.couvert)
    if missing:
        angle = missing[0]
        return _question(angle, min(angle.reponses, max(0, len(angle.questions) - 1)), essentiel=True, manque=names, done=done)
    if conv.document != "refusee":
        return Etape("document", "L'essentiel est couvert : on peut passer au document, ou continuer à affiner.", manque=())
    for angle in _affinage(cadre):
        return _question(angle, angle.reponses, essentiel=False, manque=(), done=done, affiner=True)
    for angle in _facultatifs(cadre):
        return _question(angle, 0, essentiel=False, manque=(), done=done)
    return Etape("document", "Je n'ai plus de question : tu peux passer au document, ou me dire ce qui manque.", fin=True)


def resume(conv: Conversation, cadre: Cadre) -> str:
    """Où on en est, en DEUX lignes calculées par le programme : l'avancement, puis ce qui vient ensuite."""
    rangees = len(conv.ranges())
    missing = ce_qui_manque(cadre) if cadre.interview and cadre.blocking else []
    head = f"On a noté {rangees} point{'s' if rangees > 1 else ''} ; "
    head += f"il me manque encore {_liste([_simple(a) for a in missing])}." if missing else "j'ai l'essentiel."
    etape = prochaine_etape(conv, cadre)
    if conv.mode == "pause":
        etape = prochaine_etape(_without_pause(conv), cadre)
    if etape.kind == "confirmer":
        wanted = [e.texte for e in conv.en_attente() if e.id in etape.ids]
        tail = f"J'attends ton accord sur : « {' ; '.join(wanted)} »."
    elif conv.question is not None and conv.question.kind in _POSEES and conv.mode != "pause":
        tail = f"Ma prochaine question : « {conv.question.texte} »"
    elif etape.kind == "question":
        tail = f"Ma prochaine question portera sur {NOMS.get(etape.angle, etape.angle)}."
    elif etape.kind == "document":
        tail = "On peut passer au document quand tu veux."
    elif etape.kind == "brainstorm":
        tail = "On brainstorme : donne-moi tes idées, une par une."
    else:
        tail = "Rien de plus à demander pour l'instant."
    return f"{head}\n{tail}"


def _without_pause(conv: Conversation) -> Conversation:
    """Une copie légère de la conversation sans la pause, pour dire ce qui vient APRÈS elle."""
    copy = Conversation(conv.chantier, conv.cree, conv.active_le, mode=conv.mode_avant_pause, document=conv.document)
    copy.elements = conv.elements
    return copy
