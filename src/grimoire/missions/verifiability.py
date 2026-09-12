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
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "JUDGE_PATTERNS",
    "MECHANICAL_PATTERNS",
    "Verifiability",
    "as_dict",
    "classify",
    "classify_criteria",
    "explain",
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
            "commande_code_sortie",
            r"\b(code de sortie|exit code|exit status|retourne 0|"
            r"returns? exit code|commande r[ée]ussit)\b",
        ),
        (
            "build_ci",
            r"\b(build (?:passe|vert|verte|passes|green)|ci (?:verte|green|passes?)|"
            r"pipeline (?:vert|verte|green)|build r[ée]ussi)\b",
        ),
        (
            "fichier_attendu",
            r"(fichier\b[\s\S]*\bexiste\b|existence du fichier|file\s+exists|"
            r"\bcontenu (?:exact|pr[ée]cis)\b)",
        ),
    )
)

#: Vocabulaire d'une revue : le verdict passe par une personne, ou un agent
#: qui en tient lieu — jamais par un programme seul, quel que soit ce que le
#: reste du critère mentionne par ailleurs (d'où sa priorité de lecture dans
#: :func:`_classer_critere`).
JUDGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = _motifs(
    (
        ("revue", r"\b(revue|review|relecture|peer review|code review)\b"),
        ("juge", r"\b(juge|jugement|judge)\b"),
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
            r"\b(valid[ée]e? par|validation par|approuv[ée]e? par|approval by|"
            r"approved by|reviewed by|sign[- ]?off|sign[ée]e? par)\b",
        ),
    )
)


def _classer_critere(texte: str) -> _Verdict:
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
    for motif, motif_re in MECHANICAL_PATTERNS:
        if motif_re.search(texte):
            return _Verdict(_Categorie.MECANIQUE, motif)
    return _Verdict(_Categorie.AMBIGU, None)


def _criteres(task: MissionTask) -> tuple[str, ...]:
    """Les deux champs qui portent un critère — jamais l'un sans l'autre.

    ``acceptance`` et ``expected_evidence`` disent la même chose à deux
    endroits du schéma (BM board vs. mission ledger) ; ignorer l'un des
    deux ferait passer en V0 une tâche dont le seul critère sérieux vit
    dans le champ non lu.
    """
    return tuple(getattr(task, "acceptance", ()) or ()) + tuple(getattr(task, "expected_evidence", ()) or ())


def classify_criteria(criteria: Sequence[str]) -> Verifiability:
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
    """
    verdicts = [_classer_critere(c) for c in criteria]
    if not verdicts:
        return Verifiability.V2
    if any(v.categorie is _Categorie.AMBIGU for v in verdicts):
        return Verifiability.V2
    if any(v.categorie is _Categorie.JUGE for v in verdicts):
        return Verifiability.V1
    return Verifiability.V0


def classify(task: MissionTask) -> Verifiability:
    """Dérive la classe — jamais déclarée, toujours recalculée depuis le texte.

    Simple appel à :func:`classify_criteria` sur les critères du ``MissionTask``
    (``acceptance`` + ``expected_evidence``) — voir son docstring pour la règle.
    """
    return classify_criteria(_criteres(task))


def explain(task: MissionTask) -> list[tuple[str, str | None]]:
    """Le détail critère par critère — pour que le POURQUOI de la classe se voie.

    Retourne, dans l'ordre d'``acceptance`` puis ``expected_evidence``, le
    couple (texte du critère, motif reconnu). Le motif est ``None`` quand
    le critère est celui qui a fait monter la classe : c'est précisément
    celui-là que l'utilisateur doit regarder pour la faire descendre.
    """
    return [(c, _classer_critere(c).motif) for c in _criteres(task)]


def as_dict(task: MissionTask) -> dict[str, Any]:
    """Sérialise classe et explication dans un format unique.

    Le board (YAML), ``grimoire task show`` (texte et JSON) et l'outil MCP
    ``task_show`` ont chacun leur propre encodage de sortie ; sans ce point
    de passage unique, l'un des trois aurait fini par nommer la classe
    autrement ou par oublier un critère dans son explication.
    """
    klass = classify(task)
    return {
        "class": klass.value,
        "explanation": klass.explanation,
        "criteria": [{"criterion": critere, "pattern": motif} for critere, motif in explain(task)],
    }
