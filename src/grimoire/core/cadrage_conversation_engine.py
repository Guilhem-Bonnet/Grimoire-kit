"""Les gestes bornés d'un moteur de conversation, et le gardien qui les valide (épique #1477, lot A).

Le programme tient le cadre ; un moteur (modèle ou règles) ne fait que des GESTES petits, aux entrées et sorties
typées. :class:`Moteur` est l'interface que le lot B implémentera avec de vrais modèles ; :class:`MoteurSansModele`
est l'implémentation par règles, sans aucun modèle. Un moteur ne reçoit jamais de chemin, ni de document, ni de
moyen d'écrire : il reçoit des textes courts et rend des textes courts.

:class:`Garde` enveloppe tout moteur : chaque sortie est VALIDÉE (type, longueur, classe de la liste fermée,
rubrique de la liste fermée), chaque appel est borné dans le temps (fil abandonné au-delà du délai), une exception
ou une sortie hors cadre est refusée et remplacée par la sortie du moteur sans modèle, avec une note que
l'interface peut montrer. Un moteur défaillant ou hostile ne peut donc ni sortir du cadre ni écrire ailleurs :
l'écriture dans le document ne passe que par :mod:`grimoire.core.cadrage_conversation_turn`.
"""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from grimoire.core.cadrage_conversation import (
    MAX_ELEMENT_CHARS,
    MAX_QUESTION_CHARS,
    RUBRIQUES,
    ConversationError,
    clean_text,
    rubrique_key,
)

__all__ = [
    "CLASSES",
    "DELAI_MOTEUR_S",
    "MAX_ELEMENTS_PAR_TOUR",
    "MAX_PISTES",
    "RUBRIQUE_KEYS",
    "ElementExtrait",
    "EntreeClassement",
    "EntreeExtraction",
    "EntreePistes",
    "EntreeQuestion",
    "EntreeReformulation",
    "Garde",
    "Moteur",
    "MoteurSansModele",
]

#: Ce qu'un moteur peut dire d'un tour de l'humain : liste FERMÉE.
CLASSES: tuple[str, ...] = (
    "reponse", "confirme", "hors_sujet", "ne_sait_pas", "sans_objet", "contredit", "brainstorm", "reprise", "pause",
    "question",
)
#: Les rubriques qu'une extraction peut viser : toutes sauf « Questions ouvertes », que seul « je ne sais pas » remplit.
RUBRIQUE_KEYS: tuple[str, ...] = tuple(rubrique_key(p, s) for p, s in RUBRIQUES if (p, s) != ("brainstorm", "Questions ouvertes"))
MAX_ELEMENTS_PAR_TOUR = 5
MAX_PISTES = 5
MAX_PISTE_CHARS = 300
MAX_REFORMULATION = 900
#: Un geste qui dépasse ce délai est abandonné et remplacé par le repli sans modèle.
DELAI_MOTEUR_S = 8.0
T = TypeVar("T")


# ── Entrées et sorties : petites, typées ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntreeQuestion:
    """Poser une question ou une invitation. ``kind`` : amorce, brainstorm, document, pause, termine, rien, reporte."""

    kind: str
    angle: str = ""
    index: int = 0
    amorce: str = ""
    pourquoi: str = ""
    obligatoire: bool = True
    fin: bool = False
    derniers_tours: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntreeClassement:
    """Dire ce qu'est le tour de l'humain. ``contexte`` : le genre de la question en cours (``confirmer``, ``amorce``…)."""

    texte: str
    contexte: str = ""
    mode: str = "cadrage"
    question: str = ""


@dataclass(frozen=True, slots=True)
class EntreeExtraction:
    """Tirer des éléments d'une réponse. ``rubriques`` est la liste fermée ; ``rubrique_visee`` celle de la question."""

    texte: str
    rubrique_visee: str
    rubriques: tuple[str, ...] = RUBRIQUE_KEYS
    mode: str = "cadrage"


@dataclass(frozen=True, slots=True)
class ElementExtrait:
    """Un élément compris : son texte et sa rubrique (``phase/section``, de la liste fermée)."""

    texte: str
    rubrique: str


@dataclass(frozen=True, slots=True)
class EntreeReformulation:
    """Reformuler pour confirmation ce qui a été compris. ``remplace`` : c'est une correction de ce qui était noté."""

    elements: tuple[str, ...]
    rubrique: str = ""
    remplace: bool = False


