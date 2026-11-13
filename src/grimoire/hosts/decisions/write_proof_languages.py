"""Preuve d'écriture dans les langages qu'un programme reçoit en argument : ``sed``, ``awk``, et les interpréteurs
à exécution en ligne (``perl -e``, ``node -e``, ``ruby -e``, ``php -r``, ``lua -e``, ``deno eval``, ``bun -e``).

Partie de :mod:`.write_proof` (#1233) : même contrat. Ce module ne décide **jamais** d'un refus ni d'une
autorisation ; il ne dit que si le programme écrit, de façon prouvée, sur la cible. Tout ce qui n'est pas lu
(construction inconnue, chemin calculé, mode d'ouverture non constant) rend faux.

Ce qui est reconnu :

- ``sed`` : les commandes ``w``/``W`` et le drapeau ``w`` de ``s``, dans le script donné en opérande, par
  ``-e``/``--expression`` ou par un fichier ``-f`` écrit plus tôt dans la même commande ; la commande ``e``
  (exécute une commande shell, relue par l'appelant). ``--sandbox`` les interdit : rien n'est prouvé ;
- ``awk`` : ``print``/``printf`` redirigés (``>``, ``>>``) vers un fichier, ou vers une commande (``|``) ;
  ``system()`` ; ``"cmd" | getline`` ; le nom du fichier peut venir de ``-v``, d'une affectation en opérande ou
  d'une affectation dans le programme ; ``gawk -i inplace`` sur un opérande ; ``gawk -o``/``-p``/``-d`` vers un
  fichier ;
- interpréteurs : ouverture en écriture (``open`` de perl à deux ou trois arguments, ``sysopen``, ``fopen``,
  ``File.open``, ``io.open``, ``fs.openSync``… avec un mode constant qui écrit), les fonctions qui écrivent,
  suppriment, renomment, copient ou lient un chemin (``writeFileSync``, ``unlink``, ``rename``, ``FileUtils.rm_rf``,
  ``file_put_contents``, ``os.remove``…), les commandes qu'ils lancent (``system``, ``exec``, ``execSync``,
  backticks, ``qx``) ; ``perl``/``ruby`` ``-i`` avec ``-p``/``-n`` sur un opérande.

Le chemin d'un appel est lu comme le ferait la preuve python de :mod:`.write_proof` : la valeur de l'argument
(chaînes, variables affectées dans le script, concaténations, ``path.join``, le HOME par ``process.env.HOME``,
``os.homedir()``, ``$ENV{HOME}``, ``Dir.home``, ``getenv('HOME')``…), ou une chaîne littérale qu'il contient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

__all__ = ["Host", "language_writes"]

#: Valeur qu'on ne sait pas lire (même marque que :mod:`.write_proof`).
_UNKNOWN = "\x00"


class Host(Protocol):
    """Ce que :mod:`.write_proof` prête aux analyses de ce module."""

    def hit(self, word: str, dirs: tuple[str, ...] = ()) -> bool:
        """Vrai si *word* désigne la cible, résolu après les ``cd`` lus puis les dossiers *dirs*."""
        ...

    def run_script(self, text: str, stdin: str | None = None) -> bool:
        """Vrai si le script shell *text* écrit sur la cible."""
        ...

    def run_words(self, words: list[str]) -> bool:
        """Vrai si la commande *words* (déjà découpée) écrit sur la cible."""
        ...

    def source(self, operand: str) -> str | None:
        """Le contenu littéral d'un fichier écrit plus tôt dans la commande, ou ``None``."""
        ...

    def variable(self, name: str) -> str | None:
        """La valeur d'une variable affectée par la commande (préfixe ``X=…`` compris), ou ``None``."""
        ...


def _known(text: str) -> bool:
    return bool(text) and _UNKNOWN not in text


# ── sed ─────────────────────────────────────────────────────────────────────────────────────────────────────────

_SED_ADDRESS_NUMBER = re.compile(r"\d+(?:~\d+)?")
_SED_S_FLAGS = frozenset("gpiImMe0123456789")
_SED_NO_ARGUMENT = frozenset("=dDgGhHnNpPxzF")


