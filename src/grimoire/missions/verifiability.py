"""Classe de vérifiabilité d'une tâche (issue #309, lot 1).

Une tâche peut porter des critères d'acceptation sans que personne ne sache,
avant de les lire un par un, si leur verdict tombera tout seul (un test
passe ou échoue) ou s'il faudra qu'une personne — ou un agent délégué — le
rende. Le board affichait les critères en texte libre ; rien n'en tirait la
conséquence qui compte pour router une tâche : peut-on clore sans y regarder,
ou faut-il une revue avant de faire confiance au vert ?

Ce module dérive cette classe depuis le texte des critères, sans jamais la
faire déclarer à la main — une classe déclarée peut mentir par optimisme,
une classe dérivée ne peut que refléter ce que le texte dit vraiment :

- **V0** — tous les critères (acceptation + preuve attendue) nomment un
  verdict qu'un programme rend seul : test, lint/typage, schéma, gate du
  standard, code de sortie d'une commande, build/CI, fichier attendu.
- **V1** — au moins un critère nomme une revue, un jugement ou une
  validation par une personne ou un agent, et aucun critère ne tombe dans
  la catégorie suivante.
- **V2** — au moins un critère ne nomme ni verdict mécanique ni revue
  reconnue (formulation vague, du type « le code est propre »), ou la
  tâche n'a aucun critère du tout.

La règle qui gouverne tout le reste : **un faux V0 est pire qu'un faux V2**.
Un critère ambigu fait donc toujours monter la classe, jamais redescendre —
c'est ce qui rend un V0 impossible à obtenir par accident de vocabulaire.
Le vocabulaire mécanique et le vocabulaire de revue sont ci-dessous des
constantes documentées, en français et en anglais, précisément pour que
cette montée reste un motif nommé plutôt qu'un ``if`` de plus quelque part.
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "JUDGE_PATTERNS",
    "MECHANICAL_PATTERNS",
    "PERSON_VALIDATION_PATTERNS",
    "VERDICT_MARKER",
    "CriterionAdvice",
    "Verifiability",
    "as_dict",
    "awaits_person_validation",
    "classify",
    "classify_criteria",
    "diagnose",
    "explain",
    "is_bare_command",
    "reason_for_criteria",
    "requires_paid_spend",
]


class Verifiability(StrEnum):
    """Ce que la tâche exige pour qu'on croie son verdict."""

    V0 = "V0"
    V1 = "V1"
    V2 = "V2"

    @property
    def explanation(self) -> str:
        return _EXPLANATIONS[self]


_EXPLANATIONS: dict[Verifiability, str] = {
    Verifiability.V0: (
        "Tous les critères nomment un verdict qu'un programme rend seul "
        "(test, lint, schéma, gate, code de sortie, build/CI, fichier attendu)."
    ),
    Verifiability.V1: (
        "Au moins un critère nomme une revue, un jugement ou une validation "
        "par une personne ou un agent, et aucun critère ne reste ambigu."
    ),
    Verifiability.V2: (
        "Au moins un critère ne nomme ni verdict mécanique reconnu ni revue "
        "reconnue — ou la tâche n'a aucun critère : sa vérifiabilité reste "
        "à démontrer."
    ),
}


class _Categorie(Enum):
    """Catégorie interne d'UN critère — jamais exposée telle quelle.

    ``AMBIGU`` est la valeur par défaut : un critère n'est mécanique ou
    revu que si son texte le dit avec un mot du vocabulaire ci-dessous.
    Le silence ne vaut jamais confiance.
    """

    MECANIQUE = "mecanique"
    JUGE = "juge"
    AMBIGU = "ambigu"


@dataclass(frozen=True, slots=True)
class _Verdict:
    categorie: _Categorie
    motif: str | None


