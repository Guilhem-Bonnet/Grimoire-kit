"""Nature de la demande (RequestKind) — épique #1342, lot M2b.

Notion **distincte** de :class:`grimoire.missions.schemas.TaskType` (type d'exécution d'une tâche) :
la nature décrit ce que l'humain demande (un incident, une question, un nouveau projet…) et pilote
l'entretien de cadrage : questions propres, livrable attendu, recette de la MOA.

Le classement est déterministe (mots-clés français et anglais, aucun appel de modèle) ; il propose
une nature avec sa raison, l'orchestrateur ou l'humain la corrige (``--kind``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "KIND_PROFILES",
    "KindProfile",
    "RequestKind",
    "classify_request_kind",
    "parse_kind",
]


class RequestKind(StrEnum):
    NEW_PROJECT = "nouveau-projet"
    NEW_FEATURE = "nouvelle-fonctionnalite"
    EVOLUTION = "evolution"
    BUG = "anomalie"
    INCIDENT = "incident"
    INVESTIGATION = "investigation"
    ANALYSIS = "analyse"
    RESEARCH = "recherche"
    MAINTENANCE = "maintenance"
    MIGRATION = "migration"
    SECURITY = "securite"
    QUESTION = "question"


@dataclass(frozen=True, slots=True)
class KindProfile:
    """Ce que la nature fixe : libellé, questions propres, livrable, recette MOA, bornes d'entretien."""

    label: str
    questions: tuple[str, ...]
    deliverable: str
    acceptance: str
    #: ``False`` : aucune entretien (question). ``max_questions`` borne un incident.
    interview: bool = True
    max_questions: int | None = None
    #: Pas d'entretien bloquant : rétablir d'abord, cadrer après (incident).
    blocking: bool = True


KIND_PROFILES: dict[RequestKind, KindProfile] = {
    RequestKind.NEW_PROJECT: KindProfile(
        "Nouveau projet",
        (
            "Quelle est la vision : quel problème, pour qui ?",
            "Qui sont les utilisateurs ?",
            "Qui est la MOA nommée ?",
            "Quels sont les critères de réussite ?",
            "Quelles contraintes, quel budget, quels délais ?",
            "Quels sont les risques principaux ?",
        ),
        "cahier des charges, plan",
        "le cadrage couvre le besoin",
    ),
    RequestKind.NEW_FEATURE: KindProfile(
        "Nouvelle fonctionnalité",
        (
            "Qui s'en sert et pour quoi faire ?",
            "Qu'existe-t-il déjà de réutilisable ?",
            "Qu'est-ce qui est hors périmètre ?",
            "Quelle alternative plus simple suffirait ?",
        ),
        "exigences, tâches",
        "l'usage attendu fonctionne",
    ),
    RequestKind.EVOLUTION: KindProfile(
        "Évolution d'une fonction existante",
        (
            "Qu'est-ce qui change exactement ?",
            "Qui dépend de cette fonction ?",
            "Quel est le risque de régression ?",
            "Quelle compatibilité doit être préservée ?",
        ),
        "exigences, tâches",
        "le changement, sans régression",
    ),
    RequestKind.BUG: KindProfile(
        "Anomalie (bug)",
        (
            "Quel comportement attendu, quel comportement observé ?",
            "Comment le reproduire ?",
            "Depuis quand ?",
            "Quel est l'impact ?",
            "Existe-t-il un contournement ?",
        ),
        "correctif et test de non-régression",
        "le cas ne se reproduit plus",
    ),
    RequestKind.INCIDENT: KindProfile(
        "Incident en cours",
        (
            "Quel est l'impact et le périmètre touché, et qui est touché ?",
            "Depuis quand, et quel a été le dernier changement ?",
            "Que peut-on faire tout de suite pour rétablir le service ?",
        ),
        "service rétabli, puis post-mortem",
        "service rétabli, causes et actions validées",
        max_questions=3,
        blocking=False,
    ),
    RequestKind.INVESTIGATION: KindProfile(
        "Investigation / diagnostic",
        (
            "Quelle question faut-il résoudre ?",
            "Quelles hypothèses retient-on ?",
            "Quelles preuves attend-on ?",
            "Quel est le critère d'arrêt ?",
        ),
        "conclusion étayée par des preuves",
        "la question a une réponse prouvée",
    ),
    RequestKind.ANALYSIS: KindProfile(
        "Analyse / audit",
        (
            "Quelle décision l'analyse éclaire-t-elle ?",
            "Quel périmètre couvre-t-elle ?",
            "Selon quels critères ?",
            "Quel format de rendu est attendu ?",
        ),
        "rapport",
        "il permet de décider",
    ),
    RequestKind.RESEARCH: KindProfile(
        "Recherche / veille / comparatif",
        (
            "Quelle est la question ?",
            "Quels critères de choix ?",
            "Quelles sources sont acceptées ?",
            "Quelle durée alloue-t-on ?",
        ),
        "recommandation sourcée",
        "la recommandation est tranchée et sourcée",
    ),
    RequestKind.MAINTENANCE: KindProfile(
        "Maintenance / dette / nettoyage",
        (
            "Quelle est la motivation ?",
            "Quel risque cela comporte-t-il ?",
            "Quel périmètre exact ?",
            "Qu'est-ce qui ne doit pas bouger ?",
        ),
        "changement et preuve de non-régression",
        "comportement inchangé, dette réduite",
    ),
    RequestKind.MIGRATION: KindProfile(
        "Migration / montée de version",
        (
            "Quelles sont la source et la cible ?",
            "Quelles données sont concernées ?",
            "Quel retour arrière est prévu ?",
            "Quelle fenêtre de bascule ?",
        ),
        "plan et bascule",
        "bascule faite, retour arrière prouvé",
    ),
    RequestKind.SECURITY: KindProfile(
        "Sécurité",
        (
            "Quel est l'actif concerné ?",
            "Quelle est la menace ?",
            "Quelle est l'exposition ?",
            "Quelle urgence de divulgation ?",
        ),
        "correctif ou recommandation",
        "exposition fermée ou acceptée par la MOA",
    ),
    RequestKind.QUESTION: KindProfile("Question / explication", (), "réponse", "sans objet", interview=False),
}


