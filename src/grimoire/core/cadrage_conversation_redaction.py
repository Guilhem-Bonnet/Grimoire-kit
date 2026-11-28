"""Le document à la fin : le geste borné « proposer une rédaction » et la proposition en attente (épique #1477, lot E).

À partir des éléments CONFIRMÉS d'une rubrique, et d'eux seuls, un moteur propose une rédaction plus lisible : des phrases,
chacune avec la liste des éléments qui la fondent (leurs identifiants). Le moteur est traité comme hostile : la sortie est
revalidée ici (:func:`valider_redaction`) et refusée au moindre écart (phrase sans fondement, identifiant inconnu ou non
confirmé, élément confirmé oublié, rubrique hors cadre, texte hors bornes ou qui commencerait par une marque de mise en
forme). Refusée, elle est remplacée par la rédaction du programme (:func:`redaction_fidele` : la liste fidèle des éléments
confirmés, telle quelle). Sans modèle, c'est elle qui est proposée.

Une proposition n'écrit RIEN : elle est enregistrée dans ``conversation.json`` (:class:`Proposition`, statut ``attente``),
avec ses liens vers l'échange (éléments, tours d'origine) et l'empreinte du document au moment de la proposition. Seul le
geste d'acceptation de l'humain, sur le texte exact, l'écrit (:mod:`grimoire.core.cadrage_conversation_redaction_ops`, par
la seule porte d'écriture :mod:`grimoire.core.cadrage_conversation_document`). Aucun fichier n'est lu ni écrit ici.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.core.cadrage_conversation import (
    MAX_ELEMENT_CHARS,
    RUBRIQUES,
    ConversationError,
    clean_text,
    parse_date,
    rubrique_key,
)

if TYPE_CHECKING:
    from grimoire.core.cadrage_conversation_engine import Garde

__all__ = [
    "MAX_FONDEMENTS",
    "MAX_PHRASES",
    "MAX_PHRASE_CHARS",
    "MAX_PROPOSITIONS",
    "MAX_REDACTION_CHARS",
    "RUBRIQUES_REDIGEABLES",
    "STATUTS_REDACTION",
    "EntreeRedaction",
    "Fondement",
    "Passage",
    "PhraseRedigee",
    "Proposition",
    "Redaction",
    "RedactionRefuseeError",
    "borner",
    "cle_rubrique",
    "lire_propositions",
    "nouvel_id",
    "redaction_fidele",
    "rediger",
    "valider_redaction",
]

#: Les rubriques qu'une rédaction peut viser : celles où la conversation range des éléments, sauf « Questions ouvertes »
#: (que seul un « je ne sais pas » remplit, ligne de forme imposée par l'entretien).
RUBRIQUES_REDIGEABLES: tuple[tuple[str, str], ...] = tuple(r for r in RUBRIQUES if r != ("brainstorm", "Questions ouvertes"))
_CLES = {rubrique_key(p, s): (p, s) for p, s in RUBRIQUES_REDIGEABLES}
MAX_PHRASES = 12
MAX_PHRASE_CHARS = MAX_ELEMENT_CHARS
MAX_REDACTION_CHARS = 4000
MAX_FONDEMENTS = 12
#: ``attente`` (proposée, rien d'écrit), ``acceptee`` (écrite par le geste de l'humain), ``ecartee``, ``reprise`` (l'humain
#: a demandé d'y revenir avec la mascotte), ``remplacee`` (une proposition plus récente, ou une rédaction acceptée depuis).
STATUTS_REDACTION: tuple[str, ...] = ("attente", "acceptee", "ecartee", "reprise", "remplacee")
MAX_PROPOSITIONS = 60
#: Une phrase de modèle ne commence jamais par une marque de mise en forme : elle sera une puce, rien d'autre.
_MARQUES = ("#", "*", "-", "+", ">", "|", "<", "`", "[", "!", "=", "_", "~")
_HEX = re.compile(r"[0-9a-f]{64}")
_ID_ELEMENT = re.compile(r"e[0-9]{1,7}")
_ID_PROPOSITION = re.compile(r"r[0-9]{1,7}")


class RedactionRefuseeError(ConversationError):
    """Une rédaction rendue par un moteur sort du cadre : elle n'est pas proposée."""


