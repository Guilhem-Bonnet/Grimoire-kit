"""Dire si une commande shell ne fait que lire.

Le classement lecture / mutation que rend
:func:`grimoire.hosts.decisions.tool_facts.classify_tool`, sorti de ce module-là quand
il a dépassé ce qu'un fichier doit porter. Une chaîne : :mod:`.tool_facts` décrit l'appel
d'outil, ce module lit la ligne de commande (guillemets, heredocs, commentaires,
redirections, enveloppes, affectations, mots que le shell développe), et
:mod:`.read_only_arguments` porte la règle d'arguments de chaque verbe. Une ligne de
commande en entrée, un booléen en sortie (:func:`is_read_only_command`).

Il échoue fermé. Une commande n'est une lecture que si chacun de ses segments, jugé sur
ses mots une fois les guillemets retirés, passe la règle de son verbe ; ce que le
classement ne sait pas lire est une mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from grimoire.hosts.decisions.read_only_arguments import (
    ARGUMENT_CHECKS,
    READ_ONLY_GH_VERBS,
    RESOLVED_ONLY_CHECKS,
    SYSTEMCTL_READ_SUBCOMMANDS,
    git_is_read_only,
)

#: Where a quoted string stops being data and becomes a command again: whatever
#: is handed to these is executed, so it stays under inspection.
_EVAL_INTRODUCER = re.compile(
    r"(?:\b(?:bash|sh|zsh|dash|ksh|ash)\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*c"
    r"|\beval|\bxargs(?:\s+-\S+)*|\bsu\s+-c|\bssh\s+\S+|\btimeout\s+\S+"
    # Windows (issue #761) : ce que reçoivent ``pwsh -Command`` et ``cmd /c``
    # est exécuté, exactement comme ``bash -c``.
    r"|(?i:\b(?:pwsh|powershell)(?:\.exe)?(?:\s+-\S+)*?\s+-(?:c|command|e|encodedcommand))"
    r"|(?i:\bcmd(?:\.exe)?(?:\s+/\S+)*?\s+/[ck]))\s*$"
)

#: ``<<TAG`` / ``<<'TAG'`` / ``<<-TAG``, opening a body the command *writes*.
#: Pas ``<<<`` : une chaîne passée sur l'entrée n'a pas de corps, les lignes qui la
#: suivent sont des commandes. L'étiquette doit finir le mot : ``<<'EO'F`` attend ``EOF``,
#: et ce que ce motif ne lit pas en entier n'est pas une ouverture reconnue.
_HEREDOC_OPEN_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(?=$|[\s;|&<>()])")


#: Leading verbs whose blast radius never survives the session — the read
#: half of the destructive/mutation split. Matched against the first
#: *word* of a command (or of one ``&&``/``;``/``|`` segment of it), after
#: stripping the ``VAR=value`` assignments of an inert variable and the
#: transparent wrappers (:func:`_inner_words`: ``sudo``, ``env``, ``nice``,
#: ``timeout 30``, ``rtk``…) — never against the whole line, and
#: never enough on its own: :func:`is_read_only_command` also refuses any
#: output redirection (``>``, ``>>``, ``| tee``), which is what tells
#: ``cat file`` from ``cat >> file`` apart. Deliberately narrow: an
#: unrecognised verb stays classified as a mutation (the pre-existing,
#: safe default), never the reverse.
#:
#: Seuls y figurent les verbes dont AUCUNE option n'écrit ni n'exécute : n'importe
#: quel argument, même porté par une variable, reste un argument de lecture. Un verbe
#: qui a une forme qui écrit (``find -delete``, ``sort -o``, ``tree -o``, ``sed -i``…)
#: a sa règle dans :mod:`.read_only_arguments` ; ``less`` et ``more`` n'y sont plus :
#: ils journalisent dans un fichier (``-o``) et lancent des commandes (``+…``).
_READ_ONLY_LEADING_COMMANDS = frozenset(
    {
        "cat", "head", "tail", "grep", "egrep", "fgrep",
        "ls", "wc", "diff", "stat", "pwd", "whoami", "printenv",
        "which", "type", "ps", "df", "du", "od", "hexdump",
        "sha256sum", "sha1sum", "md5sum", "echo", "true", "false",
        "basename", "dirname", "readlink", "realpath", "nproc", "uptime", "id", "uname",
        # Issue #875 : se déplacer et filtrer un flux ne modifient rien.
        "cd", "pushd", "popd", "cut", "tr", "jq", "column", "nl", "comm", "paste",
        "pgrep",  # ne fait que lister : signaler est le rôle de ``pkill``
    }
)

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# ── Affectations de variables ────────────────────────────────────────────────
#
# ``NOM=valeur commande`` et ``env NOM=valeur commande`` règlent l'environnement de la
# commande portée, et ``NOM=valeur`` seul celui des commandes qui suivent. Une variable
# peut faire exécuter du code au shell porté dès son démarrage, ou changer ce que fera un
# outil de la liste blanche : son fichier de configuration, son afficheur, le chargeur de
# bibliothèques, la recherche des exécutables. Le classement ne sait pas ce qu'une
# variable quelconque déclenche : liste blanche. Une affectation dont le nom n'est pas
# ici n'est pas retirée, et la commande n'est pas une lecture.

#: Variables qui ne règlent que la langue, le fuseau, les couleurs, les dimensions du
#: terminal, les invites et l'encodage de Python. ``LC_*`` est lu par :data:`_LOCALE_RE`.
_INERT_VARIABLES = frozenset(
    {
        "LANG", "LANGUAGE", "TZ", "NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR", "TERM",
        "COLUMNS", "LINES", "GIT_TERMINAL_PROMPT", "GH_PROMPT_DISABLED",
        "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
    }
)
_LOCALE_RE = re.compile(r"LC_[A-Z]+")
#: Un afficheur est une commande que l'outil lance : il n'est inerte que neutralisé.
_PAGER_VARIABLES = frozenset({"PAGER", "GH_PAGER"})
_NEUTRAL_PAGERS = frozenset({"", "cat"})


def _assignment_is_inert(word: str) -> bool:
    """*word* (``NOM=valeur``) affecte une variable de la liste blanche.

    Sur la surface, une valeur entre guillemets est effacée et paraît vide : la valeur
    d'un afficheur est relue sur les mots résolus, où elle est entière.
    """
    name, _, value = word.partition("=")
    if name in _PAGER_VARIABLES:
        return value in _NEUTRAL_PAGERS
    return name in _INERT_VARIABLES or _LOCALE_RE.fullmatch(name) is not None


#: Output redirection or a ``tee`` in the pipeline: the one shell-level
#: signal that turns an otherwise read-only pipeline into a write, checked
#: against the quote-stripped surface so a literal ``>`` inside a commit
#: message or a grep pattern is never mistaken for one. Any ``>`` left there
#: is one, whatever follows it: ``>=f`` writes the file ``=f``.
_WRITE_REDIRECTION_RE = re.compile(r">|\btee\b")


#: Mots-clés de structure du shell (issue #875) : ils précèdent une commande
#: (``do cat f``, ``while read l``) ou la ferment (``done``) ; seule la commande
#: qu'ils portent compte.
_SHELL_LEADING_KEYWORDS = frozenset({"do", "then", "else", "elif", "while", "until", "if", "!"})
_SHELL_CLOSING_KEYWORDS = frozenset({"done", "fi", "esac"})
_SUBSTITUTION_RE = re.compile(r"\$\(|`")

# ── Enveloppes transparentes ─────────────────────────────────────────────────
#
# Une commande arrive souvent enveloppée : par l'agent (``timeout 30 git status``,
# ``env LC_ALL=C ls``) ou par un autre hook qui la réécrit avant le ``PostToolUse``
# (un proxy de terminal rend ``git status`` en ``rtk git status`` et ``cat f`` en
# ``rtk read f``). L'enveloppe ne change pas ce que fait la commande portée : on la
# retire, puis la commande portée est classée par les règles de toujours. Une
# enveloppe dont la forme n'est pas reconnue n'est pas retirée : elle devient le
# verbe, qui n'est pas un verbe de lecture.
#
# ``nohup`` n'en fait pas partie : il écrit ``nohup.out`` dès que la sortie est un
# terminal (c'est le cas de ``run_in_terminal``).

#: Mots sans option qui précèdent une commande sans rien y changer.
_PLAIN_PREFIX_WORDS = frozenset({"sudo", "ionice"}) | _SHELL_LEADING_KEYWORDS
_DURATION_RE = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_NICE_ADJUSTMENT_RE = re.compile(r"-\d+|-n-?\d+|--adjustment=-?\d+")
_TIMEOUT_FLAGS = frozenset({"--preserve-status", "--foreground", "-v", "--verbose"})
_TIMEOUT_VALUED = frozenset({"-s", "--signal", "-k", "--kill-after"})


def _after_timeout(words: list[str], i: int) -> int:
    """Indice de la commande portée par ``timeout [options] <durée>``, ou -1 (forme non reconnue)."""
    n = len(words)
    while i < n and words[i].startswith("-"):
        word = words[i]
        if word in _TIMEOUT_FLAGS or word.startswith(("--signal=", "--kill-after=")):
            i += 1
        elif word in _TIMEOUT_VALUED and i + 1 < n:
            i += 2
        else:
            return -1
    return i + 1 if i < n and _DURATION_RE.fullmatch(words[i]) else -1


def _after_env_options(words: list[str], i: int) -> int:
    """Indice du premier mot après les options de ``env`` qui ne font que régler l'environnement."""
    n = len(words)
    while i < n:
        if words[i] in {"-i", "-", "--ignore-environment"} or words[i].startswith("--unset="):
            i += 1
        elif words[i] == "-u" and i + 1 < n:
            i += 2
        else:
            break
    return i