@dataclass(frozen=True, slots=True)
class EntreePistes:
    """Proposer des pistes en brainstorm. ``besoin`` : ce que la personne a dit vouloir ; ``deja`` : ce qui est noté."""

    besoin: str = ""
    deja: tuple[str, ...] = ()


@runtime_checkable
class Moteur(Protocol):
    """L'interface d'un moteur : une méthode par geste. Le lot B la remplit avec de vrais modèles."""

    id: str
    libelle: str

    def formuler_question(self, entree: EntreeQuestion) -> str: ...

    def classer_reponse(self, entree: EntreeClassement) -> str: ...

    def extraire_elements(self, entree: EntreeExtraction) -> list[ElementExtrait]: ...

    def reformuler(self, entree: EntreeReformulation) -> str: ...

    def proposer_pistes(self, entree: EntreePistes) -> list[str]: ...

    def repondre_question(self, texte: str, pourquoi: str) -> str: ...

    # Ce tour demandera-t-il une vraie réflexion ? L'interface montre alors que la mascotte réfléchit.
    def est_tour_lourd(self, texte: str, etape: str) -> bool: ...

    # Ce que ce moteur ne sait pas faire, dit honnêtement (affiché tel quel).
    def limites(self) -> tuple[str, ...]: ...


# ── Le moteur sans modèle ───────────────────────────────────────────────────

#: Les amorces du kit, redites simplement (tutoiement, phrases courtes, aucun sigle). Clé : (angle, rang de l'amorce).
_QUESTIONS: dict[tuple[str, int], str] = {
    ("utilisateurs", 0): "Dis-moi : qui va se servir de ça, et dans quelle situation ?",
    ("utilisateurs", 1): "Et aujourd'hui, que fait cette personne pour s'en passer ? Qu'est-ce qui l'agace ?",
    ("existant", 0): "Est-ce qu'il existe déjà quelque chose qui fait tout ou une partie de ce que tu veux ?",
    ("existant", 1): "Y a-t-il des morceaux déjà là qu'on pourrait réutiliser plutôt que de tout refaire ?",
    ("criteres", 0): "Comment tu verras, concrètement, que c'est réussi ?",
    ("criteres", 1): "Qu'est-ce qui te permettrait de le vérifier : un test, une commande, une relecture ?",
    ("hors-perimetre", 0): "Qu'est-ce que tu ne veux surtout pas traiter cette fois ?",
    ("hors-perimetre", 1): "Quelles demandes voisines tu préfères laisser de côté pour l'instant ?",
    ("contraintes", 0): "Qu'est-ce qui t'impose des limites : le temps, la technique, la compatibilité ?",
    ("contraintes", 1): "Qu'est-ce qui ne doit surtout pas casser ?",
    ("risques", 0): "Imagine que ça échoue dans six mois : à ton avis, pourquoi ?",
    ("risques", 1): "Quel risque te coûterait le plus cher, et comment le repérer tôt ?",
    ("alternatives", 0): "Existe-t-il une version plus simple, peut-être sans code, qui réglerait l'essentiel ?",
    ("alternatives", 1): "Qu'est-ce que tu as déjà envisagé puis laissé de côté, et pourquoi ?",
}
_INVITATIONS: dict[str, str] = {
    "brainstorm": (
        "D'accord, on brainstorme. Sans modèle d'IA, je n'invente pas d'idées : donne-moi les tiennes, une par ligne "
        "ou séparées par des points-virgules, je les range sans les juger. Dis « c'est tout » quand tu as fini."
    ),
    "document": "Je crois qu'on a l'essentiel. On passe au document ? Réponds « oui » pour terminer, ou « non » pour que je continue à te poser des questions.",
    "document-fin": "Je n'ai plus de question. On passe au document ? Réponds « oui » pour terminer, ou dis-moi ce qui manque.",
    "pause": "D'accord, on fait une pause. Tout est gardé : tu reprends quand tu veux, exactement là où on s'est arrêtés.",
    "termine": "C'est terminé de mon côté. La conversation reste lisible, et le document est à toi.",
    "rien": "Ta demande n'a pas besoin d'un cadrage : une question simple se règle directement. Je n'ai rien à te demander.",
    "reporte": "C'est une action à rétablir d'abord : on ne bloque rien. Le cadrage viendra après, au retour d'expérience.",
}
_LIMITES = (
    "Je n'utilise aucun modèle d'IA : je ne reformule pas avec mes mots, je reprends ce que tu dis.",
    "Je ne comprends que des réponses simples : « je ne sais pas », « plus tard », « pause », « on brainstorme »…",
    "Je ne réponds pas à tes questions, sauf « pourquoi tu me demandes ça ? » ; je les garde pour plus tard.",
    "Je n'invente pas de pistes : en brainstorm, c'est toi qui listes et moi qui range.",
)
_AFFIRMATIONS = (
    "oui", "ouais", "yes", "ok", "okay", "exact", "exactement", "parfait", "d accord", "dac", "c est ca", "c est bon",
    "tout a fait", "bien sur", "carrement", "valide", "impeccable", "ca marche", "voila", "oui c est ca",
)
_NEGATIONS = ("non", "pas vraiment", "pas exactement", "plutot", "faux", "nope", "pas du tout")
_LEADS = ("en fait", "finalement", "je me corrige", "j ai mal dit", "je me suis trompe", "correction", "non", "plutot")