# ── Le geste : entrée et sortie, petites et typées ─────────────────────────


@dataclass(frozen=True, slots=True)
class Fondement:
    """Un élément confirmé que le moteur peut citer : son identifiant et son texte."""

    id: str
    texte: str


@dataclass(frozen=True, slots=True)
class EntreeRedaction:
    """Rédiger UNE rubrique (``phase/section``) à partir de ses éléments confirmés, et de rien d'autre."""

    rubrique: str
    nom: str
    elements: tuple[Fondement, ...]


@dataclass(frozen=True, slots=True)
class PhraseRedigee:
    """Une phrase proposée et les identifiants des éléments confirmés qui la fondent."""

    texte: str
    fondements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Redaction:
    """La sortie du geste : la rubrique visée (celle demandée) et ses phrases."""

    rubrique: str
    phrases: tuple[PhraseRedigee, ...]


def redaction_fidele(entree: EntreeRedaction) -> Redaction:
    """La rédaction du programme : chaque élément confirmé, tel quel, une phrase chacun (aucun modèle)."""
    return Redaction(entree.rubrique, tuple(PhraseRedigee(f.texte, (f.id,)) for f in entree.elements))


def valider_redaction(out: Any, entree: EntreeRedaction) -> Redaction:
    """La rédaction d'un moteur, revalidée ; :class:`RedactionRefuseeError` (ou ``ConversationError``) au moindre écart."""
    if not isinstance(out, Redaction):
        raise RedactionRefuseeError("une rédaction est attendue")
    if out.rubrique != entree.rubrique or out.rubrique not in _CLES:
        raise RedactionRefuseeError("rubrique hors cadre")
    if not isinstance(out.phrases, tuple | list) or not 0 < len(out.phrases) <= MAX_PHRASES:
        raise RedactionRefuseeError(f"de une à {MAX_PHRASES} phrases sont attendues")
    connus = {f.id for f in entree.elements}
    phrases: list[PhraseRedigee] = []
    for phrase in out.phrases:
        if not isinstance(phrase, PhraseRedigee) or not isinstance(phrase.fondements, tuple | list):
            raise RedactionRefuseeError("phrase mal formée")
        texte = clean_text(phrase.texte, MAX_PHRASE_CHARS, what="phrase")
        if texte.startswith(_MARQUES):
            raise RedactionRefuseeError("une phrase commence par une marque de mise en forme")
        cites = tuple(dict.fromkeys(phrase.fondements))
        if not cites or len(cites) > MAX_FONDEMENTS:
            raise RedactionRefuseeError("une phrase sans fondement (ou trop de fondements)")
        if any(not isinstance(c, str) or c not in connus for c in cites):
            raise RedactionRefuseeError("un fondement n'est pas un élément confirmé de cette rubrique")
        phrases.append(PhraseRedigee(texte, cites))
    if sum(len(p.texte) for p in phrases) > MAX_REDACTION_CHARS:
        raise RedactionRefuseeError("rédaction trop longue")
    if {c for p in phrases for c in p.fondements} != connus:
        raise RedactionRefuseeError("un élément confirmé manque à la rédaction")  # l'accepter le retirerait du document
    return Redaction(entree.rubrique, tuple(phrases))