def _sed_commands(script: str) -> list[tuple[str, str]]:
    """Les écritures (``("w", fichier)``) et commandes exécutées (``("e", commande)``) d'un script sed.

    Une construction inconnue arrête la lecture : ce qui suit n'est pas prouvé.
    """
    found: list[tuple[str, str]] = []
    n = len(script)

    def eol(pos: int) -> int:
        end = script.find("\n", pos)
        return n if end < 0 else end

    def blank(pos: int) -> int:
        while pos < n and script[pos] in " \t":
            pos += 1
        return pos

    def delimited(pos: int, delim: str) -> int:
        """Index du *delim* fermant (échappements sautés) à partir de *pos*, ou -1."""
        while pos < n:
            if script[pos] == "\\":
                pos += 2
                continue
            if script[pos] == delim:
                return pos
            pos += 1
        return -1

    def address(pos: int) -> int:
        if pos < n and script[pos] == "$":
            return pos + 1
        number = _SED_ADDRESS_NUMBER.match(script, pos)
        if number:
            return number.end()
        if pos < n and script[pos] in "/\\":
            if script[pos] == "\\":
                pos += 1
            if pos >= n:
                return -1
            end = delimited(pos + 1, script[pos])
            if end < 0:
                return -1
            pos = end + 1
            while pos < n and script[pos] in "IM":
                pos += 1
        return pos

    i = 0
    while i < n:
        while i < n and script[i] in " \t\n;}":
            i += 1
        if i >= n:
            break
        if script[i] == "#":
            i = eol(i)
            continue
        j = address(i)
        if j < 0:
            break
        if j != i:
            i = blank(j)
            if i < n and script[i] == ",":
                i = blank(i + 1)
                if i < n and script[i] in "+~":
                    number = _SED_ADDRESS_NUMBER.match(script, i + 1)
                    i = number.end() if number else i + 1
                else:
                    i = address(i)
                    if i < 0:
                        break
        while i < n and script[i] in "! \t":
            i += 1
        if i >= n:
            break
        command = script[i]
        i += 1
        if command == "{" or command in _SED_NO_ARGUMENT:
            continue
        if command in "wW":
            found.append(("w", script[blank(i) : eol(i)]))
            i = eol(i)
        elif command == "e":
            found.append(("e", script[blank(i) : eol(i)]))
            i = eol(i)
        elif command in "rR:":
            i = eol(i)
        elif command in "aic":
            end = eol(i)
            while script[i:end].endswith("\\") and end < n:  # texte continué sur la ligne suivante
                i = end + 1
                end = eol(i)
            i = end
        elif command in "btTv":
            while i < n and script[i] not in ";\n}":
                i += 1
        elif command in "qQlL":
            while i < n and (script[i].isdigit() or script[i] in " \t"):
                i += 1
        elif command in "sy":
            if i >= n or script[i] in "\n\\":
                break
            delim = script[i]
            first = delimited(i + 1, delim)
            second = delimited(first + 1, delim) if first >= 0 else -1
            if second < 0:
                break
            i = second + 1
            if command == "s":
                while i < n and script[i] in _SED_S_FLAGS:
                    i += 1
                if i < n and script[i] == "w":
                    found.append(("w", script[blank(i + 1) : eol(i)]))
                    i = eol(i)
        else:
            break
    return found


def sed_writes(args: list[str], host: Host) -> bool:
    """Vrai si ``sed`` *args* écrit sur la cible par ``w``/``W``/``s///w``, ou lance une commande qui y écrit."""
    scripts: list[str] = []
    operands: list[str] = []
    sandbox = False
    from_options = False
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            operands.extend(args[i:])
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in ("--expression", "--file"):
                if not eq and i < len(args):
                    value, i = args[i], i + 1
                from_options = True
                text = value if name == "--expression" else host.source(value)
                scripts.extend([text] if text is not None else [])
            elif name == "--line-length" and not eq:
                i += 1
            sandbox = sandbox or name == "--sandbox"
            continue
        if not arg.startswith("-") or arg == "-":
            operands.append(arg)
            continue
        for k, letter in enumerate(arg[1:], start=1):
            if letter in "efl":
                value = arg[k + 1 :]
                if not value and i < len(args):
                    value, i = args[i], i + 1
                if letter != "l":
                    from_options = True
                    text = value if letter == "e" else host.source(value)
                    scripts.extend([text] if text is not None else [])
                break
            if letter == "i":
                break  # ``-i[SUFFIXE]`` : le reste du mot est le suffixe
    if not from_options and operands:
        scripts.append(operands[0])
    if sandbox:
        return False
    for script in scripts:
        for kind, value in _sed_commands(script):
            if kind == "w" and value and host.hit(value):
                return True
            if kind == "e" and value and host.run_script(value):
                return True
    return False


# ── Lexique commun : awk et interpréteurs ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Tok:
    """Un jeton : chaîne (``s``), commande (``c`` : backticks, ``qx``), nom (``n``), opérateur (``o``), autre (``u``).

    Une chaîne porte ses morceaux : ``("t", texte)`` ou ``("x", source d'une expression interpolée)``.
    """

    kind: str
    text: str
    parts: tuple[tuple[str, str], ...] = ()


_OPERATORS = (
    "===", "!==", "**=", "...", "<<=", ">>=", "=>", "->", "::", "?.", "..", "==", "!=", "<=", ">=", "&&", "||",
    "=~", "!~", "+=", "-=", ".=", "|&", ">>", "<<", "++", "--",
)
_REGEX_AFTER = frozenset(
    {"return", "typeof", "split", "grep", "map", "if", "unless", "and", "or", "not", "when", "case", "while", "until"}
)
_PERL_QUOTES = frozenset({"q", "qq", "qw", "qx", "m", "qr", "s", "tr", "y"})
_PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_INTERPOLATING = {"perl": "$", "php": "$", "ruby": "#", "js": ""}
_NAME_START = re.compile(r"[A-Za-z_]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PERL_VAR = re.compile(r"\$(?:\{\s*([A-Za-z_]\w*)\s*\}|([A-Za-z_]\w*)(?:(\{[^}\n]*\})|(\[[^\]\n]*\]))?)")