def _inner_words(words: list[str]) -> list[str] | None:
    """Les mots de la commande portée, une fois retirés affectations inertes, mots-clés et enveloppes.

    ``None`` : une affectation hors de la liste blanche (:func:`_assignment_is_inert`)
    précède la commande, à quelque niveau d'enveloppe que ce soit (``timeout 5 env X=1
    …``) — la commande n'est pas une lecture, quoi qu'elle porte. Une liste vide : il ne
    restait que des affectations inertes ou des mots neutres, rien à muter. ``rtk`` ou
    ``timeout`` suivis de rien restent en tête de la liste rendue : une enveloppe qui ne
    porte aucune commande n'est pas une lecture.
    """
    i, n = 0, len(words)
    wrapper = -1  # premier ``rtk``/``timeout`` retiré
    while i < n:
        word = words[i]
        following = words[i + 1 : i + 2]
        if _ENV_ASSIGNMENT_RE.match(word):
            if not _assignment_is_inert(word):
                return None
            i += 1
        elif word in _PLAIN_PREFIX_WORDS:
            i += 1
        elif word == "command":
            if following in (["-v"], ["-V"]):
                break  # ``command -v gh`` : une recherche, lue par l'appelant
            i += 2 if following == ["-p"] else 1
        elif word == "time":
            i += 2 if following == ["-p"] else 1
        elif word == "nice":
            i += 1
            if following == ["-n"] and re.fullmatch(r"-?\d+", "".join(words[i + 1 : i + 2])):
                i += 2
            elif following and _NICE_ADJUSTMENT_RE.fullmatch(following[0]):
                i += 1
        elif word == "env":
            i = _after_env_options(words, i + 1)
        elif word == "rtk":
            wrapper = i if wrapper < 0 else wrapper
            if following == ["read"]:
                return ["cat", *words[i + 2 :]]  # la réécriture de ``cat``/``head``/``tail`` : aucune option n'écrit
            if following == ["grep"]:
                # Le proxy confie la recherche à ``rg`` et lui passe les options telles
                # quelles : c'est sa règle qui vaut, pas celle de ``grep``.
                return ["rg", *words[i + 2 :]]
            i += 2 if following == ["proxy"] else 1
        elif word == "timeout":
            after = _after_timeout(words, i + 1)
            if after < 0:
                break
            wrapper = i if wrapper < 0 else wrapper
            i = after
        else:
            break
    return words[wrapper:] if i >= n and wrapper >= 0 else words[i:]


def _words_read_only(words: list[str] | None, segment: str, *, resolved: bool) -> bool:
    """La règle du verbe pour une commande portée, enveloppes déjà retirées.

    Appliquée deux fois : aux mots de la surface (*resolved* faux : un argument entre
    guillemets y est effacé, donc ``sed`` et ``awk``, dont le script est cité, y passent
    d'office) et aux mots résolus, guillemets retirés, où tout se voit. C'est la seconde
    lecture qui fait foi ; la première ne peut que refuser davantage.
    """
    if words is None:
        return False  # une affectation hors liste blanche : pas une lecture
    if not words:
        return True  # nothing left but inert assignments/neutral words: nothing to mutate
    verb = words[0]
    args = words[1:]
    if verb == "command":
        return args[:1] in (["-v"], ["-V"])  # ``command -v gh`` : une recherche, jamais une exécution
    if verb in _SHELL_CLOSING_KEYWORDS:
        return True
    if verb == "for":
        return not _SUBSTITUTION_RE.search(segment)  # ``for x in a b`` n'exécute que son corps
    if verb == "git":
        return git_is_read_only(args)
    if verb in ARGUMENT_CHECKS:
        return ARGUMENT_CHECKS[verb](args)
    if verb in RESOLVED_ONLY_CHECKS:
        return RESOLVED_ONLY_CHECKS[verb](args) if resolved else True
    return verb in _READ_ONLY_LEADING_COMMANDS


def _is_read_only_segment(segment: str) -> bool:
    return _words_read_only(_inner_words(segment.split()), segment, resolved=False)


def _split_segments(command: str) -> list[tuple[str, str]]:
    """``(opérateurs qui le précèdent, segment)`` pour chaque commande simple de *command*.

    Les opérateurs (``&&``, ``||``, ``;``, ``|``, ``&``, retour à la ligne) ne séparent
    que hors guillemets et non échappés.
    """
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    operators: list[str] = []
    spans = iter(_opaque_spans(command))
    span = next(spans, None)
    i, n = 0, len(command)

    def flush() -> None:
        text = "".join(current).strip()
        current.clear()
        if text:
            segments.append(("".join(operators), text))
            operators.clear()

    while i < n:
        if span is not None and i == span[0]:
            current.append(command[i : span[1]])
            i = span[1]
            span = next(spans, None)
        elif command[i] in ";|&\n":
            flush()
            operator = command[i : i + 2] if command[i : i + 2] in {"&&", "||"} else command[i]
            operators.append(operator)
            i += len(operator)
        else:
            current.append(command[i])
            i += 1
    flush()
    return segments