def rediger(garde: Garde, entree: EntreeRedaction) -> tuple[Redaction, str]:
    """Le geste, sous le gardien : rend la rédaction validée et qui l'a faite (``sans-modele`` si c'est le programme).

    Un moteur sans ce geste (ou le moteur sans modèle) donne la rédaction fidèle, sans note. Délai, exception ou sortie hors
    cadre : la rédaction fidèle, et une note dans ``garde.notes``. Le moteur ne reçoit que des textes courts, jamais de
    chemin ni de document.
    """
    geste = getattr(garde.moteur, "proposer_redaction", None)
    if garde.interne or not callable(geste):
        return redaction_fidele(entree), "sans-modele"
    avant = len(garde.notes)
    out = garde._appeler(
        "proposer_redaction", lambda m: m.proposer_redaction(entree), lambda o: valider_redaction(o, entree),
        lambda: redaction_fidele(entree), garde.delai_s)
    return out, ("sans-modele" if len(garde.notes) > avant else garde.id)


# ── La proposition, enregistrée dans la conversation ───────────────────────


@dataclass(slots=True)
class Passage:
    """Une phrase de la proposition : son texte, les éléments qui la fondent, leurs tours d'origine."""

    texte: str
    fondements: list[str]
    tours: list[int]

    @property
    def ligne(self) -> str:
        """La ligne exacte qui sera écrite dans le fichier de phase."""
        return f"- {self.texte}"


@dataclass(slots=True)
class Proposition:
    """Une rédaction proposée pour une rubrique. ``base`` : les éléments confirmés (identifiant, texte) au moment de la
    proposition ; ``empreinte`` : celle du document à ce moment ; ``tour`` : le tour de la mascotte qui l'a présentée."""

    id: str
    phase: str
    section: str
    statut: str
    moteur: str
    date: str
    empreinte: str
    passages: list[Passage]
    base: list[Fondement]
    tranchee_le: str = ""
    tour: int = 0

    @property
    def cle(self) -> str:
        return rubrique_key(self.phase, self.section)

    @property
    def lignes(self) -> list[str]:
        return [p.ligne for p in self.passages]

    @property
    def texte(self) -> str:
        """Le texte exact affiché, tel qu'il sera écrit : c'est sur lui que porte le geste d'acceptation."""
        return "\n".join(self.lignes)

    @property
    def cites(self) -> set[str]:
        return {c for p in self.passages for c in p.fondements}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "phase": self.phase, "section": self.section, "statut": self.statut, "moteur": self.moteur,
            "date": self.date, "empreinte": self.empreinte, "tranchee_le": self.tranchee_le, "tour": self.tour,
            "passages": [{"texte": p.texte, "fondements": list(p.fondements), "tours": list(p.tours)} for p in self.passages],
            "base": [{"id": f.id, "texte": f.texte} for f in self.base],
        }


_CHAMPS = {"id", "phase", "section", "statut", "moteur", "date", "empreinte", "tranchee_le", "tour", "passages", "base"}


