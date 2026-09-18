"""« Aucun test collecté » n'est pas « tests rouges » (issue #582 lot I).

L'analyse du lot H (``_scratch/bench-h2/analyse-go-js-kit-gov.md``, 30 runs
Go/JavaScript gouvernés) a montré que le seul check qui échoue jamais sur ces
runs est ``acceptance.test_run_failed`` — et que, sur les 15 runs JavaScript,
la cause n'est jamais un bug de code : le dépôt de tâche ne contient aucun
fichier ``*.spec.js`` (masquage volontaire des tests cachés du harnais), donc
``npm test`` répond systématiquement « No tests found », code de sortie 1.
Aucune action légitime de l'agent ne peut rendre ce gate vert : écrire le
fichier de test caché est hors mandat, et ``--passWithNoTests`` truquerait le
gate sans rien prouver. Le même sillon existe pour ``go test`` sans fichier
``_test.go``, ``cargo test`` sans cible testée, ``dotnet test`` sans projet de
test, etc.

Ce module donne un nom à ce cas précis — « rien collecté », ni vert ni rouge —
pour que :func:`grimoire.core.standard_checks.acceptance_test_run.
record_acceptance_test_run` puisse l'enregistrer comme tel (``ok: null``,
``collected: 0``) plutôt que comme un échec, et que le gate
(:mod:`grimoire.core.standard_checks.gate_test_run`) réponde par un
avertissement plutôt qu'une erreur qui ne peut jamais être corrigée par le
travail demandé.

Détection **par la sortie**, jamais par le nom de la commande : ``npm test``
ne dit nulle part qu'il invoque ``jest`` en dessous, seule sa sortie le
révèle. La commande n'intervient que pour le cas pytest (code de sortie 5 est
spécifique à ce runner ; un autre outil pourrait sortir en 5 pour une tout
autre raison).
"""

from __future__ import annotations

import re

__all__ = [
    "NO_TEST_JUSTIFICATION_PATTERN",
    "classify_no_tests_collected",
    "has_no_test_justification",
]

#: pytest : code de sortie 5 = « no tests ran » (documenté par pytest
#: lui-même), toujours accompagné du message dans la sortie — le code seul
#: suffit, mais rien n'interdit de vérifier les deux.
_PYTEST_NO_TESTS_EXIT_CODE = 5

#: Une ligne "running N tests" par cible ``cargo test`` — "rien collecté"
#: seulement si *toutes* les cibles exécutées annoncent zéro, jamais si une
#: seule cible en a trouvé (mélange cibles vides/non vides = suite réelle).
_CARGO_RUNNING_TESTS_RE = re.compile(r"running (\d+) tests?\b", re.IGNORECASE)

#: mocha : « 0 passing » sans jamais mentionner « failing » — un « 0 passing »
#: à côté d'un « N failing » est un rouge réel (des tests existent et ont
#: échoué au chargement), pas une suite absente.
_MOCHA_ZERO_PASSING_RE = re.compile(r"\b0 passing\b", re.IGNORECASE)
_MOCHA_FAILING_RE = re.compile(r"\bfailing\b", re.IGNORECASE)

#: Marqueurs de sortie qui, à eux seuls, signent « rien collecté » — un par
#: runner, tous insensibles à la casse. jest/vitest partagent le même
#: message ; go test a deux graphies selon la version.
_OUTPUT_MARKERS: tuple[str, ...] = (
    "no tests ran",  # pytest, en clair, en plus de son code de sortie 5
    "no tests found",  # jest / vitest
    "no test files",  # go test (Go récents)
    "[no test files]",  # go test (graphie historique)
    "no test is available",  # dotnet test
)


def classify_no_tests_collected(*, command: str, exit_code: int | None, output_excerpt: str) -> bool:
    """True si *output_excerpt* dit « aucun test collecté », jamais « tests rouges ».

    *command* n'intervient que pour discriminer le code de sortie 5 de
    pytest (spécifique à ce runner) ; tout le reste de la détection lit la
    sortie, la seule chose qu'un ``npm test``/``go test``/``cargo test``
    partage réellement entre projets — voir le docstring du module.
    """
    lowered = output_excerpt.lower()
    if exit_code == _PYTEST_NO_TESTS_EXIT_CODE and "pytest" in command.lower():
        return True
    if any(marker in lowered for marker in _OUTPUT_MARKERS):
        return True
    if _MOCHA_ZERO_PASSING_RE.search(lowered) and not _MOCHA_FAILING_RE.search(lowered):
        return True
    counts = [int(n) for n in _CARGO_RUNNING_TESTS_RE.findall(lowered)]
    return bool(counts) and all(n == 0 for n in counts)


#: Ligne reconnue dans ``acceptance-record.md`` pour justifier une absence de
#: test sans qu'un test existe à côté — n'importe où dans le fichier (pas
#: seulement une cellule de tableau) : c'est une déclaration de l'agent, pas
#: une donnée structurée de plus.
NO_TEST_JUSTIFICATION_PATTERN = re.compile(r"(?i)sans test\s*:\s*\S")


def has_no_test_justification(acceptance_record_text: str) -> bool:
    """True quand *acceptance_record_text* porte une ligne ``sans test : <raison>``."""
    return bool(NO_TEST_JUSTIFICATION_PATTERN.search(acceptance_record_text))
