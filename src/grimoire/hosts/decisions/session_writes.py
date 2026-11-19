"""Ce que le compteur d'écritures de la session compte — et ce qu'il ne compte pas.

Le ``Stop`` d'un profil bloquant refuse une clôture hors tâche dès que la session a écrit
(:func:`grimoire.hosts.decisions.evidence_gate.decide_evidence_gate`). Il lit le compteur que
``PostToolUse`` tient (:func:`grimoire.policies.session_state.note_session_mutation`) : un
compteur gonflé est un refus à tort, un compteur qui oublie une vraie écriture désarme la garde.

Mesuré sur un poste réel : 185 refus en 14 jours, et dans une session qui n'avait fait que
lire et lancer des sous-agents de lecture, un compteur monté de 4 à 65 (redirections vers le
brouillon hors projet, commandes de vérification, ``TodoWrite``…).

La règle (:func:`counts_as_session_write`), la même pour la session et ses sous-agents :

1. une action classée lecture seule ne compte jamais ;
2. une écriture par outil compte si au moins une de ses cibles est dans le projet ; sans
   aucune cible (``TodoWrite``, la liste de tâches de l'hôte), elle ne compte pas ;
3. une commande destructive, ou dont une écriture prouvée
   (:func:`grimoire.hosts.decisions.write_proof.proves_write`) peut viser le projet, compte. Un
   mot illisible (substitution, variable) est réputé viser le projet ; le chemin relatif se lit
   depuis le ``cwd`` de l'hôte ET depuis la racine du projet (un ``cwd`` absent ou hors projet
   ne fait jamais sortir une écriture relative) ;
4. une commande dont chaque segment est une vérification ne compte pas : ``pytest``, ``mypy``,
   ``ruff check``, ``eslint``, ``tsc --noEmit``, ``cargo test``… jugés sur le premier mot du
   segment, jamais avec ``--fix``, ``format``, ``--write`` ni ``--basetemp`` ;
5. une commande dont chaque segment est une forme lisible — un lecteur (``grep``, ``cat``,
   ``echo``…), un écrivain connu (``cp``, ``mv``, ``tee``, ``mkdir``, ``touch``…) ou une
   commande classée lecture seule — ne compte pas : ses écritures sont toutes connues de
   :mod:`.write_proof`, et la règle 3 a établi qu'aucune ne vise le projet (brouillon hors
   projet). Jamais pour ``make``, un script, ``git apply``, ``patch`` ou un interpréteur ;
6. tout le reste compte : ce qu'on ne sait pas lire est jugé en échec fermé.

Analyse de texte et résolution de chemins seulement ; jamais une exception vers le hook.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.policies.schemas import ActionKind, MutationClass

if TYPE_CHECKING:
    from grimoire.hosts.decisions._shared import HookInput
    from grimoire.hosts.decisions.tool_facts import ToolFacts

#: Valeur d'un mot que l'analyse de :mod:`.write_proof` ne sait pas lire (substitution de commande).
_UNKNOWN = "\x00"

#: Lecteurs : leur seule écriture possible est une redirection, que :mod:`.write_proof` prouve
#: (``sed -i``/``sed w`` et ``sort -o`` compris).
_READERS = frozenset(
    {"cat", "echo", "printf", "grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
     "ls", "jq", "diff", "true", "date", "basename", "dirname", "realpath", "sed", "nl", "comm", "stat", "pwd"}
)
#: Écrivains dont toutes les cibles sont des opérandes ou des options que :mod:`.write_proof` lit.
_KNOWN_WRITERS = frozenset({"cp", "mv", "tee", "mkdir", "touch", "ln", "install", "rmdir"})

#: Vérifications : programme -> sous-commandes admises (vide = toutes, hors options interdites).
_CHECKS: dict[str, frozenset[str]] = {
    "pytest": frozenset(), "py.test": frozenset(), "mypy": frozenset(), "pyright": frozenset(),
    "flake8": frozenset(), "pylint": frozenset(), "shellcheck": frozenset(), "eslint": frozenset(),
    "tsc": frozenset(), "ruff": frozenset({"check"}), "cargo": frozenset({"test", "check", "clippy"}),
    "go": frozenset({"test", "vet"}), "npm": frozenset({"test"}), "pnpm": frozenset({"test"}),
    "yarn": frozenset({"test"}),
}
#: Un mot qui fait d'une vérification une écriture.
_WRITING_FLAGS = ("--fix", "--unsafe-fixes", "--write", "--basetemp", "--add-noqa", "format", "-w")
#: Lanceurs traversés pour atteindre la vérification : ``uv run pytest``, ``timeout 60 mypy``.
_RUNNERS = {"uv": ("run",), "poetry": ("run",), "pipx": ("run",), "npx": (), "pdm": ("run",)}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FD_DUP = re.compile(r"\d*>&(?:\d+|-)|&>")
_PYTHON = re.compile(r"^python[0-9.]*$")


def writes_outside_project(hook: HookInput, facts: ToolFacts) -> bool:
    """Issue #875 : vrai si l'écriture par outil n'a que des cibles hors de ``project_root``.

    Une cible relative se lit depuis ``project_root`` ; une cible illisible reste comptée.
    """
    if facts.kind is not ActionKind.FILE_WRITE or not facts.targets:
        return False
    try:
        root = hook.project_root.resolve()
        for target in facts.targets:
            path = Path(target).expanduser()
            if (path if path.is_absolute() else root / path).resolve().is_relative_to(root):
                return False
        return True
    except Exception:
        return False


def is_session_bookkeeping(facts: ToolFacts) -> bool:
    """Un outil « d'écriture » sans aucun fichier cible : la liste de tâches de l'hôte (``TodoWrite``)."""
    return facts.kind is ActionKind.FILE_WRITE and not facts.targets


class _ProjectTarget:
    """Le projet vu par :mod:`.write_proof`. Un mot illisible est réputé le viser (échec fermé)."""

    def __init__(self, root: Path, cwd: Path) -> None:
        self.root, self.cwd = root, cwd

    def _path(self, word: str, cd: tuple[str, ...]) -> Path | None:
        if _UNKNOWN in word or any(_UNKNOWN in d for d in cd):
            return None
        base = self.cwd
        for directory in cd:
            base = base / Path(directory).expanduser()
        path = Path(word).expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    def names(self, word: str, cd: tuple[str, ...]) -> bool:
        path = self._path(word, cd)
        return path is None or path.is_relative_to(self.root)

    def carries(self, word: str, cd: tuple[str, ...]) -> bool:
        path = self._path(word, cd)
        return path is None or self.root.is_relative_to(path)


def _cwds(hook: HookInput, root: Path) -> tuple[Path, ...]:
    """La racine du projet, et le ``cwd`` de l'hôte s'il est donné : une écriture relative est lue depuis les deux."""
    raw = hook.raw.get("cwd") if isinstance(hook.raw, dict) else None
    if isinstance(raw, str) and raw.strip() and Path(raw).is_absolute():
        cwd = Path(raw).resolve()
        if cwd != root:
            return (root, cwd)
    return (root,)


def _may_write_project(hook: HookInput, command: str) -> bool:
    from grimoire.hosts.decisions.write_proof import proves_write

    root = hook.project_root.resolve()
    return any(proves_write(command, _ProjectTarget(root, cwd)) for cwd in _cwds(hook, root))


def _segments(command: str) -> list[list[str]] | None:
    """Les segments (``;``, ``&&``, ``||``, ``|``, ``&``, saut de ligne) en mots, ou ``None`` si illisible.

    Les corps de heredoc sont retirés (des données), les commentaires ignorés. Une substitution
    (``$(…)``, accent grave, ``<(…)``) rend ``None`` : ce qu'elle exécute n'est pas lu ici.
    """
    from grimoire.hosts.decisions.read_only_layout import strip_heredoc_bodies

    text = strip_heredoc_bodies(command)
    if "$(" in text or "`" in text or "<(" in text or ">(" in text:
        return None
    text = _FD_DUP.sub(" ", text).replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= set(";&|"):
            segments.append([])
        else:
            segments[-1].append(token)
    words = [_without_assignments(segment) for segment in segments]
    return [segment for segment in words if segment]


def _without_assignments(words: list[str]) -> list[str]:
    index = 0
    while index < len(words) and _ASSIGNMENT.match(words[index]):
        index += 1
    return words[index:]


def _program(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _check_words(words: list[str]) -> list[str]:
    """*words* sans ``timeout N``, ``uv run``, ``python -m`` : ce qui reste est la vérification présumée."""
    words = list(words)
    while words:
        head = _program(words[0])
        if head == "timeout":
            rest = [w for w in words[1:] if not w.startswith("-")]
            words = rest[1:] if rest else []
        elif head in _RUNNERS and words[1 : 1 + len(_RUNNERS[head])] == list(_RUNNERS[head]):
            words = words[1 + len(_RUNNERS[head]) :]
        elif _PYTHON.match(head) and len(words) > 2 and words[1] == "-m":
            words = words[2:]
        else:
            return words
    return words


def _is_check(words: list[str]) -> bool:
    words = _check_words(words)
    if not words or any(w == flag or w.startswith(flag + "=") for w in words for flag in _WRITING_FLAGS):
        return False
    program = _program(words[0])
    if program not in _CHECKS:
        return False
    if program == "tsc" and "--noEmit" not in words:
        return False
    subcommands = _CHECKS[program]
    return not subcommands or (len(words) > 1 and words[1] in subcommands)


def _is_neutral(words: list[str]) -> bool:
    """``cd`` et une commande classée lecture seule n'écrivent rien par eux-mêmes."""
    from grimoire.hosts.decisions.read_only import is_read_only_command

    return words[0] in ("cd", "pushd", "popd") or is_read_only_command(shlex.join(words))


def _command_counts(hook: HookInput, facts: ToolFacts) -> bool:
    if facts.mutation is MutationClass.DESTRUCTIVE or _may_write_project(hook, facts.command):
        return True
    segments = _segments(facts.command)
    if not segments:
        return True
    if all(_is_neutral(s) or _is_check(s) for s in segments) and any(_is_check(s) for s in segments):
        return False
    readable = (_READERS | _KNOWN_WRITERS)
    return not all(_program(s[0]) in readable or _is_neutral(s) for s in segments)


def counts_as_session_write(hook: HookInput, facts: ToolFacts) -> bool:
    """Vrai si cet appel est une écriture dans le projet, réelle ou possible — voir la règle du module."""
    if facts.mutation is MutationClass.READ_ONLY:
        return False
    try:
        if facts.command:
            return _command_counts(hook, facts)
        if facts.kind is ActionKind.FILE_WRITE:
            return not is_session_bookkeeping(facts) and not writes_outside_project(hook, facts)
        return True
    except Exception:  # une analyse en panne compte : la garde reste en échec fermé
        return True