# ── Passe sur les mots résolus : celle qui fait foi ──────────────────────────
#
# La surface efface ce qui est entre guillemets : un drapeau cité (``find . "-delete"``)
# ou un verbe cité y disparaît. Cette passe relit chaque segment mot par mot, guillemets
# retirés, et n'accorde la lecture que si CHAQUE mot résolu passe la règle de son verbe.
# Elle échoue fermée : ce qu'elle ne sait pas lire (:func:`_resolve_words` rend
# ``None``) est une mutation, et un mot que le shell peut encore changer (variable,
# joker en tête, accolades) n'est admis que là où il ne peut pas devenir une option qui
# écrit. ``bash -c '<script>'`` exécute son script : il n'est une lecture que si le
# script, reclassé par les mêmes règles, n'est fait que de lectures.


@dataclass(frozen=True, slots=True)
class _Word:
    """Un mot d'un segment, guillemets retirés, et ce que le shell peut encore en faire."""

    text: str
    #: Peut devenir plusieurs mots : expansion hors guillemets, accolades d'expansion.
    splits: bool = False
    #: Peut commencer autrement qu'il s'écrit, donc devenir une option : expansion ou joker en
    #: tête, ou motif de fichiers dans un mot qui commence par un tiret (``-*`` rend les
    #: fichiers du dossier dont le nom est une option).
    leading: bool = False
    #: Motif de fichiers ailleurs qu'en tête : autant de mots que de fichiers trouvés, chacun
    #: commençant comme le mot s'écrit. Sans danger en opérande, pas là où le nombre ou la
    #: place des mots décide (:func:`_globs_allowed`).
    globbed: bool = False

    @property
    def opaque(self) -> bool:
        return self.splits or self.leading


#: ``$X``, ``${X}``, ``$1``, ``$@``… : ce que le shell remplace. Un ``$`` isolé reste un caractère.
_EXPANSION_RE = re.compile(r"\$(?:[A-Za-z_{]|[0-9@*#?$!=~^-])")
#: ``{a,b}``, ``{1..3}``, ``{"-x",y}`` : un mot écrit, plusieurs mots lancés. Sans virgule ni
#: ``..`` entre les accolades, rien n'est développé : ``{}``, ``HEAD@{1}``, ``v1^{commit}``.
_BRACE_EXPANSION_RE = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
#: ``!!``, ``!$``, ``!mot``, ``!:1`` : dans un shell interactif, le rappel est remplacé par un
#: morceau d'une ligne passée AVANT que la ligne soit lue — opérateurs compris. Ce que la
#: ligne devient ne se lit pas ici : un rappel n'est admis derrière aucun verbe.
_HISTORY_RE = re.compile(r"![!$*^#?:\w-]")
#: Ce que le shell exécute ou affecte en développant un texte, entre guillemets doubles et
#: dans le corps d'un heredoc comme hors guillemets : substitution de commande (avec ou sans
#: sous-shell), arithmétique à l'ancienne (``$[…]`` affecte et évalue ce que portent les
#: variables), drapeaux d'expansion de zsh (``${(e)X}`` développe la valeur de ``X``),
#: expansion qui affecte (``${X=v}``, ``${X:=v}``, ``${X::=v}`` : la liste blanche des
#: affectations ne se contourne pas par là), opérateur ``@P`` (développe la valeur comme une
#: invite, substitutions comprises).
_ACTIVE_EXPANSION_RE = re.compile(
    r"`|\$\(|\$\[|\$\{[\s|(]|\$\{[A-Za-z_]\w*(?:\[[^\]]*\])?:{0,2}=|\$\{[^{}]*@P\}"
)
#: Guillemets ANSI-C ou traduits : hors guillemets, le shell les décode, ce balayage non.
_DOLLAR_QUOTES = ("$'", '$"')


#: ``${NOM}``, ou ``$NOM`` sans indice ni modificateur derrière (``$W[1]``, ``$W:t`` de zsh).
#: Le nom est pris en entier : ``$fo`` n'est pas ``$f`` suivi de ``o``.
_PLAIN_EXPANSION_RE = re.compile(r"\$(?:([A-Za-z_]\w*)(?![\w\[:])|\{([A-Za-z_]\w*)\})")

# ── Variable de boucle ───────────────────────────────────────────────────────
#
# ``for f in a b; do sort $f; done`` : dans le corps de la boucle, ``$f`` vaut l'un des
# mots que la ligne écrit, pas ce que le shell pourrait y mettre. Le segment est alors
# jugé une fois par valeur, la variable remplacée. Rien d'autre ne donne une valeur
# connue : une affectation (``W=/chemin``) n'est pas une lecture (liste blanche des
# variables inertes), et hors du corps de sa boucle la variable peut valoir autre chose
# (boucle dans un tube, boucle jamais exécutée).

#: Une valeur écrite en clair, que ni le découpage en mots ni les motifs ne changent : pas
#: de blanc, pas de guillemet, pas de joker, pas de signe égal en tête (zsh y lit le chemin
#: d'une commande), et un tiret ou un plus seulement derrière une lettre ou un chiffre (ni
#: en tête, ni derrière un séparateur).
_LOOP_VALUE_RE = re.compile(r"(?!=)(?:[\w./@%:,=~]|(?<=[A-Za-z0-9_])[-+])+")
_NAME_RE = re.compile(r"[A-Za-z_]\w*")
_LOOP_OPENERS = frozenset({"for", "while", "until", "select"})
_BEFORE_A_LOOP = frozenset({"do", "then", "else", "elif", "if", "!", "time"})
#: Variables que le shell réécrit lui-même entre deux commandes.
_UNSTABLE_NAMES = frozenset(
    {"PWD", "OLDPWD", "RANDOM", "SRANDOM", "SECONDS", "LINENO", "REPLY", "OPTARG", "OPTIND", "COLUMNS", "LINES",
     "EPOCHSECONDS", "EPOCHREALTIME", "UID", "EUID", "PPID", "HOSTNAME", "IFS", "PATH", "HOME", "_", "status",
     "pipestatus", "argv"}
)
#: Au-delà, le segment n'est pas rejugé valeur par valeur : ses variables restent opaques.
_MAX_LOOP_COMBINATIONS = 32


def _shell_words(segment: str) -> list[str]:
    """Les mots bruts de *segment*, coupés aux seuls blancs que le shell lit comme tels."""
    return [word for word in re.split(r"[ \t\n]+", segment) if word]


def _opens_loop(words: list[str]) -> bool:
    for word in words:
        if word not in _BEFORE_A_LOOP:
            return word in _LOOP_OPENERS
    return False