def _objet(raw: Any, champs: set[str], quoi: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - champs:
        raise ConversationError(f"conversation invalide : {quoi} mal formé")
    return raw


def _ids(raw: Any, connus: set[str], quoi: str) -> list[str]:
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_FONDEMENTS or len(set(map(str, raw))) != len(raw):
        raise ConversationError(f"conversation invalide : {quoi} mal formés")
    if any(not isinstance(i, str) or not _ID_ELEMENT.fullmatch(i) or i not in connus for i in raw):
        raise ConversationError(f"conversation invalide : {quoi} inconnus")
    return list(raw)


def _passage(raw: Any, connus: set[str], tours: int) -> Passage:
    row = _objet(raw, {"texte", "fondements", "tours"}, "passage de rédaction")
    seen = row.get("tours", [])
    if not isinstance(seen, list) or len(seen) > MAX_FONDEMENTS or any(
        not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= tours for n in seen
    ):
        raise ConversationError("conversation invalide : tours d'un passage de rédaction hors bornes")
    return Passage(clean_text(row.get("texte"), MAX_PHRASE_CHARS, what="passage"), _ids(row.get("fondements"), connus, "fondements"),
                   list(seen))


def _proposition(raw: Any, connus: set[str], tours: int) -> Proposition:
    row = _objet(raw, _CHAMPS, "rédaction")
    ident, phase, section = row.get("id"), row.get("phase"), row.get("section")
    if not isinstance(ident, str) or not _ID_PROPOSITION.fullmatch(ident) or row.get("statut") not in STATUTS_REDACTION:
        raise ConversationError("conversation invalide : identifiant ou statut de rédaction inconnu")
    if (phase, section) not in RUBRIQUES_REDIGEABLES:
        raise ConversationError("conversation invalide : rubrique de rédaction hors liste")
    empreinte, tour = row.get("empreinte"), row.get("tour", 0)
    if not isinstance(empreinte, str) or not _HEX.fullmatch(empreinte):
        raise ConversationError("conversation invalide : empreinte de rédaction mal formée")
    if not isinstance(tour, int) or isinstance(tour, bool) or not 0 <= tour <= tours:
        raise ConversationError("conversation invalide : tour de rédaction hors bornes")
    passages = row.get("passages")
    if not isinstance(passages, list) or not 0 < len(passages) <= MAX_PHRASES:
        raise ConversationError("conversation invalide : passages de rédaction mal formés")
    base = row.get("base")
    if not isinstance(base, list) or len(base) > 200:
        raise ConversationError("conversation invalide : base de rédaction mal formée")
    fondements = []
    for item in base:
        f = _objet(item, {"id", "texte"}, "base de rédaction")
        fondements.append(Fondement(_ids([f.get("id")], connus, "éléments de base")[0],
                                    clean_text(f.get("texte"), MAX_ELEMENT_CHARS, what="base")))
    date = clean_text(row.get("date"), 40, what="date de rédaction")
    parse_date(date)
    tranchee = row.get("tranchee_le", "")
    if tranchee != "":
        tranchee = clean_text(tranchee, 40, what="date de rédaction")
        parse_date(tranchee)
    moteur = clean_text(row.get("moteur"), 40, what="moteur de rédaction")
    return Proposition(ident, str(phase), str(section), str(row["statut"]), moteur, date, empreinte,
                       [_passage(p, connus, tours) for p in passages], fondements, str(tranchee), tour)


def lire_propositions(raw: Any, elements: set[str], tours: int) -> list[Proposition]:
    """Relit les rédactions d'un ``conversation.json`` ; tout écart lève ``ConversationError`` (fichier hostile)."""
    if not isinstance(raw, list) or len(raw) > MAX_PROPOSITIONS:
        raise ConversationError(f"conversation invalide : « redactions » doit être une liste d'au plus {MAX_PROPOSITIONS}")
    out = [_proposition(r, elements, tours) for r in raw]
    if len({p.id for p in out}) != len(out):
        raise ConversationError("conversation invalide : identifiants de rédaction en double")
    attente = [p.cle for p in out if p.statut == "attente"]
    if len(set(attente)) != len(attente):
        raise ConversationError("conversation invalide : deux rédactions en attente pour une même rubrique")
    return out


def cle_rubrique(value: Any) -> tuple[str, str]:
    """La rubrique demandée, retrouvée dans la liste fermée des rubriques rédigeables ; ``ConversationError`` sinon."""
    if not isinstance(value, str) or value not in _CLES:
        raise ConversationError("rubrique hors cadre : une rubrique de la liste fermée est attendue (phase/section)")
    return _CLES[value]


def borner(propositions: list[Proposition]) -> None:
    """Garde au plus :data:`MAX_PROPOSITIONS` rédactions : les plus anciennes déjà tranchées partent, jamais l'attente ni l'acceptée en vigueur."""
    vivantes = {id(p) for p in propositions if p.statut in ("attente", "acceptee")}
    while len(propositions) > MAX_PROPOSITIONS:
        vieille = next((p for p in propositions if id(p) not in vivantes), None)
        if vieille is None:
            return
        propositions.remove(vieille)


def nouvel_id(propositions: list[Proposition]) -> str:
    return f"r{max((int(p.id[1:]) for p in propositions), default=0) + 1}"