def _motifs(paires: tuple[tuple[str, str], ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((motif, re.compile(pattern, re.IGNORECASE)) for motif, pattern in paires)


#: Qualificatif admis entre « fichier » et son nom (ensemble fermé).
_QUALIF_FICHIER = r"(?:attendu|g[ée]n[ée]r[ée]|produit|cr[ée][ée]|livr[ée]|de sortie)"

#: Un nom de fichier : un jeton, ou une chaîne citée (« dist/out.json »).
_NOM_FICHIER = r"(?:«[^»]*»|\"[^\"]*\"|'[^']*'|`[^`]*`|\S+)"

#: Vocabulaire d'un verdict qu'un programme rend seul, sans arbitrage humain.
#: Français et anglais côte à côte : une tâche peut être rédigée dans l'un
#: ou l'autre, et le classement ne doit pas dépendre de la langue choisie.
MECHANICAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = _motifs(
    (
        (
            "test",
            r"\b(pytest|tests?|suite(?: de tests?)?|test suite|tests? verts?|"
            r"suite passe|tests? passent|unit tests?|integration tests?|"
            r"couverture de tests?|test coverage|green tests?)\b",
        ),
        (
            "lint_typage",
            r"\b(ruff|mypy|lint(?:er|ing)?|flake8|pylint|type[- ]?check(?:ing)?|"
            r"typage)\b",
        ),
        (
            "schema_validation",
            # « validation » nu n'y figure pas : « validation fonctionnelle par
            # le PO » est un jugement humain, et le mot seul ne dit pas qui
            # rend le verdict. Seule la validation contre un schéma est
            # mécanique ; le reste tombe en ambigu, donc monte.
            r"\b(sch[ée]mas?|schema|valide selon le sch[ée]ma|validation du sch[ée]ma|"
            r"schema validation|json schema|pydantic)\b",
        ),
        (
            "gate",
            r"\b(grimoire standard gate|gate check|standard gate|gates?)\b",
        ),
        (
            # Le code de sortie est lui-même le verdict : il faut qu'il soit
            # nommé avec sa valeur (« retourne 0 », « code de sortie nul »).
            # « Le code de sortie est documenté » ne dit rien d'un verdict.
            "commande_code_sortie",
            r"\b(?:(?:retourne|returns?)\s+(?:(?:le\s+|the\s+)?(?:code de sortie|exit\s+(?:code|status))\s+)?(?:\d+|z[ée]ro)\b|"
            r"(?:code de sortie|exit\s+(?:code|status))\s+(?:(?:est|is|vaut|=|==)\s*)?(?:z[ée]ro|nul(?:le)?|\d+)\b|"
            r"commande r[ée]ussit)",
        ),
        (
            "build_ci",
            r"\b(build (?:passe|vert|verte|passes|green)|ci (?:verte|green|passes?)|"
            r"pipeline (?:vert|verte|green)|build r[ée]ussi)\b",
        ),
        (
            # Sujet direct de « existe » : « fichier docs/guide.md existe »,
            # « the config file exists ». Jamais `[\s\S]*` entre les deux :
            # « a file explains why the design exists » n'est pas un verdict
            # sur un fichier (W3-05, revue S1-01).
            "fichier_attendu",
            # Qualificatif et verbe en ensembles fermés : « fichier attendu X existe »,
            # « fichier X est présent / créé », jamais un mot libre entre les deux.
            rf"(\bfichier(?:\s+{_QUALIF_FICHIER})?(?:\s+(?:de\s+|nomm[ée]\s+)?{_NOM_FICHIER})?\s+"
            rf"(?:existe(?:\s+bien)?|est\s+(?:bien\s+)?(?:pr[ée]sente?|cr[ée][ée]?|g[ée]n[ée]r[ée]e?|produite?))\b|"
            rf"\bexistence du fichier\b|"
            rf"\bfile(?:\s+(?:named\s+|at\s+)?{_NOM_FICHIER})?\s+exists\b)",
        ),
        (
            "contenu_exact",
            r"\b(?:contenu (?:exact|pr[ée]cis)|exact content)\b[^.;]*\b"
            r"(?:correspond(?:ent)?|identique|[ée]gale?|matches|equals?|identical)\b",
        ),
    )
)

#: Les formulations qui attendent qu'une personne rende le verdict — partagées
#: par le vocabulaire de revue (V1) et par le diagnostic (:data:`PERSON_VALIDATION_PATTERNS`),
#: pour qu'elles ne puissent pas diverger.
_VALIDATION_HUMAINE = (
    r"valid[ée]e?s? par|validation par|relu(?:e)?s? par|accord (?:de|du)|"
    r"approuv[ée]e?s? par|approval by|approved by|reviewed by|sign[ée]e?s? par"
)

#: Vocabulaire d'une revue : le verdict passe par une personne, ou un agent
#: qui en tient lieu — jamais par un programme seul, quel que soit ce que le
#: reste du critère mentionne par ailleurs (d'où sa priorité de lecture dans
#: :func:`_classer_critere`).
JUDGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = _motifs(
    (
        ("revue", r"\b(revue|review|relecture|peer review|code review)\b"),
        ("juge", r"\b(juge|jugement|judge|judg(?:e)?ment)\b"),
        # Un test « manuel » ou une vérification « à la main » est un verdict
        # humain quel que soit le mot « test » à côté : sans ce motif, le
        # vocabulaire mécanique l'aurait avalé et produit un faux V0.
        (
            "verification_manuelle",
            r"\b(manuel(?:le)?s?|manual(?:ly)?|[àa] la main|by hand|"
            r"d[ée]mo(?:nstration)?|product owner|PO)\b",
        ),
        (
            "validation_humaine",
            rf"\b(?:{_VALIDATION_HUMAINE}|sign[- ]?off)\b",
        ),
    )
)


#: Un critère qui attend qu'une personne nommée rende le verdict (« validé par
#: le PO », « relu par le tech lead », « accord de la direction », « captures
#: validées par Alice »). Le classement le reconnaît comme une revue (V1) —
#: jamais comme un verdict mécanique, et jamais comme une attente à garder :
#: une personne nommée dans un critère ne se transforme pas en question posée
#: à cette personne. :func:`diagnose` le signale pour qu'on le reformule.
PERSON_VALIDATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = _motifs(
    (("validation_personne", rf"\b(?:{_VALIDATION_HUMAINE})\b"),)
)

#: Ce que dit un critère qui attend une personne : reformuler en vérification
#: reproductible ou en revue par le relecteur, jamais en question à cette personne.
_REFORMULER_ATTENTE_PERSONNE = (
    "attente d'une personne nommée : reformulez en vérification (test, capture comparée à une "
    "image de référence) ou en revue par le relecteur — la validation par une personne n'est pas "
    "un verdict reproductible"
)

#: Ce que dit un critère qui exige une dépense payante : le verdict doit rester
#: rejouable sans facture (doublure, fixture) ou la dépense se décide hors tâche.
_REFORMULER_DEPENSE = (
    "dépense payante : remplacez le banc ou l'API facturée par un contrôle gratuit (fixture, "
    "doublure) ou faites arbitrer la dépense hors de la tâche"
)

#: Reformulation proposée pour un critère ambigu — celui qui fait tomber en V2.
_REFORMULER_AMBIGU = (
    "reformulez en verdict mécanique (test, lint, code de sortie, fichier attendu) ou en revue "
    "par le relecteur, par exemple « pytest tests/unit/... passe » ou « `<commande>` retourne 0 »"
)

#: Les avertissements qu'un critère peut porter, avec leur consigne lisible.
_WARNING_MESSAGES: dict[str, str] = {
    "validation_personne": _REFORMULER_ATTENTE_PERSONNE,
    "depense_payante": _REFORMULER_DEPENSE,
}

#: Un critère qui attend la validation d'une personne est une revue (V1), jamais
#: un verdict mécanique : le vocabulaire de revue prime déjà dans
#: :func:`_classer_critere`. Cette fonction ne sert qu'au diagnostic.
_VERDICT_MECANIQUE = "mecanique"
_VERDICT_REVUE = "revue"
_VERDICT_AMBIGU = "ambigu"
_CATEGORIE_VERS_VERDICT: dict[_Categorie, str] = {
    _Categorie.MECANIQUE: _VERDICT_MECANIQUE,
    _Categorie.JUGE: _VERDICT_REVUE,
    _Categorie.AMBIGU: _VERDICT_AMBIGU,
}


@dataclass(frozen=True, slots=True)
class CriterionAdvice:
    """Ce qu'un critère vaut au classement, ce qu'il faut en faire, et ce qu'il coûte.

    *verdict* : ``mecanique``, ``revue`` ou ``ambigu`` (seul ``ambigu`` fait
    tomber la tâche en V2). *reformulation* : la consigne qui ferait descendre
    un critère ambigu — vide quand il n'y a rien à corriger. *warnings* : les
    catégories d'avertissement (``validation_personne``, ``depense_payante``),
    quel que soit le verdict.
    """

    criterion: str
    pattern: str | None
    verdict: str
    reformulation: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def warning_messages(self) -> tuple[str, ...]:
        """Les consignes lisibles des avertissements, dans l'ordre déclaré."""
        return tuple(_WARNING_MESSAGES[code] for code in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "pattern": self.pattern,
            "verdict": self.verdict,
            "reformulation": self.reformulation,
            "warnings": [{"code": code, "message": _WARNING_MESSAGES[code]} for code in self.warnings],
        }


def _plie(texte: str) -> str:
    """Le texte comparé par les motifs du pilote : sans accents ni casse, espaces réduits."""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_accents.lower().split())


def awaits_person_validation(texte: str) -> str | None:
    """Le motif d'attente d'une personne reconnu dans *texte*, ou ``None``."""
    for motif, motif_re in PERSON_VALIDATION_PATTERNS:
        if motif_re.search(texte):
            return motif
    return None


def requires_paid_spend(texte: str) -> str | None:
    """Le motif de dépense payante reconnu dans *texte*, ou ``None``.

    Réutilise la liste fermée du pilote (:data:`grimoire.missions.autopilot.RESERVED`,
    catégorie ``depense``) plutôt que d'en recopier une seconde, qui divergerait.
    Import paresseux : ``autopilot`` importe le ledger et le mandat, jamais ce module.
    """
    from grimoire.missions.autopilot import RESERVED

    if RESERVED["depense"].search(_plie(texte)):
        return "depense"
    return None


def diagnose(task: MissionTask) -> list[CriterionAdvice]:
    """Le diagnostic critère par critère : verdict, reformulation et avertissements.

    Même périmètre que :func:`explain` (``acceptance`` puis ``expected_evidence``)
    et même classement que :func:`classify` — jamais une seconde lecture du
    texte. C'est ce que ``task add``, ``task show`` et ``task classify
    --explain`` rendent : nommer le critère qui fait tomber en V2, proposer sa
    reformulation, et signaler une attente de personne ou une dépense payante.
    """
    commandes = _commandes(task)
    conseils: list[CriterionAdvice] = []
    for critere in _criteres(task):
        verdict = _classer_critere(critere, commandes)
        warnings: list[str] = []
        if awaits_person_validation(critere) is not None:
            warnings.append("validation_personne")
        if requires_paid_spend(critere) is not None:
            warnings.append("depense_payante")
        if verdict.categorie is _Categorie.AMBIGU:
            reformulation = _REFORMULER_AMBIGU
        elif "validation_personne" in warnings:
            reformulation = _REFORMULER_ATTENTE_PERSONNE
        else:
            reformulation = ""
        conseils.append(
            CriterionAdvice(
                criterion=critere,
                pattern=verdict.motif,
                verdict=_CATEGORIE_VERS_VERDICT[verdict.categorie],
                reformulation=reformulation,
                warnings=tuple(warnings),
            )
        )
    return conseils


#: Marqueur d'un verdict : un critère mécanique doit dire ce qui est attendu
#: du programme (passe, vert, aucune erreur, retourne 0...), pas seulement
#: nommer un outil. « Les tests sont lisibles » nomme des tests sans verdict :
#: c'est un jugement de qualité, donc ambigu, jamais V0 (W3-05, mesuré par
#: le jeu étiqueté de ``tests/unit/missions/verifiability_corpus.py``).
#:
#: Chaque marqueur est lié à ce qu'un programme constate. Les adjectifs de
#: jugement (« clean », « valide », « correspond », « seuil », « zéro »,
#: « aucun » tout nu) en sont exclus : « the test code is clean » ou « le
#: schéma est valide à mon avis » ne disent pas qui rend le verdict. « Clean »
#: ne vaut que collé à un outil de lint ; « valide » que comme verbe d'une
#: validation (« valide le fichier », « validates against ») ; « seuil » que
#: derrière un comparateur ; « aucun »/« zéro » que devant erreur, échec,
#: avertissement ou régression. L'existence (« existe », « exists ») n'est pas
#: un marqueur général : elle ne compte que dans le motif ``fichier_attendu``,
#: où elle est le verdict (« tests exist » reste donc ambigu, comme
#: « des tests existent »).
_RESULTATS = r"(?:erreurs?|errors?|[ée]checs?|failures?|failing|avertissements?|warnings?|r[ée]gressions?|d[ée]fauts?)"
VERDICT_MARKER: re.Pattern[str] = re.compile(
    r"(?:\b(?:"
    # « passe » : sauf les locutions qui n'énoncent aucun verdict.
    r"pass(?:e|es|ent|ing|ed)?\b(?!\s+(?:muster|the\s+(?:bar|smell\s+test)|la\s+barre))|"
    r"verts?|vertes?|green|r[ée]ussi\w*|r[ée]ussit|succe(?:ed|eds|ss)|succ[èe]s|"
    rf"sans\s+{_RESULTATS}|no\s+{_RESULTATS}|aucune?s?\s+{_RESULTATS}|z[ée]ro\s+{_RESULTATS}|"
    r"gate check|grimoire standard gate|validation du sch[ée]ma|schema validation|"
    r"(?:lint\w*|ruff|mypy|flake8|pylint)\s+(?:(?:is|est|reports?)\s+)?clean|"
    r"valid(?:e|es|ent)\s+(?:selon|le|la|les|l['’]|un|une|ce|cette|ces|chaque|tous|toutes|contre)|"
    # « le schéma valide » (verbe) mais pas « le schéma est valide » (adjectif).
    r"(?<!\best\s)(?<!\bsont\s)(?<!\breste\s)(?<!\brestent\s)(?<!\bsemble\s)valid(?:e|ent)(?=\s*[.!;,]?\s*$)|"
    r"valid(?:ates?|ated|ating)|valid\s+(?:against|according|per|selon)|"
    r"(?:above|below|over|under|exceeds?|d[ée]passe|au[- ]dessus d[eu]|en[- ]dessous d[eu]|"
    r"sup[ée]rieure? [àa]|inf[ée]rieure? [àa])\s+(?:the\s+|le\s+|du\s+|d['’]un\s+)?(?:seuil|threshold|\d)"
    r")|[<>]=?\s*\d|[≥≤])",
    re.IGNORECASE,
)

#: Marqueur d'un verdict pour un critère qui CITE une commande déclarée (``--check``) : ceux de
#: :data:`VERDICT_MARKER`, plus ce que dit une commande qui ne teste pas (PR fusionnée, sortie 0).
#: Citer la commande dit sur QUOI porte le critère, pas ce qui en est attendu.
_VERDICT_COMMANDE: re.Pattern[str] = re.compile(
    VERDICT_MARKER.pattern + r"|\b(?:fusionn[ée]e?s?|merg[ée]e?s?|merged|"
    r"(?:sortie|exit(?:\s+(?:code|status))?)\s*(?:=\s*)?(?:0|z[ée]ro|nulle?)|"
    r"code de sortie\s+(?:0|z[ée]ro|nul(?:le)?))\b",
    re.IGNORECASE,
)

#: Outils dont l'invocation nue rend un code de sortie : lancer la commande
#: EST le verdict (docstring du module : « code de sortie d'une commande »).
#: Volontairement une liste fermée d'outils de vérification, pas « tout ce qui
#: ressemble à une commande ».
_COMMANDE_TETE = re.compile(
    r"^(?:\$\s*)?(?:(?:run|lancer|lance|ex[ée]cuter|execute)\s+)?"
    r"(?:(?:uv\s+run|python3?\s+-m|poetry\s+run|npx|pipx\s+run)\s+)?"
    r"(?:pytest|py\.test|mypy|ruff|flake8|pylint|tox|nox|pre-commit|"
    r"cargo\s+(?:test|clippy|check|build|fmt)|"
    r"(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|lint|build|typecheck)|"
    r"go\s+(?:test|vet|build)|make\s+(?:test|lint|check|ci|build)|"
    r"grimoire\s+standard\s+(?:gate|verify|audit))"
    r"(?=\s|$)",
    re.IGNORECASE,
)

#: Un argument de ligne de commande : option, chemin/sélecteur (``/ . = : * [``)
#: ou sous-commande connue. Un mot de prose (« est », « and », « great »)
#: n'en est pas un : la ligne retombe alors dans le vocabulaire ordinaire.
_COMMANDE_ARG = re.compile(
    r"^(?:-\S*|[\w.\-]*[/.=:*\[\]][\w./:=*\[\]\-,]*|check|tests?|src|run|build|lint|clippy|fmt|vet|ci|all|\d+)$",
    re.IGNORECASE,
)

#: Motifs dont la simple présence énonce déjà le verdict : ils n'ont pas besoin
#: d'un marqueur à côté.
_MOTIFS_AUTO_VERDICT = frozenset({"fichier_attendu", "commande_code_sortie", "contenu_exact"})


def _est_commande_nue(texte: str) -> bool:
    """Vrai si tout le critère est une ligne de commande d'un outil de vérification.

    ``pytest tests/unit -q`` ou ``uv run mypy --strict src/`` : le verdict est
    le code de sortie, sans prose de jugement. ``Test ; audit ...`` ou
    ``pytest est un bon outil`` n'en sont pas : le premier ne nomme aucune
    commande, le second contient un mot qui n'est pas un argument.
    """
    for segment in texte.replace("`", "").strip().rstrip(".").split("&&"):
        segment = segment.strip()
        tete = _COMMANDE_TETE.match(segment)
        if tete is None:
            return False
        if not all(_COMMANDE_ARG.match(jeton) for jeton in segment[tete.end() :].split()):
            return False
    return True


def is_bare_command(texte: str) -> bool:
    """Public (W6-07b) : le critère n'est qu'une commande d'outil de vérification — voir :func:`_est_commande_nue`."""
    return _est_commande_nue(texte)


#: Un jeton « fichier » : porte une extension (``tests/test_x.py``), ou un
#: sélecteur ``::``. Un dossier nu (``src/``) n'en est pas un : « le code de
#: src/ est propre » ne devient pas mécanique parce que ``ruff check src/``
#: est déclaré.
_JETON_FICHIER = re.compile(r"^[\w./\-]*[\w\-]\.\w+(?:::\S+)?$|^\S+::\S+$")

#: Un identifiant numérique (``763`` dans ``gh pr view 763``) ; le critère doit
#: le citer sous la forme ``#763`` — un « 3 » nu dans la prose ne compte pas.
_JETON_NUMERO = re.compile(r"^\d{2,}$")


def _normaliser(texte: str) -> str:
    return " ".join(texte.replace("`", "").split()).casefold()


def _cite(texte: str, jeton: str) -> bool:
    """Vrai si *jeton* figure dans *texte* comme mot entier (pas comme fragment d'un autre)."""
    return re.search(rf"(?<![\w./\-]){re.escape(jeton)}(?![\w\-]|\.\w)", texte) is not None


def _couvert_par_commande(texte: str, commandes: Sequence[str]) -> str | None:
    """Dit si une commande déclarée (``--check``) porte le critère — issue #876.

    Règle, déterministe et prudente : le critère est couvert quand il **cite**
    une commande déclarée, c'est-à-dire s'il contient, comme mots entiers,

    - la commande entière (espaces normalisés, casse ignorée) ; ou
    - un jeton fichier de la commande (extension ou sélecteur ``::``, par
      exemple ``tests/unit/test_x.py``) ; ou
    - un identifiant numérique de la commande (``763`` de ``gh pr view 763``)
      écrit ``#763`` dans le critère.

    Ni le nom d'un outil seul (« pytest »), ni un dossier nu (``src/``), ni
    un nombre sans ``#`` ne suffisent : ils reviennent dans trop de prose. Cette
    couverture ne relève QUE un critère ambigu — la revue (``JUDGE_PATTERNS``)
    garde la priorité, et un critère qui ne cite aucune commande garde sa
    classe. Retourne le jeton qui a fait la correspondance, ou ``None``.

    La citation est une condition nécessaire, jamais suffisante : l'appelant
    (:func:`_classer_critere`) exige en plus un marqueur de verdict (:data:`_VERDICT_COMMANDE`).
    """
    brut = " ".join(texte.replace("`", "").split())
    plat = _normaliser(texte)
    for cmd in commandes:
        entiere = _normaliser(cmd)
        if entiere and entiere in plat:
            return cmd.strip()
        try:
            jetons = shlex.split(cmd)
        except ValueError:
            jetons = cmd.split()
        for jeton in jetons:
            if jeton.startswith("-"):
                continue
            if _JETON_FICHIER.match(jeton) and _cite(plat, jeton.casefold()):
                return jeton
            if _JETON_NUMERO.match(jeton) and re.search(rf"#{jeton}\b", brut):
                return f"#{jeton}"
    return None


#: Une commande citée entre accents graves dans le texte d'un critère.
_ENTRE_ACCENTS = re.compile(r"`([^`]+)`")


def _commande_citee(texte: str, commandes: Sequence[str]) -> str | None:
    """La commande entre accents graves identique à une ``--check`` déclarée, ou ``None``.

    Règle de l'issue GAO-crit-res-dir-001 : citer entre accents graves la
    commande déclarée, exactement, dit que le verdict est son code de sortie —
    inutile d'ajouter « passe » ou « retourne 0 » à côté. La comparaison est
    exacte (espaces normalisés, casse ignorée) : une commande voisine ou un
    fragment de prose entre accents graves ne suffit pas.
    """
    if not commandes:
        return None
    citees = {" ".join(m.group(1).split()).casefold() for m in _ENTRE_ACCENTS.finditer(texte)}
    for commande in commandes:
        if " ".join(commande.split()).casefold() in citees:
            return commande.strip()
    return None


def _classer_critere(texte: str, commandes: Sequence[str] = ()) -> _Verdict:
    """Classe un critère isolé — la revue prime sur le mécanique.

    Un critère qui mentionne à la fois une suite de tests et une relecture
    (« revue du rapport de tests ») reste un jugement humain : c'est la
    personne qui arbitre, le test n'est qu'une pièce qu'elle regarde. Lire
    le vocabulaire de revue en premier évite qu'un mot mécanique n'efface
    ce fait.
    """
    for motif, motif_re in JUDGE_PATTERNS:
        if motif_re.search(texte):
            return _Verdict(_Categorie.JUGE, motif)
    if _est_commande_nue(texte):
        return _Verdict(_Categorie.MECANIQUE, "commande_nue")
    # Citer entre accents graves la commande déclarée, à l'identique, vaut verdict
    # mécanique : c'est le code de sortie de cette commande qui conclut.
    if _commande_citee(texte, commandes) is not None:
        return _Verdict(_Categorie.MECANIQUE, "commande_citee")
    for motif, motif_re in MECHANICAL_PATTERNS:
        if motif_re.search(texte) and (motif in _MOTIFS_AUTO_VERDICT or VERDICT_MARKER.search(texte)):
            return _Verdict(_Categorie.MECANIQUE, motif)
    # Citer la commande ne suffit pas : « tests/unit/test_x.py est bien conçu » cite un fichier
    # sans énoncer de verdict. Il faut aussi un marqueur de verdict (passe, vert, sortie 0...).
    if _VERDICT_COMMANDE.search(texte) and _couvert_par_commande(texte, commandes) is not None:
        return _Verdict(_Categorie.MECANIQUE, "commande_declaree")
    # Outil nommé sans verdict, ou rien de reconnu : le silence ne vaut pas confiance.
    return _Verdict(_Categorie.AMBIGU, None)


def _commandes(task: MissionTask) -> tuple[str, ...]:
    """Les commandes exécutables déclarées de la tâche (``task.checks``, issue #772)."""
    return tuple(getattr(c, "cmd", "") for c in (getattr(task, "checks", ()) or ()))


def _criteres(task: MissionTask) -> tuple[str, ...]:
    """Les critères d'une tâche : ``acceptance``, plus la preuve attendue qui en est un.

    ``acceptance`` et ``expected_evidence`` disent la même chose à deux
    endroits du schéma (BM board vs. mission ledger) ; ignorer l'un des
    deux ferait passer en V0 une tâche dont le seul critère sérieux vit
    dans le champ non lu. Une preuve attendue en prose (« x.json et x.md »)
    décrit ce qu'on produit, pas un verdict : non reconnue, elle est
    ignorée au lieu de faire monter la classe en V2. Reconnue (mécanique
    ou revue), elle compte comme avant.
    """
    acceptance = tuple(getattr(task, "acceptance", ()) or ())
    preuves = tuple(
        e
        for e in (getattr(task, "expected_evidence", ()) or ())
        if _classer_critere(e, _commandes(task)).categorie is not _Categorie.AMBIGU
    )
    return acceptance + preuves


def classify_criteria(criteria: Sequence[str], commands: Sequence[str] = ()) -> Verifiability:
    """Dérive la classe depuis du texte nu — le cœur de :func:`classify`, sans ``MissionTask``.

    Extrait pour l'issue #428 : le format de blueprint (``flows.blueprint_loader``)
    a besoin de la même dérivation *avant* qu'un ``MissionTask`` existe (au
    chargement du fichier, pas au dispatch), pour détecter un nœud classé V0
    dont le seul texte d'acceptance ne peut jamais être exécuté. Précédence :
    un seul critère ambigu suffit à faire V2, même si tous les autres sont
    mécaniques ou revus — c'est la garantie qu'un faux V0 est impossible par
    construction. Un juge ne fait V1 que si rien n'est tombé en V2. L'absence
    totale de critère est elle-même un V2 : rien de muet sur ses critères
    n'est vérifiable par rien.

    *commands* : les commandes déclarées (``--check``) ; un critère qui en cite
    une est mécanique — règle dans :func:`_couvert_par_commande` (issue #876).
    """
    verdicts = [_classer_critere(c, commands) for c in criteria]
    if not verdicts:
        return Verifiability.V2
    if any(v.categorie is _Categorie.AMBIGU for v in verdicts):
        return Verifiability.V2
    if any(v.categorie is _Categorie.JUGE for v in verdicts):
        return Verifiability.V1
    return Verifiability.V0


def reason_for_criteria(criteria: Sequence[str], commands: Sequence[str] = ()) -> str:
    """Dit POURQUOI la classe est celle-là, en citant le critère décisif.

    Pour V2, le premier critère sans verdict reconnu (ou l'absence de
    critère) ; pour V1, le premier critère de revue et son motif ; pour V0,
    le nombre de critères mécaniques. Une classe sans raison lisible ne
    permet pas de la faire descendre.
    """
    if not criteria:
        return "aucun critère déclaré : rien à vérifier mécaniquement"
    verdicts = [(c, _classer_critere(c, commands)) for c in criteria]
    for critere, v in verdicts:
        if v.categorie is _Categorie.AMBIGU:
            return f"critère sans verdict reconnu : {critere!r}"
    for critere, v in verdicts:
        if v.categorie is _Categorie.JUGE:
            return f"critère de revue ({v.motif}) : {critere!r}"
    return f"{len(verdicts)} critère(s) mécanique(s), chacun avec un verdict"


def _risque_accepte(task: MissionTask) -> bool:
    """Vrai si un ``accept_as_is`` de checkpoint a été inscrit sur cette tâche (W2-10)."""
    return bool(getattr(task, "accepted_risks", ()))


def _commande_declaree(task: MissionTask) -> bool:
    """Vrai si la tâche porte au moins une commande exécutable (``task.checks``, issue #772)."""
    return bool(getattr(task, "checks", ()))


def classify(task: MissionTask, *, commands_declared: bool = False) -> Verifiability:
    """Dérive la classe — jamais déclarée, toujours recalculée depuis le texte.

    Appel à :func:`classify_criteria` sur les critères du ``MissionTask``
    (``acceptance`` + ``expected_evidence``) — voir son docstring pour la règle.
    Une seule exception, W2-10 : une tâche V0 dont un checkpoint a été clos par
    ``accept_as_is`` (``task.accepted_risks``) remonte en V1 — un humain a
    accepté un risque en l'état, une relecture reste due. C'est ici, et nulle
    part ailleurs, que la remontée se décide : ``dispatch_advice``, l'outil MCP
    ``task_show``, ``task_claim``, ``grimoire task show``, la transition
    ``needs_verification`` de ``task dispatch`` et l'exécuteur de flow passent
    tous par cette fonction. Une V2 reste V2 ; le texte des critères n'est pas
    réécrit.

    Seconde exception, issue #772 : V0 est réservé aux tâches qui déclarent une
    commande exécutable (``task.checks``). « Tests verts » sans commande à
    lancer est une promesse que personne ne peut rejouer : la tâche remonte en
    V1 (donc ``sonnet``, jamais ``haiku``) et ``reason_for_task`` le dit.
    ``classify_criteria`` reste, elle, purement textuelle (blueprints).
    *commands_declared* : l'appelant détient lui-même une commande à lancer
    (le ``--check`` de ``task dispatch``) — elle vaut déclaration.
    """
    klass = classify_criteria(_criteres(task), _commandes(task))
    if klass is Verifiability.V0 and (_risque_accepte(task) or not (commands_declared or _commande_declaree(task))):
        return Verifiability.V1
    return klass


def explain(task: MissionTask) -> list[tuple[str, str | None]]:
    """Le détail critère par critère — pour que le POURQUOI de la classe se voie.

    Retourne, dans l'ordre d'``acceptance`` puis ``expected_evidence``, le
    couple (texte du critère, motif reconnu). Le motif est ``None`` quand
    le critère est celui qui a fait monter la classe : c'est précisément
    celui-là que l'utilisateur doit regarder pour la faire descendre.
    """
    return [(c, _classer_critere(c, _commandes(task)).motif) for c in _criteres(task)]


def as_dict(task: MissionTask) -> dict[str, Any]:
    """Sérialise classe et explication dans un format unique.

    Le board (YAML), ``grimoire task show`` (texte et JSON) et l'outil MCP
    ``task_show`` ont chacun leur propre encodage de sortie ; sans ce point
    de passage unique, l'un des trois aurait fini par nommer la classe
    autrement ou par oublier un critère dans son explication.
    """
    klass = classify(task)
    conseils = diagnose(task)
    data: dict[str, Any] = {
        "class": klass.value,
        "explanation": klass.explanation,
        "reason": reason_for_criteria(_criteres(task), _commandes(task)),
        "criteria": [{"criterion": critere, "pattern": motif} for critere, motif in explain(task)],
        # Ce qui fait tomber en V2, avec la reformulation qui le ferait descendre :
        # sans les nommer, la classe ne dit pas quoi corriger (GAO-crit-res-dir-001).
        "v2_causes": [
            {"criterion": conseil.criterion, "reformulation": conseil.reformulation}
            for conseil in conseils
            if conseil.verdict == _VERDICT_AMBIGU
        ],
        # Un avertissement ne change pas la classe : il signale un critère à
        # reformuler même quand il a un verdict reconnu (attente de personne, dépense).
        "warnings": [
            {
                "criterion": conseil.criterion,
                "code": code,
                "message": _WARNING_MESSAGES[code],
            }
            for conseil in conseils
            for code in conseil.warnings
        ],
    }
    textual = classify_criteria(_criteres(task), _commandes(task))
    if klass is not textual:
        if textual is Verifiability.V0 and not _commande_declaree(task):
            data["explanation"] = (
                "Remontée de V0 : les critères nomment un verdict mécanique mais aucune commande "
                "n'est déclarée (`checks`) — rien à exécuter, le verdict reste à rendre par une revue."
            )
            data["reason"] = "aucune commande déclarée (checks) : le verdict mécanique n'est pas exécutable"
            data["raised_by"] = "no_declared_command"
        else:
            data["explanation"] = (
                "Remontée de V0 : un risque a été accepté en l'état (accept_as_is) — une relecture reste due."
            )
            data["raised_by"] = "accepted_risk"
    return data
