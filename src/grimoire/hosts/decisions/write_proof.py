"""Preuve d'écriture : dit si une commande shell écrit, de façon prouvée, sur une cible protégée (#1173).

La garde d'autorité (:mod:`.authority_guard`) refuse toute commande qui nomme la zone de contrôle. Ce module ne
décide **jamais** d'un refus ni d'une autorisation : il ne dit que si le refus doit aussi révoquer le mandat. La
révocation n'a lieu que pour une écriture prouvée ; tout ce qui n'est pas prouvé (lecture, mention, forme
incertaine ou illisible, langage non lu) rend faux, et la commande reste refusée sans révocation.

Formes d'écriture reconnues, la cible étant donnée par un :class:`Target` :

- redirection de sortie (``>``, ``>>``, ``>|``, ``&>``, ``&>>``, ``N>``, ``>&fichier``, ``<>``) ;
- ``tee``, ``sponge``, ``sed -i``, ``dd of=``, ``truncate``, ``touch``, ``mkdir``, ``rm``, ``rmdir``, ``unlink``,
  ``shred``, ``chmod``, ``chown``, ``chgrp``, ``mv``, ``rename`` (un opérande dans la cible) ;
- ``cp``, ``install``, ``rsync``, ``ln`` dont la destination est dans la cible ;
- ``find`` avec ``-delete``/``-exec``/``-execdir``/``-ok``/``-okdir`` sur la cible, ou ``-fprint*``/``-fls`` vers elle ;
- ``curl -o``, ``wget -O/-P``, ``sort -o`` vers la cible ;
- ``python`` qui ouvre en ``w``/``a``/``x``/``+``, appelle ``write_text``/``write_bytes``/``unlink``/``rename``/
  ``touch``/``mkdir``/``rmdir``/``chmod``/``symlink_to``, ``os.remove``/``os.rename``/``shutil.rmtree``/… ou
  ``shutil.copy*`` vers un chemin de la cible, ou lance une des commandes ci-dessus par ``subprocess``/``os.system`` ;
  ``fileinput`` en place, un gestionnaire de journal sur fichier, ``sqlite3.connect``, ``os.open`` qui écrit,
  ``zipfile``/``tarfile``/``shelve``/``dbm`` ouverts en écriture, une extraction d'archive, ``os.symlink``/
  ``os.link``, un fichier temporaire créé dans la cible (#1233) ;
- ``sed`` (``w``, ``W``, ``s///w``), ``awk`` (redirection dans le programme), ``perl``/``node``/``ruby``/``php``/
  ``lua``/``deno``/``bun`` à exécution en ligne : voir :mod:`.write_proof_languages` (#1233) ;
- ``git``, archives et compresseurs, ``scp``, ``mktemp``, éditeurs en mode script, ``sqlite3``… : voir
  :mod:`.write_proof_programs` (#1233) ; ``mkfifo``, ``mknod``, ``fallocate``, ``shuf -o``, ``iconv -o``,
  ``pandoc -o``.

Ce que l'analyse traverse pour atteindre ces formes : préfixes d'affectation (``VAR=x cmd``) et enveloppes
(``env``, ``sudo``, ``nohup``, ``timeout``, ``nice``, ``command``, ``exec``, ``time``, ``flock``, ``strace``,
``rtk``, ``rtk proxy``…) ; variables affectées dans la commande (``F=…; echo x > "$F"``) ; ``cd``/``pushd`` ;
substitutions de commande et de processus ; ``sh -c``, ``eval``, ``source`` ; un script littéral envoyé à un shell
ou à python (tube depuis ``echo``/``printf``, heredoc, here-string, fichier écrit plus tôt dans la même commande) ;
``read`` sur une entrée littérale ; ``xargs``.

Ce qui n'est **pas** reconnu tombe en refus sans révocation : les langages non lus (PowerShell, ``osascript``…),
les écrivains hors liste, une variable que la commande n'affecte pas, un chemin que du code calcule.

Analyse de texte pure : aucune E/S ici (la résolution des chemins est le fait du :class:`Target`).
"""

from __future__ import annotations

import ast
import re
import shlex
import textwrap
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING, Protocol

from grimoire.hosts.decisions.write_proof_languages import language_writes
from grimoire.hosts.decisions.write_proof_programs import program_writes

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Target", "proves_write"]


class Target(Protocol):
    """La cible protégée, vue comme un prédicat sur un mot de commande."""

    def names(self, word: str, cd: tuple[str, ...]) -> bool:
        """Vrai si *word* désigne la cible ou ce qu'elle contient, *cd* étant les ``cd`` déjà lus."""
        ...

    def carries(self, word: str, cd: tuple[str, ...]) -> bool:
        """Vrai si *word* est le dossier qui porte la cible (le supprimer ou le déplacer l'emporte)."""
        ...


#: Profondeur maximale de scripts imbriqués (``sh -c`` dans ``eval`` dans ``$(…)``…) ; au-delà : pas de preuve.
_MAX_DEPTH = 6
#: Valeur qu'on ne sait pas lire (le résultat d'une substitution de commande).
_UNKNOWN = "\x00"
#: Préfixe d'un mot qui est une substitution de processus : ``\x01<n>`` renvoie à ``_Proof.procsubs[n]``.
_PROCSUB = "\x01"
#: Marque d'un antislash lu hors guillemets, devant le caractère qu'il précède (voir :func:`_readings`).
_ESCAPED = "\x02"
#: ``%s`` d'un format ``printf`` (``%%`` n'en est pas un).
_PRINTF_STRING = re.compile(r"(?<!%)%s")

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_HEREDOC_TAG = re.compile(r"""\\?(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1""")
_PYTHON = re.compile(r"^(?:python|pypy)[0-9.]*$")
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash"})
_KEYWORDS = frozenset({"{", "}", "!", "then", "do", "else", "elif", "if", "while", "until"})
_DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly"})

