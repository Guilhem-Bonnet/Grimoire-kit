"""Aucun import POSIX-only non protégé dans la zone gelée (issue #37).

Contexte : l'issue #33 a été ouverte par un utilisateur Windows sur un
``import fcntl`` fatal dans ``framework/tools/``, jamais exercé en CI parce
que le job de tests framework ne tourne que sur ubuntu.

La réponse évidente — ajouter une matrice Windows — investit du temps de CI
dans une zone que ``framework/FREEZE.md`` fait justement décroître, et qui est
passée de 108 à 53 outils. Ce test attrape la même classe de défaut sans
runner Windows : il vérifie que tout import réservé à POSIX est enveloppé
d'un repli, ce qui est la seule chose que la matrice aurait démontrée.

Il vaut aussi pour ``src/`` — le SDK, lui, est déjà couvert par une matrice
Windows, mais rien n'empêche d'y introduire le même défaut entre deux runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: Modules absents de l'interpréteur Windows. Les importer sans repli rend le
#: fichier inimportable, donc l'outil inutilisable — pas dégradé, mort.
POSIX_ONLY = frozenset({"fcntl", "grp", "pwd", "termios", "resource", "syslog"})

ROOT = Path(__file__).resolve().parents[2]
ZONES = ("framework/tools", "src/grimoire")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for zone in ZONES:
        base = ROOT / zone
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def _unguarded_posix_imports(source: str) -> list[str]:
    """Modules POSIX-only importés hors d'un ``try`` — donc sans repli.

    Un import protégé vit dans un ``try/except ImportError``. On ne cherche
    pas à valider le repli lui-même : la seule chose qui casse à coup sûr est
    l'import nu au niveau du module.
    """
    tree = ast.parse(source)
    guarded: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                guarded.add(child)

    found: list[str] = []
    for node in ast.walk(tree):
        if node in guarded:
            continue
        if isinstance(node, ast.Import):
            found.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module.split(".")[0])
    return sorted({m for m in found if m in POSIX_ONLY})


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_pas_d_import_posix_only_sans_repli(path: Path) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - fichier illisible
        pytest.skip(f"illisible : {path}")
    try:
        unguarded = _unguarded_posix_imports(source)
    except SyntaxError:  # pragma: no cover - fixture volontairement invalide
        pytest.skip(f"non parsable : {path}")
    assert not unguarded, (
        f"{path.relative_to(ROOT)} importe {', '.join(unguarded)} sans repli — "
        f"le fichier devient inimportable sous Windows. Envelopper dans un "
        f"try/except ImportError avec une alternative (voir "
        f"framework/tools/stigmergy.py, qui bascule sur msvcrt)."
    )


def test_le_detecteur_voit_un_import_nu() -> None:
    """Sans ce contrôle, un test toujours vert passerait pour une garantie."""
    assert _unguarded_posix_imports("import fcntl\n") == ["fcntl"]
    assert _unguarded_posix_imports("from pwd import getpwnam\n") == ["pwd"]


def test_le_detecteur_accepte_un_import_protege() -> None:
    protege = "try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\n"
    assert _unguarded_posix_imports(protege) == []
