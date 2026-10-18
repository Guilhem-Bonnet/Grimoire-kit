#!/usr/bin/env python3
"""Garde de documentation — toute commande ``grimoire ...`` citée existe.

La documentation cite des commandes dans des spans de code et des blocs
clôturés. Une commande renommée ou supprimée laisse sa citation intacte : le
lecteur copie, et la CLI répond « No such command ». Les tests ne le voient
pas, la documentation n'étant pas du code.

Ce garde extrait chaque citation commençant par ``grimoire`` et la marche dans
l'arbre Typer réel : tant que le nœud courant est un groupe, le mot suivant
doit en être une sous-commande ; une fois sur une feuille, le reste est
arguments. Les mots s'arrêtent au premier jeton qui n'est pas un mot en
minuscules (option, placeholder, chemin, chiffre).

Périmètre : ``README.md``, ``README.fr.md``, ``CONTRIBUTING.md`` et tout
``docs/**/*.md`` dont l'audience n'est pas ``décision`` (voir
``check-doc-audience.py``) — un ADR, un plan ou une veille décrit un instant, et
cite légitimement une commande qui n'existe pas encore ou plus.

Usage::

    python scripts/check-doc-commands.py
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_DOCS = ("README.md", "README.fr.md", "CONTRIBUTING.md")
HISTORICAL_AUDIENCE = "décision"

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_COMMAND = re.compile(r"^grimoire((?: +[a-z][a-z0-9-]*)+)(?=$|\s)")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---", re.DOTALL)
_AUDIENCE = re.compile(r"(?m)^audience:[ \t]*(?P<value>[^\r\n#]*)")


def audience_of(text: str) -> str:
    front = _FRONT_MATTER.match(text)
    line = _AUDIENCE.search(front.group("body")) if front else None
    return line.group("value").strip().strip("'\"") if line else ""


def citations(text: str) -> Iterator[str]:
    """Les chaînes de code d'un document : spans inline et lignes de blocs clôturés."""
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            stripped = line.strip()
            yield stripped[2:] if stripped.startswith("$ ") else stripped
        else:
            for span in _CODE_SPAN.finditer(line):
                yield span.group(1).strip()


def _children(command: Any, ctx: Any) -> dict[str, Any] | None:
    list_commands = getattr(command, "list_commands", None)
    if list_commands is None:
        return None
    return {name: command.get_command(ctx, name) for name in list_commands(ctx)}


def load_root() -> tuple[Any, Any]:
    import typer

    from grimoire.cli.app import app

    # Pas d'`import click` : depuis typer 0.27 click n'est plus une dépendance
    # (le CI n'installe que les dépendances du noyau). La classe de contexte se
    # lit sur la commande elle-même, quelle que soit la bibliothèque qui l'a bâtie.
    root = typer.main.get_command(app)
    return root, root.context_class(root)


def unknown_command(citation: str, root: Any, root_ctx: Any) -> str | None:
    """Le préfixe de ``citation`` qui n'existe pas dans l'arbre, ou ``None`` si elle est valide."""
    match = _COMMAND.match(citation)
    if not match:
        return None
    words = match.group(1).split()
    command, ctx = root, root_ctx
    for index, word in enumerate(words):
        children = _children(command, ctx)
        if children is None:
            return None
        if word not in children or children[word] is None:
            return "grimoire " + " ".join(words[: index + 1])
        command = children[word]
        ctx = command.context_class(command, parent=ctx)
    return None


def in_scope(root: Path = ROOT) -> list[Path]:
    files = [root / name for name in TOP_LEVEL_DOCS if (root / name).is_file()]
    for path in sorted((root / "docs").rglob("*.md")):
        if audience_of(path.read_text(encoding="utf-8")) == HISTORICAL_AUDIENCE:
            continue
        files.append(path)
    return files


def problems(root: Path = ROOT) -> list[str]:
    command_root, ctx = load_root()
    found: list[str] = []
    for path in in_scope(root):
        reported: set[str] = set()
        for citation in citations(path.read_text(encoding="utf-8")):
            missing = unknown_command(citation, command_root, ctx)
            if missing and missing not in reported:
                reported.add(missing)
                found.append(f"{path.relative_to(root).as_posix()} : `{missing}` n'existe pas dans la CLI")
    return found


def main() -> int:
    found = problems()
    if found:
        print(f"{len(found)} commande(s) citée(s) en documentation absente(s) de la CLI :", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nCorriger la citation, ou classer le fichier `audience: décision` s'il décrit un instant révolu.",
            file=sys.stderr,
        )
        return 1
    print(f"ok — {len(in_scope())} documents, toutes les commandes `grimoire ...` citées existent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
