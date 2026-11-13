"""Verbes dont la lecture se décide sur les arguments : une règle par verbe.

Extrait de :mod:`.tool_facts`, qui applique ces règles (:data:`ARGUMENT_CHECKS`) sur
le chemin lecture / mutation et sur lui seul. Chaque règle reçoit les arguments du verbe
et dit si la commande ne fait que lire. Toutes fonctionnent en liste blanche ou refusent
une forme qu'elles ne reconnaissent pas : classer en lecture une commande qui écrit est
le seul sens dangereux.
"""

from __future__ import annotations

import re

# ``journalctl``, ``systemctl``, ``ss``, ``curl`` et ``gh`` lisent dans leurs formes
# courantes et écrivent dans d'autres. Chacun a sa règle, appliquée deux fois : sur
# la surface (guillemets effacés) et sur les mots résolus par ``shlex``
# (``tool_facts._without_read_only_scripts``), où un drapeau écrit entre guillemets
# (``curl "-o" f``) redevient visible. Une forme non reconnue est une mutation.

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


_SYSTEMCTL_READ_SUBCOMMANDS = frozenset(
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
        return word in _SYSTEMCTL_READ_SUBCOMMANDS
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
_READ_ONLY_GH_VERBS = frozenset({"view", "list", "status", "diff", "show", "checks"})


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
    return any(word in _READ_ONLY_GH_VERBS for word in args[:2])


ARGUMENT_CHECKS = {
    "curl": _curl_is_read_only,
    "gh": _gh_is_read_only,
    "journalctl": _journalctl_is_read_only,
    "ss": _ss_is_read_only,
    "systemctl": _systemctl_is_read_only,
}