class _Lexer:
    """Découpe un programme en jetons. Tolérant : une construction inconnue devient un jeton ``u``."""

    def __init__(self, src: str, lang: str) -> None:
        self.src, self.lang, self.n = src, lang, len(src)
        self.toks: list[_Tok] = []
        self.depth = 0

    def regex_allowed(self) -> bool:
        if self.lang in ("lua", "php"):
            return False
        if not self.toks:
            return True
        last = self.toks[-1]
        if last.kind == "o":
            return last.text not in (")", "]", "}")
        return last.kind == "n" and last.text in _REGEX_AFTER

    def skip_delimited(self, i: int, opener: str) -> tuple[int, str]:
        """Lit un texte délimité ouvert en ``i - 1`` par *opener* ; rend l'index suivant et le texte brut."""
        closer = _PAIRS.get(opener, opener)
        nesting = 0
        start = i
        while i < self.n:
            ch = self.src[i]
            if ch == "\\":
                i += 2
                continue
            if ch == opener and closer != opener:
                nesting += 1
            elif ch == closer:
                if nesting == 0:
                    return i + 1, self.src[start:i]
                nesting -= 1
            i += 1
        return self.n, self.src[start:]

    def string(self, i: int, quote: str, *, interpolate: str) -> tuple[int, tuple[tuple[str, str], ...]]:
        """Lit une chaîne ouverte en ``i - 1`` ; rend l'index après sa fermeture et ses morceaux."""
        parts: list[tuple[str, str]] = []
        buf: list[str] = []
        closer = _PAIRS.get(quote, quote)
        raw = interpolate == "" and quote == "'" and self.lang in ("perl", "ruby", "php")
        while i < self.n and self.src[i] != closer:
            ch = self.src[i]
            if ch == "\\" and i + 1 < self.n:
                nxt = self.src[i + 1]
                if raw and nxt not in "\\'":
                    buf.append(ch + nxt)
                else:
                    buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                i += 2
                continue
            if interpolate == "$" and ch == "$":
                var = _PERL_VAR.match(self.src, i)
                if var:
                    parts.extend([("t", "".join(buf))] if buf else [])
                    buf = []
                    parts.append(("x", var.group(0)))
                    i = var.end()
                    continue
            if (interpolate == "#" and self.src.startswith("#{", i)) or (
                quote == "`" and self.lang == "js" and self.src.startswith("${", i)
            ):
                end, inner = self.skip_delimited(i + 2, "{")
                parts.extend([("t", "".join(buf))] if buf else [])
                buf = []
                parts.append(("x", inner))
                i = end
                continue
            buf.append(ch)
            i += 1
        parts.extend([("t", "".join(buf))] if buf else [])
        return i + 1, tuple(parts)

    def emit(self, kind: str, text: str, parts: tuple[tuple[str, str], ...] = ()) -> None:
        self.toks.append(_Tok(kind, text, parts))

    def run(self) -> list[_Tok]:
        src, n, lang = self.src, self.n, self.lang
        i = 0
        while i < n:
            ch = src[i]
            if ch == "\n":
                if self.depth == 0:
                    self.emit("o", ";")
                i += 1
            elif ch in " \t\r":
                i += 1
            elif (ch == "#" and lang in ("perl", "ruby", "php", "awk")) or (
                src.startswith("//", i) and lang in ("js", "php")
            ) or (src.startswith("--", i) and lang == "lua" and not src.startswith("--[[", i)):
                end = src.find("\n", i)
                i = n if end < 0 else end
            elif src.startswith("/*", i) and lang in ("js", "php"):
                end = src.find("*/", i + 2)
                i = n if end < 0 else end + 2
            elif lang == "lua" and src.startswith(("--[[", "[["), i):
                comment = src.startswith("--[[", i)
                start = i + (4 if comment else 2)
                end = src.find("]]", start)
                text = src[start:] if end < 0 else src[start:end]
                i = n if end < 0 else end + 2
                if not comment:
                    self.emit("s", text, (("t", text),))
            elif (ch in "'\"`" and lang != "awk") or ch == '"':
                if ch == "`" and lang not in ("js", "perl", "ruby", "php"):
                    self.emit("u", ch)
                    i += 1
                    continue
                interpolate = ""
                if ch == '"' or (ch == "`" and lang != "js"):
                    interpolate = _INTERPOLATING.get(lang, "")
                i, parts = self.string(i + 1, ch, interpolate=interpolate)
                self.emit("c" if ch == "`" and lang != "js" else "s", ch, parts)
            elif ch == "/" and self.regex_allowed():
                i, _ = self.skip_delimited(i + 1, "/")
                while i < n and src[i].isalpha():
                    i += 1
                self.emit("u", "/re/")
            elif lang == "ruby" and ch == "%" and i + 1 < n and src[i + 1] in "qQwWixI({[" and self.regex_allowed():
                kind = src[i + 1] if src[i + 1].isalpha() else "Q"
                opener_at = i + 2 if src[i + 1].isalpha() else i + 1
                if opener_at >= n:
                    break
                interpolate = "#" if kind in "QxW" else ""
                i, parts = self.string(opener_at + 1, src[opener_at], interpolate=interpolate)
                self.emit({"x": "c", "q": "s", "Q": "s"}.get(kind, "u"), "%", parts)
            elif _NAME_START.match(ch) or (ch in "$@%&" and lang in ("perl", "php") and i + 1 < n and (
                _NAME_START.match(src[i + 1]) or (ch == "$" and src[i + 1] in "{_0123456789")
            )) or (ch == "$" and lang in ("js", "awk")):
                i = self.name(i)
            elif ch.isdigit():
                j = i
                while j < n and (src[j].isalnum() or src[j] == "."):
                    j += 1
                self.emit("u", src[i:j])
                i = j
            else:
                op = next((o for o in _OPERATORS if src.startswith(o, i)), ch)
                if op in ("(", "[", "{"):
                    self.depth += 1 if op != "{" else 0
                elif op in (")", "]"):
                    self.depth = max(0, self.depth - 1)
                self.emit("o", op)
                i += len(op)
        return self.toks

    def name(self, i: int) -> int:
        src, n, lang = self.src, self.n, self.lang
        sigil = ""
        if src[i] in "$@%&" and lang in ("perl", "php", "js", "awk"):
            sigil, i = src[i], i + 1
            if lang == "awk":  # ``$1``, ``$NF`` : un champ, jamais un chemin connu
                match = re.compile(r"[A-Za-z0-9_]*").match(src, i)
                self.emit("u", "$")
                return match.end() if match else i
            if sigil == "$" and i < n and src[i] == "{" and lang == "perl":
                end, inner = self.skip_delimited(i + 1, "{")
                self.emit("n", "$" + inner.strip())
                return end
            if i < n and src[i].isdigit():  # ``$1``, ``$0``
                self.emit("n", sigil + src[i])
                return i + 1
        match = _NAME.match(src, i)
        if match is None:
            self.emit("u", sigil)
            return i
        word = match.group(0)
        end = match.end()
        if lang == "js":
            while end < n and (src[end].isalnum() or src[end] in "_$"):
                end += 1
            word = src[i:end]
        if not sigil and lang == "perl" and word in _PERL_QUOTES and end < n:
            opener = src[end]
            if not (opener.isalnum() or opener in " \t\n=,;)" or src.startswith("=>", end)):
                return self.perl_quote(word, end)
        self.emit("n", sigil + word)
        return end

    def perl_quote(self, word: str, at: int) -> int:
        """``q()``, ``qq{}``, ``qx//``, ``m//``, ``s///``… ouvert en *at*."""
        opener = self.src[at]
        if word in ("q", "qq", "qx"):
            interpolate = "" if word == "q" or opener == "'" else "$"
            end, parts = self.string(at + 1, opener, interpolate=interpolate)
            self.emit("c" if word == "qx" else "s", word, parts)
            return end
        end, _ = self.skip_delimited(at + 1, opener)
        if word in ("s", "tr", "y"):
            if opener in _PAIRS:
                while end < self.n and self.src[end] in " \t\n":
                    end += 1
                if end < self.n:
                    end, _ = self.skip_delimited(end + 1, self.src[end])
            else:
                end, _ = self.skip_delimited(end, opener)
        while end < self.n and self.src[end].isalpha():
            end += 1
        self.emit("u", word)
        return end