def _loop_values(segments: list[tuple[str, str]]) -> dict[str, tuple[int, int, tuple[str, ...]]]:
    """``{nom: (rang du for, rang de son done, valeurs)}`` des variables de boucle lisibles sur la ligne.

    Une variable n'est connue que si UNE seule boucle de la ligne la porte, sous la forme
    ``for NOM in mot…`` dont chaque mot est écrit en clair (:data:`_LOOP_VALUE_RE`), et si
    le ``done`` de cette boucle se retrouve. Une variable inerte, qu'une affectation seule
    pourrait réécrire sans faire de la ligne une mutation, n'est jamais connue.
    """
    found: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    seen: set[str] = set()
    for index, (_operators, segment) in enumerate(segments):
        words = _shell_words(segment)
        while words and words[0] in _BEFORE_A_LOOP:
            words = words[1:]
        if len(words) < 2 or words[0] != "for":
            continue
        name, values = words[1], words[3:]
        if name in seen:
            found.pop(name, None)  # portée par deux boucles : aucune ne fait foi
            continue
        seen.add(name)
        end = _loop_end(segments, index)
        if (
            end is not None
            and words[2:3] == ["in"]
            and values
            and _NAME_RE.fullmatch(name)
            and name not in _UNSTABLE_NAMES
            and not name.startswith(("BASH", "ZSH"))
            and not _assignment_is_inert(f"{name}=")
            and name not in _PAGER_VARIABLES
            and all(_LOOP_VALUE_RE.fullmatch(value) for value in values)
        ):
            found[name] = (index, end, tuple(values))
    return found


def _loop_end(segments: list[tuple[str, str]], start: int) -> int | None:
    """Rang du ``done`` qui ferme la boucle ouverte au segment *start*, ou ``None``.

    Un mot-clé de fermeture en suit un autre sans séparateur (``fi done``) : chaque
    ``done`` de la tête du segment compte.
    """
    depth = 1
    for index in range(start + 1, len(segments)):
        words = _shell_words(segments[index][1])
        closers = 0
        while closers < len(words) and words[closers] in _SHELL_CLOSING_KEYWORDS:
            if words[closers] == "done":
                depth -= 1
                if depth == 0:
                    return index
            closers += 1
        if not closers and _opens_loop(words):
            depth += 1
    return None


def _loop_bindings(
    loops: dict[str, tuple[int, int, tuple[str, ...]]], index: int, segment: str
) -> list[dict[str, str]]:
    """Les jeux de valeurs sous lesquels juger le segment de rang *index* : un par combinaison, au moins un."""
    bound = {
        name: values
        for name, (start, end, values) in loops.items()
        if start < index < end and re.search(rf"\$\{{?{name}\b", segment)
    }
    count = 1
    for values in bound.values():
        count *= len(values)
    if not bound or count > _MAX_LOOP_COMBINATIONS:
        return [{}]
    return [dict(zip(bound, combination, strict=True)) for combination in product(*bound.values())]


def _resolve_words(
    segment: str, values: dict[str, str] | None = None, *, strict: bool = True
) -> list[_Word] | None:
    """Les mots de *segment* tels que le programme les recevra, ou ``None`` s'il ne se lit pas.

    Ne se lit pas : un guillemet jamais fermé, une substitution de commande ou de
    processus, une expansion qui affecte ou exécute (:data:`_ACTIVE_EXPANSION_RE`), des
    guillemets ANSI-C, une parenthèse nue (sous-shell, fonction, qualificatif de glob et
    substitution de zsh), un rappel d'historique, une redirection de sortie (celles qui
    n'écrivent aucun fichier ont été retirées avant), un antislash en fin de ligne (le mot
    continue sur la ligne suivante). Un ``\\"`` hors guillemets est un caractère, pas
    l'ouverture d'un texte cité. Une variable de *values* est remplacée par sa valeur.

    Sans *strict*, pour ce qui inspecte la ligne au lieu de la classer : seuls un
    guillemet jamais fermé et un antislash en fin de ligne rendent ``None`` ; le reste
    est recopié tel qu'il est écrit.
    """
    values = values or {}
    words: list[_Word] = []
    text: list[str] = []
    splits = leading = globbed = in_word = False
    i, n = 0, len(segment)
    while i < n:
        c = segment[i]
        if c in " \t\n":
            if in_word:
                words.append(_Word("".join(text), splits, leading, globbed))
                text, splits, leading, globbed, in_word = [], False, False, False, False
            i += 1
            continue
        in_word = True
        at_start = not any(text)
        if c == "\\":
            if i + 1 >= n or segment[i + 1] == "\n":
                return None
            text.append(segment[i + 1])
            i += 2
        elif c == "'":
            end = segment.find("'", i + 1)
            if end < 0:
                return None
            text.append(segment[i + 1 : end])
            i = end + 1
        elif c == '"':
            j = i + 1
            while j < n and segment[j] != '"':
                d = segment[j]
                if d == "\\" and j + 1 < n:
                    if segment[j + 1] == "\n":
                        return None
                    text.append(segment[j + 1] if segment[j + 1] in '"\\$`' else segment[j : j + 2])
                    j += 2
                    continue
                if strict and _is_unreadable_at(segment, j):
                    return None
                known = _PLAIN_EXPANSION_RE.match(segment, j) if d == "$" else None
                if known is not None and (known.group(1) or known.group(2)) in values:
                    text.append(values[known.group(1) or known.group(2)])
                    j = known.end()
                    continue
                if d == "$" and _EXPANSION_RE.match(segment, j) and not any(text):
                    leading = True
                text.append(d)
                j += 1
            if j >= n:
                return None
            i = j + 1
        elif strict and (c in "()>" or _is_unreadable_at(segment, i) or segment.startswith(_DOLLAR_QUOTES, i)):
            return None
        else:
            known = _PLAIN_EXPANSION_RE.match(segment, i) if c == "$" else None
            if known is not None and (known.group(1) or known.group(2)) in values:
                text.append(values[known.group(1) or known.group(2)])
                i = known.end()
                continue
            if c == "$" and _EXPANSION_RE.match(segment, i):
                splits, leading = True, leading or at_start
            elif c in "*?[":
                if at_start or "".join(text).startswith("-"):
                    leading = True
                else:
                    globbed = True
            elif c == "{" and _BRACE_EXPANSION_RE.match(segment, i):
                splits, leading = True, leading or at_start
            text.append(c)
            i += 1
    if in_word:
        words.append(_Word("".join(text), splits, leading, globbed))
    return words


def _is_unreadable_at(segment: str, i: int) -> bool:
    """Vrai si le shell exécute, affecte ou rappelle une ligne passée en lisant ce qui commence en *i*.

    Vaut hors guillemets comme entre guillemets doubles, où le shell développe encore.
    """
    c = segment[i]
    if c == "!":
        return _HISTORY_RE.match(segment, i) is not None
    return c in "`$" and _ACTIVE_EXPANSION_RE.match(segment, i) is not None


def _prefix_is_readable(prefix: list[_Word]) -> bool:
    """Ce que :func:`_inner_words` a retiré : une expansion n'y est admise que dans une affectation de tête.

    ``X=$Y cmd`` affecte sans découper. Passé une enveloppe, tout mot est un argument de
    l'enveloppe : ``env -u $X ls`` lance ce que ``$X`` contient après son premier mot.
    """
    assignments = True
    for word in prefix:
        if assignments and (_ENV_ASSIGNMENT_RE.match(word.text) or word.text in _SHELL_LEADING_KEYWORDS):
            continue
        assignments = False
        if word.opaque or word.globbed:
            return False  # ``env -u N* ls`` : le second fichier trouvé devient la commande
    return True


