"""Verbes dont la lecture se décide sur les arguments : une règle par verbe.

La feuille du classement lecture / mutation : :mod:`.read_only` lit la ligne de commande
(guillemets, heredocs, redirections, enveloppes) et applique ces règles aux arguments de
chaque verbe ; ce module ne dépend que de ``re``. Chaque règle reçoit les arguments du
verbe et dit si la commande ne fait que lire. Toutes fonctionnent en liste blanche ou
refusent une forme qu'elles ne reconnaissent pas : classer en lecture une commande qui
écrit est le seul sens dangereux.

Deux tables : :data:`ARGUMENT_CHECKS`, appliquée à la surface (guillemets effacés) puis
aux mots résolus, où un drapeau écrit entre guillemets (``curl "-o" f``) redevient
visible ; :data:`RESOLVED_ONLY_CHECKS`, pour les verbes dont l'argument décisif est
cité (le script de ``sed``, le programme de ``awk``) et que seuls les mots résolus
peuvent juger. Une forme non reconnue est une mutation.
"""

from __future__ import annotations

import re

#: Options longues de ``journalctl`` qui écrivent : rotation, purge, vidage,
#: clés de scellement, catalogue, et ``--cursor-file`` qui met son fichier à jour.
_JOURNALCTL_WRITING_OPTIONS = (
    "--cursor-file", "--flush", "--force", "--relinquish-var", "--rotate", "--setup-keys",
    "--smart-relinquish-var", "--sync", "--update-catalog", "--vacuum-files", "--vacuum-size", "--vacuum-time",
)


def _abbreviates(word: str, options: tuple[str, ...]) -> bool:
    """*word* (``--nom`` ou ``--nom=valeur``) désigne une des *options*, abréviation de getopt comprise."""
    name = word.split("=", 1)[0]
    return len(name) > 2 and name.startswith("--") and any(option.startswith(name) for option in options)


def _journalctl_is_read_only(args: list[str]) -> bool:
    # ``--cursor`` (lecture) est le préfixe exact de ``--cursor-file`` (écriture).
    return not any(
        word.split("=", 1)[0] != "--cursor" and _abbreviates(word, _JOURNALCTL_WRITING_OPTIONS) for word in args
    )


SYSTEMCTL_READ_SUBCOMMANDS = frozenset(
    {"status", "is-active", "is-enabled", "list-timers", "list-units", "show", "cat"}
)
#: Options sans valeur admises AVANT la sous-commande. Une option à valeur séparée
#: (``-H hôte``, ``-n 20``) avalerait le mot suivant : ``systemctl -H status restart x``
#: redémarre ``x``. Écrite ``--nom=valeur``, elle n'avale rien.
_SYSTEMCTL_LEADING_FLAGS = frozenset(
    {"--user", "--system", "--no-pager", "--full", "-l", "--all", "-a", "--failed", "--no-legend", "--plain",
     "--quiet", "-q"}
)


def _systemctl_is_read_only(args: list[str]) -> bool:
    for word in args:
        if word in _SYSTEMCTL_LEADING_FLAGS or (word.startswith("--") and "=" in word):
            continue
        return word in SYSTEMCTL_READ_SUBCOMMANDS
    return True  # sans sous-commande, ``systemctl`` est ``list-units``


def _ss_is_read_only(args: list[str]) -> bool:
    """``ss`` lit, sauf ``-K``/``--kill`` (ferme des sockets) et ``-D``/``--diag`` (écrit un fichier)."""
    for word in args:
        if word.startswith("--"):
            if _abbreviates(word, ("--kill", "--diag")):
                return False
        elif word.startswith("-") and ("K" in word or "D" in word):
            return False
    return True