def _subscript(previous: _Tok | None, lang: str) -> bool:
    """Vrai si une accolade ouverte après *previous* est un indice de hachage perl (``$ENV{HOME}``, ``->{x}``)."""
    if lang != "perl" or previous is None:
        return False
    return (previous.kind == "n" and previous.text[:1] in "$%") or (previous.kind == "o" and previous.text == "->")


def _statements(toks: list[_Tok], lang: str) -> list[list[_Tok]]:
    """Les instructions : coupées sur ``;`` et les accolades de bloc hors parenthèses, crochets et indices."""
    found: list[list[_Tok]] = []
    current: list[_Tok] = []
    opened: list[str] = []
    for tok in toks:
        if tok.kind == "o" and (tok.text in ("(", "[") or (tok.text == "{" and _subscript(
            current[-1] if current else None, lang
        ))):
            opened.append(tok.text)
        elif tok.kind == "o" and opened and tok.text == _PAIRS[opened[-1]]:
            opened.pop()
            current.append(tok)
            continue
        if not opened and tok.kind == "o" and tok.text in (";", "{", "}"):
            if current:
                found.append(current)
            current = []
            continue
        current.append(tok)
    if current:
        found.append(current)
    return found


def _group(toks: list[_Tok], start: int) -> int:
    """Index après le groupe ouvert en *start* (``(``, ``[`` ou ``{``), ou la fin."""
    opener = toks[start].text
    closer = _PAIRS[opener]
    depth = 0
    for k in range(start, len(toks)):
        if toks[k].kind == "o" and toks[k].text == opener:
            depth += 1
        elif toks[k].kind == "o" and toks[k].text == closer:
            depth -= 1
            if depth == 0:
                return k + 1
    return len(toks)


def _split_commas(toks: list[_Tok]) -> list[list[_Tok]]:
    """Les éléments d'une liste d'arguments, coupés sur les virgules (et ``=>``) hors groupes."""
    items: list[list[_Tok]] = [[]]
    depth = 0
    for tok in toks:
        if tok.kind == "o" and tok.text in ("(", "[", "{"):
            depth += 1
        elif tok.kind == "o" and tok.text in (")", "]", "}"):
            depth -= 1
        if depth == 0 and tok.kind == "o" and tok.text in (",", "=>"):
            items.append([])
            continue
        items[-1].append(tok)
    return [item for item in items if item]