def _git_opaque_allowed(args: list[_Word]) -> bool:
    """``git`` : la valeur de ``-C`` en un seul mot, et les chemins après ``--``, peuvent venir d'une variable."""
    i, n = 0, len(args)
    while i < n and args[i].text in {"--no-pager", "-P", "--paginate", "-C"}:
        if args[i].text == "-C":
            if i + 1 < n and args[i + 1].splits:
                return False
            i += 1
        i += 1
    for arg in args[i:]:
        if arg.text == "--" and not arg.opaque:
            return True
        if arg.opaque:
            return False
    return True


def _opaque_args_allowed(verb: str, args: list[_Word]) -> bool:
    """Un mot que le shell peut encore changer n'est admis que là où il ne peut pas devenir une option qui écrit.

    Partout pour un verbe dont aucune option n'écrit ; après le verbe de lecture de ``gh``
    ou la sous-commande de ``systemctl``, qui fixent ce que fait la commande ; nulle part
    ailleurs.
    """
    if verb in _READ_ONLY_LEADING_COMMANDS or verb in _SHELL_CLOSING_KEYWORDS or verb in {"for", "command"}:
        return True
    if verb == "git":
        return _git_opaque_allowed(args)
    texts = [arg.text for arg in args]
    fixed = len(args)  # nombre de mots de tête qui doivent se lire tels qu'ils sont écrits
    if verb == "gh" and texts[:1] != ["api"]:
        fixed = next((i + 1 for i, text in enumerate(texts[:2]) if text in READ_ONLY_GH_VERBS), fixed)
    elif verb == "systemctl":
        fixed = next((i + 1 for i, text in enumerate(texts) if text in SYSTEMCTL_READ_SUBCOMMANDS), fixed)
    elif verb == "printf":
        fixed = 1  # seul le premier mot peut être l'option qui affecte ; la suite est le format et ses arguments
    return not any(arg.opaque for arg in args[:fixed])


#: Verbes dont la règle ne cherche qu'un mot interdit, où qu'il soit, sans compter les
#: opérandes ni lire leur place : un motif de fichiers en opérande n'y change rien.
_COUNT_BLIND_VERBS = frozenset({"ag", "file", "find", "git", "printf", "rg", "sort", "tree"})


def _globs_allowed(verb: str, args: list[_Word]) -> bool:
    """Un motif de fichiers ailleurs qu'en tête de mot n'est admis que là où ni le nombre ni la place des mots ne décide.

    Il devient autant de mots que de fichiers trouvés, chacun commençant comme il est
    écrit : jamais une option, mais ``uniq a*`` a deux opérandes dès que deux fichiers
    répondent, et le second est sa sortie. Pour ``sed`` et ``awk``, il n'est admis qu'en
    opérande : après un mot écrit en clair (le script, le programme), et pas derrière une
    option dont il serait la valeur. Les verbes qui parlent au système ont leur propre
    règle (:func:`_arguments_are_written_out`).
    """
    if not any(arg.globbed for arg in args):
        return True
    if (
        verb in _READ_ONLY_LEADING_COMMANDS
        or verb in _SHELL_CLOSING_KEYWORDS
        or verb in {"for", "command"}
        or verb in _COUNT_BLIND_VERBS
        or verb in _WRITTEN_OUT_VERBS
    ):
        return True
    if verb not in {"sed", "awk", "gawk", "mawk"}:
        return False  # ``uniq``, ``xxd``, ``date`` : le nombre d'opérandes décide
    anchored = False
    previous = ""
    for arg in args:
        if arg.globbed and (not anchored or previous.startswith("-")):
            return False
        anchored = anchored or not (arg.globbed or arg.text.startswith("-"))
        previous = arg.text
    return True


# ── Arguments que le shell développe, pour les verbes qui parlent au système ─
#
# ``journalctl``, ``systemctl``, ``ss``, ``curl`` et ``gh api`` : la règle du verbe lit
# ses arguments tels qu'ils sont écrits, et rien de ce que le shell développe n'y est
# admis, où que ce soit — une variable, même citée, même de boucle (elle porte ce que la
# liste lui donne), des accolades, un motif de fichiers. Plus strict que
# :func:`_opaque_args_allowed`, qui vaut pour les autres verbes. ``gh`` hors ``gh api``
# garde sa lecture (``gh pr view $PR``).

_WRITTEN_OUT_VERBS = frozenset({"curl", "gh", "journalctl", "ss", "systemctl"})
#: Dans un mot résolu : une variable ou une substitution. Les guillemets sont déjà
#: retirés, un dollar cité entre guillemets simples est donc refusé lui aussi.
_SHELL_VALUE_RE = re.compile(r"[$`]")
#: Sur la surface, donc hors guillemets : des accolades qui portent une virgule ou un
#: intervalle. ``{owner}`` ne se développe pas.
_OPEN_BRACE_EXPANSION_RE = re.compile(r"\{[^{}]*(?:,|\.\.)")
#: Sur la surface, mot par mot : un motif de fichiers. Un ``?`` derrière une barre oblique
#: ne rend que des chemins sous un dossier nommé, jamais une option (``pulls?per_page=5``).
_GLOB_WORD_RE = re.compile(r"[*\[]|^[^/]*\?")


def _arguments_are_written_out(verb: str, args: list[str], segment: str) -> bool:
    """Les arguments de *verb* arrivent à la commande tels que *segment* les écrit.

    *args* sont les mots résolus, sans qu'aucune variable y soit remplacée, *segment* le
    texte brut dont ils viennent : la sous-commande se lit sur les premiers, ce que le
    shell développe hors guillemets sur la surface du second.
    """
    if verb == "gh" and args[:1] != ["api"]:
        return True
    if any(_SHELL_VALUE_RE.search(word) for word in args):
        return False
    surface = _read_surface(segment)
    return not _OPEN_BRACE_EXPANSION_RE.search(surface) and not any(
        _GLOB_WORD_RE.search(word) for word in surface.split()
    )


_INNER_SHELLS = frozenset({"bash", "sh", "zsh"})
#: Un script dans un script est encore relu ; au-delà, il reste une mutation.
_MAX_SCRIPT_DEPTH = 2
_PLAIN_SCRIPT_RE = re.compile(r"[\w./-]+")
#: Dans des guillemets doubles, le shell appelant réécrit le script avant de le passer.
_OUTER_EXPANSION_RE = re.compile(r'["$`\\]')
#: ``${ cmd }`` et ``${| cmd }`` : substitution de commande sans sous-shell (bash 5.3, zsh).
_INLINE_SUBSTITUTION_RE = re.compile(r"\$\{[\s|]")


def _inner_script(shell: str, segment: str, args: list[str]) -> str | None:
    """Le script de ``<shell> -c <script>`` tel que le shell porté le recevra, ou ``None``.

    Seule la forme exacte ``-c`` suivie du script est lue (``-lc``, un fichier, un
    argument de plus : mutation). Le script doit arriver intact : entre guillemets
    simples, entre guillemets doubles sans rien que le shell appelant développe
    (``"ls $DIR"`` devient ``ls ; rm x`` si ``DIR`` le dit), ou en un mot nu.
    """
    if len(args) != 2 or args[0] != "-c":
        return None
    script = args[1]
    raw = segment.rstrip()
    intact = (
        ("'" not in script and raw.endswith(f"'{script}'"))
        or (not _OUTER_EXPANSION_RE.search(script) and raw.endswith(f'"{script}"'))
        or (_PLAIN_SCRIPT_RE.fullmatch(script) is not None and raw.endswith(script))
    )
    if not intact or "<<" in script or _INLINE_SUBSTITUTION_RE.search(script):
        return None
    if shell == "zsh" and "(" in script:
        return None  # ``*(e:'cmd':)`` et ``=(cmd)`` exécutent : hors de ce que la passe sait lire
    return script


