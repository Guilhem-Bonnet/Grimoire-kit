"""Vert vide : un exécuteur de tests qui a rendu 0 sans avoir exécuté aucun test (W1-04).

``_classify_unrunnable`` ne reconnaissait que le code de sortie 5 de pytest.
Or vitest, go, cargo, unittest ou mocha rendent 0 quand rien n'est collecté :
le check passait « vert » alors qu'aucun test n'avait tourné. Ce module lit la
sortie de l'exécuteur, quel que soit le code de sortie.

Règle commune à chaque exécuteur : le motif « rien exécuté » ne suffit pas,
car ces outils l'impriment aussi par paquet ou par binaire sans test au milieu
d'une exécution réelle (``go test ./...``, ``cargo test`` et ses doc-tests).
Le diagnostic n'est rendu que si aucune ligne de la même sortie ne prouve
qu'au moins un test a tourné, **quel que soit l'outil qui l'imprime** : un
``pnpm -r`` mêle un paquet vide et un paquet testé, ``pytest && go test`` met
deux exécuteurs dans une sortie, ``go test -json`` enferme la ligne ``ok`` dans
un champ JSON, et un vrai rouge peut citer un motif dans l'identifiant d'un
test. Une sortie sans motif reconnu rend ``None`` : comportement inchangé pour
une commande dont l'exécuteur est inconnu.
"""

from __future__ import annotations

import re

_NO_FILES_VITEST = re.compile(r"No test files? found", re.IGNORECASE)
_NO_TESTS_JEST = re.compile(r"No tests found", re.IGNORECASE)
_GO_NO_TEST = re.compile(r"\[no test files\]")
_GO_RAN = re.compile(r"^(?:ok|FAIL)\s+\S+\s+\S+|^--- (?:PASS|FAIL)", re.MULTILINE)
_CARGO_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed")
_UNITTEST_ZERO = re.compile(r"^Ran 0 tests?\b", re.MULTILINE)
_PYTEST_NONE = re.compile(r"^(?:=+ )?no tests ran\b", re.MULTILINE | re.IGNORECASE)
_MOCHA_PASSING = re.compile(r"^\s*(\d+) passing\b", re.MULTILINE)

#: Preuves qu'au moins un test a tourné, une par exécuteur (compte non nul).
_PROOF_PATTERNS = (
    # pytest, vitest (« Tests  7 passed »), jest (« Tests: 1 failed, 2 passed »).
    re.compile(r"\b[1-9]\d* (?:passed|failed|errors?|xfailed|xpassed)\b"),
    # go : ligne de paquet ou de test, en texte.
    _GO_RAN,
    # go test -json : événement d'un test, ou ligne ``ok``/``FAIL`` dans un champ Output.
    re.compile(r'"Action":\s*"(?:pass|fail)"[^\n]*"Test"|"Test":[^\n]*"Action":\s*"(?:pass|fail)"'),
    re.compile(r'"Output":\s*"(?:ok|FAIL|--- (?:PASS|FAIL))\b'),
    # cargo : un binaire a compté au moins un test.
    re.compile(r"test result: \w+\. (?:[1-9]\d* passed|\d+ passed; [1-9]\d* failed)"),
    # unittest.
    re.compile(r"^Ran [1-9]\d* tests?\b", re.MULTILINE),
    # mocha.
    re.compile(r"^\s*[1-9]\d* (?:passing|failing)\b", re.MULTILINE),
)


def has_execution_proof(output: str) -> bool:
    """Vrai si *output* prouve qu'au moins un test a tourné, quel que soit l'exécuteur."""
    return any(pattern.search(output) for pattern in _PROOF_PATTERNS)


def classify_empty_green(output: str) -> str | None:
    """Diagnostic nommé si *output* décrit une exécution sans aucun test — ``None`` sinon.

    Toute preuve d'exécution (:func:`has_execution_proof`) l'emporte sur un motif
    « rien exécuté » : un test qui a tourné fait un vert ou un rouge, jamais un vide.
    """
    if has_execution_proof(output):
        return None
    if _NO_FILES_VITEST.search(output):
        return "vitest/jest : aucun fichier de test trouvé (« No test files found »)"
    if _NO_TESTS_JEST.search(output):
        return "jest : aucun test trouvé (« No tests found »)"
    if _GO_NO_TEST.search(output):
        return "go test : aucun paquet ne contient de test (« [no test files] »)"
    cargo = _CARGO_RESULT.findall(output)
    if cargo and all(int(passed) + int(failed) == 0 for passed, failed in cargo):
        return "cargo test : aucun test exécuté (« 0 passed » sur chaque binaire)"
    if _UNITTEST_ZERO.search(output):
        return "unittest : aucun test exécuté (« Ran 0 tests »)"
    if _PYTEST_NONE.search(output):
        return "pytest : aucun test exécuté (« no tests ran »)"
    mocha = _MOCHA_PASSING.findall(output)
    if mocha and all(int(n) == 0 for n in mocha):
        return "mocha : aucun test exécuté (« 0 passing »)"
    return None