def _fold(text: str) -> str:
    """Minuscules, sans accents, apostrophes typographiques unifiées."""
    text = unicodedata.normalize("NFKD", text.replace("’", "'"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alts) + r")", re.IGNORECASE)


#: Ordre = priorité : un incident l'emporte sur une anomalie, une anomalie sur une évolution…
_RULES: tuple[tuple[RequestKind, re.Pattern[str]], ...] = (
    (
        RequestKind.INCIDENT,
        _rx(
            r"ca plante en prod",
            r"en prod(?:uction)?\b.{0,30}\b(?:plante|down|tombe|hs|ko|cass)",
            r"incident",
            r"production (?:est |is )?(?:down|hs|ko|tombee)",
            r"panne",
            r"outage",
            r"is down\b",
            r"production (?:is )?down",
            r"site (?:est )?(?:down|tombe|hs)",
            r"service (?:est )?(?:down|indisponible|hs)",
            r"crash(?:e|es|ed)? (?:in|en) prod",
            r"urgence prod",
            r"prod (?:est )?(?:down|tombee|hs|ko)",
            r"on[- ]call",
            r"sev ?[012]\b",
            r"p[01]\b",
        ),
    ),
    (
        RequestKind.SECURITY,
        _rx(
            r"securit",
            r"vulnerabilit",
            r"\bcve\b",
            r"faille",
            r"\bxss\b",
            r"injection",
            r"exploit",
            r"security",
            r"leak(?:ed)? (?:of )?(?:secret|key|token|password)",
            r"fuite de (?:secret|cle|mot de passe|donnees)",
            r"secret expose",
            r"exposed secret",
            r"pentest",
            r"hardening",
            r"durcissement",
        ),
    ),
    (
        RequestKind.MIGRATION,
        _rx(
            r"migr(?:e|er|ation|ons|at)",
            r"montee de version",
            r"mise a (?:jour|niveau) majeure",
            r"upgrade (?:to|from)",
            r"passer de .{1,40} a ",
            r"bascul",
            r"porter vers",
            r"port(?:ing)? to",
            r"switch from",
            r"version majeure",
        ),
    ),
    (
        RequestKind.INVESTIGATION,
        _rx(
            r"investigu",
            r"diagnosti",
            r"enquet",
            r"investigat",
            r"debug(?:ger)? pourquoi",
            r"root cause",
            r"cause racine",
            r"comprendre pourquoi",
            r"find out why",
            r"figure out why",
            r"trouve pourquoi",
            r"trouver pourquoi",
            r"cherche la cause",
        ),
    ),
    (
        # Corriger la documentation n'est pas une anomalie du produit : c'est de l'entretien.
        RequestKind.MAINTENANCE,
        _rx(
            r"(?:corrig|fix|mettre a jour|update|reprendre|relire)\w*\b.{0,40}\b"
            r"(?:doc\b|docs\b|documentation|readme|changelog|commentaires?\b|typos?\b|coquilles?\b|faute de frappe)",
            r"typos? in",
        ),
    ),
    (
        RequestKind.BUG,
        _rx(
            r"corrig",
            r"correct(?:if|ion) d[eu]",
            r"repar",
            r"\bbug",
            r"anomalie",
            r"regress",
            r"ne marche (?:plus|pas)",
            r"ne fonctionne (?:plus|pas)",
            r"marche plus",
            r"plante",
            r"\bfix",
            r"broken",
            r"crash",
            r"doesn't work",
            r"does not work",
            r"not working",
            r"stopped working",
            r"erreur",
            r"\berror\b",
            r"exception",
            r"traceback",
            r"echoue",
            r"\bfails?\b",
            r"failing",
            r"affiche (?:mal|faux)",
            r"incorrect",
            r"wrong",
        ),
    ),
    (
        RequestKind.MAINTENANCE,
        _rx(
            r"nettoy",
            r"refactor",
            r"dette",
            r"maintenance",
            r"clean ?up",
            r"tech(?:nical)? debt",
            r"supprimer le code mort",
            r"dead code",
            r"depreci",
            r"renomm",
            r"rename",
            r"reorganis",
            r"simplifi",
            r"mettre a jour les dependances",
            r"bump dependenc",
            r"chore",
        ),
    ),
    (
        RequestKind.RESEARCH,
        _rx(
            r"compar",
            r"\bcompare",
            r"\bvs\b",
            r"versus",
            r"veille",
            r"benchmark",
            r"quelle? (?:est la )?meilleure?",
            r"quel (?:outil|framework|choix)",
            r"which (?:tool|library|framework|one)",
            r"state of the art",
            r"etat de l'art",
            r"research (?:on|about|into)",
            r"recherche (?:sur|d[eu]s?|comparati)",
            r"survey",
            r"alternatives? a",
            r"pros and cons",
            r"avantages et inconvenients",
            r"choisir entre",
            r"choose between",
            r"evaluer les options",
            r"evaluate options",
        ),
    ),
    (
        RequestKind.ANALYSIS,
        _rx(
            r"analys",
            r"audit",
            r"revue de",
            r"review of",
            r"etat des lieux",
            r"assess",
            r"evalu",
            r"diagnostic de l'existant",
            r"inventaire",
            r"rapport sur",
            r"report on",
            r"cartograph",
        ),
    ),
    (
        RequestKind.NEW_PROJECT,
        _rx(
            r"nouveau projet",
            r"new project",
            r"nouvelle application",
            r"new app\b",
            r"from scratch",
            r"de zero",
            r"demarrer un projet",
            r"start(?:ing)? a (?:new )?project",
            r"creer (?:un|une) (?:application|appli|site|service|produit|projet)",
            r"create an? (?:app|application|site|service|product|project)",
            r"build an? (?:app|application|site|service|product)",
            r"lancer (?:un|une) (?:produit|projet|service)",
            r"bootstrap",
        ),
    ),
    (
        RequestKind.EVOLUTION,
        _rx(
            r"ameliorer",
            r"improve",
            r"etendre",
            r"extend",
            r"modifier le comportement",
            r"changer le comportement",
            r"faire evoluer",
            r"evolution",
            r"enhance",
            r"update the",
            r"mettre a jour (?:le|la|l'|les)",
            r"adapter",
            r"rendre .{1,30} configurable",
            r"make .{1,30} configurable",
            r"change how",
            r"change the way",
            r"au lieu de",
            r"instead of",
            r"plutot que",
            r"rather than",
        ),
    ),
    (
        RequestKind.NEW_FEATURE,
        _rx(
            r"ajout",
            r"\badd\b",
            r"nouvelle fonctionnalite",
            r"new feature",
            r"implement",
            r"creer",
            r"\bcreate\b",
            r"permettre",
            r"allow (?:users|the)",
            r"support(?:er)? (?:de |du |des |le |la )?",
            r"je voudrais",
            r"i would like",
            r"i want",
            r"je veux",
            r"on pourrait",
            r"we could",
            r"feature",
            r"fonctionnalite",
            r"export",
            r"import",
        ),
    ),
)
_QUESTION_RX = _rx(
    r"pourquoi",
    r"comment (?:ca|fonctionne|marche|est|fait|faire|on|puis|peut)",
    r"c'est quoi",
    r"qu'est[- ]ce que?",
    r"qu'est[- ]ce qu",
    r"explique",
    r"expliquer",
    r"quelle? (?:est|sont)\b",
    r"quel(?:le)?s? sont",
    r"peux[- ]tu (?:m'expliquer|me dire)",
    r"\bwhy\b",
    r"\bhow (?:does|do|is|are|can|to)\b",
    r"\bwhat (?:is|are|does)\b",
    r"explain",
    r"\bwhere (?:is|are|does)\b",
    r"\bou (?:est|se trouve)\b",
    r"\bwhat's\b",
    r"\bdifference entre",
)