#: Enveloppes : ``nom -> (options qui prennent une valeur séparée, nombre d'arguments positionnels propres)``.
_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "sudo": (
        frozenset(
            {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-D", "--chdir",
             "-R", "--chroot", "-r", "--role", "-t", "--type", "-T", "--command-timeout", "-U", "--other-user"}
        ),
        0,
    ),
    "doas": (frozenset({"-u", "-C"}), 0),
    "nohup": (frozenset(), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "ionice": (frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid"}), 0),
    "command": (frozenset(), 0),
    "builtin": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "time": (frozenset({"-o", "--output", "-f", "--format"}), 0),
    "setsid": (frozenset(), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e"}), 0),
    "flock": (frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"}), 1),
    "strace": (frozenset({"-o", "-e", "-p", "-s", "-a", "-u", "-E", "-P", "-I", "-O", "-S", "-X"}), 0),
    "busybox": (frozenset(), 0),
}

#: Programmes dont tout opérande est écrit : ``nom -> options dont la valeur séparée n'est pas un fichier écrit``.
_ANY_OPERAND: dict[str, frozenset[str]] = {
    "tee": frozenset(),
    "sponge": frozenset(),
    "touch": frozenset({"-r", "--reference", "-d", "--date", "-t"}),
    "truncate": frozenset({"-s", "--size", "-r", "--reference"}),
    "mkdir": frozenset({"-m", "--mode"}),
    "rm": frozenset(),
    "rmdir": frozenset(),
    "unlink": frozenset(),
    "shred": frozenset({"-n", "--iterations", "-s", "--size"}),
    "chmod": frozenset(),
    "chown": frozenset(),
    "chgrp": frozenset(),
    "mv": frozenset({"-S", "--suffix"}),
    "rename": frozenset(),
    "mkfifo": frozenset({"-m", "--mode"}),
    "mknod": frozenset({"-m", "--mode"}),
    "fallocate": frozenset({"-l", "--length", "-o", "--offset"}),
}
#: Parmi eux, ceux pour qui nommer le dossier qui porte la cible suffit (``rm -rf``, ``mv``, ``chmod -R``).
_CARRIERS = frozenset({"rm", "rmdir", "mv", "rename", "chmod", "chown", "chgrp"})
#: Programmes qui écrivent leur destination : ``nom -> options à valeur séparée qui ne sont pas des opérandes``.
_DESTINATION: dict[str, frozenset[str]] = {
    "cp": frozenset({"-S", "--suffix"}),
    "install": frozenset({"-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix"}),
    "rsync": frozenset({"-e", "--rsh", "-f", "--filter", "--exclude", "--include", "--exclude-from", "--include-from"}),
    "ln": frozenset({"-S", "--suffix"}),
}
_TARGET_DIR = frozenset({"-t", "--target-directory"})
#: Programmes qui écrivent le fichier donné à une option de sortie.
_OUTPUT_OPTIONS: dict[str, frozenset[str]] = {
    "curl": frozenset({"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar"}),
    "wget": frozenset(
        {"-O", "--output-document", "-P", "--directory-prefix", "-o", "--output-file", "-a", "--append-output"}
    ),
    "sort": frozenset({"-o", "--output"}),
    "shuf": frozenset({"-o", "--output"}),
    "iconv": frozenset({"-o", "--output"}),
    "pandoc": frozenset({"-o", "--output"}),
}
_FIND_ACTIONS = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir"})
_FIND_OUTPUTS = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
_SED_IN_PLACE = re.compile(r"^-[nrsuzE]*i")
_SED_SCRIPT_OPTIONS = frozenset({"-e", "--expression", "-f", "--file"})
_XARGS_VALUED = frozenset(
    {"-n", "--max-args", "-P", "--max-procs", "-L", "--max-lines", "-d", "--delimiter", "-a", "--arg-file", "-E",
     "-s", "--max-chars"}
)

#: Python : méthodes de chemin (le receveur est le fichier), fonctions (un argument est le fichier), copies.
_PY_PATH_METHODS = frozenset(
    {"write_text", "write_bytes", "unlink", "rename", "touch", "mkdir", "rmdir", "chmod", "symlink_to", "hardlink_to"}
)
_PY_FUNCTIONS = frozenset(
    {"remove", "unlink", "rename", "replace", "rmtree", "makedirs", "mkdir", "rmdir", "chmod", "chown", "truncate",
     "removedirs", "move"}
)
_PY_COPIES = frozenset({"copy", "copy2", "copyfile", "copytree"})
_PY_MODULES = frozenset({"os", "shutil"})
_PY_LAUNCHERS = frozenset(
    {"run", "call", "check_call", "check_output", "Popen", "system", "popen", "getoutput", "getstatusoutput"}
)
_PY_MODE = re.compile(r"^[rwaxbtU+]{1,4}$")


class _TooDeep(Exception):  # noqa: N818 — signal interne : imbrication au-delà de ce que l'analyse suit
    """La commande imbrique plus de scripts que :data:`_MAX_DEPTH` : pas de preuve."""


@dataclass
class _Simple:
    """Une commande simple : ses mots, les cibles de ses redirections de sortie, son entrée littérale."""

    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    #: Corps de heredoc ou here-string lu sur l'entrée standard ; ``None`` sinon.
    stdin: str | None = None
    #: Fichier donné par ``< fichier``.
    stdin_file: str = ""
    #: La commande en amont du tube, quand il y en a une.
    upstream: _Simple | None = None


class _Lexer:
    """Découpe un texte shell en commandes simples, sans rien rejeter.

    Tolérant par construction : une citation jamais fermée est lue jusqu'au bout, une construction inconnue reste
    un mot. Les commandes des substitutions (``$(…)``, backticks, ``<(…)``, sous-shells) sont rendues à plat, avant
    la commande qui les porte. Une erreur de lecture ici ne peut que faire manquer une preuve d'écriture.
    """

    def __init__(self, text: str, procsubs: list[list[_Simple]]) -> None:
        self.text = text
        self.commands: list[_Simple] = []
        #: Table partagée par tous les scripts d'une même analyse : les index restent valables d'un script à l'autre.
        self.procsubs = procsubs
        self._heredocs: list[tuple[str, bool, _Simple]] = []

    def run(self) -> list[_Simple]:
        self._list(0, "", 0)
        return self.commands

    def _list(self, start: int, closer: str, depth: int) -> tuple[int, list[_Simple]]:
        """Lit les commandes depuis *start* jusqu'à *closer* (ou la fin) ; rend l'index suivant et ces commandes."""
        if depth > _MAX_DEPTH:
            raise _TooDeep
        text, n = self.text, len(self.text)
        produced: list[_Simple] = []
        cmd = _Simple()
        word: list[str] = []
        in_word = False
        redirect = ""
        piped: _Simple | None = None
        parens = 0
        i = start

        def end_word() -> None:
            nonlocal in_word, redirect
            if not in_word:
                return
            value = "".join(word)
            word.clear()
            in_word = False
            if not redirect:
                cmd.words.append(value)
            elif redirect == "<<<":
                cmd.stdin = value.replace(_ESCAPED, "") + "\n"
            elif redirect == "<":
                cmd.stdin_file = value
            elif redirect != "<&" and not (redirect == ">&" and (value.isdigit() or value == "-")):
                cmd.writes.append(value)
            redirect = ""

        def end_command(*, pipe: bool = False) -> None:
            nonlocal cmd, piped, redirect
            end_word()
            redirect = ""
            if cmd.words or cmd.writes or cmd.stdin is not None or cmd.stdin_file:
                cmd.upstream = piped
                self.commands.append(cmd)
                produced.append(cmd)
                piped = cmd if pipe else None
            else:
                piped = None
            cmd = _Simple()

        def open_redirect(op: str) -> None:
            nonlocal redirect, in_word
            if in_word and "".join(word).isdigit():  # ``2>`` : descripteur collé
                word.clear()
                in_word = False
            end_word()
            redirect = op

        while i < n:
            ch = text[i]
            if closer == "`" and ch == "`":
                end_command()
                return i + 1, produced
            if ch == "'":
                end = text.find("'", i + 1)
                end = n if end < 0 else end
                word.append(text[i + 1 : end])
                in_word = True
                i = end + 1
            elif ch == '"':
                i = self._double(i + 1, word, depth)
                in_word = True
            elif ch == "\\":
                if text.startswith("\\\n", i):
                    i += 2
                    continue
                # L'antislash hors guillemets est marqué, pas tranché : échappement POSIX ou séparateur d'un
                # chemin Windows, le mot sera lu des deux façons (:func:`_readings`).
                word.append(_ESCAPED + text[i + 1 : i + 2])
                in_word = True
                i += 2
            elif ch == "#" and not in_word:
                end = text.find("\n", i)
                i = n if end < 0 else end
            elif ch in " \t\r":
                end_word()
                i += 1
            elif ch == "\n":
                end_command()
                i += 1
                if self._heredocs:
                    i = self._bodies(i)
            elif ch == ";":
                end_command()
                i += 1
            elif ch == "|":
                if text.startswith("||", i):
                    end_command()
                    i += 2
                else:
                    end_command(pipe=True)
                    i += 2 if text.startswith("|&", i) else 1
            elif ch == "&":
                if text.startswith("&>", i):
                    op = "&>>" if text.startswith("&>>", i) else "&>"
                    open_redirect(op)
                    i += len(op)
                else:
                    end_command()
                    i += 2 if text.startswith("&&", i) else 1
            elif ch == "`":
                i, _ = self._list(i + 1, "`", depth + 1)
                word.append(_UNKNOWN)
                in_word = True
            elif text.startswith("$(", i) and not text.startswith("$((", i):
                i, _ = self._list(i + 2, ")", depth + 1)
                word.append(_UNKNOWN)
                in_word = True
            elif ch in "<>" and text.startswith("(", i + 1):
                i, inner = self._list(i + 2, ")", depth + 1)
                self.procsubs.append(inner)
                word.append(f"{_PROCSUB}{len(self.procsubs) - 1}")
                in_word = True
            elif ch == "(":
                end_command()
                parens += 1
                i += 1
            elif ch == ")":
                end_command()
                if parens == 0 and closer == ")":
                    return i + 1, produced
                parens = max(0, parens - 1)
                i += 1
            elif ch == "<":
                if text.startswith("<<<", i):
                    open_redirect("<<<")
                    i += 3
                elif text.startswith("<<", i):
                    end_word()
                    i = self._open_heredoc(i + 2, cmd)
                else:
                    op = next(o for o in ("<>", "<&", "<") if text.startswith(o, i))
                    open_redirect(op)
                    i += len(op)
            elif ch == ">":
                op = next(o for o in (">>", ">|", ">&", ">") if text.startswith(o, i))
                open_redirect(op)
                i += len(op)
            else:
                word.append(ch)
                in_word = True
                i += 1
        end_command()
        return n, produced

    def _double(self, start: int, word: list[str], depth: int) -> int:
        """Lit l'intérieur d'un guillemet double ouvert avant *start* ; rend l'index après sa fermeture."""
        text, n = self.text, len(self.text)
        i = start
        while i < n and text[i] != '"':
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                if nxt != "\n":
                    word.append(nxt if nxt in '"\\$`' else ch + nxt)
                i += 2
            elif ch == "`":
                i, _ = self._list(i + 1, "`", depth + 1)
                word.append(_UNKNOWN)
            elif text.startswith("$(", i) and not text.startswith("$((", i):
                i, _ = self._list(i + 2, ")", depth + 1)
                word.append(_UNKNOWN)
            else:
                word.append(ch)
                i += 1
        return i + 1

    def _open_heredoc(self, start: int, owner: _Simple) -> int:
        """Lit le tag d'un heredoc (``<<`` déjà passé) ; son corps sera lu au prochain saut de ligne."""
        text, n = self.text, len(self.text)
        i = start
        strip_tabs = text.startswith("-", i)
        i += 1 if strip_tabs else 0
        while i < n and text[i] in " \t":
            i += 1
        tag = _HEREDOC_TAG.match(text, i)
        if tag is None:
            return i
        self._heredocs.append((tag.group(2), strip_tabs, owner))
        return tag.end()

    def _bodies(self, start: int) -> int:
        """Lit le corps de chaque heredoc en attente et le rend à sa commande."""
        text, n = self.text, len(self.text)
        i = start
        while self._heredocs:
            tag, strip_tabs, owner = self._heredocs.pop(0)
            lines: list[str] = []
            while i < n:
                end = text.find("\n", i)
                line = text[i:] if end < 0 else text[i:end]
                i = n if end < 0 else end + 1
                line = line.lstrip("\t") if strip_tabs else line
                if line == tag:
                    break
                lines.append(line)
            owner.stdin = "\n".join(lines) + "\n"
        return i


def _plain(word: str) -> str:
    """*word* tel qu'un shell POSIX le remet au programme : l'antislash hors guillemets est consommé."""
    return word.replace(_ESCAPED, "")


def _readings(word: str) -> tuple[str, ...]:
    """Les lectures d'un mot comme chemin : échappement consommé (POSIX), ou séparateur de dossier (Windows)."""
    if _ESCAPED not in word:
        return (word,)
    return (_plain(word), word.replace(_ESCAPED, "/"))


def _program(word: str) -> str:
    """Le nom du programme lancé par *word* : sans échappement (``\\rm``) ni dossier (``/bin/rm``)."""
    return _plain(word).rsplit("/", 1)[-1]


def _unwrap(words: list[str]) -> list[str]:
    """Les mots d'une commande sans mots-clés, préfixes d'affectation ni enveloppes ; ``[]`` s'il ne reste rien."""
    rest = list(words)
    for _ in range(8):
        while rest and (rest[0] in _KEYWORDS or _ASSIGNMENT.match(rest[0])):
            rest.pop(0)
        if not rest:
            return []
        name = _program(rest[0])
        if name == "rtk":  # proxy de terminal : ``rtk git …``, ``rtk proxy cmd``, ``rtk read f`` (= ``cat``)
            rest = rest[1:]
            while rest and rest[0].startswith("-") and rest[0] != "-":
                rest.pop(0)
            if rest[:1] == ["proxy"]:
                rest = rest[1:]
            elif rest[:1] in (["read"], ["grep"]):
                rest = ["cat" if rest[0] == "read" else "rg", *rest[1:]]
            continue
        spec = _WRAPPERS.get(name)
        if spec is None:
            return rest
        valued, positionals = spec
        split: list[str] = []
        i = 1
        while i < len(rest):
            arg = rest[i]
            if arg == "--":
                i += 1
                break
            if name == "command" and arg in ("-v", "-V"):
                return []  # ``command -v X`` cherche X, il ne le lance pas
            if name == "env" and arg in ("-S", "--split-string") and i + 1 < len(rest):
                split, i = shlex.split(rest[i + 1]), i + 2
                break
            if name == "env" and arg.startswith(("-S", "--split-string=")):
                split, i = shlex.split(arg.split("=", 1)[1] if arg.startswith("--") else arg[2:]), i + 1
                break
            if arg.startswith("-") and len(arg) > 1:
                i += 2 if arg in valued else 1
            elif positionals:
                positionals -= 1
                i += 1
            else:
                break
        rest = split + rest[i:]
    return rest


def _file_key(path: str) -> str:
    """Le nom sous lequel un fichier écrit par la commande est retrouvé (``./s.sh`` et ``s.sh`` sont le même)."""
    return _plain(path).removeprefix("./")


def _operands(args: list[str], valued: frozenset[str] = frozenset()) -> list[str]:
    """Les arguments qui ne sont ni une option ni la valeur séparée d'une option de *valued*."""
    found: list[str] = []
    literal = False
    i = 0
    while i < len(args):
        arg = args[i]
        if literal or arg == "-" or not arg.startswith("-"):
            found.append(arg)
        elif arg == "--":
            literal = True
        elif arg in valued:
            i += 1
        i += 1
    return found


def _option_values(args: list[str], names: frozenset[str]) -> list[str]:
    """Les valeurs des options *names* : ``-o v``, ``--output v``, ``--output=v``, ``-ov``."""
    found: list[str] = []
    for i, arg in enumerate(args):
        if arg in names:
            if i + 1 < len(args):
                found.append(args[i + 1])
            continue
        for name in names:
            if name.startswith("--"):
                if arg.startswith(name + "="):
                    found.append(arg[len(name) + 1 :])
            elif arg.startswith(name) and not arg.startswith("--"):
                found.append(arg[len(name) :])
    return found


def _py_write_mode(node: ast.AST | None) -> bool:
    """Vrai si *node* est un mode d'ouverture constant qui écrit (``w``, ``a``, ``x``, ``+``)."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    return bool(_PY_MODE.match(node.value)) and bool(set(node.value) & set("wax+"))


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _py_aliases(tree: ast.AST) -> dict[str, str]:
    """Les noms liés par les ``import`` du script : ``h -> logging.handlers``, ``FileHandler -> logging.FileHandler``."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update({a.asname: a.name for a in node.names if a.asname})
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update({a.asname or a.name: f"{node.module}.{a.name}" for a in node.names})
    return found


def _py_dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    """Le nom qualifié d'un appelé (``logging.handlers.RotatingFileHandler``), ou ``""`` s'il n'est pas nommé."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _py_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value)


def _py_constant(node: ast.AST | None, allowed: tuple[str, ...]) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value[:1] in allowed


_PY_WRITE_FLAGS = re.compile(r"O_(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)")
_PY_ARCHIVES = frozenset(
    {"zipfile.ZipFile", "zipfile.PyZipFile", "tarfile.open", "tarfile.TarFile", "tarfile.TarFile.open"}
)
_PY_DESTINATION_FUNCTIONS = frozenset({"os.symlink", "os.link"})
_PY_PATH_FUNCTIONS = frozenset({"os.mkfifo", "os.mknod", "os.utime", "os.lchown", "os.lchmod", "os.chflags"})


def _py_more(call: ast.Call, names: Callable[[ast.AST | None], bool], aliases: dict[str, str]) -> bool:
    """Les écritures python reconnues depuis #1233 : en place, journal, base, archive, lien, fichier temporaire."""
    dotted = _py_dotted(call.func, aliases)
    args = call.args
    last = call.func.attr if isinstance(call.func, ast.Attribute) else dotted

    def arg(index: int, keyword: str) -> ast.AST | None:
        return args[index] if len(args) > index else _keyword(call, keyword)

    if dotted in ("fileinput.input", "fileinput.FileInput"):
        return _py_true(arg(1, "inplace")) and names(arg(0, "files"))
    if dotted.startswith("logging.") and last.endswith("FileHandler"):
        return not _py_true(arg(3, "delay")) and names(arg(0, "filename"))
    if dotted == "logging.basicConfig":
        return names(_keyword(call, "filename"))
    if dotted in ("sqlite3.connect", "sqlite3.dbapi2.connect"):
        database = arg(0, "database")
        text = ast.unparse(database) if database is not None else ""
        read_only = (_py_true(_keyword(call, "uri")) and "mode=ro" in text) or ":memory:" in text
        return not read_only and names(database)
    if dotted == "os.open":
        flags = arg(1, "flags")
        return flags is not None and bool(_PY_WRITE_FLAGS.search(ast.unparse(flags))) and names(arg(0, "path"))
    if dotted in _PY_ARCHIVES:
        return _py_constant(arg(1, "mode"), ("w", "a", "x")) and names(arg(0, "name") or _keyword(call, "file"))
    if dotted in ("shelve.open", "dbm.open", "dbm.gnu.open", "dbm.ndbm.open", "dbm.dumb.open", "dbm.sqlite3.open"):
        flag = arg(1, "flag")
        created = (flag is None and dotted == "shelve.open") or _py_constant(flag, ("c", "n"))
        return created and names(arg(0, "filename") or _keyword(call, "file"))
    if dotted == "shutil.unpack_archive":
        return names(arg(1, "extract_dir"))
    if dotted == "shutil.make_archive":
        return names(arg(0, "base_name"))
    if dotted in _PY_DESTINATION_FUNCTIONS:
        return names(arg(1, "dst"))
    if dotted in _PY_PATH_FUNCTIONS:
        return names(arg(0, "path"))
    if dotted.startswith("tempfile."):
        return names(_keyword(call, "dir"))
    if last == "extractall":
        return names(arg(0, "path"))
    if last == "extract" and isinstance(call.func, ast.Attribute):
        return names(args[1] if len(args) > 1 else _keyword(call, "path"))
    return False


class _Proof:
    """L'analyse d'une commande : variables affectées, ``cd`` lus, fichiers écrits avec un contenu littéral."""

    def __init__(self, target: Target) -> None:
        self.target = target
        self.env: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.cd: list[str] = []
        #: Substitutions de processus de tous les scripts lus : un mot ``\x01<n>`` y renvoie.
        self.procsubs: list[list[_Simple]] = []
        #: Affectations en préfixe de la commande en cours (``GIT_DIR=x git …``).
        self.local_env: dict[str, str] = {}

    # ── Prédicats sur un mot ────────────────────────────────────────────────

    def hit(self, word: str, dirs: tuple[str, ...] = ()) -> bool:
        """Vrai si *word* désigne la cible, relu après les ``cd`` lus puis les dossiers *dirs* (``git -C``…)."""
        if not word or word.startswith(_PROCSUB):
            return False
        return any(self.target.names(reading, (*self.cd, *dirs)) for reading in _readings(word))

    def carried(self, word: str) -> bool:
        if not word or word.startswith(_PROCSUB):
            return False
        return self.hit(word) or any(self.target.carries(reading, tuple(self.cd)) for reading in _readings(word))

    def expand(self, word: str) -> str:
        """*word* avec les variables que la commande a affectées ; les autres restent telles quelles."""
        if "$" not in word or not self.env:
            return word
        return _VARIABLE.sub(lambda m: self.env.get(m.group(1) or m.group(2), m.group(0)), word)

    # ── Scripts shell ───────────────────────────────────────────────────────

    def script(self, text: str, stdin: str | None, depth: int) -> bool:
        """Vrai si le script shell *text* (lu avec l'entrée littérale *stdin*) écrit sur la cible."""
        if depth > _MAX_DEPTH:
            return False
        return any(self.command(cmd, stdin, depth) for cmd in _Lexer(text, self.procsubs).run())

    def command(self, cmd: _Simple, inherited: str | None, depth: int) -> bool:
        words = [self.expand(w) for w in cmd.words]
        writes = [self.expand(w) for w in cmd.writes]
        if any(self.hit(target) for target in writes):
            return True
        stdin = self.stdin_of(cmd, inherited)
        literal = self.output(cmd, inherited)
        if literal is not None:
            for target in writes:
                self.files[_file_key(target)] = literal
        if words and all(_ASSIGNMENT.match(w) for w in words):
            self.assign(words)
            return False
        launched = _unwrap(words)
        if not launched:
            return False
        prefix = words[: max(0, len(words) - len(launched))]
        outer_env = self.local_env  # ``GIT_DIR=x sh -c 'git …'`` : le shell lancé hérite du préfixe
        self.local_env = {**outer_env, **dict(w.split("=", 1) for w in prefix if _ASSIGNMENT.match(w))}
        try:
            return self.launch(launched, cmd, stdin, depth)
        finally:
            self.local_env = outer_env

    def assign(self, words: list[str]) -> None:
        for word in words:
            if _ASSIGNMENT.match(word):
                name, value = word.split("=", 1)
                self.env[name] = value

    def stdin_of(self, cmd: _Simple, inherited: str | None) -> str | None:
        """Le texte littéral que *cmd* lit sur son entrée standard, ou ``None`` s'il n'est pas connu."""
        if cmd.stdin is not None:
            return cmd.stdin
        if cmd.stdin_file:
            return self.source(self.expand(cmd.stdin_file))
        if cmd.upstream is not None:
            return self.output(cmd.upstream, inherited)
        return inherited

    def output(self, cmd: _Simple, inherited: str | None) -> str | None:
        """Ce que *cmd* écrit sur sa sortie quand c'est un texte littéral (``echo``, ``printf``, ``cat`` d'un heredoc)."""
        words = _unwrap([self.expand(w) for w in cmd.words])
        if not words:
            return None
        # Ce que le shell remet au programme : les échappements hors guillemets sont consommés.
        program, args = _program(words[0]), [_plain(w) for w in words[1:]]
        if program == "echo":
            while args and re.fullmatch(r"-[neE]+", args[0]):
                args = args[1:]
            return " ".join(args) + "\n"
        if program == "printf":
            args = args[1:] if args[:1] == ["--"] else args
            if not args:
                return ""
            values = iter(args[1:])
            text = _PRINTF_STRING.sub(lambda _m: next(values, ""), args[0])  # ``%s`` : les arguments, dans l'ordre
            return " ".join([text, *values]).replace("\\n", "\n").replace("\\t", "\t")
        if program == "cat" and all(a.startswith("-") for a in args):
            return self.stdin_of(cmd, inherited)
        return None

    def source(self, operand: str) -> str | None:
        """Le contenu littéral du script *operand* : substitution de processus ou fichier écrit par la commande."""
        if operand.startswith(_PROCSUB):
            index = operand[len(_PROCSUB) :]
            inner = self.procsubs[int(index)] if index.isdigit() and int(index) < len(self.procsubs) else []
            return self.output(inner[-1], None) if inner else None
        return self.files.get(_file_key(operand))

    def launch(self, words: list[str], cmd: _Simple, stdin: str | None, depth: int) -> bool:
        """Vrai si le programme lancé (*words*, enveloppes retirées) écrit sur la cible."""
        program, args = _program(words[0]), words[1:]
        if program in ("cd", "pushd"):
            operands = _operands(args)
            self.cd.append(operands[0] if operands else "~")
            return False
        if program in _DECLARERS:
            self.assign(args)
            return False
        if program == "read":
            names = _operands(args)
            if names and stdin is not None:
                self.env[names[-1]] = stdin.strip().split("\n", 1)[0]
            return False
        if program == "for" and len(args) >= 3 and args[1] == "in":
            self.env[args[0]] = next((item for item in args[2:] if self.hit(item)), args[2])
            return False
        if self.writer(program, args):
            return True
        host = _Host(self, depth)
        if language_writes(program, args, host) or program_writes(program, args, host, stdin):
            return True
        if program in _SHELLS:
            return self.shell(args, stdin, depth)
        if program == "eval":
            return self.script(" ".join(_plain(a) for a in args), stdin, depth + 1)
        if program in ("source", "."):
            text = self.source(args[0]) if args else None
            return text is not None and self.script(text, stdin, depth + 1)
        if _PYTHON.match(program):
            return self.python_program(args, stdin, depth)
        if program == "xargs":
            return self.xargs(args, cmd, stdin, depth)
        direct = self.files.get(_file_key(words[0]))
        if direct is not None:  # ``./s.sh`` écrit plus tôt dans la commande
            return self.python(direct, depth) if "python" in direct.split("\n", 1)[0] else self.script(direct, stdin, depth + 1)
        return False

    def shell(self, args: list[str], stdin: str | None, depth: int) -> bool:
        """``sh -c corps``, ``sh script`` (contenu connu) ou ``sh`` lisant un script littéral sur son entrée."""
        i = 0
        from_stdin = False
        while i < len(args):
            arg = args[i]
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
                return i + 1 < len(args) and self.script(_plain(args[i + 1]), stdin, depth + 1)
            if arg in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
            elif arg[:1] in ("-", "+") and len(arg) > 1:
                from_stdin = from_stdin or (arg.startswith("-") and not arg.startswith("--") and "s" in arg[1:])
                i += 1
            else:
                break
        operand = args[i] if i < len(args) and args[i] != "-" and not from_stdin else ""
        text = self.source(operand) if operand else stdin
        return text is not None and self.script(text, None, depth + 1)

    def python_program(self, args: list[str], stdin: str | None, depth: int) -> bool:
        """``python -c corps``, ``python script`` (contenu connu) ou ``python -`` lisant un script littéral."""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "-m":
                return False
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
                attached = arg[arg.index("c", 1) + 1 :]
                body = attached or (args[i + 1] if i + 1 < len(args) else "")
                return self.python(_plain(body), depth)
            if arg in ("-W", "-X"):
                i += 2
            elif arg.startswith("-") and len(arg) > 1:
                i += 1
            else:
                break
        operand = args[i] if i < len(args) and args[i] != "-" else ""
        text = self.source(operand) if operand else stdin
        return text is not None and self.python(text, depth)

    def xargs(self, args: list[str], cmd: _Simple, stdin: str | None, depth: int) -> bool:
        """``xargs [-I R] cmd…`` : la commande est jugée avec, pour arguments, ce que l'amont nomme de la cible."""
        replace = ""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "-I" and i + 1 < len(args):
                replace, i = args[i + 1], i + 2
            elif arg.startswith("-I") and len(arg) > 2:
                replace, i = arg[2:], i + 1
            elif arg.startswith("--replace"):
                replace, i = (arg.split("=", 1)[1] if "=" in arg else "{}"), i + 1
            elif arg.startswith("-i"):
                replace, i = (arg[2:] or "{}"), i + 1
            elif arg in _XARGS_VALUED:
                i += 2
            elif arg.startswith("-") and len(arg) > 1:
                i += 1
            else:
                break
        inner = args[i:]
        if not inner:
            return False
        if stdin is not None:
            items = stdin.split()[:16]
        elif cmd.upstream is not None:
            items = [w for w in (self.expand(w) for w in cmd.upstream.words[1:]) if self.hit(w)][:16]
        else:
            items = []
        variants = [[w.replace(replace, item) for w in inner] for item in items] if replace else [inner + items]
        for variant in variants or [inner]:
            launched = _unwrap(variant)
            if launched and self.launch(launched, cmd, None, depth + 1):
                return True
        return False

    # ── Programmes qui écrivent ─────────────────────────────────────────────

    def writer(self, program: str, args: list[str]) -> bool:
        """Vrai si *program*, avec *args*, est une des formes d'écriture reconnues et vise la cible."""
        if program in _ANY_OPERAND:
            check = self.carried if program in _CARRIERS else self.hit
            operands = _operands(args, _ANY_OPERAND[program])
            return any(check(o) for o in operands) or any(check(v) for v in _option_values(args, _TARGET_DIR))
        if program in _DESTINATION:
            return any(self.carried(d) for d in self.destinations(program, args))
        if program == "sed":
            if not any(a.startswith("--in-place") or (not a.startswith("--") and _SED_IN_PLACE.match(a)) for a in args):
                return False
            files = _operands(args, _SED_SCRIPT_OPTIONS | {"-l", "--line-length"})
            if not any(a in _SED_SCRIPT_OPTIONS or a.startswith(("--expression=", "--file=")) for a in args):
                files = files[1:]  # sans ``-e``/``-f``, le premier opérande est le script
            return any(self.hit(f) for f in files)
        if program == "dd":
            return any(a.startswith("of=") and self.hit(a[3:]) for a in args)
        if program == "find":
            if any(a in _FIND_ACTIONS for a in args) and any(self.hit(a) for a in args):
                return True
            return any(a in _FIND_OUTPUTS and self.hit(b) for a, b in pairwise(args))
        if program in _OUTPUT_OPTIONS:
            return any(self.hit(v) for v in _option_values(args, _OUTPUT_OPTIONS[program]))
        return False

    def destinations(self, program: str, args: list[str]) -> list[str]:
        """Ce que ``cp``/``install``/``rsync``/``ln`` écrivent : ``-t dossier``, sinon le dernier opérande."""
        targets = _option_values(args, _TARGET_DIR)
        operands = _operands(args, _DESTINATION[program] | _TARGET_DIR)
        if program == "install" and any(
            a == "--directory" or (a.startswith("-") and not a.startswith("--") and "d" in a) for a in args
        ):
            return operands  # ``install -d`` crée chaque opérande
        if targets:
            return targets
        if len(operands) >= 2:
            return operands[-1:]
        if program == "ln" and operands:
            return [operands[0].rstrip("/").rsplit("/", 1)[-1]]  # lien créé dans le répertoire courant
        return []

    # ── Python ──────────────────────────────────────────────────────────────

    def python(self, body: str, depth: int) -> bool:
        """Vrai si le script python *body* écrit sur la cible (voir le docstring du module) ; illisible : faux."""
        try:
            tree = ast.parse(textwrap.dedent(body))
        except (SyntaxError, ValueError):
            return False
        tainted: set[str] = set()

        def names(node: ast.AST | None) -> bool:
            if node is None:
                return False
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id in tainted:
                    return True
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and self.hit(inner.value):
                    return True
            return self.hit(ast.unparse(node))

        def bind(target: ast.AST | None) -> bool:
            if target is None:
                return False
            fresh = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)} - tainted
            tainted.update(fresh)
            return bool(fresh)

        for _ in range(4):  # propagation des variables qui portent un chemin de la cible
            changed = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and names(node.value):
                    for assigned in node.targets:
                        changed = bind(assigned) or changed
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and names(node.value):
                    changed = bind(node.target) or changed
                elif isinstance(node, ast.withitem) and names(node.context_expr):
                    changed = bind(node.optional_vars) or changed
                elif isinstance(node, ast.For) and names(node.iter):
                    changed = bind(node.target) or changed
            if not changed:
                break
        aliases = _py_aliases(tree)
        return any(
            isinstance(node, ast.Call) and (_py_more(node, names, aliases) or self.python_call(node, names, depth))
            for node in ast.walk(tree)
        )

    def python_call(self, call: ast.Call, names: Callable[[ast.AST | None], bool], depth: int) -> bool:
        fn = call.func
        args = call.args
        if isinstance(fn, ast.Name):
            name, receiver, module = fn.id, None, True
        elif isinstance(fn, ast.Attribute):
            name, receiver = fn.attr, fn.value
            module = isinstance(receiver, ast.Name) and receiver.id in (*_PY_MODULES, "subprocess", "io", "codecs")
        else:
            return False
        if name == "open":
            if module:  # ``open(chemin, mode)``, ``io.open(chemin, mode)``
                mode = args[1] if len(args) > 1 else _keyword(call, "mode")
                return _py_write_mode(mode) and names(args[0] if args else _keyword(call, "file"))
            mode = args[0] if args else _keyword(call, "mode")  # ``Path(chemin).open(mode)``
            return _py_write_mode(mode) and names(receiver)
        if module and name in _PY_LAUNCHERS and args:
            first = args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return self.script(first.value, None, depth + 1)
            if isinstance(first, (ast.List, ast.Tuple)):
                words = [e.value for e in first.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                launched = _unwrap(words) if len(words) == len(first.elts) else []
                return bool(launched) and self.launch(launched, _Simple(), None, depth + 1)
            return False
        if module:
            if name in _PY_FUNCTIONS:
                return any(names(a) for a in args)
            if name in _PY_COPIES:
                return names(args[1] if len(args) > 1 else _keyword(call, "dst"))
            return False
        if name in _PY_PATH_METHODS:
            return names(receiver) or (name in ("rename", "symlink_to", "hardlink_to") and any(names(a) for a in args))
        return False


class _Host:
    """Ce que :mod:`.write_proof_languages` et :mod:`.write_proof_programs` voient d'une analyse en cours."""

    def __init__(self, proof: _Proof, depth: int) -> None:
        self.proof, self.depth = proof, depth

    def hit(self, word: str, dirs: tuple[str, ...] = ()) -> bool:
        return self.proof.hit(word, dirs)

    def run_script(self, text: str, stdin: str | None = None) -> bool:
        return self.proof.script(text, stdin, self.depth + 1)

    def run_words(self, words: list[str]) -> bool:
        launched = _unwrap(words)
        return bool(launched) and self.proof.launch(launched, _Simple(), None, self.depth + 1)

    def source(self, operand: str) -> str | None:
        return self.proof.source(operand)

    def variable(self, name: str) -> str | None:
        return self.proof.local_env.get(name, self.proof.env.get(name))


def proves_write(command: str, target: Target) -> bool:
    """Vrai seulement si *command* écrit, de façon prouvée, sur *target*.

    Faux pour tout le reste, et pour toute erreur d'analyse : l'appelant refuse alors sans révoquer. Ce verdict
    ne sert jamais à autoriser une commande.
    """
    try:
        return _Proof(target).script(command, None, 0)
    except Exception:  # illisible ou trop imbriqué : pas de preuve, le refus de l'appelant demeure
        return False
