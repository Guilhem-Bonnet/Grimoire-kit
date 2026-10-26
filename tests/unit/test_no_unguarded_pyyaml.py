"""PyYAML n'est pas une dépendance du kit : tout ``import yaml`` de ``src/`` a un repli sur ``ImportError``.

La CI installe les dépendances de développement, PyYAML compris : un ``import yaml`` nu y passe et
casse chez l'utilisateur. Vu au test de fumée de la publication 3.66.0 (``missions/finition.py``,
« No module named 'yaml' ») : le paquet n'est pas parti sur PyPI.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "grimoire"
_GUARDS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _imports_yaml(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == "yaml" or alias.name.startswith("yaml.") for alias in node.names)
    return isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "yaml"


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in _GUARDS for n in names)


def _unguarded(tree: ast.AST) -> list[int]:
    found: list[int] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        if _imports_yaml(node) and not guarded:
            found.append(node.lineno)  # type: ignore[attr-defined]
        if isinstance(node, ast.Try):
            protects = any(_catches_import_error(h) for h in node.handlers)
            for child in node.body:
                visit(child, guarded or protects)
            for child in (*node.handlers, *node.orelse, *node.finalbody):
                visit(child, guarded)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return found


def test_aucun_import_yaml_sans_repli_dans_src() -> None:
    offenders = [
        f"{path.relative_to(SRC.parent)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in _unguarded(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"import yaml sans repli sur ImportError (utiliser ruamel.yaml) : {offenders}"


def test_la_garde_voit_un_import_nu_et_accepte_un_repli() -> None:
    nu = "def f():\n    import yaml\n"
    protege = "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
    mauvais_repli = "try:\n    import yaml\nexcept ValueError:\n    pass\n"
    assert _unguarded(ast.parse(nu)) == [2]
    assert _unguarded(ast.parse(protege)) == []
    assert _unguarded(ast.parse(mauvais_repli)) == [2]