_LEADING_QUESTION_RX = re.compile(r"^\W*(?:pourquoi|comment|c'est quoi|qu'est|why|how|what|where|explique|explain)\b")
#: Verbe d'action : une demande interrogative (« Comment ajouter… ? ») ou préfixée (« incident: ajouter… »)
#: qui en porte un reste une demande de travail, classée par les règles.
_ACTION_RX = _rx(
    r"ajout", r"implement", r"creer", r"cree\b", r"construi", r"developp", r"mettre en place", r"mise en place",
    r"integrer", r"\badd\b", r"\badding\b", r"\bcreate\b", r"\bbuild\b", r"\bdevelop\b", r"set ?up\b",
    r"\bintegrate\b",
)
#: Étiquette en tête de demande (« incident: », « urgent : », « bug — ») : un mot suivi d'un séparateur.
_PREFIX_RX = re.compile(r"^\W*[a-z][\w-]{0,20}\s*[:\u2014-]\s+")


def parse_kind(value: str) -> RequestKind:
    """La nature nommée par l'humain : identifiant, ou libellé sans accent ; sinon ValueError."""
    key = _fold(value).strip().replace("_", "-").replace(" ", "-")
    for kind in RequestKind:
        if key in (kind.value, kind.name.lower().replace("_", "-")):
            return kind
    raise ValueError(f"nature inconnue : {value!r} — {', '.join(k.value for k in RequestKind)}")