# ── Ce que les appels écrivent ──────────────────────────────────────────────────────────────────────────────────
#
# ``nom -> règle`` : ``"0"``, ``"1"``, ``"0,1"`` (arguments chemins), ``"1:"``/``"2:"`` (à partir de), ``"*"``
# (tous), ``"mode"`` (chemin 0 si le mode 1 écrit), ``"script"`` (commande lancée). Les noms ambigus d'un langage
# à l'autre ont leur règle par langage.

_COMMON_RULES: dict[str, str] = {
    **dict.fromkeys(("rename", "renameSync", "move", "mv", "move_file"), "0,1"),
    **dict.fromkeys(
        ("symlink", "symlinkSync", "link", "linkSync", "ln", "ln_s", "ln_sf", "ln_sr", "copy", "cp", "cp_r",
         "copyFile", "copyFileSync", "cpSync", "copy_file", "copy_entry", "install"),
        "1",
    ),
    **dict.fromkeys(
        ("unlink", "unlinkSync", "rm", "rmSync", "rm_f", "rm_r", "rm_rf", "rmtree", "remove_tree", "remove_dir",
         "remove_file", "remove_entry", "remove_entry_secure", "rmdir", "rmdirSync", "removeSync", "make_path",
         "mkpath"),
        "*",
    ),
    **dict.fromkeys(
        ("mkdir", "mkdirSync", "mkdir_p", "makedirs", "mkdtemp", "mkdtempSync", "touch", "truncate", "truncateSync",
         "writeFile", "writeFileSync", "appendFile", "appendFileSync", "writeTextFile", "writeTextFileSync",
         "createWriteStream", "file_put_contents", "binwrite", "utimes", "utimesSync", "lutimes", "lutimesSync",
         "lchown", "lchownSync", "tempnam"),
        "0",
    ),
    **dict.fromkeys(
        ("system", "exec", "execSync", "spawn", "spawnSync", "execFile", "execFileSync", "shell_exec", "passthru",
         "proc_open", "popen", "execute", "fork"),
        "script",
    ),
}
_LANG_RULES: dict[str, dict[str, str]] = {
    "perl": {"open": "perl-open", "sysopen": "sysopen", "chmod": "1:", "chown": "2:", "utime": "2:"},
    "ruby": {"chmod": "1:", "chmod_R": "1:", "chown": "2:", "chown_R": "2:", "open": "mode", "new": "mode",
             "delete": "*", "write": "0", "remove": "*", "spawn": "script"},
    "php": {"fopen": "mode", "chmod": "0", "chown": "0", "chgrp": "0"},
    "js": {"chmod": "0", "chmodSync": "0", "chown": "0", "chownSync": "0", "open": "flag", "openSync": "flag",
           "remove": "*", "create": "0", "createSync": "0", "write": "0"},
    "lua": {"open": "mode", "remove": "*", "output": "0"},
}
#: Règles qui ne valent que sur un receveur nommé : ``File.write`` écrit, ``fh.write(x)`` non.
_RECEIVERS: dict[str, frozenset[str]] = {
    "write": frozenset({"File", "IO", "Bun", "Deno"}),
    "new": frozenset({"File"}),
    "delete": frozenset({"File", "Dir"}),
    "remove": frozenset({"FileUtils", "Deno", "os", "fs", "fse", "fsp", "promises"}),
    "create": frozenset({"Deno"}),
    "createSync": frozenset({"Deno"}),
    "output": frozenset({"io"}),
    "open": frozenset({"File", "io", "fs", "fsp", "promises", "fsPromises", "Kernel", "IO", "Deno", "", "CORE"}),
}
_WRITE_FLAGS = re.compile(r"WRONLY|RDWR|CREAT|APPEND|TRUNC|EXCL")
_MODE = re.compile(r"^[rwaxbt+ce]{1,5}(?::|$)")
_PERL_WRITE_MODE = re.compile(r"^\s*(\+?>{1,2}|\+<)")
_STOP_WORDS = frozenset({"or", "and", "if", "unless", "||", "&&", "do", "while", "until", "then"})
_NO_PAREN_LANGS = frozenset({"perl", "ruby"})
_ENV_NAMES = frozenset({"env", "ENV", "$ENV", "%ENV", "getenv", "$_SERVER", "$_ENV", "environ", "ENVIRON"})
_HOME_NAMES = frozenset({"homedir", "home", "homeDir"})
_JOINERS = frozenset({"join", "resolve", "catfile", "catdir", "joinpath"})
_IDENTITY = frozenset({"expand_path", "normalize", "realpath", "abspath", "rel2abs", "absolute_path", "String"})


def _writes_mode(text: str, raw: list[_Tok]) -> bool:
    """Vrai si *text* (valeur d'un mode d'ouverture) ou les noms de *raw* (drapeaux ``O_…``) écrivent."""
    mode = text.strip()
    if _MODE.match(mode) and set(mode.split(":", 1)[0]) & set("wax+"):
        return True
    return any(tok.kind == "n" and _WRITE_FLAGS.search(tok.text) for tok in raw)