def _segment_is_script(segment: str, values: dict[str, str], depth: int) -> bool | None:
    """Vrai si *segment* est un ``bash -c`` en lecture seule, faux s'il lit sans script ; ``None`` s'il ne lit pas.

    Le segment est jugé sur ses mots résolus, les variables de *values* remplacées : la
    règle de son verbe, puis la place des mots que le shell peut encore changer.
    """
    words = _resolve_words(segment, values)
    if words is None:
        return None
    inner = _inner_words([word.text for word in words])
    if inner is None:
        return None  # affectation hors liste blanche, valeur entre guillemets comprise
    offset = len(words) - len(inner)
    if not _prefix_is_readable(words[:offset]):
        return None
    if not inner:
        return False
    if words[offset].opaque:
        return None  # le verbe lui-même vient d'une variable ou d'un joker
    verb = inner[0]
    if verb in _INNER_SHELLS:
        script = _inner_script(verb, segment, inner[1:])
        if script is None or depth >= _MAX_SCRIPT_DEPTH or not _command_is_read_only(script, depth + 1):
            return None
        return True
    args = words[offset + 1 :]
    if not (
        _words_read_only(inner, segment, resolved=True)
        and _opaque_args_allowed(verb, args)
        and _globs_allowed(verb, args)
    ):
        return None
    if verb in _WRITTEN_OUT_VERBS:
        written = words if not values else _resolve_words(segment)
        plain = _inner_words([word.text for word in written or []])
        if not plain or not _arguments_are_written_out(verb, plain[1:], segment):
            return None
    return False


def _without_read_only_scripts(command: str, depth: int) -> str | None:
    """*command* si chacun de ses segments lit, ses ``bash -c`` en lecture seule retirés ; ``None`` sinon.

    *command* arrive sans corps de heredoc ni commentaire. Dans le corps d'une boucle
    ``for`` dont les valeurs se lisent sur la ligne, un segment est jugé une fois par
    valeur (:func:`_loop_values`). Sans ``bash -c``, *command* est rendue telle quelle.
    """
    segments = _split_segments(command)
    loops = _loop_values(segments)
    kept: list[str] = []
    scripts = 0
    for index, (_operators, segment) in enumerate(segments):
        verdicts = [_segment_is_script(segment, values, depth) for values in _loop_bindings(loops, index, segment)]
        if any(verdict is None for verdict in verdicts):
            return None
        scripts += verdicts[0] is True
        kept.append("true" if verdicts[0] else segment)
    if not scripts:
        return command
    if "<<" in command:
        return None  # un script à côté d'un heredoc : la ligne n'est pas recomposée
    return " ; ".join(kept)


# ── Redirections qui n'écrivent aucun fichier ────────────────────────────────
#
# ``2>&1`` duplique un descripteur, ``2>/dev/null`` jette une sortie : ni l'une ni
# l'autre n'écrit de fichier, et une lecture qui les porte reste une lecture. Liste
# blanche de formes exactes, lues hors guillemets : la duplication vers la sortie
# standard ou d'erreur, ou la fermeture (``>&2``, ``2>&1``, ``2>&-``), et la sortie vers
# ``/dev/null`` écrit exactement ainsi (``>``, ``>>``, ``N>``, ``&>``, ``&>>``, avec ou
# sans espace avant la cible). Chaque forme doit finir sur une frontière de mot.
# Tout le reste garde son ``>`` et reste une mutation : un autre périphérique
# (``/dev/stdout``), une autre graphie (``/dev//null``, guillemets), un chemin qui
# commence pareil (``/dev/null/../x``), ``>|``, ``<>``, une cible portée par une
# variable, et la duplication vers un autre descripteur (``>&3`` : un shell qui dure
# a pu l'ouvrir sur un fichier par un ``exec 3>f`` antérieur).

_REDIRECTION_END = r"(?=$|[\s;|&)])"
_FILELESS_TARGET = r"(?:&[12-]|>?[ \t]*/dev/null)"
#: ``2>&1``, ``2>/dev/null`` : un descripteur d'un chiffre en début de mot.
_FILELESS_FD_RE = re.compile(r"[0-9]>" + _FILELESS_TARGET + _REDIRECTION_END)
#: ``>&2``, ``>/dev/null``, ``>>/dev/null``.
_FILELESS_OUT_RE = re.compile(r">" + _FILELESS_TARGET + _REDIRECTION_END)
#: ``&>/dev/null``, ``&>>/dev/null``.
_FILELESS_BOTH_RE = re.compile(r"&>>?[ \t]*/dev/null" + _REDIRECTION_END)


_QUOTED, _ESCAPED, _UNCLOSED = "cité", "échappé", "non fermé"


def _quoted_end(text: str, i: int) -> int:
    """Indice qui suit le texte cité ouvert en *i* (``'`` ou ``"``), ou -1 s'il n'est jamais fermé.

    Dans des guillemets doubles, un antislash échappe le caractère qui le suit, comme pour le shell.
    """
    if text[i] == "'":
        end = text.find("'", i + 1)
        return end + 1 if end >= 0 else -1
    j, n = i + 1, len(text)
    while j < n and text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return j + 1 if j < n else -1


def _opaque_spans(text: str) -> list[tuple[int, int, str]]:
    """Les morceaux de *text* que le shell ne lit pas comme de la syntaxe : ``(début, fin, nature)``.

    :data:`_QUOTED` : un texte entre guillemets fermés. :data:`_ESCAPED` : un caractère
    échappé hors guillemets (``\\"`` n'ouvre aucun texte cité). :data:`_UNCLOSED` : un
    guillemet jamais fermé, qui couvre tout le reste.
    """
    spans: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            spans.append((i, min(i + 2, n), _ESCAPED))
            i += 2
        elif c in "'\"":
            end = _quoted_end(text, i)
            if end < 0:
                spans.append((i, n, _UNCLOSED))
                break
            spans.append((i, end, _QUOTED))
            i = end
        else:
            i += 1
    return spans


def _opens_dollar_quote(text: str) -> bool:
    """Vrai si *text* ouvre, hors guillemets, une chaîne ``$'…'`` (ANSI-C) ou ``$"…"`` (localisée).

    :func:`_opaque_spans` ne modélise ni l'une ni l'autre. Dans ``$'…'``, ``\\'`` est un
    guillemet échappé qui ne ferme rien ; le balayage, lui, ferme la chaîne là et prend la
    suite, que le shell exécute, pour un texte cité. La forme n'est pas modélisée : elle
    est refusée. S'arrêter à la première suffit, puisque le balayage et le shell lisent la
    même chose jusqu'à elle. Entre guillemets simples ou doubles, ou derrière un antislash,
    ``$'`` est un texte (``grep 'a$' f``).
    """
    cursor = 0
    for start, end, _kind in _opaque_spans(text):
        # ``start > cursor`` : le dollar n'appartient pas à la zone précédente (``\\$'a'``).
        if text[start] in "'\"" and start > cursor and text[start - 1] == "$":
            return True
        cursor = end
    return False