def classify_request_kind(text: str) -> dict[str, Any]:
    """Propose la nature d'une demande et la raison du choix (déterministe, français et anglais).

    Une demande sans signal retombe sur « nouvelle fonctionnalité » avec une confiance faible ;
    un texte interrogatif sans verbe d'action est une question ; une étiquette de tête (« incident: »)
    ne l'emporte pas sur un verbe d'action.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("la demande est vide")
    folded = _fold(raw)
    action = _ACTION_RX.search(folded)
    prefix = _PREFIX_RX.match(folded)
    if prefix and action and action.start() >= prefix.end():
        folded = folded[prefix.end():]  # l'étiquette ne l'emporte pas sur le verbe d'action
    lead = None if action else _LEADING_QUESTION_RX.match(folded)
    for kind, rx in _RULES:
        if lead and kind is not RequestKind.INCIDENT:
            return _proposal(RequestKind.QUESTION, f"formulation interrogative « {lead.group(0).strip()} »", "haute")
        m = rx.search(folded)
        if m:
            return _proposal(kind, f"mot-clé « {m.group(0).strip()} »", "haute")
    m = _QUESTION_RX.search(folded)
    if m:
        return _proposal(RequestKind.QUESTION, f"formulation interrogative « {m.group(0).strip()} »", "haute")
    if folded.rstrip().endswith("?"):
        return _proposal(RequestKind.QUESTION, "demande formulée comme une question", "moyenne")
    return _proposal(RequestKind.NEW_FEATURE, "aucun signal net : nature par défaut, à confirmer", "faible")


def _proposal(kind: RequestKind, reason: str, confidence: str) -> dict[str, Any]:
    profile = KIND_PROFILES[kind]
    return {
        "kind": kind.value,
        "label": profile.label,
        "reason": reason,
        "confidence": confidence,
        "interview": profile.interview,
        "correct_with": "grimoire cadrage interview --kind <nature>",
    }