#: ``curl`` : liste blanche. Tant d'options écrivent un fichier ou envoient un corps
#: (``-o``, ``-O``, ``-d``, ``-F``, ``-T``, ``-c``, ``-D``, ``--json``, ``-K``,
#: ``--trace``, ``--libcurl``…) qu'une liste noire en oublierait : une option absente
#: d'ici est une mutation. Les en-têtes et les identifiants (``-H``, ``-u``, ``-b``)
#: en sont absents à dessein : un en-tête peut changer ce que fait la requête
#: (``X-HTTP-Method-Override: DELETE``), et un appel authentifié reste évalué et tracé
#: comme avant. Seul le GET anonyme, sans en-tête choisi, est une lecture.
#:
#: Ce qui change de protocole, d'hôte ou de chemin réseau en cours de route en est absent
#: aussi : ``--proto*``, ``--connect-to``, ``--resolve``, ``-x``/``--proxy``, ``--socks*``,
#: ``--unix-socket``, ``--location-trusted``. ``-L`` y reste : sans ``--proto-redir``, curl
#: borne lui-même les protocoles qu'une redirection peut atteindre.
_CURL_SHORT_FLAGS = frozenset("sSfLiIvk46gN#")
_CURL_SHORT_VALUED = frozenset("mwX")
_CURL_LONG_FLAGS = frozenset(
    {
        "--silent", "--show-error", "--fail", "--fail-with-body", "--location", "--include", "--head",
        "--verbose", "--insecure", "--compressed", "--globoff", "--no-buffer", "--no-progress-meter",
        "--ipv4", "--ipv6", "--http1.1", "--http2",
    }
)
_CURL_LONG_VALUED = frozenset(
    {
        "--max-time", "--connect-timeout", "--retry", "--retry-delay", "--retry-max-time", "--max-redirs",
        "--url", "--cacert", "--write-out", "--request",
    }
)
#: Une URL que ``curl`` lira en HTTP, et elle seule. Le schéma est écrit en clair : un
#: autre schéma envoie des octets choisis à un service qui n'est pas un serveur web
#: (``gopher``, ``dict``, ``smtp``…) ou lit hors du réseau (``file``), et sans schéma curl
#: devine le protocole d'après le nom d'hôte. L'hôte est nu : ni identifiant devant ``@``
#: (un appel authentifié reste évalué), ni littéral entre crochets. Rien de ce que le
#: globbing de curl (accolades, crochets) ou le shell (``$``, backtick, antislash)
#: développerait en une autre URL.
_CURL_URL_RE = re.compile(r"https?://[A-Za-z0-9._-]+(?::[0-9]+)?(?:[/?#][^\s{}\[\]$`\\]*)?", re.IGNORECASE)


def _curl_reads_url(url: str) -> bool:
    return _CURL_URL_RE.fullmatch(url) is not None


def _curl_value_reads(option: str, value: str) -> bool:
    if option in {"X", "--request"}:
        return value.upper() == "GET"
    if option in {"w", "--write-out"}:
        return "%output" not in value and not value.startswith("@")  # ``%output{f}`` écrit ``f``
    if option == "--url":
        return _curl_reads_url(value)
    return True


def _curl_is_read_only(args: list[str]) -> bool:
    """``curl`` en lecture : un GET (ou HEAD) anonyme en HTTP dont la réponse va sur la sortie standard.

    Chaque URL, en opérande ou valeur de ``--url``, doit être une URL ``http://`` ou
    ``https://`` écrite en clair (:data:`_CURL_URL_RE`). Une option à valeur en fin de
    ligne passe, et une URL entre guillemets n'est pas vue ici : sur la surface, l'une et
    l'autre ont été effacées ; elles sont relues sur les mots résolus, où ``curl`` sans
    valeur s'arrête sans rien faire.
    """
    i, n = 0, len(args)
    while i < n:
        word = args[i]
        i += 1
        if word == "-" or not word.startswith("-"):
            if not _curl_reads_url(word):
                return False
            continue
        if word.startswith("--"):
            if word in _CURL_LONG_FLAGS:
                continue
            if word not in _CURL_LONG_VALUED:
                return False
            if i < n and not _curl_value_reads(word, args[i]):
                return False
            i += 1
            continue
        for position, flag in enumerate(word[1:], start=2):
            if flag in _CURL_SHORT_FLAGS:
                continue
            if flag not in _CURL_SHORT_VALUED:
                return False
            value = word[position:] or None  # ``-XGET`` : la valeur suit dans le même mot
            if value is None and i < n:
                value = args[i]
                i += 1
            if value is not None and not _curl_value_reads(flag, value):
                return False
            break
    return True


#: ``gh <resource> <verb> ...`` — the verb (second or third word) decides,
#: independently of the resource (``pr``, ``issue``, ``repo``, ``run``…):
#: ``gh pr view``, ``gh issue list``, ``gh run view``, ``gh pr checks`` never
#: mutate. ``gh api`` is read apart (:func:`_gh_api_is_read_only`).
READ_ONLY_GH_VERBS = frozenset({"view", "list", "status", "diff", "show", "checks"})


