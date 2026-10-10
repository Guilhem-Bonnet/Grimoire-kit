"""Détection de la lecture d'un test caché dans la transcription d'un run (#694, W1-08b).

Pendant d'écriture de ``task_repo.assert_task_repo_clean`` (W1-08a) : cette
garde-là prouve que le dépôt est propre AVANT l'appel au modèle ; celle-ci
regarde, APRÈS le run, ce que l'agent a réellement lu. Le lot K a montré le
cas : ``python/bowling`` ``kit-gov`` run 2 a lu ``hidden-tests/bowling_test.py``
(la disposition du banc place ``hidden-tests/`` et les ``run<i>`` précédents,
tests copiés compris, à un ``..`` du dépôt de l'agent) et comptait comme succès.

Le détecteur lit le flux ``--output-format stream-json`` et ne regarde que les
APPELS D'OUTIL de l'agent (``tool_use``), jamais le texte : citer un nom de
fichier dans une réponse n'est pas le lire. Il résout chaque référence de chemin
contre le répertoire courant suivi de l'agent (``cd`` compris, le shell de
Claude Code est persistant) et la compare aux emplacements qui exposent les tests :

- ``direct`` : le chemin est un fichier de test exposé (``hidden-tests/<test>``
  ou ``<test>`` copié dans un autre dossier ``run<i>`` de la tâche) ;
- ``glob`` : un motif (``*``, ``**``, ``?``, ``[..]``) qui en désigne un, que ce
  soit dans un argument ``Bash`` ou dans l'outil ``Glob`` ;
- ``directory`` : le dossier ``hidden-tests`` (de n'importe quelle tâche) ou
  un autre ``run<i>`` désigné nommément, ou un ancêtre parcouru récursivement
  (outil ``Grep``, ``grep -r``, ``rg``, ``find``...).

Un drapeau n'est pas un verdict de fraude : il invalide le run (exclu des
taux) et la ligne déclenchante est listée pour revue humaine. Limites connues,
volontairement du côté du faux positif ou du « non vu » écrit : variables
shell non développées (``$HOME``, ``$(...)``), liens symboliques créés par
l'agent, ``cd`` d'une commande qui échoue, contenu d'un script écrit puis lancé
(le script n'est pas relu), accès par un serveur MCP qui n'expose pas de chemin.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

#: Nom du dossier où ``_run_one`` range les tests cachés, à côté des dépôts de run.
HIDDEN_DIR_NAME = "hidden-tests"

#: Outils qui n'écrivent que : seul leur ``file_path`` est une référence (le
#: ``content``/``new_string`` est du code produit par l'agent, pas une lecture).
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

_BASH_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
#: Corps de heredoc (``<<'EOF' ... EOF``) : du code ou du texte, pas des arguments.
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?:\n[ \t]*\2[ \t]*(?=\n|\Z)|\Z)", re.DOTALL)
_NAME_CHAR = re.compile(r"[\w.-]")
_FRAGMENT = re.compile(r"[^\s'\"`()<>;|&=,{}]+")
_GLOB_CHARS = frozenset("*?[")
#: Commandes ou drapeaux qui parcourent un dossier récursivement.
_RECURSIVE = re.compile(r"(?<![\w-])(?:rg|ag|find|tree|fd)\b|\s-[A-Za-z]*[rR][A-Za-z]*\b|--recursive\b")
_EXCERPT_MAX = 200
_KIND_RANK = {"direct": 0, "glob": 1, "directory": 2}


@dataclass(frozen=True)
class HiddenTestRead:
    """Un appel d'outil de la transcription qui désigne un test caché."""

    line: int  # numéro de ligne (1-based) dans la transcription
    tool: str
    kind: str  # "direct" | "glob" | "directory"
    reference: str  # le fragment de chemin ou de motif qui déclenche
    excerpt: str  # l'entrée de l'outil, tronquée

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "tool": self.tool,
            "kind": self.kind,
            "reference": self.reference,
            "excerpt": self.excerpt,
        }


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Motif de glob → regex : ``*`` ne franchit pas ``/``, ``**`` oui."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\").replace("[", "\\[") + "]")
            i = end + 1
        else:
            out.append(re.escape(char))
            i += 1
    try:
        return re.compile("".join(out))
    except re.error:
        return re.compile(re.escape(pattern))


def _lexical(path: str | PurePath) -> PurePath:
    return PurePath(os.path.normpath(str(path)))


def _forms(path: Path) -> set[PurePath]:
    """La forme lexicale et la forme résolue (liens symboliques) d'un chemin."""
    forms = {_lexical(path)}
    with contextlib.suppress(OSError, RuntimeError):
        forms.add(PurePath(path.resolve()))
    return forms


class _Exposure:
    """Ce que l'agent ne doit pas lire : tests cachés et copies de tests."""

    def __init__(self, test_files: Sequence[str], run_dir: Path, hidden_dir: Path, other_run_dirs: Sequence[Path]):
        self.run_dirs = _forms(run_dir)
        self.dirs: set[PurePath] = _forms(hidden_dir)
        for other in other_run_dirs:
            self.dirs |= _forms(other)
        self.files: set[PurePath] = {base / rel for base in self.dirs for rel in test_files}

    def _inside_run_dir(self, path: PurePath) -> bool:
        return any(path == run or path.is_relative_to(run) for run in self.run_dirs)

    def classify(self, path: PurePath, *, is_glob: bool, recursive: bool) -> str | None:
        """Nature de l'exposition de *path* (absolu, normalisé), ou ``None``.

        Un chemin qui passe par un lien symbolique est jugé sous ses deux formes
        (lexicale et résolue) : le dépôt de l'agent y reste le sien, et un test
        caché atteint par un alias reste un test caché.
        """
        forms = {path}
        if not is_glob:
            with contextlib.suppress(OSError, RuntimeError):
                forms.add(PurePath(Path(path).resolve()))
        if any(self._inside_run_dir(form) for form in forms):
            return None
        kinds = [k for form in forms if (k := self._classify(form, is_glob=is_glob, recursive=recursive))]
        return min(kinds, key=lambda k: _KIND_RANK[k]) if kinds else None

    def _classify(self, path: PurePath, *, is_glob: bool, recursive: bool) -> str | None:
        if HIDDEN_DIR_NAME in path.parts:  # y compris le dossier d'une autre tâche
            return "glob" if is_glob else ("direct" if path in self.files else "directory")
        if is_glob:
            for pattern in {str(path), str(path).replace("/**/", "/")}:
                regex = _glob_regex(pattern)
                if any(regex.fullmatch(str(candidate)) for candidate in (*self.files, *self.dirs)):
                    return "glob"
            return None
        if path in self.files:
            return "direct"
        if any(path == d or path.is_relative_to(d) for d in self.dirs):
            return "directory"
        if recursive and any(d.is_relative_to(path) for d in self.dirs):
            return "directory"
        return None


def _resolve(token: str, cwd: PurePath) -> PurePath:
    return _lexical(token) if PurePath(token).is_absolute() else _lexical(cwd / token)


def _standalone(text: str, match: re.Match[str]) -> bool:
    """Le fragment est un argument à lui seul (pas collé à un guillemet ou une variable : ``"$SP"/``)."""
    before = text[match.start() - 1 : match.start()]
    after = text[match.end() : match.end() + 1]
    return (not before or before.isspace()) and (not after or after.isspace())


def _shell_references(command: str, cwd: PurePath) -> tuple[list[tuple[str, PurePath, bool]], PurePath]:
    """``(fragment, chemin résolu, parcours récursif)`` d'une commande Bash, et le cwd final.

    ``cd``/``pushd`` change le répertoire des segments suivants (``&&``, ``;``,
    ``|``, saut de ligne). Les corps de heredoc (code écrit ou lancé) sont lus
    sans parcours récursif : une barre oblique de commentaire ou de division
    (``//``, ``/``) n'y désigne pas la racine. Un fragment sans caractère de nom
    (``//``, ``///``, ``/**``) n'est jamais une référence ; ``/`` seul l'est
    uniquement comme argument à part entière de la commande (``find / -name x``).
    """
    bodies = [m.group(3) for m in _HEREDOC.finditer(command)]
    head = _HEREDOC.sub("", command)
    recursive = bool(_RECURSIVE.search(head))
    refs: list[tuple[str, PurePath, bool]] = []
    for segment in _BASH_SEGMENT.split(head):
        matches = list(_FRAGMENT.finditer(segment))
        tokens = [m.group() for m in matches]
        for match in matches:
            word = match.group()
            if word.startswith(("-", "$", "~")):
                continue
            if not _NAME_CHAR.search(word) and not (word == "/" and _standalone(segment, match)):
                continue
            refs.append((word, _resolve(word, cwd), recursive))
        if tokens and tokens[0] in ("cd", "pushd") and len(tokens) > 1 and not tokens[1].startswith(("-", "$", "~")):
            cwd = _resolve(tokens[1], cwd)
    for body in bodies:
        for token in _FRAGMENT.findall(body):
            if not token.startswith(("-", "$", "~")) and _NAME_CHAR.search(token):
                refs.append((token, _resolve(token, cwd), False))
    return refs, cwd


def _tool_references(
    name: str, tool_input: dict[str, Any], cwd: PurePath
) -> tuple[list[tuple[str, PurePath, bool]], PurePath]:
    """``(fragment, chemin résolu, parcours récursif)`` pour un appel d'outil, et le cwd suivant."""
    refs: list[tuple[str, PurePath, bool]] = []

    def add(value: Any, *, base: PurePath = cwd, recursive: bool = False) -> None:
        if isinstance(value, str) and value:
            refs.append((value, _resolve(value, base), recursive))

    if name in _WRITE_TOOLS:
        add(tool_input.get("file_path"))
        add(tool_input.get("notebook_path"))
    elif name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            shell_refs, cwd = _shell_references(command, cwd)
            refs.extend(shell_refs)
    elif name == "Glob":
        base = _resolve(tool_input["path"], cwd) if isinstance(tool_input.get("path"), str) else cwd
        add(tool_input.get("pattern"), base=base)
    elif name == "Grep":
        # Grep parcourt récursivement : un dossier ancêtre des tests suffit.
        add(tool_input.get("path") or ".", recursive=True)
        add(tool_input.get("glob"), recursive=True)
    else:
        # Read, NotebookRead, outils MCP, sous-agents... : fail-closed sur toute chaîne.
        for value in tool_input.values():
            if isinstance(value, str):
                if not any(char.isspace() for char in value):
                    add(value)  # chemin entier (un nom peut contenir `=` ou `,`)
                for token in _FRAGMENT.findall(value):
                    if not token.startswith(("-", "$", "~")):
                        add(token)
    return refs, cwd


def _excerpt(tool_input: dict[str, Any]) -> str:
    text = json.dumps(tool_input, ensure_ascii=False)
    return text if len(text) <= _EXCERPT_MAX else text[: _EXCERPT_MAX - 1] + "…"


def find_hidden_test_reads(
    lines: Iterable[str],
    *,
    test_files: Sequence[str],
    run_dir: Path,
    hidden_dir: Path,
    other_run_dirs: Sequence[Path] = (),
) -> list[HiddenTestRead]:
    """Les appels d'outil d'une transcription stream-json qui désignent un test caché.

    *run_dir* est le dépôt de l'agent (son répertoire courant de départ),
    *hidden_dir* le dossier ``hidden-tests`` de la tâche, *other_run_dirs* les
    autres dossiers ``run<i>`` de la même tâche (ils contiennent les tests
    copiés par ``run_hidden_tests``). Une liste vide est une transcription
    propre ; l'appelant décide quoi faire d'une transcription illisible.
    """
    exposure = _Exposure(test_files, run_dir, hidden_dir, other_run_dirs)
    cwd = _lexical(run_dir)
    found: list[HiddenTestRead] = []
    for number, raw in enumerate(lines, start=1):
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init" and isinstance(event.get("cwd"), str):
            cwd = _lexical(event["cwd"])
            continue
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            name = str(block.get("name") or "?")
            refs, cwd = _tool_references(name, tool_input, cwd)
            hits = [
                (kind, token)
                for token, path, recursive in refs
                if (kind := exposure.classify(path, is_glob=any(c in token for c in _GLOB_CHARS), recursive=recursive))
            ]
            if hits:  # une ligne déclenchante par appel d'outil : la référence la plus précise
                kind, token = min(hits, key=lambda hit: _KIND_RANK[hit[0]])
                found.append(HiddenTestRead(number, name, kind, token, _excerpt(tool_input)))
    return found


def check_transcript(
    log_path: Path, *, test_files: Sequence[str], run_dir: Path, hidden_dir: Path
) -> tuple[str, list[dict[str, Any]] | None]:
    """Verdict d'un run : ``("clean", [])``, ``("invalid", [lectures])`` ou ``("unevaluated", None)``.

    ``unevaluated`` : transcription absente, illisible ou vide. Un contrôle qui
    n'a rien pu lire ne rend jamais « propre ». Les autres dossiers ``run<i>``
    de la tâche (frères de *run_dir* sous ``hidden_dir.parent``) sont exposés
    au même titre que ``hidden-tests`` : ils contiennent les tests copiés.
    """
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ("unevaluated", None)
    if not any(line.strip() for line in lines):
        return ("unevaluated", None)
    others = sorted(d for d in hidden_dir.parent.glob("*/run*") if d.is_dir() and d != run_dir)
    reads = find_hidden_test_reads(
        lines, test_files=test_files, run_dir=run_dir, hidden_dir=hidden_dir, other_run_dirs=others
    )
    return ("invalid", [read.to_dict() for read in reads]) if reads else ("clean", [])


def invalid_runs_rows(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Lignes de rapport des runs invalidés (``RunRecord.invalid``), triées, avec les lectures."""
    return [
        {
            "task_id": r.task_id,
            "arm": r.arm,
            "run_index": r.run_index,
            "success": r.success,
            "reads": r.hidden_test_reads or [],
        }
        for r in sorted(records, key=lambda r: (r.task_id, r.arm, r.run_index))
        if r.invalid
    ]


def render_invalid_runs(invalid_runs: Sequence[dict[str, Any]]) -> list[str]:
    """Section Markdown « Runs invalidés » ; liste vide s'il n'y en a aucun."""
    if not invalid_runs:
        return []
    lines = [
        "## Runs invalidés (W1-08b)",
        "",
        "La transcription de ces runs désigne un test caché (`hidden_reads.py`) : ils sont exclus de "
        "tous les taux ci-dessus (leur coût reste compté). Un drapeau n'est pas un verdict de fraude : "
        "la ligne déclenchante est listée pour revue humaine.",
        "",
    ]
    for run in invalid_runs:
        outcome = "réussi" if run["success"] else "échoué"
        lines.append(f"- `{run['task_id']}` / `{run['arm']}` / run{run['run_index']} (brut : {outcome})")
        for read in run["reads"]:
            lines.append(
                f"  - ligne {read['line']}, outil `{read['tool']}`, {read['kind']} : "
                f"`{read['reference']}` — `{read['excerpt']}`"
            )
    lines.append("")
    return lines


def invalid_run_alert(record: Any) -> str:
    """Message d'alerte (stderr) quand un run vient d'être invalidé."""
    first = (record.hidden_test_reads or [{}])[0]
    return (
        f"[INVALIDE] {record.task_id} / {record.arm} / run{record.run_index} : la transcription désigne un test "
        f"caché (ligne {first.get('line', '?')}, {first.get('kind', '?')}) — exclu des taux, à revoir dans le rapport."
    )
