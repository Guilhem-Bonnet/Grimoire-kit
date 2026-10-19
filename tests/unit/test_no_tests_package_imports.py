"""Aucun test n'importe ``tests.…`` : ``tests/`` n'est pas un paquet en CI.

``tests/unit`` a un ``__init__.py`` mais ``tests/`` n'en a pas : ``from tests.unit.x
import y`` ne marche que si le répertoire courant est sur ``sys.path`` (en local),
et casse la collecte de toute la suite en CI (``ModuleNotFoundError: No module
named 'tests'``). Le défaut a fait tomber la CI de trois PR (#747, #759, #794).
Importer un voisin : ``from .x import y`` depuis ``tests/unit``, ou un chargement
par chemin (``importlib``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]


def _imports_tests_package(source: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        if any(name == "tests" or name.startswith("tests.") for name in names):
            lines.append(node.lineno)
    return lines


def test_no_test_imports_the_tests_package() -> None:
    offenders = [
        f"{path.relative_to(_TESTS.parent).as_posix()}:{line}"
        for path in sorted(_TESTS.rglob("*.py"))
        for line in _imports_tests_package(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"import du paquet `tests` (cassé en CI), utiliser un import relatif : {offenders}"


def test_the_guard_catches_both_forms() -> None:
    assert _imports_tests_package("from tests.unit.x import y\n") == [1]
    assert _imports_tests_package("import tests.unit.x\n") == [1]
    assert _imports_tests_package("from .x import y\nfrom testsuite import z\n") == []