def _gh_api_is_read_only(args: list[str]) -> bool:
    """``gh api`` en lecture : méthode GET (le défaut sans champ) et aucun corps envoyé."""
    i, n = 0, len(args)
    while i < n:
        word = args[i]
        i += 1
        if word.startswith("--"):
            name, has_value, value = word.partition("=")
            if name in {"--field", "--raw-field", "--input"}:
                return False
            if name == "--method":
                if not has_value:
                    value = args[i] if i < n else ""
                    i += 1
                if value.upper() != "GET":
                    return False
        elif word.startswith("-") and any(flag in word for flag in "XfF"):
            if word == "-X":
                value = args[i] if i < n else ""
                i += 1
            elif word.startswith("-X"):
                value = word[2:]
            else:
                return False  # ``-f``, ``-F``, ou un groupe d'options qui en porte un
            if value.upper() != "GET":
                return False
    return True


def _gh_is_read_only(args: list[str]) -> bool:
    if args[:1] == ["api"]:
        return _gh_api_is_read_only(args[1:])
    return any(word in READ_ONLY_GH_VERBS for word in args[:2])


# ── Verbes de lecture qui ont une forme qui écrit ou exécute ─────────────────

#: ``find`` primaries that act instead of just reporting (issue #643): a bare
#: ``find`` only prints, but any of these delete, run an arbitrary command, or
#: prompt before doing so.
_FIND_MUTATING_PRIMARIES = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)


def _find_is_read_only(args: list[str]) -> bool:
    return not any(word in _FIND_MUTATING_PRIMARIES for word in args)


def _sort_is_read_only(args: list[str]) -> bool:
    """``sort`` lit, sauf ``-o``/``--output`` (écrit) et ``--compress-program`` (exécute), abréviations comprises."""
    return not any(
        _abbreviates(word, ("--output", "--compress-program"))
        or (word.startswith("-") and not word.startswith("--") and "o" in word)
        for word in args
    )


def _uniq_is_read_only(args: list[str]) -> bool:
    return len([word for word in args if not word.startswith("-")]) < 2  # ``uniq in out`` écrit ``out``


def _tree_is_read_only(args: list[str]) -> bool:
    """``tree`` lit, sauf ``-o fichier`` et ``-R`` (qui relance ``tree -o`` dans chaque dossier)."""
    return not any(word.startswith("-") and not word.startswith("--") and ("o" in word or "R" in word) for word in args)


_XXD_FLAGS = frozenset(
    {"-a", "-autoskip", "-b", "-bits", "-C", "-capitalize", "-d", "-decimal", "-e", "-E", "-EBCDIC", "-i",
     "-include", "-p", "-ps", "-postscript", "-plain", "-u", "-uppercase"}
)
_XXD_VALUED = frozenset({"-c", "-cols", "-g", "-groupsize", "-l", "-len", "-n", "-name", "-o", "-s", "-seek", "-R"})


def _xxd_is_read_only(args: list[str]) -> bool:
    """``xxd`` lit un fichier au plus : un second est sa sortie, et ``-r`` reconstruit un binaire."""
    files = 0
    i, n = 0, len(args)
    while i < n:
        word = args[i]
        i += 1
        if word in _XXD_VALUED:
            i += 1
        elif word == "-" or not word.startswith("-"):
            files += 1
        elif word not in _XXD_FLAGS:
            return False
    return files <= 1


def _rg_is_read_only(args: list[str]) -> bool:
    """``rg`` lit, sauf quand il lance un programme : préprocesseur ou commande de nom d'hôte."""
    return not any(word.split("=", 1)[0] in {"--pre", "--pre-glob", "--hostname-bin"} for word in args)


def _ag_is_read_only(args: list[str]) -> bool:
    return not any(_abbreviates(word, ("--pager",)) for word in args)  # ``--pager`` lance une commande


def _clustered_flag(args: list[str], flag: str, valued: str) -> bool:
    """Vrai si un groupe d'options courtes porte *flag* avant toute option à valeur (*valued*)."""
    for word in args:
        if not word.startswith("-") or word.startswith("--"):
            continue
        for letter in word[1:]:
            if letter == flag:
                return True
            if letter in valued:
                break  # le reste du mot est la valeur de cette option
    return False


