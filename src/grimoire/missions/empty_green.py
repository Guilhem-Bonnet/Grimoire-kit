"""Vert vide : un exécuteur de tests qui a rendu 0 sans avoir exécuté aucun test (W1-04).

``_classify_unrunnable`` ne reconnaissait que le code de sortie 5 de pytest.
Or vitest, go, cargo, unittest ou mocha rendent 0 quand rien n'est collecté :
le check passait « vert » alors qu'aucun test n'avait tourné. Ce module lit la
sortie de l'exécuteur, quel que soit le code de sortie.

Règle commune à chaque exécuteur : le motif « rien exécuté » ne suffit pas,
car ces outils l'impriment aussi par paquet ou par binaire sans test au milieu
d'une exécution réelle (``go test ./...``, ``cargo test`` et ses doc-tests).
Le diagnostic n'est rendu que si aucune ligne de la même sortie ne prouve
qu'au moins un test a tourné, **quel que soit l'outil qui l'imprime parmi ceux
que la liste couvre** (pytest, vitest, jest, go, cargo, unittest, mocha,
node:test et TAP, minitest, rspec, phpunit, dotnet) : un ``pnpm -r`` mêle un
paquet vide et un paquet testé, ``pytest && go test`` met deux exécuteurs dans
une sortie, ``go test -json`` enferme la ligne ``ok`` dans un champ JSON, et un
vrai rouge peut citer un motif dans l'identifiant d'un test. Les orchestrateurs
de monorepo (turbo, ``pnpm -r``, nx, lerna) préfixent chaque ligne
(``api:test: ``, ``packages/api test: ``) : les preuves ancrées en début de
ligne acceptent ce préfixe. Une sortie sans motif reconnu rend ``None`` :
comportement inchangé pour une commande dont l'exécuteur est inconnu, y compris
un rouge d'un exécuteur hors liste mêlé à un motif de vide d'un autre outil
(limite assumée, voir le CHANGELOG).
"""

from __future__ import annotations

import re

#: Préfixe de ligne optionnel des orchestrateurs de monorepo (``api:test: ``, ``packages/api test: ``,
#: ``[0] ``) : tout jusqu'au premier ``: `` ou ``] `` de la ligne.
_PFX = r"^(?:[^\n]*?[:\]] )?"

_NO_FILES_VITEST = re.compile(r"No test files? found", re.IGNORECASE)
_NO_TESTS_JEST = re.compile(r"No tests found", re.IGNORECASE)
_GO_NO_TEST = re.compile(r"\[no test files\]")
#: go a trouvé des fichiers de test mais n'a rien lancé (filtre ``-run`` sans correspondance,
#: fichiers sans fonction ``TestXxx``) : la ligne ``ok`` du paquet porte ``[no tests to run]``,
#: et ``-v`` l'annonce en amont par ``testing: warning: no tests to run``.
_GO_NO_TEST_TO_RUN = re.compile(r"\[no tests to run\]|testing: warning: no tests to run")
_GO_OK_VIDE = r"(?![^\n]*\[no tests to run\])"
_GO_RAN = re.compile(rf"{_PFX}(?:(?:ok|FAIL){_GO_OK_VIDE}\s+\S+\s+\S+|--- (?:PASS|FAIL))", re.MULTILINE)
#: Avec ``-v``, un paquet sans test imprime l'avertissement, ``PASS`` puis une ligne ``ok`` sans
#: suffixe : cette ligne n'est pas une preuve (texte, puis ``-json`` où chaque ligne est un événement).
_GO_VIDE_VERBOSE = re.compile(
    r"testing: warning: no tests to run\r?\n(?:(?:[^\n]*?[:\]] )?PASS\r?\n)?(?:[^\n]*?[:\]] )?ok\b[^\n]*\n?"
    r'|"Output":\s*"testing: warning: no tests to run\\n"[^\n]*\n'
    r'(?:[^\n]*"Output":\s*"PASS\\n"[^\n]*\n)?[^\n]*"Output":\s*"ok\b[^\n]*(?:\n|$)'
)
_CARGO_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed")
_UNITTEST_ZERO = re.compile(rf"{_PFX}Ran 0 tests?\b", re.MULTILINE)
_PYTEST_NONE = re.compile(r"^(?:=+ )?no tests ran\b", re.MULTILINE | re.IGNORECASE)
_MOCHA_PASSING = re.compile(rf"{_PFX}\s*(\d+) passing\b", re.MULTILINE)

#: Preuves qu'au moins un test a tourné, une par exécuteur (compte non nul).
_PROOF_PATTERNS = (
    # pytest, vitest (« Tests  7 passed »), jest (« Tests: 1 failed, 2 passed »).
    re.compile(r"\b[1-9]\d* (?:passed|failed|errors?|xfailed|xpassed)\b"),
    # go : ligne de paquet ou de test, en texte.
    _GO_RAN,
    # go test -json : événement d'un test, ou ligne ``ok``/``FAIL`` dans un champ Output.
    re.compile(r'"Action":\s*"(?:pass|fail)"[^\n]*"Test"|"Test":[^\n]*"Action":\s*"(?:pass|fail)"'),
    re.compile(rf'"Output":\s*"(?:ok|FAIL|--- (?:PASS|FAIL))\b{_GO_OK_VIDE}'),
    # cargo : un binaire a compté au moins un test.
    re.compile(r"test result: \w+\. (?:[1-9]\d* passed|\d+ passed; [1-9]\d* failed)"),
    # cargo : un binaire a démarré des tests, même s'il a avorté (SIGABRT, ``exit``) avant d'imprimer
    # son ``test result:`` ; ligne de test individuelle déjà rendue.
    re.compile(rf"{_PFX}running [1-9]\d* tests?\s*$", re.MULTILINE),
    re.compile(rf"{_PFX}test \S.* \.\.\. (?:ok|FAILED)\b", re.MULTILINE),
    # unittest.
    re.compile(rf"{_PFX}Ran [1-9]\d* tests?\b", re.MULTILINE),
    # mocha.
    re.compile(rf"{_PFX}\s*[1-9]\d* (?:passing|failing)\b", re.MULTILINE),
    # node:test (« ℹ tests 3 », « ℹ pass 3 », « ℹ fail 1 ») et TAP (« # pass 3 »).
    re.compile(rf"{_PFX}(?:ℹ|#) (?:tests|pass|fail) [1-9]\d*\s*$", re.MULTILINE),
    # minitest (« 3 runs, 5 assertions, ... »), rspec (« 3 examples, 0 failures »).
    re.compile(rf"{_PFX}[1-9]\d* runs?, \d+ assertions?", re.MULTILINE),
    re.compile(rf"{_PFX}[1-9]\d* examples?, \d+ failures?", re.MULTILINE),
    # phpunit (« OK (3 tests, 5 assertions) », « Tests: 3, Assertions: 5, Failures: 1 »).
    re.compile(rf"{_PFX}(?:OK \([1-9]\d* tests?,|Tests: [1-9]\d*,)", re.MULTILINE),
    # dotnet test (« Passed!  - Failed: 0, Passed: 3, Skipped: 0, Total: 3 »).
    re.compile(rf"{_PFX}(?:Passed|Failed)!\s+- Failed:[^\n]*Total:\s+[1-9]", re.MULTILINE),
)


def has_execution_proof(output: str) -> bool:
    """Vrai si *output* prouve qu'au moins un test a tourné, quel que soit l'exécuteur."""
    output = _GO_VIDE_VERBOSE.sub("", output)
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
    if _GO_NO_TEST_TO_RUN.search(output):
        return "go test : aucun test à lancer (« [no tests to run] »)"
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