# ── Ce que le shell ne lit pas comme du code : commentaires et corps de heredoc ──
#
# Un heredoc n'est reconnu que là où le shell en ouvre un : hors guillemets, non échappé,
# hors commentaire, et pas dans un ``<<<`` (une chaîne passée sur l'entrée, dont les lignes
# suivantes sont des commandes). Son corps commence après la fin de la ligne qui l'ouvre
# et s'arrête à la ligne qui porte son étiquette, écrite exactement : rien devant (des
# tabulations pour ``<<-``), rien derrière. Une ligne en retrait ne ferme rien pour le
# shell ; la prendre pour la fermeture ferait lire comme du code la suite du corps, et ce
# qu'elle cite ouvrirait un autre « heredoc » sur les commandes qui suivent la vraie fin.
# Sans ligne de fermeture, rien n'est retiré.
#
# Un ``<<`` dont l'étiquette ne se lit pas (``<<\EOF``, ``<<'EO'F``, ``<<$X``) ouvre quand
# même un heredoc pour le shell : ce balayage ne sait pas où il finit, la ligne entière
# ne se lit pas.
#
# Un ``#`` se lit de deux façons : commentaire jusqu'à la fin de la ligne (``sh -c``, un
# script), ou caractère ordinaire (zsh interactif, où ``echo a # b ; rm f`` supprime
# ``f``). Chaque lecture a ses commentaires et ses corps ; ce qui dépend de la ligne vaut
# sous les deux.


@dataclass(frozen=True, slots=True)
class _Layout:
    """Les morceaux d'une ligne de commande que le shell ne lit pas comme du code."""

    #: ``(début, fin)`` de chaque commentaire, sans le retour à la ligne qui le termine.
    comments: tuple[tuple[int, int], ...]
    #: ``(début du corps, fin du corps, fin de la ligne de fermeture)`` de chaque heredoc.
    bodies: tuple[tuple[int, int, int], ...]
    #: Un ``<<`` dont l'étiquette ne se lit pas : la ligne entière ne se lit pas.
    unreadable: bool


def _readings(command: str) -> tuple[bool, ...]:
    """Les lectures d'un ``#`` sous lesquelles juger *command* : avec commentaires, puis sans."""
    return (True, False) if "#" in command else (False,)


def _layout(command: str, *, comments: bool) -> _Layout:
    """Commentaires et corps de heredoc de *command*, lue avec ou sans commentaires."""
    comment_spans: list[tuple[int, int]] = []
    bodies: list[tuple[int, int, int]] = []
    pending: list[tuple[str, bool]] = []
    unreadable = False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
        elif c in "'\"":
            end = _quoted_end(command, i)
            i = n if end < 0 else end
        elif comments and c == "#" and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
            end = command.find("\n", i)
            end = n if end < 0 else end
            comment_spans.append((i, end))
            i = end
        elif command.startswith("<<<", i):
            i += 3
        elif command.startswith("<<", i):
            opening = _HEREDOC_OPEN_RE.match(command, i)
            if opening is None:
                unreadable = True
                i += 2
            else:
                pending.append((opening.group(2), opening.group(0).startswith("<<-")))
                i = opening.end()
        elif c == "\n" and pending:
            i += 1
            for tag, tabbed in pending:
                indent = r"\t*" if tabbed else ""
                closing = re.compile(rf"^{indent}{re.escape(tag)}$", re.MULTILINE).search(command, i)
                if closing is None:
                    break  # jamais fermé : la suite reste du code, donc inspectée
                bodies.append((i, closing.start(), closing.end()))
                i = closing.end()
            pending.clear()
        else:
            i += 1
    return _Layout(tuple(comment_spans), tuple(bodies), unreadable)


def _without_spans(text: str, spans: list[tuple[int, int]]) -> str:
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        out.append(text[cursor:start])
        cursor = max(cursor, end)
    out.append(text[cursor:])
    return "".join(out)


def _code_and_bodies(command: str, *, comments: bool) -> tuple[str, list[str]] | None:
    """Le code de *command* sans ses commentaires ni ses corps de heredoc, et ces corps ; ``None`` s'il ne se lit pas."""
    layout = _layout(command, comments=comments)
    if layout.unreadable:
        return None
    removed = [*layout.comments, *((start, closed) for start, _end, closed in layout.bodies)]
    return _without_spans(command, removed), [command[start:end] for start, end, _closed in layout.bodies]


def strip_heredoc_bodies(command: str) -> str:
    """*command* sans les corps de heredoc que le shell ne lira comme du code sous aucune lecture.

    Pour ce qui inspecte la ligne (détection destructive, secrets, garde d'autorité) :
    un corps est une donnée, mais seulement s'il en est une sous les deux lectures d'un
    ``#``. Si la ligne ne se lit pas, rien n'est retiré : tout reste inspecté.
    """
    if "<<" not in command:
        return command
    layouts = [_layout(command, comments=comments) for comments in _readings(command)]
    if any(layout.unreadable for layout in layouts):
        return command
    spans = [(start, closed) for start, _end, closed in layouts[0].bodies]
    for other in layouts[1:]:
        others = [(start, closed) for start, _end, closed in other.bodies]
        spans = [
            (max(start, other_start), min(end, other_end))
            for start, end in spans
            for other_start, other_end in others
            if max(start, other_start) < min(end, other_end)
        ]
    return _without_spans(command, spans)


def executed_surfaces(command: str) -> tuple[str, ...]:
    """Ce que le shell exécutera de *command*, guillemets lus comme lui, sous chaque lecture d'un ``#``.

    Pour la détection destructive, à côté de la surface historique
    (:func:`.tool_facts.command_surface`) qui lit les guillemets par un motif : un
    guillemet échappé hors guillemets n'ouvre aucun texte cité, et un commentaire n'en
    ouvre pas non plus. Là où la ligne ne se lit pas — étiquette de heredoc illisible,
    guillemets ``$'…'`` que ce balayage ne décode pas — aucun texte cité n'est effacé.
    """
    surfaces: list[str] = []
    for comments in _readings(command):
        read = _code_and_bodies(command, comments=comments)
        if read is None:
            surfaces.append(command)
        elif _opens_dollar_quote(read[0]):
            surfaces.append(read[0])
        else:
            surfaces.append(_read_surface(read[0]))
            surfaces.append(_unquoted(read[0], prose=False))
    return tuple(dict.fromkeys(surfaces))


def unquoted_text(command: str) -> str:
    """*command* mot par mot, guillemets et antislashs retirés comme le shell les retire.

    Pour la détection des lectures de secret, à côté de la ligne brute : un nom de
    fichier coupé par des guillemets (``.e"n"v``) est le nom que le programme reçoit.
    """
    texts = [read[0] for comments in _readings(command) if (read := _code_and_bodies(command, comments=comments))]
    return "\n".join(dict.fromkeys(_unquoted(text, prose=True) for text in texts if not _opens_dollar_quote(text)))


def _unquoted(code: str, *, prose: bool) -> str:
    """*code* segment par segment, chaque mot tel que le programme le reçoit.

    Un verbe ou un drapeau cité (``'rm' "-rf"``), ou coupé par des guillemets ou un
    antislash, est le mot que le shell lance. Sans *prose*, un mot qui porte un blanc —
    un message, une description, un motif de recherche — est une donnée : il est effacé,
    comme sur les autres surfaces. Un segment qui ne se lit pas est rendu tel quel.
    """
    parts: list[str] = []
    for _operators, segment in _split_segments(code):
        words = _resolve_words(segment, strict=False)
        if words is None:
            parts.append(segment)
        else:
            parts.append(
                " ".join(
                    word.text if prose or not any(char.isspace() for char in word.text) else " " for word in words
                )
            )
    return " ; ".join(parts)