def _date_is_read_only(args: list[str]) -> bool:
    """``date`` lit l'horloge, sauf ``-s``/``--set`` et une date positionnelle, qui la règlent."""
    if _clustered_flag(args, "s", "dfIr") or any(_abbreviates(word, ("--set",)) for word in args):
        return False
    i, n = 0, len(args)
    while i < n:
        word = args[i]
        i += 1
        if word in {"-d", "-f", "-r", "--date", "--file", "--reference"}:
            i += 1  # la valeur de l'option
        elif not word.startswith(("-", "+")):
            return False  # ``date MMJJhhmm`` règle l'horloge
    return True


def _file_is_read_only(args: list[str]) -> bool:
    """``file`` lit, sauf ``-C``/``--compile`` qui écrit une base de signatures."""
    return not (_clustered_flag(args, "C", "mefFP") or any(_abbreviates(word, ("--compile",)) for word in args))


# ── ``git`` ──────────────────────────────────────────────────────────────────

#: ``git`` subcommands kept out of this list on purpose because they have a
#: common mutating form (``git branch -d``, ``git remote add``, ``git tag
#: v1``, ``git config user.name ...``): treating them as always read-only
#: would be the exact defect this classifier exists to fix, just moved one
#: layer down. Only genuinely read-only-in-every-form subcommands qualify.
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe", "shortlog",
        "grep", "cat-file", "ls-tree",  # issue #875
    }
)


def _git_subcommand(words: list[str]) -> tuple[str, list[str]]:
    """Sous-commande git et ses arguments, après ``-C <dir>`` / ``--no-pager``."""
    i = 0
    while i < len(words) and (words[i] in {"--no-pager", "-P", "--paginate"} or words[i] == "-C"):
        i += 2 if words[i] == "-C" else 1
    return (words[i], words[i + 1 :]) if i < len(words) else ("", [])


def _writes_to_file(arg: str) -> bool:
    """``--output[=f]`` de ``git diff|log|show``, abréviations de git comprises (``--out``, ``--outpu=f``)."""
    name = arg.split("=", 1)[0]
    return name.startswith("--ou") and (name.startswith("--output") or "--output".startswith(name))


def git_is_read_only(words: list[str]) -> bool:
    sub, args = _git_subcommand(words)
    if sub in {"diff", "log", "show"} and any(_writes_to_file(a) for a in args):
        return False  # ``git log --output=f`` écrit ``f`` (``format-patch`` et ``archive`` ne sont pas dans la liste)
    if sub == "grep":
        return not any(a.startswith("-O") or _abbreviates(a, ("--open-files-in-pager",)) for a in args)
    if sub == "remote":
        # Liste blanche sur la liste COMPLÈTE des arguments : ``git remote -v add x y``
        # ajoute un dépôt malgré son premier argument.
        if not args or args in (["-v"], ["--verbose"]):
            return True
        if args[0] != "get-url":
            return False
        rest = args[1:]
        names = [a for a in rest if not a.startswith("-")]
        return len(names) == 1 and all(a in {"--push", "--all"} for a in rest if a.startswith("-"))
    if sub == "branch":
        # ``--list`` rend les autres arguments motifs de recherche ; sans lui, un nom nu crée une branche.
        listing = "--list" in args or "-l" in args
        flags = {"--show-current", "-a", "-r", "-v", "-vv", "--list", "-l", "--all", "--remotes"}
        return all(a in flags or (listing and not a.startswith("-")) for a in args)
    return sub in _READ_ONLY_GIT_SUBCOMMANDS


# ── ``sed`` et ``awk`` : listes blanches de ce que leur script peut faire ────
#
# La « surface » efface le contenu des guillemets, donc le script d'un ``sed`` ou le
# programme d'un ``awk`` y est invisible. Leurs règles ne s'appliquent qu'aux mots
# résolus, et en liste blanche : tout ce qui n'est pas explicitement reconnu comme une
# lecture est une mutation.

_SED_FLAG_CLUSTER_RE = re.compile(r"^-[nErz]*e?$")
_SED_ADDR_STEP_RE = re.compile(r"\d+(?:~\d+)?|\+\d+|~\d+")
_SED_S_FLAGS = frozenset("gpiImM0123456789")
_AWK_FORBIDDEN_RE = re.compile(r"system|getline|>|\||@load|close\s*\(")
_AWK_FORBIDDEN_OPTIONS = ("-f", "-i", "-E", "-l", "--f", "--i", "--e", "--l")