class _Script:
    """L'analyse d'un programme : variables affectées, appels qui écrivent, commandes lancées."""

    def __init__(self, lang: str, host: Host, env: dict[str, str] | None = None) -> None:
        self.lang, self.host = lang, host
        self.env: dict[str, str] = dict(env or {})

    # ── Valeurs ──────────────────────────────────────────────────────────

    def text(self, tok: _Tok) -> str:
        out: list[str] = []
        for kind, value in tok.parts:
            if kind == "t":
                out.append(value)
            else:
                out.append(self.value(_Lexer(value, self.lang).run()))
        return "".join(out)

    def value(self, toks: list[_Tok]) -> str:
        """La valeur de l'expression *toks* : chaînes et variables connues, ``_UNKNOWN`` pour le reste."""
        out: list[str] = []
        i = 0
        while i < len(toks):
            tok = toks[i]
            if tok.kind == "s":
                out.append(self.text(tok))
                i += 1
            elif tok.kind == "n":
                i, text = self.chain(toks, i)
                out.append(text)
            elif tok.kind == "o" and tok.text in ("(", ")", ".", "+", "..", ";"):
                i += 1
            else:
                out.append(_UNKNOWN)
                i += 1
        return "".join(out)

    def chain(self, toks: list[_Tok], start: int) -> tuple[int, str]:
        """La valeur d'une chaîne d'accès ``a.b(c)[d]`` qui commence en *start* ; rend l'index suivant."""
        separators = ("->", "::") if self.lang in ("perl", "php") else (".", "?.", "::", ":")
        names = [toks[start].text]
        calls: list[list[list[_Tok]]] = []
        keys: list[str] = []
        i = start + 1
        while i < len(toks):
            tok = toks[i]
            if tok.kind == "o" and tok.text in separators and i + 1 < len(toks) and toks[i + 1].kind == "n":
                names.append(toks[i + 1].text)
                i += 2
            elif tok.kind == "o" and tok.text == "(":
                end = _group(toks, i)
                calls.append(_split_commas(toks[i + 1 : end - 1]))
                i = end
            elif tok.kind == "o" and (tok.text == "[" or (tok.text == "{" and self.lang == "perl")):
                end = _group(toks, i)
                inner = toks[i + 1 : end - 1]
                keys.append(inner[0].text if len(inner) == 1 and inner[0].kind == "n" else self.value(inner))
                i = end
            else:
                break
        last = names[-1]
        literal_args = [self.value(arg) for call in calls for arg in call]
        if "HOME" in (*keys, *literal_args, last) and set(names) & _ENV_NAMES:
            return i, "$HOME"
        if last in _HOME_NAMES:
            return i, "$HOME"
        if calls and last in _JOINERS:
            return i, "/".join(self.value(arg) for arg in calls[-1])
        if calls and last in _IDENTITY and calls[-1]:
            return i, self.value(calls[-1][0])
        if len(names) == 1 and not calls and not keys:
            return i, self.env.get(last, _UNKNOWN)
        return i, _UNKNOWN

    def names(self, toks: list[_Tok], dirs: tuple[str, ...] = ()) -> bool:
        """Vrai si l'argument *toks* désigne la cible : sa valeur, ou une chaîne littérale qu'il contient."""
        if not toks:
            return False
        if self.host.hit(self.value(toks), dirs):
            return True
        return any(tok.kind == "s" and self.host.hit(self.text(tok), dirs) for tok in toks)

    # ── Programme ────────────────────────────────────────────────────────

    def run(self, src: str) -> bool:
        """Vrai si le programme *src* écrit sur la cible ; les instructions sont lues dans l'ordre (affectations)."""
        return any(self.statement(statement) for statement in _statements(_Lexer(src, self.lang).run(), self.lang))

    def statement(self, toks: list[_Tok]) -> bool:
        if any(tok.kind == "c" and self.command(self.text(tok)) for tok in toks):
            return True
        for k, tok in enumerate(toks):
            if tok.kind == "n" and self.call(toks, k):
                return True
        body = toks
        while body and body[0].kind == "n" and body[0].text in ("my", "our", "local", "const", "let", "var"):
            body = body[1:]
        if len(body) >= 3 and body[0].kind == "n" and body[1].kind == "o" and body[1].text == "=":
            self.env[body[0].text] = self.value(body[2:])
        return False

    def command(self, text: str) -> bool:
        return _known(text) and self.host.run_script(text)

    def call(self, toks: list[_Tok], k: int) -> bool:
        name = toks[k].text.lstrip("&")
        rule = _LANG_RULES.get(self.lang, {}).get(name) or _COMMON_RULES.get(name)
        if rule is None:
            return False
        receiver = ""
        if k >= 2 and toks[k - 1].kind == "o" and toks[k - 1].text in (".", "->", "::", "?.", ":"):
            receiver = toks[k - 2].text if toks[k - 2].kind == "n" else "?"
        elif k >= 1 and toks[k - 1].kind == "o" and toks[k - 1].text in (".", "->", "?."):
            return False
        wanted = _RECEIVERS.get(name)
        if wanted is not None and receiver not in wanted:
            return False
        if k + 1 < len(toks) and toks[k + 1].kind == "o" and toks[k + 1].text == "(":
            args = _split_commas(toks[k + 2 : _group(toks, k + 1) - 1])
        elif self.lang in _NO_PAREN_LANGS and k + 1 < len(toks) and toks[k + 1].kind in ("s", "n"):
            end = k + 1
            while end < len(toks) and not (toks[end].kind in ("n", "o") and toks[end].text in _STOP_WORDS):
                end += 1
            args = _split_commas(toks[k + 1 : end])
        else:
            return False
        return self.apply(rule, args)

    def apply(self, rule: str, args: list[list[_Tok]]) -> bool:
        if rule == "script":
            return self.launched(args)
        if rule == "perl-open":
            return self.perl_open(args)
        if rule == "sysopen":
            return len(args) >= 3 and _writes_mode("", args[2]) and self.names(args[1])
        if rule in ("mode", "flag"):
            mode = self.value(args[1]) if len(args) > 1 else ""
            return len(args) > 1 and _writes_mode(mode, args[1]) and self.names(args[0])
        if rule == "*":
            return any(self.names(arg) for arg in args)
        if rule.endswith(":"):
            return any(self.names(arg) for arg in args[int(rule[:-1]) :])
        return any(self.names(args[int(index)]) for index in rule.split(",") if int(index) < len(args))

    def perl_open(self, args: list[list[_Tok]]) -> bool:
        """``open(FH, MODE, CHEMIN)`` ou ``open(FH, "MODECHEMIN")`` : écrit si le mode constant écrit."""
        if len(args) >= 3:
            mode = self.value(args[1]).strip()
            if mode.startswith("|"):
                return self.launched(args[2:])
            return bool(_PERL_WRITE_MODE.match(mode)) and self.names(args[2])
        if len(args) == 2:
            spec = self.value(args[1]).strip()
            if spec.startswith("|") or spec.endswith("|"):
                return self.command(spec.strip("|").strip())
            write = _PERL_WRITE_MODE.match(spec)
            return write is not None and self.host.hit(spec[write.end() :].strip())
        return False

    def launched(self, args: list[list[_Tok]]) -> bool:
        """``system("cmd")`` (une chaîne : un script shell) ou ``system("cp", "a", "b")``/``spawn("cp", [..])``."""
        words: list[str] = []
        for arg in args:
            if arg and arg[0].kind == "o" and arg[0].text == "[":
                words.extend(self.value(item) for item in _split_commas(arg[1 : _group(arg, 0) - 1]))
            else:
                words.append(self.value(arg))
        if not words or not all(_known(word) for word in words):
            return False
        if len(words) == 1:
            return self.command(words[0])
        return self.host.run_words(words)


