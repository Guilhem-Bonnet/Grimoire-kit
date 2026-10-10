"""Vert vide : un exécuteur de tests qui a rendu 0 sans avoir exécuté aucun test (W1-04).

``_classify_unrunnable`` ne reconnaissait que le code de sortie 5 de pytest.
Or vitest, go, cargo, unittest ou mocha rendent 0 quand rien n'est collecté :
le check passait « vert » alors qu'aucun test n'avait tourné. Ce module lit la
sortie de l'exécuteur, quel que soit le code de sortie.

Règle commune à chaque exécuteur : le motif « rien exécuté » ne suffit pas,
car ces outils l'impriment aussi par paquet ou par binaire sans test au milieu
d'une exécution réelle (``go test ./...``, ``cargo test`` et ses doc-tests).
Le diagnostic n'est rendu que si aucune ligne de la même sortie ne prouve
qu'au moins un test a tourné. Une sortie sans motif reconnu rend ``None`` :
comportement inchangé pour une commande dont l'exécuteur est inconnu.
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


def classify_empty_green(output: str) -> str | None:
    """Diagnostic nommé si *output* décrit une exécution sans aucun test — ``None`` sinon."""
    if _NO_FILES_VITEST.search(output):
        return "vitest/jest : aucun fichier de test trouvé (« No test files found »)"
    if _NO_TESTS_JEST.search(output):
        return "jest : aucun test trouvé (« No tests found »)"
    if _GO_NO_TEST.search(output) and not _GO_RAN.search(output):
        return "go test : aucun paquet ne contient de test (« [no test files] »)"
    cargo = _CARGO_RESULT.findall(output)
    if cargo and all(int(passed) + int(failed) == 0 for passed, failed in cargo):
        return "cargo test : aucun test exécuté (« 0 passed » sur chaque binaire)"
    if _UNITTEST_ZERO.search(output):
        return "unittest : aucun test exécuté (« Ran 0 tests »)"
    if _PYTEST_NONE.search(output):
        return "pytest : aucun test exécuté (« no tests ran »)"
    mocha = _MOCHA_PASSING.findall(output)
    if mocha and all(int(n) == 0 for n in mocha) and "failing" not in output:
        return "mocha : aucun test exécuté (« 0 passing »)"
    return None