def _without_fileless_redirections(command: str) -> str:
    """*command* sans ses redirections de la liste blanche, chacune remplacée par une espace.

    Le texte entre guillemets et un caractère échappé sont recopiés tels quels : une
    redirection citée est une donnée. Un chiffre n'est un descripteur qu'en début de mot
    (``cat fichier2>/dev/null`` lit ``fichier2``).
    """
    if ">" not in command:
        return command
    out: list[str] = []
    spans = iter(_opaque_spans(command))
    span = next(spans, None)
    i, n = 0, len(command)
    while i < n:
        if span is not None and i == span[0]:
            out.append(command[i : span[1]])
            i = span[1]
            span = next(spans, None)
            continue
        c = command[i]
        previous = command[i - 1] if i else " "
        match = None
        if c.isdigit() and (previous.isspace() or previous in ";|&("):
            match = _FILELESS_FD_RE.match(command, i)
        elif c == "&" and previous not in "&<>":
            match = _FILELESS_BOTH_RE.match(command, i)
        elif c == ">" and previous not in "&<>":
            match = _FILELESS_OUT_RE.match(command, i)
        if match is not None:
            out.append(" ")
            i = match.end()
            continue
        out.append(c)
        i += 1
    return "".join(out)


def is_read_only_command(command: str) -> bool:
    """Whether *command* — a Bash-shaped call's full command line — only reads.

    Defect 1 of the 2026-09-12 session-budget incident (issue #463): every
    Bash call used to be classified :attr:`MutationClass.MUTATION_CONTROLLED`
    the instant it carried a command string at all, so ``cat``, ``grep``,
    ``find`` or ``git status`` counted as a write for
    ``per_session.max_writes`` exactly like ``rm`` or ``git commit`` would —
    a session doing nothing but reading could exhaust a write budget meant
    to bound actual mutation.

    Conservative by construction, in every direction: an unrecognised
    leading verb, an ambiguous ``git``/``gh`` subcommand, any output
    redirection to a file or ``tee`` anywhere in the line, or an unparseable
    segment all fall back to "not read-only" — the pre-existing (safe)
    behaviour. Only a command every one of whose ``&&``/``;``/``|``/``||``
    segments is a *known* read verb, with no redirection to a file anywhere
    in the line, is read-only. Quoted text is data, so a literal ``>`` or
    ``rm`` inside a commit message never flips the verdict — but each word is
    judged with its quotes removed, the way the program receives it.

    Two things are read on this path only, never for the destructive check:
    a redirection that writes no file (``2>&1``, ``2>/dev/null`` —
    :func:`_without_fileless_redirections`) is dropped, and quotes are read
    the way the shell reads them (:func:`_read_surface`), so a double-quoted
    string carrying a backslash (``grep "a\\|b" f``) is data like any other.

    A wrapped read is still a read: a transparent wrapper is stripped before
    the verb is looked up (:func:`_inner_words` — ``rtk git status``,
    ``timeout 30 ls``), and a ``bash -c '<script>'`` is read-only when every
    segment of its script is (:func:`_without_read_only_scripts`).

    What this classifier cannot read is never a read: ANSI-C quoting
    (``$'…'``) or a localized string (``$"…"``) outside quotes
    (:func:`_opens_dollar_quote`), a command substitution, a bare
    parenthesis; a variable assignment in front of a command, directly or
    through ``env``, unless the variable is one of a short list of inert ones
    (:func:`_assignment_is_inert`); a word the shell can still change where
    it could become an option that writes (:func:`_opaque_args_allowed`,
    :func:`_arguments_are_written_out`).
    """
    return _command_is_read_only(command, 0)


def _command_is_read_only(command: str, depth: int) -> bool:
    """:func:`is_read_only_command` pour *command*, script de niveau *depth* (0 : la ligne elle-même)."""
    if _opens_dollar_quote(command):
        return False
    # La commande ne lit que si elle lit sous chaque lecture d'un ``#`` (:func:`_readings`).
    for comments in _readings(command):
        read = _code_and_bodies(command, comments=comments)
        if read is None or not _reading_is_read_only(*read, depth):
            return False
    return True


def _reading_is_read_only(command: str, bodies: list[str], depth: int) -> bool:
    """:func:`_command_is_read_only` pour une lecture de la ligne : son code, et les corps de ses heredocs."""
    # Le corps d'un heredoc est une donnée, sauf ce que le shell y exécute ou y affecte en le développant.
    if any(_ACTIVE_EXPANSION_RE.search(body) for body in bodies):
        return False
    # Lue telle qu'écrite (plus haut) puis sans ses corps de heredoc : une apostrophe dans
    # un corps décale le balayage de la première lecture, la seconde ne la voit pas.
    if not command.strip() or _opens_dollar_quote(command):
        return False
    command = _without_fileless_redirections(command)
    reduced = _without_read_only_scripts(command, depth)
    if reduced is None:
        return False
    # La passe sur les mots résolus fait foi ; la surface, relue ensuite, ne peut que refuser davantage.
    surface = _read_surface(reduced)
    if _WRITE_REDIRECTION_RE.search(surface):
        return False
    # ``&`` seul (arrière-plan) sépare aussi deux commandes : ``ls & rm f``.
    # ``2>&1`` et ``&>/dev/null`` ont été retirés plus haut ; toute autre redirection est refusée.
    segments = [seg.strip() for seg in re.split(r"&&|\|\||;|\||&|\n", surface)]
    segments = [seg for seg in segments if seg]
    return bool(segments) and all(_is_read_only_segment(seg) for seg in segments)


def _read_surface(command: str) -> str:
    """La surface du classement lecture / mutation : les guillemets y sont lus comme le shell les lit.

    :data:`_QUOTED_RE` exige deux antislashs pour un échappement : ``"a\\|b"`` lui échappe
    et reste sur la surface, où son ``|`` coupe la commande. Elle prend aussi un guillemet
    échappé hors guillemets pour l'ouverture d'un texte cité. :func:`_opaque_spans` lit les
    deux comme le shell. Cette surface ne sert qu'à :func:`_command_is_read_only` : la
    détection destructive garde :func:`command_surface` et ce qu'elle inspecte aujourd'hui.
    *command* arrive sans corps de heredoc (:func:`_code_and_bodies`) : rien n'en est retiré ici.
    """
    return _blank_quoted(command, [(start, end) for start, end, kind in _opaque_spans(command) if kind == _QUOTED])


def _blank_quoted(surface: str, spans: list[tuple[int, int]]) -> str:
    """*surface* sans ses textes cités (*spans*), sauf là où un shell va les exécuter."""
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        preceding = surface[cursor:start]
        out.append(preceding)
        # Keep the quotes as a word boundary so ``rm -rf`` cannot be spliced
        # together out of two neighbouring fragments.
        out.append(surface[start:end] if _EVAL_INTRODUCER.search(preceding.rstrip()) else " ")
        cursor = end
    out.append(surface[cursor:])
    return "".join(out)
