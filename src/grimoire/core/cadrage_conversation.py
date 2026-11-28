"""Conversation de cadrage — l'état que tient le programme (épique #1477, lot A).

Une conversation appartient à UN chantier. Elle est un objet explicite et sérialisable : les tours, « ce qui est
compris » (éléments rangés par rubrique, chacun avec sa source et son statut), les questions ouvertes, la liste
« pour plus tard », le mode, la question en cours. Aucun modèle, aucune E/S ici : ce module est pur. Le magasin de
fichier est dans :mod:`grimoire.core.cadrage_conversation_store`, la machine à états dans
:mod:`grimoire.core.cadrage_conversation_flow`, les gestes bornés dans
:mod:`grimoire.core.cadrage_conversation_engine`.

Le fichier se relit comme ``entretien.json`` : tout est validé (types, longueurs, rubrique de la liste fermée,
source qui existe). Un fichier édité à la main ou hostile lève :class:`ConversationError` ; il ne plante rien et
ne fait écrire nulle part.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "ANGLE_IDS",
    "GENRES",
    "MAX_ELEMENTS",
    "MAX_ELEMENT_CHARS",
    "MAX_TURNS",
    "MAX_TURN_CHARS",
    "MODES",
    "ORIGINES",
    "PLEINE_A",
    "RUBRIQUES",
    "SCHEMA",
    "STATUTS",
    "Conversation",
    "ConversationConflictError",
    "ConversationError",
    "Element",
    "Note",
    "Question",
    "Tour",
    "angle_pour",
    "clean_text",
    "confirmable_en_groupe",
    "mots_de_l_humain",
    "parse_date",
    "rubrique_key",
    "rubrique_ok",
]

SCHEMA = 1
MODES: tuple[str, ...] = ("cadrage", "brainstorm", "pause", "termine")
#: ``propose`` (compris, pas encore rangé), ``confirme`` (l'humain a dit oui), ``corrige`` (l'humain a réécrit), ``ecarte``.
STATUTS: tuple[str, ...] = ("propose", "confirme", "corrige", "ecarte")
#: ``element`` (une information), ``inconnu`` (« je ne sais pas » : question ouverte), ``sansobjet`` (angle écarté).
GENRES: tuple[str, ...] = ("element", "inconnu", "sansobjet")
AUTEURS: tuple[str, ...] = ("utilisateur", "mascotte")
KINDS_QUESTION: tuple[str, ...] = (
    "amorce", "confirmer", "brainstorm", "document", "pause", "termine", "rien", "reporte",
)
DOCUMENT_STATES: tuple[str, ...] = ("", "posee", "refusee")
#: Les gestes qu'un tour peut porter (ce que la mascotte ou l'humain vient de faire).
GESTES_TOUR: tuple[str, ...] = (
    "accueil", "question", "reponse", "reformulation", "confirmation", "correction", "hors-sujet", "ne-sait-pas",
    "sans-objet", "contradiction", "brainstorm", "pause", "reprise", "fin", "explication", "note",
)
MAX_TURNS = 500
#: Au-delà, la conversation se termine d'elle-même en le disant (il reste de la place pour ses dernières paroles).
PLEINE_A = MAX_TURNS - 3
#: ``document`` : le texte a été relu dans le fichier de phase par le programme (reprise après panne), ni dit ni reformulé.
ORIGINES: tuple[str, ...] = ("humain", "moteur", "document")
MAX_TURN_CHARS = 4000
MAX_BOT_CHARS = 1500
MAX_ELEMENTS = 200
MAX_ELEMENT_CHARS = 800
MAX_NOTES = 50
MAX_NOTE_CHARS = 400
MAX_QUESTION_CHARS = 600
MAX_FILE_BYTES = 2_097_152

#: Les rubriques qu'une conversation peut remplir : celles que visent les angles de l'entretien du kit, plus les
#: questions ouvertes. Liste FERMÉE, tirée de ``cadrage_interview`` : aucune rubrique n'est écrite en dur ici.
def _rubriques() -> tuple[tuple[str, str], ...]:
    from grimoire.core.cadrage_interview import ANGLES

    seen: list[tuple[str, str]] = []
    for angle in ANGLES:
        for pair in ((angle.phase, angle.section), *angle.targets):
            if pair not in seen:
                seen.append(pair)
    return (*seen, ("brainstorm", "Questions ouvertes"))


RUBRIQUES: tuple[tuple[str, str], ...] = _rubriques()


def _angle_ids() -> tuple[str, ...]:
    from grimoire.core.cadrage_interview import ANGLES

    return tuple(a.id for a in ANGLES)


ANGLE_IDS: tuple[str, ...] = _angle_ids()


class ConversationError(ValueError):
    """Le fichier de conversation ou une demande ne respecte pas le cadre : rien n'est écrit."""