def _sed_script_is_read_only(script: str) -> bool:
    """Un script ``sed`` n'est une lecture que fait de commandes de la liste blanche."""
    i, n = 0, len(script)

    def skip_blank(pos: int, *, separators: bool = False) -> int:
        chars = " \t\n;" if separators else " \t"
        while pos < n and script[pos] in chars:
            pos += 1
        return pos

    def regex_end(pos: int, delim: str) -> int:
        """Indice du délimiteur fermant à partir de *pos*, ou -1."""
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
        m = _SED_ADDR_STEP_RE.match(script, pos)
        if m:
            return m.end()
        if pos < n and script[pos] in "/\\":
            if script[pos] == "\\":
                pos += 1
                if pos >= n:
                    return -1
            end = regex_end(pos + 1, script[pos])
            if end < 0:
                return -1
            end += 1
            while end < n and script[end] in "IM":
                end += 1
            return end
        return pos  # pas d'adresse

    while True:
        i = skip_blank(i, separators=True)
        if i >= n:
            return True
        start = i
        i = address(i)
        if i < 0:
            return False
        if i < n and script[i] == "," and i > start:
            i = address(skip_blank(i + 1))
            if i < 0:
                return False
        i = skip_blank(i)
        while i < n and script[i] == "!":
            i = skip_blank(i + 1)
        if i >= n:
            return False
        cmd = script[i]
        i += 1
        if cmd in "{}":
            continue
        if cmd in "pP=dDnN":
            pass
        elif cmd in "lqQ":
            while i < n and script[i].isdigit():
                i += 1
        elif cmd == "s":
            if i >= n or script[i].isalnum() or script[i] in "\\\n; \t":
                return False
            delim = script[i]
            mid = regex_end(i + 1, delim)
            end = -1 if mid < 0 else regex_end(mid + 1, delim)
            if end < 0:
                return False
            i = end + 1
            while i < n and script[i] in _SED_S_FLAGS:
                i += 1
        else:
            return False  # w, W, e, r, R, y, a, i, c, #, ... : mutation ou non analysé
        j = skip_blank(i)
        if j < n and script[j] not in ";\n}":
            return False
        i = j


def _sed_is_read_only(args: list[str]) -> bool:
    """``sed`` en lecture seule : options et scripts tous dans la liste blanche."""
    scripts: list[str] = []
    positional: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "-" or not word.startswith("-"):
            positional.append(word)
        elif word in {"--quiet", "--silent", "--regexp-extended"}:
            continue
        elif _SED_FLAG_CLUSTER_RE.match(word):
            if word.endswith("e"):
                if i >= len(args):
                    return False
                scripts.append(args[i])
                i += 1
        else:
            return False  # -f, --file, -i*, --in*, -s, --expression=..., inconnu
    if not scripts:
        if not positional:
            return False
        scripts.append(positional.pop(0))
    return all(_sed_script_is_read_only(script) for script in scripts)


def _awk_is_read_only(args: list[str]) -> bool:
    """``awk`` en lecture seule : ni fichier de programme, ni sortie, ni commande.

    Un ``>`` de comparaison (``$1 > 3``) est lui aussi refusé : c'est le côté sûr.
    """
    for word in args:
        if word.startswith(_AWK_FORBIDDEN_OPTIONS) and word.startswith("-"):
            return False
        if _AWK_FORBIDDEN_RE.search(word):
            return False
    return True


#: Verbes dont la lecture dépend des arguments, jugés sur la surface comme sur les mots résolus.
ARGUMENT_CHECKS = {
    "ag": _ag_is_read_only,
    "curl": _curl_is_read_only,
    "date": _date_is_read_only,
    "file": _file_is_read_only,
    "find": _find_is_read_only,
    "gh": _gh_is_read_only,
    "journalctl": _journalctl_is_read_only,
    "rg": _rg_is_read_only,
    "sort": _sort_is_read_only,
    "ss": _ss_is_read_only,
    "systemctl": _systemctl_is_read_only,
    "tree": _tree_is_read_only,
    "uniq": _uniq_is_read_only,
    "xxd": _xxd_is_read_only,
}
#: Verbes dont l'argument décisif est cité (le script de ``sed``, le programme de ``awk``) :
#: la surface l'a effacé, seuls les mots résolus peuvent le juger.
RESOLVED_ONLY_CHECKS = {
    "awk": _awk_is_read_only,
    "gawk": _awk_is_read_only,
    "mawk": _awk_is_read_only,
    "sed": _sed_is_read_only,
}
