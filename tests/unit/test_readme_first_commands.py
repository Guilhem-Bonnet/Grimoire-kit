"""Les commandes d'accroche ne doivent utiliser aucune option dépréciée.

`README.md`, `README.fr.md` et `docs/index.md` sont la première chose qu'un
nouvel utilisateur copie-colle. Une option dépréciée y survit facilement à la
dépréciation du code : c'est arrivé à `--lite`, resté en tête des trois
fichiers alors que `grimoire init` l'annonçait déjà « Deprecated ».

Ce test extrait les commandes `grimoire …` des blocs de code des trois
fichiers, résout chaque commande dans le vrai arbre Click, et échoue si l'une
emploie une option dont l'aide commence par « Deprecated ».
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer.main

from grimoire.cli.app import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_FILES = ("README.md", "README.fr.md", "docs/index.md")


def _grimoire_commands(path: Path) -> Iterator[tuple[int, str, list[str]]]:
    """Yield ``(line_number, line, tokens)`` for each ``grimoire …`` code line."""
    in_fence = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        stripped = line.strip()
        if not stripped.startswith("grimoire ") and stripped != "grimoire":
            continue
        # ``comments=True`` ignores the explanatory comment after the command,
        # except inside a quoted argument (e.g. ``-a "pytest …"``).
        tokens = shlex.split(stripped, comments=True)
        yield line_number, stripped, tokens


def _option_param(command: Any, option: str) -> Any | None:
    for param in command.params:
        if option in param.opts:
            return param
    return None


def _deprecated_options(tokens: list[str], root: Any) -> list[tuple[str, str]]:
    """Return ``(option, help)`` for each deprecated option used by ``tokens``.

    Walks the command tree in order: a positional token that names a
    sub-command descends into it; an option is checked against the command
    current at that point (so ``grimoire --global`` and ``grimoire cmd
    --local`` are both resolved, and an option's value is not mistaken for a
    sub-command).
    """
    command = root
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        arg = tokens[index]
        if arg.startswith("-") and arg != "-":
            option = arg.split("=", 1)[0]
            param = _option_param(command, option)
            if param is not None:
                help_text = param.help or ""
                if help_text.strip().startswith("Deprecated"):
                    found.append((option, help_text))
                if "=" not in arg and not getattr(param, "is_flag", False):
                    index += 2  # skip the option's value
                    continue
            index += 1
            continue
        # Duck-typed on purpose: this typer version vendors its own click, so
        # ``isinstance(command, click.Group)`` is False for a real group.
        get_command = getattr(command, "get_command", None)
        if get_command is not None:
            sub_command = get_command(None, arg)
            if sub_command is not None:
                command = sub_command
        index += 1
    return found


def test_first_commands_use_no_deprecated_option() -> None:
    root = typer.main.get_command(app)
    violations: list[str] = []
    extracted = 0

    for relative_path in _DOC_FILES:
        path = _REPO_ROOT / relative_path
        assert path.is_file(), f"{relative_path} est absent du dépôt"
        for line_number, line, tokens in _grimoire_commands(path):
            extracted += 1
            for option, help_text in _deprecated_options(tokens[1:], root):
                violations.append(
                    f"{relative_path}:{line_number} : « {line} » utilise {option} "
                    f"dont l'aide commence par « Deprecated » ({help_text})"
                )

    assert extracted, "aucune commande grimoire trouvée dans les fichiers d'accroche"
    assert not violations, "options dépréciées dans les commandes d'accroche :\n" + "\n".join(violations)