# ── awk ─────────────────────────────────────────────────────────────────────────────────────────────────────────

_AWK_VALUED = frozenset({"-F", "-v", "-f", "-e", "-i", "-l", "-E", "-W"})
_AWK_LONG_VALUED = frozenset(
    {"--field-separator", "--assign", "--file", "--source", "--include", "--load", "--exec"}
)
_AWK_OUTPUT_FILES = {"-o": "--pretty-print", "-p": "--profile", "-d": "--dump-variables"}
_INPLACE = frozenset({"inplace", "inplace.awk"})


class _Awk(_Script):
    """Un programme awk : ``print``/``printf`` redirigés, ``system``, ``getline`` depuis une commande."""

    def statement(self, toks: list[_Tok]) -> bool:
        for k, tok in enumerate(toks):
            if tok.kind == "n" and tok.text in ("print", "printf") and self.redirected(toks[k + 1 :]):
                return True
            following = toks[k + 1].text if k + 1 < len(toks) else ""
            if (
                tok.kind == "n"
                and tok.text == "system"
                and following == "("
                and self.command(self.value(toks[k + 2 : _group(toks, k + 1) - 1]))
            ):
                return True
            if (
                tok.kind == "o"
                and tok.text in ("|", "|&")
                and following == "getline"
                and k >= 1
                and self.command(self.value(toks[k - 1 : k]))
            ):
                return True
        if len(toks) >= 3 and toks[0].kind == "n" and toks[1].kind == "o" and toks[1].text == "=":
            self.env[toks[0].text] = self.value(toks[2:])
        return False

    def redirected(self, toks: list[_Tok]) -> bool:
        depth = 0
        for k, tok in enumerate(toks):
            if tok.kind == "o" and tok.text in ("(", "["):
                depth += 1
            elif tok.kind == "o" and tok.text in (")", "]"):
                depth -= 1
            elif depth == 0 and tok.kind == "o" and tok.text in (">", ">>"):
                return self.names(toks[k + 1 :])
            elif depth == 0 and tok.kind == "o" and tok.text in ("|", "|&"):
                return self.command(self.value(toks[k + 1 :]))
        return False


def awk_writes(args: list[str], host: Host) -> bool:
    """Vrai si ``awk`` *args* écrit sur la cible : redirection dans le programme, ``-i inplace``, fichier de sortie."""
    programs: list[str] = []
    env: dict[str, str] = {}
    inplace = False
    has_program = False
    i = 0
    rest: list[str] = []
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            rest = args[i:]
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name in _AWK_OUTPUT_FILES.values():
                if eq and host.hit(value):
                    return True
                continue
            if name not in _AWK_LONG_VALUED:
                continue
            if not eq and i < len(args):
                value, i = args[i], i + 1
        elif arg.startswith("-") and len(arg) > 1:
            name, value = arg[:2], arg[2:]
            if name in _AWK_OUTPUT_FILES:
                if value and host.hit(value):
                    return True
                continue
            if name not in _AWK_VALUED:
                continue
            if not value and i < len(args):
                value, i = args[i], i + 1
        else:
            rest = args[i - 1 :]
            break
        if name in ("-v", "--assign") and "=" in value:
            key, val = value.split("=", 1)
            env[key] = val
        elif name in ("-f", "--file", "-E", "--exec"):
            has_program = True
            text = host.source(value)
            programs.extend([text] if text is not None else [])
        elif name in ("-e", "--source"):
            has_program = True
            programs.append(value)
        elif name in ("-i", "--include"):
            inplace = inplace or value in _INPLACE
    if not has_program and rest:
        programs.append(rest[0])
        rest = rest[1:]
    files: list[str] = []
    for operand in rest:
        assignment = re.fullmatch(r"([A-Za-z_]\w*)=(.*)", operand, re.DOTALL)
        if assignment:
            env[assignment.group(1)] = assignment.group(2)
        else:
            files.append(operand)
    if inplace and any(host.hit(f) for f in files):
        return True
    return any(_Awk("awk", host, env).run(program) for program in programs)