def _norm(texte: str) -> str:
    """Minuscules, sans accents, ponctuation en espaces : de quoi reconnaître une tournure simple."""
    plain = unicodedata.normalize("NFKD", texte.lower().replace("’", "'"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() or c == "?" else " " for c in plain).split())


def _has(norm: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {norm} "
    return any(f" {p} " in padded for p in phrases)


def _starts(norm: str, phrases: tuple[str, ...]) -> bool:
    return any(norm == p or norm.startswith(p + " ") for p in phrases)


def _morceaux(texte: str, *, liste: bool) -> list[str]:
    """Découpe une réponse en éléments : par ligne et « ; » en brainstorm, sinon par phrases tenant dans la limite."""
    if liste:
        raw = [p.strip(" -•*\t") for chunk in texte.splitlines() for p in chunk.split(";")]
        items = [" ".join(p.split()) for p in raw if p.strip()]
    else:
        items = [" ".join(texte.split())]
    out: list[str] = []
    for item in items:
        if len(item) <= MAX_ELEMENT_CHARS:
            out.append(item)
            continue
        current = ""
        for sentence in item.replace("? ", "?\n").replace("! ", "!\n").replace(". ", ".\n").splitlines():
            if current and len(current) + 1 + len(sentence) > MAX_ELEMENT_CHARS:
                out.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
            if len(current) > MAX_ELEMENT_CHARS:  # une phrase seule trop longue : coupée à un mot, la fin est marquée
                out.append(current[: MAX_ELEMENT_CHARS - 1].rsplit(" ", 1)[0] + "…")
                current = ""
        if current:
            out.append(current)
    return out[:MAX_ELEMENTS_PAR_TOUR]


class MoteurSansModele:
    """Le moteur par règles simples : aucun modèle, aucune initiative, honnête sur ses limites."""

    id = "sans-modele"
    libelle = "Questions guidées, sans modèle"

    def formuler_question(self, entree: EntreeQuestion) -> str:
        if entree.kind == "amorce":
            return _QUESTIONS.get((entree.angle, entree.index)) or f"Dis-moi : {entree.amorce or 'qu’est-ce qu’il faut savoir ?'}"
        key = "document-fin" if entree.kind == "document" and entree.fin else entree.kind
        return _INVITATIONS.get(key, "Dis-moi ce que tu veux ajouter.")

    def classer_reponse(self, entree: EntreeClassement) -> str:
        norm = _norm(entree.texte)
        words = norm.split()
        if len(words) <= 8 and _has(norm, ("pause", "je dois y aller", "je reviens plus tard", "on reprend plus tard", "a plus tard", "je reviens")):
            return "pause"
        if any(m in norm for m in ("brainstorm", "remue meninge", "chercher des idees", "trouver des idees")):
            return "brainstorm"
        if (entree.mode == "brainstorm" or entree.contexte == "brainstorm") and len(words) <= 6 and _starts(
            norm, ("c est tout", "j ai fini", "on continue", "on reprend", "retour", "termine", "on arrete", "stop", "suite")
        ):
            return "reprise"
        if len(words) <= 10 and _has(norm, ("je ne sais pas", "je sais pas", "sais pas", "aucune idee", "pas d idee", "je ne sais plus", "ca depend", "je n en sais rien")):
            return "ne_sait_pas"
        if len(words) <= 10 and _has(norm, ("sans objet", "pas concerne", "ne s applique pas", "non applicable", "pas pertinent", "rien a voir", "ca ne me concerne pas")):
            return "sans_objet"
        if "?" in norm and len(words) <= 25 and _starts(
            norm, ("pourquoi", "c est quoi", "qu est ce", "ca veut dire", "ca sert a quoi", "a quoi ca sert", "tu peux", "peux tu", "comment ca")
        ):
            return "question"
        if "plus tard" in norm or _has(norm, ("hors sujet", "au fait")):
            return "hors_sujet"
        if entree.contexte in ("confirmer", "document"):
            if _starts(norm, _AFFIRMATIONS):
                return "contredit" if "mais" in words else ("confirme" if len(words) <= 6 else "reponse")
            if _starts(norm, _NEGATIONS):
                return "contredit"
        if _starts(norm, _LEADS[:6]):  # une tournure de correction explicite ; un simple « non » peut être une vraie réponse
            return "contredit"
        return "reponse"

    def extraire_elements(self, entree: EntreeExtraction) -> list[ElementExtrait]:
        text = entree.texte
        stripping = True
        while stripping:  # la tournure de correction (« non, en fait… ») n'est pas le contenu
            stripping = False
            norm = _norm(text)
            for lead in _LEADS:
                size = len(lead.split())
                if norm == lead:
                    return []
                if norm.startswith(lead + " "):
                    text, stripping = text.lstrip().split(None, size)[-1], True
                    break
        liste = entree.rubrique_visee == "brainstorm/Pistes" or entree.mode == "brainstorm"
        return [ElementExtrait(m, entree.rubrique_visee) for m in _morceaux(text, liste=liste) if m]

    def reformuler(self, entree: EntreeReformulation) -> str:
        said = " ; ".join(entree.elements)
        lead = "Je corrige donc" if entree.remplace else "Si je comprends bien"
        return f"{lead} : {said}. C'est bien ça ?"

    def proposer_pistes(self, entree: EntreePistes) -> list[str]:
        return []  # sans modèle, aucune piste inventée : on invite l'humain à lister les siennes

    def repondre_question(self, texte: str, pourquoi: str) -> str:
        if "pourquoi" in _norm(texte) and pourquoi:
            return pourquoi
        return ("Je ne peux pas répondre à ça : je n'utilise aucun modèle d'IA. "
                "Je le garde dans tes points « pour plus tard », et je te repose ma question.")

    def est_tour_lourd(self, texte: str, etape: str) -> bool:
        return False  # des règles répondent tout de suite

    def limites(self) -> tuple[str, ...]:
        return _LIMITES


# ── Le gardien : toute sortie est validée ───────────────────────────────────


class _RefusError(ValueError):
    """Une sortie de moteur hors cadre."""


class Garde:
    """Enveloppe un moteur : sorties validées, appels bornés, repli sans modèle, notes pour l'interface.

    ``notes`` dit, en français, ce qui a été refusé ou remplacé pendant le tour. Rien n'est jamais écrit d'une sortie
    refusée. ``delai_s`` borne chaque geste ; un moteur qui bloque est laissé à son fil (abandonné) et remplacé.
    """

    def __init__(self, moteur: Any, *, delai_s: float = DELAI_MOTEUR_S, repli: MoteurSansModele | None = None) -> None:
        self.moteur = moteur
        self.delai_s = delai_s
        self.repli = repli or MoteurSansModele()
        self.notes: list[str] = []

    @property
    def id(self) -> str:
        ident = getattr(self.moteur, "id", "")
        return ident if isinstance(ident, str) and 0 < len(ident) <= 40 and ident.isprintable() else "inconnu"

    @property
    def degrade(self) -> bool:
        return bool(self.notes)

    def _appel(self, nom: str, run: Callable[[Any], Any], check: Callable[[Any], T], repli: Callable[[], T]) -> T:
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["ok"] = run(self.moteur)
            except BaseException as exc:  # un moteur défaillant ne casse jamais le tour
                box["err"] = exc

        thread = threading.Thread(target=work, daemon=True, name=f"moteur-{nom}")
        thread.start()
        thread.join(self.delai_s)
        if thread.is_alive():
            self.notes.append(f"{nom} : le moteur a mis trop de temps, je reprends sans modèle")
            return repli()
        if "err" in box:
            self.notes.append(f"{nom} : le moteur a échoué ({type(box['err']).__name__}), je reprends sans modèle")
            return repli()
        try:
            return check(box["ok"])
        except (_RefusError, ConversationError) as exc:
            self.notes.append(f"{nom} : sortie refusée ({exc}), je reprends sans modèle")
            return repli()

    # ── les gestes, validés ────────────────────────────────────────────────

    def formuler_question(self, entree: EntreeQuestion) -> str:
        return self._appel(
            "formuler_question", lambda m: m.formuler_question(entree),
            lambda out: clean_text(out, MAX_QUESTION_CHARS, what="question"), lambda: self.repli.formuler_question(entree))

    def classer_reponse(self, entree: EntreeClassement) -> str:
        def check(out: Any) -> str:
            if out not in CLASSES:
                raise _RefusError(f"classe inconnue {str(out)[:30]!r}")
            return str(out)

        return self._appel("classer_reponse", lambda m: m.classer_reponse(entree), check, lambda: self.repli.classer_reponse(entree))

    def extraire_elements(self, entree: EntreeExtraction) -> list[ElementExtrait]:
        def check(out: Any) -> list[ElementExtrait]:
            if not isinstance(out, list | tuple):
                raise _RefusError("une liste est attendue")
            kept: list[ElementExtrait] = []
            for item in list(out)[:MAX_ELEMENTS_PAR_TOUR]:
                if not isinstance(item, ElementExtrait) or item.rubrique not in entree.rubriques or item.rubrique not in RUBRIQUE_KEYS:
                    self.notes.append("extraire_elements : un élément hors cadre (rubrique inconnue) est refusé")
                    continue
                try:
                    kept.append(ElementExtrait(clean_text(item.texte, MAX_ELEMENT_CHARS, what="élément"), item.rubrique))
                except ConversationError as exc:
                    self.notes.append(f"extraire_elements : un élément est refusé ({exc})")
            if out and not kept:
                raise _RefusError("aucun élément valide")
            return kept

        return self._appel("extraire_elements", lambda m: m.extraire_elements(entree), check, lambda: self.repli.extraire_elements(entree))

    def reformuler(self, entree: EntreeReformulation) -> str:
        return self._appel(
            "reformuler", lambda m: m.reformuler(entree),
            lambda out: clean_text(out, MAX_REFORMULATION, what="reformulation"), lambda: self.repli.reformuler(entree))

    def proposer_pistes(self, entree: EntreePistes) -> list[str]:
        def check(out: Any) -> list[str]:
            if not isinstance(out, list | tuple):
                raise _RefusError("une liste est attendue")
            return [clean_text(p, MAX_PISTE_CHARS, what="piste") for p in list(out)[:MAX_PISTES]]

        return self._appel("proposer_pistes", lambda m: m.proposer_pistes(entree), check, lambda: self.repli.proposer_pistes(entree))

    def repondre_question(self, texte: str, pourquoi: str) -> str:
        return self._appel(
            "repondre_question", lambda m: m.repondre_question(texte, pourquoi),
            lambda out: clean_text(out, MAX_REFORMULATION, what="réponse"), lambda: self.repli.repondre_question(texte, pourquoi))

    def est_tour_lourd(self, texte: str, etape: str) -> bool:
        def check(out: Any) -> bool:
            if not isinstance(out, bool):
                raise _RefusError("un booléen est attendu")
            return out

        return self._appel("est_tour_lourd", lambda m: m.est_tour_lourd(texte, etape), check, lambda: False)

    def limites(self) -> tuple[str, ...]:
        def check(out: Any) -> tuple[str, ...]:
            if not isinstance(out, list | tuple):
                raise _RefusError("une liste est attendue")
            return tuple(clean_text(x, 300, what="limite") for x in list(out)[:8])

        return self._appel("limites", lambda m: m.limites(), check, lambda: self.repli.limites())