class ConversationConflictError(ConversationError):
    """La conversation a changé depuis ce que l'appelant a vu (écriture concurrente, élément déjà tranché) : 409."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def rubrique_key(phase: str, section: str) -> str:
    return f"{phase}/{section}"


def rubrique_ok(phase: Any, section: Any) -> bool:
    return isinstance(phase, str) and isinstance(section, str) and (phase, section) in RUBRIQUES


def angle_pour(phase: str, section: str, prefer: str | None = None) -> str:
    """L'angle de l'entretien par lequel la rubrique s'écrit : ``prefer`` s'il la porte, sinon le premier qui la porte."""
    from grimoire.core.cadrage_interview import ANGLES

    if (phase, section) == ("brainstorm", "Questions ouvertes"):
        return prefer if prefer in ANGLE_IDS else ANGLES[0].id
    fits = [a for a in ANGLES if (phase, section) in {(a.phase, a.section), *a.targets}]
    if not fits:
        raise ConversationError(f"rubrique hors liste : {phase}/{section}")
    return next((a.id for a in fits if a.id == prefer), fits[0].id)


def clean_text(value: Any, limit: int, *, what: str = "texte") -> str:
    """Un texte d'une ligne : caractères de contrôle et invisibles retirés, espaces repliés, borné ; jamais vide.

    ``ConversationError`` si ce n'est pas une chaîne, si le résultat est vide ou plus long que ``limit``.
    """
    if not isinstance(value, str):
        raise ConversationError(f"{what} : une chaîne est attendue")
    kept = "".join(
        " " if unicodedata.category(c) in {"Zl", "Zp", "Zs", "Cc"} else c
        for c in value
        if unicodedata.category(c) not in {"Cf", "Cs", "Co", "Cn"}
    )
    flat = " ".join(kept.split())
    if not flat:
        raise ConversationError(f"{what} vide")
    if len(flat) > limit:
        raise ConversationError(f"{what} trop long ({limit} caractères au plus)")
    return flat


def _plat(texte: str) -> str:
    plain = unicodedata.normalize("NFKD", texte.lower())
    return " ".join("".join(c if c.isalnum() else " " for c in plain if not unicodedata.combining(c)).split())


_FIN_DE_PROPOSITION = re.compile(r"[.!?;…。？！；\n\r]+")
#: Les mots qui renversent ou restreignent ce qui précède ou suit : une partie retirée qui en contient un change le sens.
_RESTRICTIONS = frozenset({"ne", "n", "pas", "jamais", "aucun", "aucune", "aucunement", "sans", "sauf", "ni", "non", "plus", "rien",
                           "personne", "nul", "nulle", "hormis", "excepte", "exceptes", "exceptee", "exceptees", "plutot", "que",
                           "contre", "moins", "seulement", "uniquement", "excluant", "exclure", "exclu", "mais", "hors",
                           "condition", "exclus", "exclue", "exclues", "exclusion", "interdit", "interdite", "interdits",
                           "interdites"})
_RESTRICTIONS_EXPRESSIONS = ("a l exception", "en dehors de", "a part", "pas question", "hors de", "au lieu de",
                             "a condition", "seulement si", "a l exclusion")


def _restreint(reste: str) -> bool:
    """La partie retirée contient-elle une négation ou une restriction ? Dans le doute, oui (liste large, volontairement)."""
    flat = f" {reste} "
    return any(w in _RESTRICTIONS for w in reste.split()) or any(f" {p} " in flat for p in _RESTRICTIONS_EXPRESSIONS)


def _propositions(tour: str) -> list[str]:
    """Les propositions du tour, à plat (casse, accents, ponctuation), sans les vides."""
    return [p for p in (_plat(m) for m in _FIN_DE_PROPOSITION.split(tour)) if p]


def confirmable_en_groupe(elements: list[tuple[str, bool]], tour: str) -> bool:
    """Les éléments tirés de ce tour peuvent-ils se confirmer par le « oui » d'un groupe ?

    Règle simple et sûre, sans liste de mots : oui seulement si le tour ne contient QUE ces éléments, c'est-à-dire si l'un
    d'eux est le tour entier, ou si chaque proposition du tour est elle-même un de ces éléments. Dès qu'une autre
    proposition du tour n'est pas dans le groupe (« Sauf les bots », « Uniquement en lecture », « Jamais le week-end »…),
    elle peut retirer ou restreindre ce que les autres accordent : confirmation individuelle. ``(texte, tronque)`` : un
    élément tronqué par le programme couvre la proposition dont il est le début.
    """
    whole = _plat(tour)
    wanted = [(_plat(t.rstrip("…").rstrip() if cut else t), cut) for t, cut in elements]
    if any(w == whole for w, _cut in wanted):
        return True
    props = _propositions(tour)
    return bool(props) and all(any(p == w or (cut and p.startswith(w + " ")) for w, cut in wanted) for p in props)


def mots_de_l_humain(element: str, tour: str, *, tronque: bool = False) -> bool:
    """L'élément reprend-il, mot pour mot, le tour ENTIER de l'humain ou une proposition ENTIÈRE de ce tour ?

    La comparaison se fait à la casse, aux accents et à la ponctuation près. Une proposition va d'une ponctuation forte
    (``. ! ? ; …``) ou d'un retour à la ligne à l'autre. Un fragment plus court qu'une proposition (« export CSV » tiré de
    « je ne veux pas d'export CSV ») n'est PAS « les mots de l'humain » : il a perdu son contexte, donc peut-être sa
    négation ou sa restriction. Seule exception : un élément que le PROGRAMME a tronqué (``tronque``, drapeau posé à
    l'instant de la coupe, jamais déduit du texte) reprend le début d'une proposition, à condition que la suite coupée ne
    contienne ni négation ni restriction. Un texte qui finit par « … » sans ce drapeau (venu d'un moteur) n'est « les mots
    de l'humain » que si l'humain a lui-même écrit ces points de suspension. Dans le doute : non.
    """
    wanted = _plat(element.rstrip("…").rstrip())
    if not wanted:
        return False
    if element.rstrip().endswith("…"):
        if not tronque and " ".join(element.split()) not in " ".join(tour.split()):
            return False
    else:
        tronque = False
    morceaux = [tour, *_FIN_DE_PROPOSITION.split(tour)]
    for morceau in morceaux:
        plat = _plat(morceau)
        if not plat:
            continue
        if plat == wanted:
            return True
        if tronque and plat.startswith(wanted + " ") and not _restreint(plat[len(wanted):]):
            return True
    return False


@dataclass(slots=True)
class Tour:
    """Un tour de parole : qui, quoi, quand, quel geste."""

    n: int
    auteur: str
    texte: str
    date: str
    geste: str


@dataclass(slots=True)
class Question:
    """La question en cours : d'où elle vient dans l'entretien, et POURQUOI le programme la pose."""

    kind: str
    texte: str
    pourquoi: str
    angle: str = ""
    index: int = 0
    phase: str = ""
    section: str = ""
    obligatoire: bool = True


@dataclass(slots=True)
class Element:
    """Une information comprise : sa rubrique, sa source (numéro du tour), son statut.

    Elle n'entre dans le document qu'une fois ``confirme`` ou ``corrige`` (``ecrit`` le dit). ``remplace`` : l'élément
    que celui-ci corrige (retiré du document à la confirmation). ``question`` : la question posée, lisible devant la
    réponse dans le document, comme l'entretien guidé.
    """

    id: str
    genre: str
    texte: str
    phase: str
    section: str
    angle: str
    source: int
    statut: str = "propose"
    question: str = ""
    ecrit: bool = False
    rang: int = 0
    remplace: str = ""
    avant: list[str] = field(default_factory=list)
    #: ``humain`` (ses mots sont dans le tour source) ou ``moteur`` (reformulé ou inventé : confirmation individuelle).
    origine: str = "humain"
    #: Confirmé seul, jamais par le « oui » d'un groupe : une autre rubrique que la question, ou un texte du moteur.
    individuel: bool = False
    #: ``ecrire`` : l'intention d'écrire est enregistrée AVANT l'écriture du document ; une reprise après panne la termine ou l'annule.
    intention: str = ""


@dataclass(slots=True)
class Note:
    """Une entrée de « pour plus tard » ou des questions ouvertes : le texte et le tour d'où elle vient."""

    texte: str
    tour: int


@dataclass(slots=True)
class Conversation:
    """L'état complet d'une conversation de cadrage."""

    chantier: str
    cree: str
    active_le: str
    schema: int = SCHEMA
    rev: int = 0
    mode: str = "cadrage"
    mode_avant_pause: str = "cadrage"
    classee_le: str = ""
    document: str = ""
    moteur: str = "sans-modele"
    question: Question | None = None
    tours: list[Tour] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    ouvertes: list[Note] = field(default_factory=list)
    plus_tard: list[Note] = field(default_factory=list)
    rang: int = 0
    seq: int = 0
    #: Les rédactions proposées pour le document (lot E) : rien n'est écrit sans le geste d'acceptation de l'humain.
    redactions: list[Any] = field(default_factory=list)
    #: Non enregistré : un fichier lisible mais à réparer à la main (identifiants en double) ne se modifie pas.
    reparer: str = ""

    # ── lecture ────────────────────────────────────────────────────────────

    def element(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def en_attente(self) -> list[Element]:
        """Ce qui est compris mais pas encore confirmé, dans l'ordre."""
        return [e for e in self.elements if e.statut == "propose"]

    def ranges(self) -> list[Element]:
        """Ce que l'humain a confirmé ou corrigé : ce qui est (ou doit être) dans le document."""
        return [e for e in self.elements if e.statut in ("confirme", "corrige")]

    # ── mutations (l'appelant tient le verrou et enregistre) ───────────────

    def say(self, auteur: str, texte: str, geste: str, date: str) -> Tour:
        limit = MAX_TURN_CHARS if auteur == "utilisateur" else MAX_BOT_CHARS
        if len(self.tours) >= MAX_TURNS:
            raise ConversationError("conversation trop longue : classe-la et reprends dans une nouvelle")
        tour = Tour(len(self.tours) + 1, auteur, clean_text(texte, limit, what="tour"), date, geste)
        self.tours.append(tour)
        return tour

    def new_element(self, genre: str, texte: str, phase: str, section: str, angle: str, source: int, **extra: Any) -> Element:
        if len(self.elements) >= MAX_ELEMENTS:
            raise ConversationError("trop d'éléments compris dans cette conversation : classe-la et reprends")
        if not rubrique_ok(phase, section):
            raise ConversationError(f"rubrique hors liste : {phase}/{section}")
        # Jamais d'identifiant déjà pris : le compteur rattrape le plus grand numéro existant (fichier édité à la main).
        self.seq = max(self.seq, *(int(e.id[1:]) for e in self.elements), 0) + 1
        item = Element(f"e{self.seq}", genre, clean_text(texte, MAX_ELEMENT_CHARS, what="élément"), phase, section,
                       angle, source, **extra)
        self.elements.append(item)
        return item

    def note(self, where: list[Note], texte: str, tour: int) -> None:
        """Ajoute une note (tronquée à la longueur permise : un hors-sujet long n'est pas un refus) ; les plus anciennes partent."""
        flat = " ".join(texte.split())
        where.append(Note(clean_text(flat[: MAX_NOTE_CHARS - 1] + "…" if len(flat) > MAX_NOTE_CHARS else flat,
                                     MAX_NOTE_CHARS, what="note"), tour))
        del where[:-MAX_NOTES]

    # ── (dé)sérialisation ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        out = {
            "schema": self.schema, "rev": self.rev, "chantier": self.chantier, "cree": self.cree,
            "active_le": self.active_le, "classee_le": self.classee_le, "mode": self.mode,
            "mode_avant_pause": self.mode_avant_pause, "document": self.document, "moteur": self.moteur,
            "question": None if self.question is None else _plain(self.question),
            "tours": [_plain(t) for t in self.tours], "elements": [_plain(e) for e in self.elements],
            "ouvertes": [_plain(n) for n in self.ouvertes], "plus_tard": [_plain(n) for n in self.plus_tard],
            "rang": self.rang, "seq": self.seq,
        }
        if self.redactions:  # absent tant qu'aucune rédaction n'a été proposée : les fichiers d'avant le lot E restent identiques
            out["redactions"] = [r.to_dict() for r in self.redactions]
        return out

    @classmethod
    def from_dict(cls, raw: Any, *, chantier: str | None = None) -> Conversation:
        """Relit un fichier de conversation. Tout ce qui sort du cadre lève :class:`ConversationError`."""
        return _parse(raw, chantier)


def _plain(item: Any) -> dict[str, Any]:
    return {name: getattr(item, name) for name in item.__dataclass_fields__}


def _text(raw: dict[str, Any], key: str, limit: int, *, empty: bool = False) -> str:
    value = raw.get(key, "")
    if empty and value == "":
        return ""
    return clean_text(value, limit, what=key)


def parse_date(value: str) -> datetime:
    """Une date ISO 8601 ; sans fuseau, elle est lue en UTC. ``ConversationError`` si elle ne se lit pas."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConversationError(f"date illisible : {value[:40]!r}") from exc
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _date(raw: dict[str, Any], key: str, *, empty: bool = False) -> str:
    value = _text(raw, key, 40, empty=empty)
    if value:
        parse_date(value)
    return value


def _int(raw: dict[str, Any], key: str, *, low: int = 0, high: int = 10_000_000) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConversationError(f"conversation invalide : « {key} » hors bornes")
    return value


def _only(raw: Any, allowed: set[str], what: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ConversationError(f"conversation invalide : {what} mal formé")
    return raw


def _list(raw: dict[str, Any], key: str, limit: int) -> list[Any]:
    rows = raw.get(key, [])
    if not isinstance(rows, list) or len(rows) > limit:
        raise ConversationError(f"conversation invalide : « {key} » doit être une liste d'au plus {limit}")
    return rows


def _tour(row: Any) -> Tour:
    row = _only(row, {"n", "auteur", "texte", "date", "geste"}, "tour")
    auteur = row.get("auteur")
    if auteur not in AUTEURS or row.get("geste") not in GESTES_TOUR:
        raise ConversationError("conversation invalide : auteur ou geste de tour inconnu")
    limit = MAX_TURN_CHARS if auteur == "utilisateur" else MAX_BOT_CHARS
    return Tour(_int(row, "n", low=1, high=MAX_TURNS), auteur, _text(row, "texte", limit),
                _date(row, "date"), str(row["geste"]))


def _question(row: Any) -> Question | None:
    if row is None:
        return None
    row = _only(row, set(Question.__dataclass_fields__), "question")
    if row.get("kind") not in KINDS_QUESTION:
        raise ConversationError("conversation invalide : genre de question inconnu")
    angle, phase, section = str(row.get("angle", "")), str(row.get("phase", "")), str(row.get("section", ""))
    if angle and angle not in ANGLE_IDS:
        raise ConversationError("conversation invalide : angle de la question inconnu")
    if (phase or section) and not rubrique_ok(phase, section):
        raise ConversationError("conversation invalide : rubrique de la question hors liste")
    if not isinstance(row.get("obligatoire", True), bool):
        raise ConversationError("conversation invalide : « obligatoire » mal typé")
    return Question(str(row["kind"]), _text(row, "texte", MAX_QUESTION_CHARS), _text(row, "pourquoi", MAX_QUESTION_CHARS),
                    angle, _int({"i": row.get("index", 0)}, "i", high=50), phase, section, row.get("obligatoire", True))


def _element(row: Any, turns: int) -> Element:
    row = _only(row, set(Element.__dataclass_fields__), "élément")
    if row.get("genre") not in GENRES or row.get("statut") not in STATUTS:
        raise ConversationError("conversation invalide : genre ou statut d'élément inconnu")
    if not rubrique_ok(row.get("phase"), row.get("section")):
        raise ConversationError("conversation invalide : rubrique d'élément hors liste")
    if row.get("angle") not in ANGLE_IDS:
        raise ConversationError("conversation invalide : angle d'élément inconnu")
    source = _int(row, "source", low=1, high=MAX_TURNS)
    if source > turns:
        raise ConversationError("conversation invalide : source d'élément absente des tours")
    if not isinstance(row.get("ecrit", False), bool):
        raise ConversationError("conversation invalide : « ecrit » mal typé")
    avant = row.get("avant", [])
    if not isinstance(avant, list) or len(avant) > 10:
        raise ConversationError("conversation invalide : « avant » mal formé")
    origine, individuel, intention = row.get("origine", "humain"), row.get("individuel", False), row.get("intention", "")
    if origine not in ORIGINES or not isinstance(individuel, bool) or intention not in ("", "ecrire"):
        raise ConversationError("conversation invalide : origine, confirmation individuelle ou intention mal formée")
    ident = _text(row, "id", 20)
    if not ident.startswith("e") or not ident[1:].isdigit():
        raise ConversationError("conversation invalide : identifiant d'élément mal formé")
    return Element(
        ident, str(row["genre"]), _text(row, "texte", MAX_ELEMENT_CHARS), str(row["phase"]), str(row["section"]),
        str(row["angle"]), source, str(row["statut"]), _text(row, "question", MAX_QUESTION_CHARS, empty=True),
        bool(row.get("ecrit", False)), _int({"r": row.get("rang", 0)}, "r"), _text(row, "remplace", 20, empty=True),
        [clean_text(a, MAX_ELEMENT_CHARS, what="avant") for a in avant],
        origine, individuel, intention,
    )


def _notes(raw: dict[str, Any], key: str, turns: int) -> list[Note]:
    out: list[Note] = []
    for row in _list(raw, key, MAX_NOTES):
        row = _only(row, {"texte", "tour"}, key)
        tour = _int(row, "tour", low=1, high=MAX_TURNS)
        if tour > turns:
            raise ConversationError(f"conversation invalide : tour de « {key} » absent")
        out.append(Note(_text(row, "texte", MAX_NOTE_CHARS), tour))
    return out


_KEYS = {"schema", "rev", "chantier", "cree", "active_le", "classee_le", "mode", "mode_avant_pause", "document",
         "moteur", "question", "tours", "elements", "ouvertes", "plus_tard", "rang", "seq", "redactions"}


def _parse(raw: Any, chantier: str | None) -> Conversation:
    data = _only(raw, _KEYS, "fichier")
    if data.get("schema") != SCHEMA:
        raise ConversationError(f"conversation invalide : schéma {data.get('schema')!r} inconnu (attendu {SCHEMA})")
    name = _text(data, "chantier", 80)
    if chantier is not None and name != chantier:
        raise ConversationError("conversation invalide : elle appartient à un autre chantier")
    if data.get("mode") not in MODES or data.get("mode_avant_pause") not in MODES or data.get("document") not in DOCUMENT_STATES:
        raise ConversationError("conversation invalide : mode ou état du document inconnu")
    tours = [_tour(r) for r in _list(data, "tours", MAX_TURNS)]
    if [t.n for t in tours] != list(range(1, len(tours) + 1)):
        raise ConversationError("conversation invalide : tours mal numérotés")
    elements = [_element(r, len(tours)) for r in _list(data, "elements", MAX_ELEMENTS)]
    reparer = ""
    if len({e.id for e in elements}) != len(elements):  # lisible, jamais modifiable : la réparation est à la main
        reparer = "identifiants d'éléments en double dans conversation.json : renomme les doublons (e1, e2…) ou retire le fichier"
    for e in elements:
        if e.remplace and e.remplace not in {x.id for x in elements}:
            raise ConversationError("conversation invalide : élément remplacé introuvable")
    conv = Conversation(
        chantier=name, cree=_date(data, "cree"), active_le=_date(data, "active_le"), schema=SCHEMA,
        rev=_int(data, "rev"), mode=str(data["mode"]), mode_avant_pause=str(data["mode_avant_pause"]),
        classee_le=_date(data, "classee_le", empty=True), document=str(data["document"]),
        moteur=_text(data, "moteur", 40), question=_question(data.get("question")), tours=tours, elements=elements,
        ouvertes=_notes(data, "ouvertes", len(tours)), plus_tard=_notes(data, "plus_tard", len(tours)),
        rang=_int(data, "rang"), seq=_int(data, "seq"),
    )
    conv.seq = max(conv.seq, *(int(e.id[1:]) for e in elements), 0)  # jamais un identifiant déjà pris
    conv.reparer = reparer
    if "redactions" in data:  # lot E : validées comme le reste (fondements connus, rubrique de la liste fermée…)
        from grimoire.core.cadrage_conversation_redaction import lire_propositions

        conv.redactions = lire_propositions(data["redactions"], {e.id for e in elements}, len(tours))
    return conv