# ── Interpréteurs à exécution en ligne ──────────────────────────────────────────────────────────────────────────

#: ``programme -> (langage, options qui portent du code, options à valeur séparée qui ne sont pas du code)``.
_INTERPRETERS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "perl": ("perl", frozenset({"-e", "-E"}), frozenset()),
    "ruby": ("ruby", frozenset({"-e"}), frozenset({"-r", "-I", "-C", "-E", "-F"})),
    "node": ("js", frozenset({"-e", "--eval", "-p", "--print"}), frozenset(
        {"-r", "--require", "--import", "--input-type", "--loader", "--experimental-loader", "-C", "--conditions",
         "--env-file", "--title"}
    )),
    "bun": ("js", frozenset({"-e", "--eval", "-p", "--print"}), frozenset({"-r", "--preload", "--cwd", "--env-file"})),
    "php": ("php", frozenset({"-r", "-B", "-R", "-E"}), frozenset({"-d", "-c", "-z", "-t", "-S"})),
    "lua": ("lua", frozenset({"-e"}), frozenset({"-l"})),
}
_PROGRAM_ALIASES = {"nodejs": "node", "luajit": "lua", "gawk": "awk", "mawk": "awk", "nawk": "awk", "gsed": "sed"}
_LUA = re.compile(r"^lua[0-9.]*$")
_PERL = re.compile(r"^perl[0-9.]*$")
_RUBY = re.compile(r"^ruby[0-9.]*$")
_PHP = re.compile(r"^php[0-9.]*$")


def _interpreter_name(program: str) -> str:
    for pattern, name in ((_LUA, "lua"), (_PERL, "perl"), (_RUBY, "ruby"), (_PHP, "php")):
        if pattern.match(program):
            return name
    return _PROGRAM_ALIASES.get(program, program)


def _interpreter_writes(name: str, args: list[str], host: Host) -> bool:
    lang, code_options, valued = _INTERPRETERS[name]
    bodies: list[str] = []
    operands: list[str] = []
    inplace = loops = False
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--" or not arg.startswith("-") or arg == "-":
            operands = args[i:] if arg == "--" else args[i - 1 :]
            break
        if arg.startswith("--"):
            option, eq, value = arg.partition("=")
            if option in code_options:
                if not eq and i < len(args):
                    value, i = args[i], i + 1
                bodies.append(value)
            elif option in valued and not eq:
                i += 1
            continue
        if arg in code_options or arg in valued:
            if i < len(args):
                bodies.extend([args[i]] if arg in code_options else [])
                i += 1
            continue
        if lang in ("perl", "ruby"):  # grappes d'options : ``-pi.bak -e``, ``-ne``, ``-i``
            for k, letter in enumerate(arg[1:], start=1):
                if letter in "eE":
                    value = arg[k + 1 :]
                    if not value and i < len(args):
                        value, i = args[i], i + 1
                    bodies.append(value)
                    break
                if letter == "i":
                    inplace = True
                    break
                loops = loops or letter in "pn"
                if letter in "MmIrCFdDxl0" and arg[k + 1 :]:
                    break  # le reste du mot est la valeur de cette option
            continue
        letters = {o[1] for o in code_options if len(o) == 2}
        if len(arg) > 2 and set(arg[1:]) <= letters:  # ``node -pe code``
            bodies.extend(args[i : i + 1])
            i += 1
            continue
        option = next((o for o in code_options if arg.startswith(o) and len(o) == 2), "")
        if option:
            bodies.append(arg[2:])
    if not bodies and operands:
        text = host.source(operands[0])
        bodies.extend([text] if text is not None else [])
        operands = operands[1:]
    if inplace and loops and any(host.hit(operand) for operand in operands):
        return True
    return any(_Script(lang, host).run(body) for body in bodies)


def language_writes(program: str, args: list[str], host: Host) -> bool:
    """Vrai si *program* (``sed``, ``awk``, un interpréteur reconnu) écrit, de façon prouvée, sur la cible.

    Faux pour tout autre programme : l'appelant poursuit son analyse.
    """
    name = _interpreter_name(program)
    if name == "sed":
        return sed_writes(args, host)
    if name == "awk":
        return awk_writes(args, host)
    if name == "deno":
        if args[:1] != ["eval"]:
            return False
        code = [a for a in args[1:] if not a.startswith("-")]
        return bool(code) and _Script("js", host).run(code[0])
    if name in _INTERPRETERS:
        return _interpreter_writes(name, args, host)
    return False
