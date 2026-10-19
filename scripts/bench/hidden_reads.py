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
contre le répertoire courant suivi de l'agent et la compare aux emplacements
qui exposent les tests. Le suivi du répertoire courant calque Claude Code (voir
``_ShellCwd``) : un ``cd`` vaut pour la suite de la commande, sauf dans un sous-shell
``( ... )`` ou un élément de tube, dont il ne sort pas, et il peut échouer : après
``;``, un saut de ligne ou ``||``, la suite est jugée aussi depuis la position d'avant
(seul ``&&`` ne regarde que le succès) ; d'un appel ``Bash``
à l'autre, le répertoire final n'est gardé que s'il reste dans le dépôt du run.
Sinon le shell est ramené à ce dépôt (« Shell cwd was reset to ... »), ce que le
``tool_result`` confirme ; sans confirmation (message absent, résultat en erreur
ou absent), le dépôt reste candidat à côté du répertoire où la commande a fini.

- ``direct`` : le chemin est un fichier de test exposé (``hidden-tests/<test>``,
  ``<test>`` copié dans un autre dossier ``run<i>`` de la tâche, ou ``<test>``
  de l'exercice canonique du clone ``polyglot-benchmark``) ;
- ``glob`` : un motif (``*``, ``**``, ``?``, ``[..]``) qui en désigne un, que ce
  soit dans un argument ``Bash`` ou dans l'outil ``Glob`` ;
- ``directory`` : le dossier ``hidden-tests`` (de n'importe quelle tâche), un
  autre ``run<i>`` ou le dossier de l'exercice canonique (son ``.meta/`` porte
  la solution de référence) désigné nommément, ou un ancêtre parcouru
  récursivement (outil ``Grep``, ``grep -r``, ``rg``, ``find``...).

Un drapeau n'est pas un verdict de fraude : il invalide le run (exclu des
taux) et la ligne déclenchante est listée pour revue humaine. Limites connues,
volontairement du côté du faux positif ou du « non vu » écrit : variables
shell non développées (``$HOME``, ``$(...)``), liens symboliques créés par
l'agent, ``cd`` en fin de commande ou d'une chaîne ``&&`` qui échoue dans le dépôt (la
cible reste le cwd suivi d'un appel au suivant), ``popd``/``pushd`` dont la
pile vient d'un autre appel (joué comme ``cd -`` : rester, ou revenir au dépôt),
mots-clés devant un ``cd`` (``then cd src``), citation ANSI-C (``$'\\056\\056'``), contenu d'un script écrit puis lancé
(le script n'est pas relu), accès par un serveur MCP qui n'expose pas de chemin.

Un heredoc ne retire du texte analysé que son corps et son délimiteur : la fin de la
ligne d'ouverture (``<<'EOF' && cat x``, ``<<EOF; cp x .``) est une commande qui
s'exécute et reste lue ; ``<<<`` (here-string) n'est pas un heredoc, pas plus qu'un ``<<`` entre
guillemets ou dans ``$((1<<3))``, ni un ``<<`` dont aucune ligne ne ferme le délimiteur (les lignes
suivantes restent alors des commandes). Dans une liste ``a || b && c``, la suite s'exécute
après un succès de ``a`` (``b`` sauté) comme de ``b`` ; un saut de ligne (ou ``\\`` + saut de ligne) qui
suit ``&&``, ``||`` ou ``|`` continue la liste au lieu de la terminer, y compris à travers des lignes vides
(W1-08b-T3-01), mais pas un commentaire ``# ...`` intercalé. Chaque mot du shell est recomposé avant d'être
jugé (W1-08b-T3-02) : guillemets retirés et parties collées concaténées (``..'/run0/x'``, ``../"run0"/x``),
barre inverse levée, accolades développées (``../{run0,run2}/x``, ``hidden-test{s,}``, séquences ``{0..2}``) ;
le texte brut, coupé aux guillemets et aux accolades, reste jugé aussi. Au-delà de 256 variantes, chaque
groupe d'accolades devient ``*`` (glob, du côté du faux positif) ; accolade ou virgule entre guillemets ou
échappées, ``${..}`` et groupe sans virgule ni séquence ne se développent pas. Une transcription
qui n'a pas été lue en entier (aucun événement ``system/init`` ou ``assistant``, ou une
ligne illisible qui porte une accolade : un événement coupé par une ligne de stderr)
rend ``unevaluated``, jamais ``clean`` ; une lecture déjà trouvée reste ``invalid``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

#: Nom du dossier où ``_run_one`` range les tests cachés, à côté des dépôts de run.
HIDDEN_DIR_NAME = "hidden-tests"

#: Outils qui n'écrivent que : seul leur ``file_path`` est une référence (le
#: ``content``/``new_string`` est du code produit par l'agent, pas une lecture).
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

#: Séparateurs de commandes ; ``(`` et ``)`` ouvrent et ferment un sous-shell (``_split_command`` les trie).
_BASH_SEPARATOR = re.compile(r"&&|\|\||[;|\n()]")
#: Opérateurs après lesquels un saut de ligne ne termine pas la commande : la liste continue sur la ligne suivante.
_LIST_OPERATORS = frozenset({"&&", "||", "|"})
#: Ouverture d'un heredoc (``<<'EOF'``, ``<<-EOF``, ``<<\\EOF``, ``<<END-OF``) : groupes 1 et 2, le
#: délimiteur entre guillemets ; groupe 3, le délimiteur nu (un mot du shell : tiret, point... compris) ;
#: groupe 4, la fin de la ligne d'ouverture (``&& cat x``, ``; cp x .`` : des commandes qui s'exécutent).
#: ``<<<`` (here-string) n'en est pas un. Le corps ne vaut que si une ligne de fermeture existe
#: (``_split_heredocs``) et que ``<<`` n'est ni entre guillemets ni dans une expression arithmétique.
_HEREDOC_OPEN = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([^\s;&|<>()'\"\\]+))([^\n]*)")
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

    def __init__(
        self,
        test_files: Sequence[str],
        run_dir: Path,
        hidden_dir: Path,
        other_run_dirs: Sequence[Path],
        canonical_dirs: Sequence[Path],
    ):
        self.run_dirs = _forms(run_dir)
        self.dirs: set[PurePath] = _forms(hidden_dir)
        # Les copies de tests (``run<i>`` frères) et la source canonique (le
        # dossier de l'exercice dans le clone polyglot-benchmark, solution de
        # référence de ``.meta/`` comprise) exposent les mêmes fichiers que ``hidden-tests``.
        for other in (*other_run_dirs, *canonical_dirs):
            self.dirs |= _forms(other)
        self.files: set[PurePath] = {base / rel for base in self.dirs for rel in test_files}

    def inside_run_dir(self, path: PurePath) -> bool:
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
        if any(self.inside_run_dir(form) for form in forms):
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


#: Options de ``cd`` qui ne changent pas la cible.
_CD_OPTIONS = frozenset({"-L", "-P", "-e", "-@", "--"})
#: Un ``cd -`` / ``pushd -`` / ``popd`` quelque part dans la commande (retour au répertoire précédent).
_CD_DASH = re.compile(r"(?:^|[;&|\n(])[ \t]*(?:(?:cd|pushd)[ \t]+(?:-[LPe@][ \t]+|--[ \t]+)*-|popd)(?=[ \t;&|\n)]|$)")


@dataclass(frozen=True)
class _Dirs:
    """Où est le shell : répertoire courant, précédent (``cd -``) et pile de ``pushd``."""

    cwd: PurePath
    previous: PurePath | None = None
    stack: tuple[PurePath, ...] = ()


def _cd_target(tokens: Sequence[str]) -> str | None:
    """La cible d'un ``cd``/``pushd`` (``-`` : le répertoire précédent), ``None`` si inconnue ou absente."""
    if not tokens or tokens[0] not in ("cd", "pushd"):
        return None
    for word in tokens[1:]:
        if word in _CD_OPTIONS:
            continue
        if word == "-":
            return word
        return None if word.startswith(("-", "$", "~")) else word
    return None


def _quote_states(text: str) -> tuple[list[str], bool]:
    """Le guillemet ouvert (``''``, ``'\'``, ``'"'``) avant chaque caractère, et si un guillemet reste ouvert à la fin."""
    states: list[str] = []
    quote = ""
    escaped = False
    for char in text:
        states.append(quote)
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif not quote and char in "'\"":
            quote = char
        elif char == quote:
            quote = ""
    return states, bool(quote)


def _split_command(head: str) -> tuple[list[tuple[str, str]], bool]:
    """``[(séparateur qui précède, segment)]`` d'une commande, et si la lecture des guillemets est sûre.

    ``(``/``)`` ouvrent et ferment un sous-shell (``$(...)``, ``<(...)`` compris) ; un
    séparateur de tube est ``|`` (jamais ``||``). Une parenthèse entre guillemets simples,
    ou entre guillemets doubles sans ``$`` devant, est du texte ; ``(dq``/``)dq`` marquent
    celles que seuls les guillemets doubles ont laissées passer (``"$(...)"``). Un guillemet
    jamais fermé rend la lecture douteuse : toutes les parenthèses comptent alors.
    """
    quotes, unclosed = _quote_states(head)
    pieces: list[tuple[str, str]] = []
    separator, start = "", 0
    for match in _BASH_SEPARATOR.finditer(head):
        mark = match.group()
        blank_after_operator = mark == "\n" and separator in _LIST_OPERATORS and not head[start : match.start()].strip()
        if blank_after_operator and not unclosed and not quotes[match.start()]:
            # Un saut de ligne qui suit `&&`, `||` ou `|` continue la liste : ce n'est pas un `;` (W1-08b-T3-01).
            start = match.end()
            continue
        if mark in ("(", ")") and not unclosed:
            quote = quotes[match.start()]
            if quote == "'" or (quote == '"' and mark == "(" and head[match.start() - 1 : match.start()] != "$"):
                continue
            if quote == '"':
                mark += "dq"
        pieces.append((separator, head[start : match.start()]))
        separator, start = mark, match.end()
    pieces.append((separator, head[start:]))
    return pieces, unclosed


def _join_continuations(text: str) -> str:
    """Retire les ``\\`` suivis d'un saut de ligne, sauf entre guillemets simples (continuation de ligne du shell)."""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'" and i + 1 < len(text):
            if text[i + 1] != "\n":
                out.append(text[i : i + 2])
            i += 2
            continue
        if not quote and char in "'\"":
            quote = char
        elif char == quote:
            quote = ""
        out.append(char)
        i += 1
    return "".join(out)


#: Accolades et virgule entre guillemets (ou précédées d'une barre inverse) : littérales, jamais développées.
_LITERAL = {"{": "\ue000", "}": "\ue001", ",": "\ue002"}
_UNLITERAL = str.maketrans({v: k for k, v in _LITERAL.items()})
#: Plafond de variantes d'un développement d'accolades ; au-delà, chaque groupe devient ``*`` (fail-closed).
_BRACE_LIMIT = 256


class _TooManyVariantsError(Exception):
    """Un développement d'accolades dépasse ``_BRACE_LIMIT``."""


def _shell_words(segment: str) -> list[str]:
    """Les mots d'un segment tels que le shell les compose : guillemets retirés, parties collées concaténées.

    ``..'/run0/x'``, ``../"run0"/x`` et ``..\\/run0`` valent ``../run0/x``. Les espaces et ``<>&`` et
    l'apostrophe inverse séparent les mots ; une accolade ou une virgule entre guillemets ou
    échappée est marquée littérale (``_LITERAL``) pour que ``_brace_variants`` ne la développe pas.
    """
    words: list[str] = []
    current: list[str] = []
    quote = ""
    i = 0
    while i < len(segment):
        char = segment[i]
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                current.append(_LITERAL.get(char, char))
        elif quote == '"':
            if char == '"':
                quote = ""
            elif char == "\\" and i + 1 < len(segment) and segment[i + 1] in '"\\$`':
                i += 1
                current.append(_LITERAL.get(segment[i], segment[i]))
            else:
                current.append(_LITERAL.get(char, char))
        elif char == "\\" and i + 1 < len(segment):
            i += 1
            current.append(_LITERAL.get(segment[i], segment[i]))
        elif char in "'\"":
            quote = char
        elif char.isspace() or char in "<>&`":
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(char)
        i += 1
    if current:
        words.append("".join(current))
    return words


_SEQUENCE = re.compile(r"(-?\w+)\.\.(-?\w+)(?:\.\.(-?\d+))?")


def _sequence_items(body: str) -> list[str] | None:
    """Les éléments de ``{a..b}`` / ``{1..9}`` / ``{01..10..3}``, ``None`` si ce n'est pas une séquence."""
    match = _SEQUENCE.fullmatch(body)
    if match is None:
        return None
    first, last, raw_step = match.groups()
    step = abs(int(raw_step)) or 1 if raw_step else 1
    numeric = re.fullmatch(r"-?\d+", first) and re.fullmatch(r"-?\d+", last)
    if numeric:
        low, high = int(first), int(last)
        width = (
            max(len(first), len(last)) if first.lstrip("-").startswith("0") or last.lstrip("-").startswith("0") else 0
        )
    elif len(first) == 1 and len(last) == 1 and first.isalpha() and last.isalpha():
        low, high, width = ord(first), ord(last), 0
    else:
        return None
    count = abs(high - low) // step + 1
    if count > _BRACE_LIMIT:
        raise _TooManyVariantsError
    values = range(low, high + 1, step) if low <= high else range(low, high - 1, -step)
    return [f"{v:0{width}d}" if numeric else chr(v) for v in values]


def _brace_variants(word: str) -> list[str]:
    """Le développement des accolades d'un mot (``a{b,c}d`` → ``abd``, ``acd``), comme bash.

    Un groupe n'est développé que s'il porte une virgule au premier niveau ou une séquence ``..`` ;
    ``${..}`` et un groupe sans virgule restent tels quels. Lève ``_TooManyVariantsError`` au-delà de
    ``_BRACE_LIMIT`` variantes.
    """
    start = 0
    while (opening := word.find("{", start)) != -1:
        depth, commas, closing = 0, [], -1
        for j in range(opening, len(word)):
            if word[j] == "{":
                depth += 1
            elif word[j] == "}":
                depth -= 1
                if depth == 0:
                    closing = j
                    break
            elif word[j] == "," and depth == 1:
                commas.append(j)
        if closing == -1:
            start = opening + 1
            continue
        body = word[opening + 1 : closing]
        dollar = opening > 0 and word[opening - 1] == "$"
        items: list[str] | None = None
        if not dollar and commas:
            cuts = [opening, *commas, closing]
            items = [word[cuts[k] + 1 : cuts[k + 1]] for k in range(len(cuts) - 1)]
        elif not dollar:
            items = _sequence_items(body)
        if items is None:
            start = closing + 1 if dollar else opening + 1
            continue
        variants: list[str] = []
        for item in items:
            variants.extend(_brace_variants(word[:opening] + item + word[closing + 1 :]))
            if len(variants) > _BRACE_LIMIT:
                raise _TooManyVariantsError
        return variants
    return [word]


def _brace_glob(word: str) -> str:
    """Repli quand le développement est trop grand : chaque groupe d'accolades de premier niveau devient ``*``."""
    out: list[str] = []
    depth = 0
    for char in word:
        if char == "{":
            depth += 1
            if depth == 1 and not (out and out[-1] == "*"):  # `**` serait un parcours récursif
                out.append("*")
        elif char == "}" and depth:
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _word_variants(word: str) -> list[str]:
    """Les chemins que désigne un mot du shell : accolades développées, guillemets déjà retirés."""
    try:
        variants = _brace_variants(word)
    except _TooManyVariantsError:
        variants = [_brace_glob(word)]
    return [variant.translate(_UNLITERAL) for variant in variants]


def _in_arithmetic(prefix: str) -> bool:
    """``prefix`` finit à l'intérieur d'un ``$(( ... ))`` ou d'un ``(( ... ))`` encore ouvert."""
    return prefix.rfind("((") > prefix.rfind("))")


#: Extensions de fichiers de prose : un heredoc écrit par ``cat``/``tee`` vers eux n'est pas du code (B-06).
_PROSE_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc")
_WRITER_CUT = re.compile(r"&&|\|\||[;|(\n]|(?<!>)&(?![>])")
_REDIRECT = re.compile(r"^(?:\d*>>?|&>>?)(.*)$")


def _writes_prose_only(prefix: str, tail: str) -> bool:
    """Le heredoc ouvert par *prefix* ``<<`` *tail* n'est que le texte d'un ``cat``/``tee`` vers des fichiers de prose.

    Le gras Markdown ``**zipper**`` d'un artefact écrit n'est pas un motif de lecture (B-06, pilote). Fail-closed :
    tout doute (autre commande, tube en fin de ligne, redirection d'entrée, cible hors prose, shell illisible)
    laisse le corps analysé, comme avant. Un corps écrit vers un script (``.py``, ``.sh``...) reste analysé :
    il peut lire un test caché une fois lancé.
    """
    first = re.split(_WRITER_CUT, tail, maxsplit=1)[0]
    if "|" in first.replace("||", ""):
        return False
    try:
        words = shlex.split(_WRITER_CUT.split(prefix)[-1] + " " + first)
    except ValueError:
        return False
    while words and re.fullmatch(r"\w+=\S*", words[0]):
        words = words[1:]
    if not words or words[0] not in ("cat", "tee") or any("<" in w for w in words):
        return False
    targets: list[str] = []
    take_next = False
    for word in words[1:]:
        if take_next:
            targets.append(word)
            take_next = False
        elif (m := _REDIRECT.match(word)) is not None:
            if m.group(1).startswith("&"):  # ``2>&1`` : duplication de descripteur, pas un fichier
                continue
            if m.group(1):
                targets.append(m.group(1))
            else:
                take_next = True
        elif words[0] == "tee" and not word.startswith("-"):
            targets.append(word)
    return not take_next and bool(targets) and all(t.lower().endswith(_PROSE_SUFFIXES) for t in targets)


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """``(texte hors heredoc, corps de heredoc)`` d'une commande.

    Un heredoc ne retire du texte que son corps et sa ligne de fermeture ; la fin de la ligne
    d'ouverture reste une commande. ``<<`` n'ouvre un heredoc que s'il n'est ni entre guillemets
    (``grep '<<EOF'``, ``python -c 'print(1 << 2)'``) ni dans une expression arithmétique
    (``$((1<<3))``), et seulement si une ligne ne contenant que le délimiteur le ferme : sans elle,
    rien n'est avalé et les lignes suivantes restent des commandes (fail-closed, W1-08b-T2-02).
    """
    head = ""
    bodies: list[str] = []
    pos = 0
    while True:
        match = _HEREDOC_OPEN.search(command, pos)
        if match is None:
            return head + command[pos:], bodies
        before = head + command[pos : match.start()]
        quote = _quote_states(before + "x")[0][-1]
        if quote or _in_arithmetic(before):
            head, pos = before + command[match.start() : match.start() + 2], match.start() + 2
            continue
        delimiter = match.group(1) or match.group(2) or match.group(3)
        closing = re.compile(r"\n[ \t]*" + re.escape(delimiter) + r"[ \t]*(?=\n|\Z)")
        end = closing.search(command, match.end())
        if end is None:
            head, pos = before + command[match.start() : match.start() + 2], match.start() + 2
            continue
        if not _writes_prose_only(before.rsplit("\n", 1)[-1], match.group(4)):
            bodies.append(command[match.end() + 1 : end.start()])
        head, pos = before + match.group(4), end.end()


def _after_command(state: _Dirs, tokens: Sequence[str], dash: PurePath | None) -> set[_Dirs]:
    """Les positions possibles du shell après une commande (``cd``, ``pushd``, ``popd`` ou rien)."""
    if tokens and tokens[0] == "popd":
        if len(tokens) > 1:  # `popd -n`, `popd +2` : on ne sait pas laquelle de la pile bouge
            return {state} | _after_command(state, tokens[:1], dash)
        if state.stack:
            return {_Dirs(state.stack[0], state.cwd, state.stack[1:])}
        # pile vide : le `pushd` vient d'un autre appel, comme `cd -` sans précédent
        return {_Dirs(dash, state.cwd)} if dash is not None else {state}
    target = _cd_target(tokens)
    if target is None:
        return {state}
    if target != "-":
        moved = _Dirs(_resolve(target, state.cwd), state.cwd, state.stack)
    elif state.previous is not None:  # retour au répertoire d'avant le dernier cd de la commande
        moved = _Dirs(state.previous, state.cwd, state.stack)
    elif dash is not None:  # précédent inconnu : l'appelant essaie `dash` (le dépôt) puis ne bouge pas
        moved = _Dirs(dash, state.cwd, state.stack)
    else:
        moved = state
    if tokens[0] == "pushd":
        moved = _Dirs(moved.cwd, moved.previous, (state.cwd, *state.stack))
    return {moved}


def _segment_candidates(segment: str, matches: Sequence[re.Match[str]], words: Sequence[str]) -> list[str]:
    """Les fragments de chemin d'un segment : ceux du texte brut, puis ceux des mots recomposés (W1-08b-T3-02).

    Le texte brut est coupé aux guillemets, ``=``, ``,`` et accolades : ``..'/run0/x'``, ``../"run0"/x`` ou
    ``../{run0,run2}/x`` n'y donnent que des morceaux qui ne désignent rien. Le mot recomposé
    (guillemets retirés, accolades développées) est jugé en plus, entier et par fragments.
    """
    found: dict[str, None] = {}
    for match in matches:
        word = match.group()
        if word.startswith(("-", "$", "~")):
            continue
        if _NAME_CHAR.search(word) or (word == "/" and _standalone(segment, match)):
            found[word] = None
    for word in words:
        for variant in _word_variants(word):
            parts = [variant] if not any(c.isspace() for c in variant) else []
            parts.extend(_FRAGMENT.findall(variant))
            for part in parts:
                if part.startswith(("-", "$", "~")):
                    continue
                if _NAME_CHAR.search(part) or part == variant == "/":
                    found[part] = None
    return list(found)


def _shell_references(
    command: str, cwd: PurePath, dash: PurePath | None = None
) -> tuple[list[tuple[str, PurePath, bool]], set[PurePath]]:
    """``(fragment, chemin résolu, parcours récursif)`` d'une commande Bash, et les cwd finaux possibles.

    ``cd``/``pushd`` change le répertoire des segments suivants (``&&``, ``;``,
    saut de ligne), mais il peut échouer et laisser le shell en place : ``&&`` ne regarde
    que le succès du précédent, ``||`` les positions où un segment de la chaîne a pu
    échouer, ``;`` et le saut de ligne toutes les positions possibles
    (W1-08b-T1-02). Pas de déplacement dans un sous-shell ``( ... )`` ou un élément de tube
    (``|``), qui n'en laissent rien derrière eux (W1-08b-S3-01) ; ``popd``
    dépile le ``pushd`` de la commande. Le dernier élément d'un tube s'exécute dans
    le shell courant sous zsh (ou ``lastpipe``) : son ``cd`` laisse les deux
    répertoires candidats, de même qu'une parenthèse jamais refermée. Les corps de
    heredoc (code écrit ou lancé) sont lus sans parcours récursif : une barre
    oblique de commentaire ou de division (``//``, ``/``) n'y désigne pas la
    racine. Un fragment sans caractère de nom (``//``, ``///``, ``/**``) n'est
    jamais une référence ; ``/`` seul l'est uniquement comme argument à part
    entière de la commande (``find / -name x``). ``cd -`` (et ``popd`` sans
    ``pushd`` dans la commande) revient au répertoire d'avant le dernier ``cd`` de la
    commande ; s'il n'y en a pas (le précédent vient d'un autre appel), il mène à
    *dash*, ou ne bouge pas si *dash* est ``None`` : l'appelant joue les deux
    (fail-closed). Un ensemble de positions remplace le cwd unique, chaque
    segment est jugé contre chacune.
    """
    head, bodies = _split_heredocs(command)  # le corps part, la fin de la ligne d'ouverture reste
    recursive = bool(_RECURSIVE.search(head))
    refs: list[tuple[str, PurePath, bool]] = []
    pieces, unclosed = _split_command(_join_continuations(head))
    # Où est le shell quand le segment démarre (*entry*) et après le segment précédent s'il a réussi
    # (*ok*). Un ``cd`` peut échouer et laisser le shell en place : ``&&`` ne regarde que le succès,
    # ``||`` que les positions où un segment de la chaîne a pu échouer (*chain*), ``;`` et le saut de
    # ligne toutes les positions possibles (*visited*) (W1-08b-T1-02).
    entry: set[_Dirs] = {_Dirs(cwd)}
    ok = entry
    visited: set[_Dirs] = set()
    chain: set[_Dirs] = set()
    ored = False
    left_ok: set[_Dirs] | None = None  # succès de la partie gauche d'un ``||`` en cours (W1-08b-T2-01)
    scopes: list[
        tuple[set[_Dirs], set[_Dirs], set[_Dirs], bool, bool]
    ] = []  # à l'ouverture d'un sous-shell : ok, visited, chain, ored et s'il est entre guillemets doubles
    for index, (separator, segment) in enumerate(pieces):
        matches = list(_FRAGMENT.finditer(segment))
        tokens = [m.group() for m in matches]
        words = _shell_words(segment)
        if separator in (";", "\n"):
            if not tokens:  # ligne vide, ``;`` final : pas de commande
                continue
            entry = visited | ok
            visited, chain, ored, left_ok = set(), set(), False, None
        elif separator == "||":
            entry, ored, left_ok = chain or ok, True, ok
        elif separator in ("(", "(dq"):
            scopes.append((ok, visited, chain, ored, separator == "(dq"))
            entry, visited, chain, ored = ok, set(), set(), False
        elif separator in (")", ")dq") and scopes and (separator == ")" or scopes[-1][4]):
            ok, visited, chain, ored, _ = scopes.pop()  # le sous-shell ne déplace pas le shell extérieur
            entry, visited, chain = ok, visited | ok, chain | ok
        elif separator != "|":  # début, ``&&``, parenthèse sans paire : la suite d'un succès
            entry = ok
        # ``|`` : tous les éléments d'un tube partent de la même position.
        candidates = _segment_candidates(segment, matches, words)
        for here in sorted({state.cwd for state in entry}):
            refs.extend((word, _resolve(word, here), recursive) for word in candidates)
        command_words = words[1:] if words[:1] in (["{"], ["!"]) else words  # `{ cd src; }`, `! cd x`
        moved = {after for state in entry for after in _after_command(state, command_words, dash)}
        if index + 1 < len(pieces) and pieces[index + 1][0] == "|":  # élément de tube : sous-shell
            ok = entry
        else:  # dernier élément d'un tube : zsh (ou lastpipe) l'exécute dans le shell courant
            ok = entry | moved if separator == "|" else moved
            if left_ok is not None:  # ``a || b`` réussit par ``a`` (``b`` sauté) ou par ``b``
                ok, left_ok = ok | left_ok, None
        visited, chain = visited | entry | ok, chain | entry
    finals_states = ok | visited if ored else ok
    for outer_ok, *_ in scopes:  # sous-shell jamais refermé : l'entrée reste possible
        finals_states = finals_states | outer_ok
    if unclosed:
        finals_states = finals_states | {_Dirs(cwd)}
    finals = {state.cwd for state in finals_states}
    for body in bodies:
        for here in sorted(finals):
            for token in _FRAGMENT.findall(body):
                if not token.startswith(("-", "$", "~")) and _NAME_CHAR.search(token):
                    refs.append((token, _resolve(token, here), False))
    return refs, finals


def _tool_references(
    name: str, tool_input: dict[str, Any], cwd: PurePath, dash: PurePath | None = None
) -> tuple[list[tuple[str, PurePath, bool]], set[PurePath]]:
    """``(fragment, chemin résolu, parcours récursif)`` pour un appel d'outil, et les cwd suivants possibles."""
    refs: list[tuple[str, PurePath, bool]] = []
    finals = {cwd}

    def add(value: Any, *, base: PurePath = cwd, recursive: bool = False) -> None:
        if isinstance(value, str) and value:
            refs.append((value, _resolve(value, base), recursive))

    if name in _WRITE_TOOLS:
        add(tool_input.get("file_path"))
        add(tool_input.get("notebook_path"))
    elif name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            shell_refs, finals = _shell_references(command, cwd, dash)
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
    return refs, finals


def _excerpt(tool_input: dict[str, Any]) -> str:
    text = json.dumps(tool_input, ensure_ascii=False)
    return text if len(text) <= _EXCERPT_MAX else text[: _EXCERPT_MAX - 1] + "…"


_CWD_RESET = re.compile(r"Shell cwd was reset to (.+?)\s*$")


def _result_text(content: Any) -> str:
    """Le texte d'un ``tool_result`` (chaîne, ou liste de blocs ``text``)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class _ShellCwd:
    """Le répertoire courant du shell de l'agent, calqué sur Claude Code (W1-08b-S1-02).

    Dans une commande, un ``cd`` vaut pour les segments suivants. D'un appel
    ``Bash`` au suivant, le répertoire final n'est gardé que s'il reste dans le
    dépôt du run ; sinon Claude Code ramène le shell au dépôt et l'écrit dans le
    ``tool_result`` (``Shell cwd was reset to <dépôt>``). Cette réinitialisation
    n'est pas systématique (un ``cd`` vers le scratchpad de l'agent y échappe dans
    certaines transcriptions archivées) : tant que le ``tool_result`` ne tranche
    pas, les DEUX répertoires restent candidats et une référence relative est
    jugée contre chacun (fail-closed). Seul le message tranche, vers le répertoire
    qu'il nomme. Son absence ne prouve pas que le shell soit resté dehors
    (W1-08b-S2-01) : sur les 483 transcriptions archivées, 160 résultats
    portent le message, tous réussis, et aucun des 383 résultats en erreur ; après
    un ``cd <tâche>; find ..`` en erreur, la commande suivante a tourné dans le dépôt. Un résultat en erreur
    ou sans message laisse donc le dépôt candidat à côté du répertoire où la
    commande a fini. ``cd -`` est joué des deux
    façons (précédent inconnu : pas de déplacement, ou retour au dépôt).
    """

    def __init__(self, home: PurePath, exposure: _Exposure):
        self.home = home
        self.exposure = exposure
        self.candidates: set[PurePath] = {home}
        self._pending: tuple[object, set[PurePath]] | None = None

    def restart(self, cwd: PurePath) -> None:
        self.candidates = {cwd}
        self._pending = None

    def references(
        self, name: str, tool_input: dict[str, Any], tool_use_id: object
    ) -> list[tuple[str, PurePath, bool]]:
        refs: list[tuple[str, PurePath, bool]] = []
        finals: set[PurePath] = set()
        command = tool_input.get("command")
        back = name == "Bash" and isinstance(command, str) and _CD_DASH.search(command) is not None
        for cwd in sorted(self.candidates):
            for dash in (None, self.home) if back else (None,):
                found, final = _tool_references(name, tool_input, cwd, dash)
                refs.extend(found)
                finals |= final
        if name != "Bash":
            return refs
        self._pending = None
        outside = {c for c in finals if not self.exposure.inside_run_dir(c)}
        if outside:
            self._pending = (tool_use_id, finals)
            finals = finals | {self.home}
        self.candidates = finals
        return refs

    def settle(self, tool_use_id: object, text: str) -> None:
        """Tranche, d'après le résultat de la dernière commande, où le shell se trouve.

        Le message de réinitialisation est le seul signal positif : il ramène au dépôt
        qu'il nomme. Sans lui, le dépôt reste candidat (résultat en erreur compris : le
        corpus n'y porte jamais le message alors que le shell est revenu au dépôt).
        """
        if self._pending is None or tool_use_id is None or self._pending[0] != tool_use_id:
            return
        _, finals = self._pending
        self._pending = None
        last = next((line for line in reversed(text.splitlines()) if line.strip()), "")
        reset = _CWD_RESET.search(last)
        self.candidates = {_lexical(reset.group(1))} if reset else finals | {self.home}


@dataclass(frozen=True)
class _Scan:
    """Ce que la lecture d'une transcription a rendu : lectures, lignes illisibles, événements reconnus."""

    reads: list[HiddenTestRead]
    unreadable: int  # lignes qui portent une accolade mais ne se décodent pas en objet JSON
    events: int  # événements ``system/init`` ou ``assistant`` décodés


def find_hidden_test_reads(
    lines: Iterable[str],
    *,
    test_files: Sequence[str],
    run_dir: Path,
    hidden_dir: Path,
    canonical_dirs: Sequence[Path],
    other_run_dirs: Sequence[Path] = (),
) -> list[HiddenTestRead]:
    """Les appels d'outil d'une transcription stream-json qui désignent un test caché.

    *run_dir* est le dépôt de l'agent (son répertoire courant de départ),
    *hidden_dir* le dossier ``hidden-tests`` de la tâche, *other_run_dirs* les
    autres dossiers ``run<i>`` de la même tâche (ils contiennent les tests
    copiés par ``run_hidden_tests``), *canonical_dirs* les dossiers d'exercice
    du clone ``polyglot-benchmark`` d'où viennent les tests (``task.exercise_dir``,
    chemin absolu) : obligatoire, car l'oublier laisse la source canonique
    lisible sans drapeau. Une liste vide est une transcription propre SI la
    transcription a été lue en entier : ``check_transcript`` rend ``unevaluated``
    quand des lignes sont illisibles ou qu'aucun événement n'a été reconnu.
    """
    return _scan(
        lines,
        test_files=test_files,
        run_dir=run_dir,
        hidden_dir=hidden_dir,
        canonical_dirs=canonical_dirs,
        other_run_dirs=other_run_dirs,
    ).reads


def _scan(
    lines: Iterable[str],
    *,
    test_files: Sequence[str],
    run_dir: Path,
    hidden_dir: Path,
    canonical_dirs: Sequence[Path],
    other_run_dirs: Sequence[Path],
) -> _Scan:
    exposure = _Exposure(test_files, run_dir, hidden_dir, other_run_dirs, canonical_dirs)
    shell = _ShellCwd(_lexical(run_dir), exposure)
    found: list[HiddenTestRead] = []
    unreadable = events = 0
    for number, raw in enumerate(lines, start=1):
        try:
            event = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            # Une ligne de stderr (``Popen(stderr=STDOUT)``) peut couper un événement : ses morceaux
            # portent l'accolade ouvrante ou fermante de l'objet. Un texte sans accolade (la bannière
            # « Ignoring N permissions.allow entries... », 389 des 449 transcriptions archivées) n'en cache pas.
            if isinstance(raw, str) and ("{" in raw or "}" in raw):
                unreadable += 1
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant" or (event.get("type") == "system" and event.get("subtype") == "init"):
            events += 1
        if event.get("type") == "system" and event.get("subtype") == "init" and isinstance(event.get("cwd"), str):
            shell.restart(_lexical(event["cwd"]))
            continue
        content = (event.get("message") or {}).get("content")
        if event.get("type") == "user":
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    shell.settle(block.get("tool_use_id"), _result_text(block.get("content")))
            continue
        if event.get("type") != "assistant":
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            name = str(block.get("name") or "?")
            refs = shell.references(name, tool_input, block.get("id"))
            hits = [
                (kind, token)
                for token, path, recursive in refs
                if (kind := exposure.classify(path, is_glob=any(c in token for c in _GLOB_CHARS), recursive=recursive))
            ]
            if hits:  # une ligne déclenchante par appel d'outil : la référence la plus précise
                kind, token = min(hits, key=lambda hit: _KIND_RANK[hit[0]])
                found.append(HiddenTestRead(number, name, kind, token, _excerpt(tool_input)))
    return _Scan(found, unreadable, events)


def check_transcript(
    log_path: Path, *, test_files: Sequence[str], run_dir: Path, hidden_dir: Path, canonical_dirs: Sequence[Path]
) -> tuple[str, list[dict[str, Any]] | None]:
    """Verdict d'un run : ``("clean", [])``, ``("invalid", [lectures])`` ou ``("unevaluated", None)``.

    ``unevaluated`` : transcription absente, vide, sans événement ``system/init`` ni
    ``assistant`` reconnu, ou avec une ligne illisible qui porte une accolade (un
    événement coupé par une ligne de stderr peut cacher un ``tool_use``). Un contrôle
    qui n'a pas tout lu ne rend jamais « propre » ; une lecture déjà trouvée reste
    ``invalid``, quelles que soient les lignes illisibles. Les autres dossiers
    ``run<i>`` de la tâche (frères de *run_dir* sous ``hidden_dir.parent``) sont exposés
    au même titre que ``hidden-tests`` : ils contiennent les tests copiés. Les
    *canonical_dirs* (``task.exercise_dir``) le sont aussi : le test canonique
    du clone ``polyglot-benchmark`` est la même fuite.
    """
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ("unevaluated", None)
    if not any(line.strip() for line in lines):
        return ("unevaluated", None)
    others = sorted(d for d in hidden_dir.parent.glob("*/run*") if d.is_dir() and d != run_dir)
    scan = _scan(
        lines,
        test_files=test_files,
        run_dir=run_dir,
        hidden_dir=hidden_dir,
        canonical_dirs=canonical_dirs,
        other_run_dirs=others,
    )
    if scan.reads:
        return ("invalid", [read.to_dict() for read in scan.reads])
    return ("unevaluated", None) if scan.unreadable or not scan.events else ("clean", [])


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


def unevaluated_runs_rows(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Lignes de rapport des runs dont la transcription n'a pas pu être contrôlée, triées."""
    return [
        {"task_id": r.task_id, "arm": r.arm, "run_index": r.run_index, "success": r.success}
        for r in sorted(records, key=lambda r: (r.task_id, r.arm, r.run_index))
        if r.transcript_check == "unevaluated"
    ]


def render_unevaluated_runs(unevaluated_runs: Sequence[dict[str, Any]]) -> list[str]:
    """Section Markdown « Transcriptions non évaluées » ; liste vide s'il n'y en a aucune.

    Choix : ces runs RESTENT dans les taux. Une transcription illisible n'est pas la preuve d'une
    lecture de test caché, et les retirer en silence réduirait l'échantillon sur la foi d'un
    défaut du journal ; mais le contrôle n'a pas conclu, donc le rapport les nomme.
    """
    if not unevaluated_runs:
        return []
    per_arm: dict[str, int] = {}
    for run in unevaluated_runs:
        per_arm[run["arm"]] = per_arm.get(run["arm"], 0) + 1
    lines = [
        "## Transcriptions non évaluées (W1-08b)",
        "",
        "Le contrôle de lecture de test caché n'a pas pu conclure pour ces runs (transcription absente, "
        "vide, sans événement reconnu, ou avec une ligne illisible qui peut cacher un appel d'outil). "
        "Ils ne sont PAS exclus des taux ci-dessus : à vérifier à la main avant de s'y fier. "
        "Par bras : " + ", ".join(f"{arm} {count}" for arm, count in sorted(per_arm.items())) + ".",
        "",
    ]
    for run in unevaluated_runs:
        outcome = "réussi" if run["success"] else "échoué"
        lines.append(f"- `{run['task_id']}` / `{run['arm']}` / run{run['run_index']} (brut : {outcome})")
    lines.append("")
    return lines


def unevaluated_run_alert(record: Any) -> str:
    """Message d'alerte (stderr) quand la transcription d'un run n'a pas pu être contrôlée."""
    return (
        f"[NON ÉVALUÉ] {record.task_id} / {record.arm} / run{record.run_index} : la transcription n'a pas pu être "
        "contrôlée (absente, vide, sans événement reconnu ou ligne illisible) — le run reste dans les taux, à revoir."
    )
