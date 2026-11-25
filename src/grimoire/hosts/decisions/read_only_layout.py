"""Ce que le shell ne lit pas comme du code dans une ligne de commande.

La feuille du bas de la chaîne de classement : guillemets, commentaires et corps de
heredoc. :mod:`.read_only` s'en sert pour juger le code qui reste, :mod:`.tool_facts`
pour ne retirer de ce qu'il inspecte (détection destructive, secrets, garde d'autorité)
que ce qui est une donnée pour le shell. Ne dépend que de ``re``.

Il échoue fermé : là où le balayage ne sait pas ce que le shell fait d'un ``<<``, la ligne
est illisible — pas une lecture, et aucun corps retiré.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``<<TAG`` / ``<<'TAG'`` / ``<<-TAG``, opening a body the command *writes*.
#: Pas ``<<<`` : une chaîne passée sur l'entrée n'a pas de corps, les lignes qui la
#: suivent sont des commandes. L'étiquette doit finir le mot : ``<<'EO'F`` attend ``EOF``,
#: et ce que ce motif ne lit pas en entier n'est pas une ouverture reconnue.
_HEREDOC_OPEN_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(?=$|[\s;|&<>()])")


#: Ce que le shell exécute ou affecte en développant un texte, entre guillemets doubles et
#: dans le corps d'un heredoc comme hors guillemets : substitution de commande (avec ou sans
#: sous-shell), arithmétique à l'ancienne (``$[…]`` affecte et évalue ce que portent les
#: variables), drapeaux d'expansion de zsh (``${(e)X}`` développe la valeur de ``X``),
#: expansion qui affecte (``${X=v}``, ``${X:=v}``, ``${X::=v}`` : la liste blanche des
#: affectations ne se contourne pas par là), opérateur ``@P`` (développe la valeur comme une
#: invite, substitutions comprises), indice ou décalage arithmétique qui affecte
#: (``${a[N=1]}``, ``${x:N=1:1}``, ``++``, ``--`` : il peut poser ``IFS`` comme une autre).
_EXPANDED_NAME = r"[!#]?(?:[A-Za-z_]\w*|[@*]|[0-9]+)"
_ARITHMETIC_WRITE = r"(?:=|\+\+|--)"
_ACTIVE_EXPANSION_RE = re.compile(
    r"`|\$\(|\$\[|\$\{[\s|(]|\$\{[A-Za-z_]\w*(?:\[[^\]]*\])?:{0,2}=|\$\{[^{}]*@P\}"
    rf"|\$\{{{_EXPANDED_NAME}\[[^\]}}]*{_ARITHMETIC_WRITE}"
    rf"|\$\{{{_EXPANDED_NAME}(?:\[[^\]]*\])?:(?![-+=?])[^}}]*{_ARITHMETIC_WRITE}"
)
#: Guillemets ANSI-C ou traduits : hors guillemets, le shell les décode, ce balayage non.
_DOLLAR_QUOTES = ("$'", '$"')


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
# Un heredoc n'est reconnu que là où le shell en ouvre un : dans une commande simple.
# Hors guillemets, non échappé, hors commentaire, pas dans un ``<<<`` (une chaîne passée
# sur l'entrée, dont les lignes suivantes sont des commandes) — et hors de toute
# construction ouverte : expansion ``${…}``, arithmétique ``$((…))`` ou ``$[…]``,
# parenthèses, crochets d'un test ou d'un indice, substitution. Là, ``<<`` est un
# décalage de bits ou un texte, le shell n'ouvre rien et exécute les lignes qui suivent.
# Le balayage ne modélise pas ces constructions : il les compte. Un ``<<`` rencontré à
# l'intérieur de l'une d'elles, collé à un mot (``1<<n``), derrière ``let`` ou ``case``,
# ou dans une ligne dont les constructions ne se referment pas, rend la ligne illisible.
# Illisible veut dire : pas une lecture, et aucun corps retiré pour ce qui inspecte.
#
# Le corps commence après la fin de la ligne qui ouvre le heredoc et s'arrête à la ligne
# qui porte son étiquette, écrite exactement : rien devant (des tabulations pour
# ``<<-``), rien derrière. Une ligne en retrait ne ferme rien pour le shell ; la prendre
# pour la fermeture ferait lire comme du code la suite du corps, et ce qu'elle cite
# ouvrirait un autre « heredoc » sur les commandes qui suivent la vraie fin. Sans ligne
# de fermeture, rien n'est retiré.
#
# Un ``<<`` dont l'étiquette ne se lit pas (``<<\EOF``, ``<<'EO'F``, ``<<$X``) ouvre quand
# même un heredoc pour le shell : ce balayage ne sait pas où il finit, la ligne entière
# ne se lit pas.
#
# Entre guillemets doubles, une substitution est du code et peut porter ses propres
# guillemets : le balayage ne sait alors plus où le texte cité finit. Une seule forme y
# est lue, celle d'un message écrit par heredoc — ``"$(cat <<'EOF'`` en fin de ligne, le
# corps, ``EOF``, puis ``)"`` — et seulement en argument d'une commande connue pour ne
# prendre que du texte (:data:`_TEXT_CONSUMER_RE`) : le message d'un commit, le corps
# d'une PR, un texte affiché. Devant un interpréteur (``bash -c``, ``eval``, ``ssh``) ou
# une commande qui lit le fichier que le corps nomme, le corps est du code ou un nom : il
# reste inspecté. Toute autre substitution qui porte un guillemet, un antislash, une
# parenthèse ou une redirection rend la ligne irrégulière : aucun texte cité n'y est
# effacé pour ce qui inspecte, et s'il s'y trouve un ``<<`` hors texte cité elle est
# illisible.
#
# Un ``#`` se lit de deux façons : commentaire jusqu'à la fin de la ligne (``sh -c``, un
# script), ou caractère ordinaire (zsh interactif, où ``echo a # b ; rm f`` supprime
# ``f``). Chaque lecture a ses commentaires et ses corps ; ce qui dépend de la ligne vaut
# sous les deux.
#
# Enfin, un terminal interactif rejette seule une ligne mal formée : son heredoc n'est
# jamais ouvert, et les lignes suivantes sont lues comme des commandes. Ce balayage ne
# connaît pas la grammaire du shell ; il ne retire donc un corps que d'une ligne de
# commande dont la forme est sûre (:func:`_flat_step`) : une suite de commandes simples,
# sans mot réservé en tête de commande, sans commande vide entre deux opérateurs, sans
# redirection sans cible, sans parenthèse ni substitution hors guillemets, sans rappel
# d'historique. C'est ce qui précède le corps qui compte, ligne d'ouverture comprise : une
# fois le corps lu, la suite ne défait pas le heredoc. Un corps ouvert derrière autre chose
# rend la ligne illisible.


@dataclass(frozen=True, slots=True)
class _Layout:
    """Les morceaux d'une ligne de commande que le shell ne lit pas comme du code."""

    #: ``(début, fin)`` de chaque commentaire, sans le retour à la ligne qui le termine.
    comments: tuple[tuple[int, int], ...]
    #: ``(début du corps, fin du corps, fin de la ligne de fermeture, développé)`` de chaque
    #: heredoc. Développé : l'étiquette est nue, le shell développe le corps avant de le passer.
    bodies: tuple[tuple[int, int, int, bool], ...]
    #: La ligne ne se lit pas : un ``<<`` là où ce balayage ne sait pas ce que le shell en fait.
    unreadable: bool
    #: Le balayage ne sait pas où finissent les textes cités : rien n'en est effacé pour ce qui inspecte.
    irregular: bool


def _readings(command: str) -> tuple[bool, ...]:
    """Les lectures d'un ``#`` sous lesquelles juger *command* : avec commentaires, puis sans."""
    return (True, False) if "#" in command else (False,)


#: Devant ``<<``, ce qui en fait une redirection : un blanc ou un opérateur de commande.
_BEFORE_A_HEREDOC = frozenset(" \t\n;|&")
#: ``!!``, ``!$``, ``!mot``, ``!:1`` : dans un shell interactif, le rappel est remplacé par un
#: morceau d'une ligne passée AVANT que la ligne soit lue — opérateurs compris. Ce que la
#: ligne devient ne se lit pas ici : un rappel n'est admis derrière aucun verbe.
_HISTORY_RE = re.compile(r"![!$*^#?:\w-]")
#: Mots qui, en tête de commande, ouvrent ou ferment une commande composée, ou lisent leur
#: suite autrement qu'une commande simple. Hors de leur place, le shell rejette la ligne.
_RESERVED_WORDS = frozenset(
    {
        "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "select", "in",
        "function", "time", "coproc", "let", "repeat", "foreach", "end", "always", "{", "}", "!", "[[", "]]",
    }
)
_SKELETON_TOKEN_RE = re.compile(
    r"(?P<redirection>[0-9]*(?:<<<|<<-?|>>|>\||<>|[<>]&|&>>?|[<>]))"
    r"|(?P<operator>&&|\|\||\|&|;;|[;|&])"
    r"|(?P<newline>\n)"
    r"|(?P<blank>[ \t]+)"
    r"|(?P<word>[^ \t\n;|&<>]+)"
)


def _flat_step(state: tuple[bool, bool], text: str) -> tuple[bool, bool] | None:
    """L'état de la lecture après *text*, ou ``None`` si ce n'est plus une suite de commandes simples.

    La seule forme dont ce balayage sait qu'un shell ne la rejettera pas : chaque
    commande a un premier mot qui n'est pas réservé, chaque redirection sa cible, aucun
    opérateur n'en suit un autre, et rien n'ouvre de parenthèse ni de substitution.
    *text* est le code, textes cités réduits à un mot. L'état — (aucun mot de la commande
    en cours n'a été lu, une redirection attend sa cible) — se reprend d'une ligne à la
    suivante : la lecture reste linéaire quel que soit le nombre de heredocs.
    """
    starting, target = state
    for token in _SKELETON_TOKEN_RE.finditer(text):
        kind, word = token.lastgroup, token.group()
        if kind == "blank":
            continue
        if kind == "newline":
            if target:
                return None
            continue  # derrière ``|`` ou ``&&`` la commande continue ; sinon une nouvelle commence
        if kind == "operator":
            if target or starting or word == ";;":
                return None  # commande vide, ou opérateur en tête de ligne
            starting = True
            continue
        if kind == "redirection":
            if target:
                return None
            target = True
            continue
        if "(" in word or ")" in word or "`" in word:
            return None
        if target:
            target = False
        elif starting:
            if word in _RESERVED_WORDS:
                return None
            starting = False
    return starting, target
_CLOSERS = {"(": ")", "[": "]", "{": "}"}
#: ``"$(cat <<'EOF'`` en fin de ligne : le message écrit par heredoc.
_QUOTED_HEREDOC_RE = re.compile(r"\"\$\(cat[ \t]+<<(-?)(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2[ \t]*\n")
#: Les commandes dont une option prend un texte qui n'est ni exécuté, ni lu comme le nom
#: d'un fichier : ``git commit|tag|notes`` (message), les sous-commandes de ``gh`` qui
#: prennent un titre, un corps, des notes ou un commentaire, ``echo`` et ``printf`` (texte
#: affiché). ``git`` n'admet devant sa sous-commande que ``-C <dossier>`` : ``-c`` y pose
#: une configuration. La commande ne suffit pas — ``git commit`` lit aussi son message dans
#: un fichier, ``gh release create`` téléverse ses arguments : c'est la place du message
#: qui décide (:func:`_takes_text`).
_TEXT_CONSUMER_RE = re.compile(
    r"[ \t]*(?:rtk[ \t]+(?:proxy[ \t]+)?)?(?:"
    r"(?P<git>git)(?:[ \t]+-C[ \t]+[\w./~-]+)?[ \t]+(?:commit|tag|notes)"
    r"|gh[ \t]+(?:pr|issue|release)[ \t]+(?P<verb>create|edit|comment|review|close|merge|reopen)"
    r"|(?P<prints>echo|printf)"
    r")(?=[ \t])"
)

# Options relevées dans l'aide de git 2.56.0 (``git commit -h``, ``git tag -h``,
# ``git notes add -h``) et de gh 2.103.3 (``gh pr|issue|release <verbe> --help``). Trois
# listes par outil : les options dont la valeur est un texte ; les drapeaux sans valeur ;
# les options dont la valeur n'est ni un fichier ni un message réutilisé. Tout ce qui lit
# un fichier (``-F``, ``--file``, ``--template``, ``--body-file``, ``--notes-file``,
# ``--attach``, ``--recover``, ``--pathspec-from-file``) ou réutilise un message
# (``-C``, ``-c``, ``--fixup``, ``--squash``, ``--trailer``) est absent des trois : une
# option absente rend la place du message illisible, et le corps reste inspecté.
_GIT_TEXT_OPTIONS = frozenset({"-m", "--message"})
_GIT_FLAGS = frozenset(
    {
        "--all", "--quiet", "--signoff", "--no-signoff", "--no-verify", "--verify", "--verbose", "--edit",
        "--no-edit", "--amend", "--allow-empty", "--allow-empty-message", "--only", "--include", "--no-gpg-sign",
        "--no-post-rewrite", "--reset-author", "--dry-run", "--annotate", "--sign", "--no-sign", "--force",
    }
)
#: Drapeaux courts sans valeur, seuls ou agglutinés (``-a``, ``-q``, ``-s``, ``-n``, ``-v``,
#: ``-e``, ``-o``, ``-i``, ``-f``), et le même groupe fini par ``-m`` (``-am``).
_GIT_SHORT_FLAGS_RE = re.compile(r"-[aqsnveoif]+")
_GIT_SHORT_TEXT_RE = re.compile(r"-[aqsnveoif]*m")
_GIT_VALUED_OPTIONS = frozenset({"--author", "--date", "--cleanup", "--ref"})
_GH_TEXT_OPTIONS = frozenset({"--title", "-t", "--body", "-b", "--notes", "-n", "--subject"})
#: ``--comment`` prend un texte pour fermer ou rouvrir ; c'est un drapeau de ``gh pr review``.
_GH_CLOSING_TEXT_OPTIONS = frozenset({"--comment", "-c"})
_GH_FLAGS = frozenset(
    {
        "--draft", "-d", "--dry-run", "--editor", "-e", "--fill", "-f", "--fill-first", "--fill-verbose",
        "--no-maintainer-edit", "--web", "-w", "--create-if-none", "--delete-last", "--edit-last", "--yes",
        "--approve", "--request-changes", "--delete-branch", "--admin", "--auto", "--disable-auto", "--merge",
        "--rebase", "--squash", "--remove-milestone", "--remove-parent", "--remove-type", "--fail-on-no-commits",
        "--generate-notes", "--latest", "--notes-from-tag", "--prerelease", "--verify-tag",
    }
)
_GH_VALUED_OPTIONS = frozenset(
    {
        "--assignee", "--base", "-B", "--head", "-H", "--label", "-l", "--milestone", "--project", "--reviewer",
        "--repo", "-R", "--add-assignee", "--add-label", "--add-project", "--add-reviewer", "--remove-assignee",
        "--remove-label", "--remove-project", "--remove-reviewer", "--author-email", "-A", "--match-head-commit",
        "--blocked-by", "--blocking", "--parent", "--type", "--add-blocked-by", "--add-blocking",
        "--add-sub-issue", "--remove-blocked-by", "--remove-blocking", "--remove-sub-issue", "--duplicate-of",
        "--reason", "--discussion-category", "--notes-start-tag", "--target", "--tag",
    }
)
#: Un argument entier : un texte cité (sans substitution ni antislash) ou un mot écrit en clair.
_ARGUMENT_RE = re.compile(r"'[^']*'|\"(?:[^\"\\`$]|\$(?!\())*\"|[^\s'\"\\`()<>;|&{}]+")


def _takes_text(taker: re.Match[str], rest: str) -> bool:
    """Vrai si le message qui suit *rest* est la valeur d'une option qui prend un texte.

    *rest* est ce qui sépare la commande du message. Il doit se lire en entier, mot par
    mot : drapeaux connus, options connues avec leur valeur, arguments positionnels — et
    finir sur une option de texte. Une option absente des listes, ``--``, un mot que le
    shell peut développer en option, une option citée : la place ne se lit pas, et le
    message n'est pas tenu pour un texte.
    """
    words: list[str] = []
    position, end = 0, len(rest)
    while position < end:
        if rest[position] in " \t":
            position += 1
            continue
        argument = _ARGUMENT_RE.match(rest, position)
        if argument is None or (argument.end() < end and rest[argument.end()] not in " \t"):
            return False
        word = argument.group()
        bare = word[1:-1] if word[0] in "'\"" else word
        # Un mot cité qui commence par un tiret est une option pour le programme ; une
        # variable ou un motif peuvent en devenir une.
        if (bare is not word and bare.startswith("-")) or bare[:1] in {"$", "*", "?", "["} or (bare is word and "$" in word):
            return False
        words.append(word)
        position = argument.end()
    if not words:
        return False
    if taker.group("git"):
        text, flags, valued = _GIT_TEXT_OPTIONS, _GIT_FLAGS, _GIT_VALUED_OPTIONS
        short_flags, short_text = _GIT_SHORT_FLAGS_RE, _GIT_SHORT_TEXT_RE
    else:
        closing = taker.group("verb") in {"close", "reopen"}
        text = _GH_TEXT_OPTIONS | (_GH_CLOSING_TEXT_OPTIONS if closing else frozenset())
        flags, valued, short_flags, short_text = _GH_FLAGS, _GH_VALUED_OPTIONS, None, None

    def is_text(word: str) -> bool:
        return word in text or (short_text is not None and short_text.fullmatch(word) is not None)

    last = words.pop()
    glued = rest[-1] not in " \t"
    if last.endswith("="):
        if not (glued and last[:-1] in text and last.startswith("--")):
            return False  # ``--message=`` collé au message, rien d'autre
    elif not is_text(last) or (glued and last.startswith("--")):
        return False
    index = 0
    while index < len(words):
        word = words[index]
        long_with_value = "=" in word and word.startswith("--") and word.partition("=")[0] in text | valued
        is_flag = word in flags or (short_flags is not None and short_flags.fullmatch(word) is not None)
        if word[0] != "-" or is_flag or long_with_value:
            index += 1  # argument positionnel, drapeau, ou option longue qui porte sa valeur
        elif (is_text(word) or word in valued) and index + 1 < len(words) and words[index + 1][0] != "-":
            index += 2
        else:
            return False
    return True


#: Derrière un texte affiché, ce qui le confie à un autre programme : tube, substitution de processus.
_HANDS_OVER_RE = re.compile(r"[|()`]")
#: Derrière un texte affiché et redirigé vers un fichier, ce qui exécute un fichier : un
#: interpréteur, ``source``, ``eval``, ``exec``, et en tête de commande ``.`` ou ``./chemin``.
_RUNS_A_FILE_RE = re.compile(
    r"(?:^|[\s;&])(?:\S*/)?(?:python[0-9.]*|node|nodejs|deno|bun|perl|ruby|php|bash|sh|zsh|dash|ksh|ash"
    r"|source|eval|exec)(?=[ \t])"
    r"|(?:^|[;&\n])[ \t]*(?:\.(?=[ \t])|\./)"
)
#: Ce qui doit suivre sa ligne de fermeture : la fin de la substitution et du texte cité.
_QUOTED_HEREDOC_END_RE = re.compile(r"\n?[ \t]*\)\"")
#: Une substitution entre guillemets doubles dont le contenu n'est fait que de mots écrits
#: en clair : ni guillemet, ni opérateur, ni redirection. Le texte cité finit bien où le
#: balayage le voit, et la ligne ne peut pas être rejetée pour ce qu'elle porte.
_PLAIN_SUBSTITUTION_RE = re.compile(r"\$\([\w ./:=+%@,*?~-]*\)")


def expanded_bodies(command: str) -> tuple[str, ...]:
    """Les corps de heredoc que le shell développe et qui portent une substitution.

    Étiquette nue : le shell exécute ce que le corps substitue avant de le passer, même
    quand ce corps est le message d'une commande qui ne prend que du texte. Ce qui inspecte
    la ligne les lit donc à part, tels qu'ils sont écrits.
    """
    found: list[str] = []
    for comments in _readings(command):
        for start, end, _closed, expands in _layout(command, comments=comments).bodies:
            if expands and _ACTIVE_EXPANSION_RE.search(command, start, end):
                found.append(command[start:end])
    return tuple(dict.fromkeys(found))


def substitutes(text: str) -> bool:
    """Vrai si *text*, lu entre guillemets doubles, porte une substitution de commande non échappée."""
    j, n = 0, len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
        elif text[j] == "`" or text.startswith("$(", j):
            return True
        else:
            j += 1
    return False


def _quoted_heredoc(command: str, i: int, consumer: str) -> tuple[int, int, int, bool, int] | None:
    """Le message écrit par heredoc ouvert en *i*, ou ``None`` : corps, fermeture, développé, indice suivant.

    *consumer* est le début de la commande simple qui le reçoit. Le message doit y être
    la valeur d'une option qui prend un texte (:func:`_takes_text`) ; derrière ``echo`` ou
    ``printf``, le texte affiché ne doit être confié à aucun autre programme dans la suite
    de la ligne de commande, ni écrit dans un fichier qu'elle exécute. La première ligne
    qui commence par l'étiquette doit être la fermeture exacte : un shell qui fermerait
    sur ``EOF)`` ne finit pas le corps où ce balayage le finit.
    """
    taker = _TEXT_CONSUMER_RE.match(consumer)
    opening = _QUOTED_HEREDOC_RE.match(command, i) if taker is not None else None
    if taker is None or opening is None:
        return None
    if not taker.group("prints") and not _takes_text(taker, consumer[taker.end() :]):
        return None
    indent = r"\t*" if opening.group(1) else ""
    tag = re.escape(opening.group(3))
    start = opening.end()
    candidate = re.compile(rf"^{indent}{tag}(?![A-Za-z0-9_])", re.MULTILINE).search(command, start)
    closing = re.compile(rf"^{indent}{tag}$", re.MULTILINE).search(command, start)
    if closing is None or candidate is None or candidate.start() != closing.start():
        return None
    after = _QUOTED_HEREDOC_END_RE.match(command, closing.end())
    if after is None:
        return None
    if taker.group("prints"):
        if _HANDS_OVER_RE.search(command, after.end()):
            return None  # jusqu'à la fin de la ligne de commande : une ligne continuée porte le tube plus bas
        written = command.find(">", after.end())
        if written >= 0 and _RUNS_A_FILE_RE.search(command, written):
            return None  # écrit dans un fichier, puis un interpréteur : le texte est peut-être ce qu'il exécute
    return start, closing.start(), closing.end(), not opening.group(2), after.end()


def _quoted_text_is_regular(span: str) -> bool:
    """Vrai si le texte entre guillemets doubles *span* finit bien où le balayage le voit finir.

    Faux dès qu'il porte une substitution à l'ancienne, une substitution dont le contenu
    peut porter ses propres guillemets, ou une expansion qui ne se referme pas.
    """
    depth = 0
    j, end = 1, len(span) - 1
    while j < end:
        if span[j] == "\\":
            j += 2  # un caractère échappé : ``\``` et ``\$(`` sont du texte
            continue
        if span[j] == "`" or _HISTORY_RE.match(span, j):
            return False
        if span.startswith("$(", j):
            plain = _PLAIN_SUBSTITUTION_RE.match(span, j)
            if plain is None or plain.end() > end:
                return False
            j = plain.end()
            continue
        if span.startswith(("${", "$["), j):
            depth += 1
            j += 1
        elif depth and span[j] in "{[":
            depth += 1
        elif depth and span[j] in "}]":
            depth -= 1
        j += 1
    return depth == 0


def _layout(command: str, *, comments: bool) -> _Layout:
    """Commentaires et corps de heredoc de *command*, lue avec ou sans commentaires."""
    comment_spans: list[tuple[int, int]] = []
    bodies: list[tuple[int, int, int, bool]] = []
    pending: list[tuple[str, bool, bool]] = []
    stack: list[str] = []  # fermants attendus des constructions ouvertes
    unreadable = irregular = False
    command_start = 0  # début de la commande simple en cours
    skeleton: list[str] = []  # le code depuis la dernière ligne jugée, textes cités réduits à un mot
    flat: tuple[bool, bool] | None = (True, False)  # état de :func:`_flat_step`, ``None`` : forme perdue

    def safe(*, line_ends: bool) -> bool:
        """Vrai si tout ce qui précède le corps, ligne d'ouverture comprise, a une forme sûre."""
        nonlocal flat
        if flat is None:
            return False
        # Ce qui est jugé ne sera pas relu. Au milieu d'une ligne, la coupe tombe juste après
        # le mot qui tient lieu du texte cité : la suite du mot, s'il y en a une, n'est qu'un mot.
        flat = _flat_step(flat, "".join(skeleton) + ("\n" if line_ends else ""))
        skeleton.clear()
        return flat is not None and not (stack or irregular)

    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            skeleton.append("x")
            i += 2
        elif c in "'\"":
            skeleton.append("x")
            idiom = c == '"' and not stack and not pending
            written = _quoted_heredoc(command, i, command[command_start:i]) if idiom else None
            if written is not None:
                unreadable = not safe(line_ends=False) or unreadable
                bodies.append(written[:4])
                i = written[4]
                continue
            end = _quoted_end(command, i)
            if end < 0:
                irregular = True  # guillemet jamais fermé : la suite ne se lit pas
                break
            if pending and "\n" in command[i:end]:
                unreadable = True  # le corps commencerait au milieu d'un texte cité
            if c == '"' and not _quoted_text_is_regular(command[i:end]):
                irregular = True
            i = end
        elif comments and c == "#" and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
            end = command.find("\n", i)
            end = n if end < 0 else end
            comment_spans.append((i, end))
            i = end
        elif command.startswith("<<<", i):
            skeleton.append("<<<")
            i += 3
        elif command.startswith("<<", i):
            opening = _HEREDOC_OPEN_RE.match(command, i)
            if opening is None or stack or (i > 0 and command[i - 1] not in _BEFORE_A_HEREDOC):
                unreadable = True
                i += 2
            else:
                skeleton.append("<< x")  # la redirection et sa cible
                pending.append((opening.group(2), opening.group(0).startswith("<<-"), not opening.group(1)))
                i = opening.end()
        elif c == "$" and command.startswith(_DOLLAR_QUOTES, i):
            irregular = True  # guillemets que ce balayage ne décode pas
            i += 1
        elif (c == "!" and _HISTORY_RE.match(command, i)) or (c == "^" and (i == 0 or command[i - 1] == "\n")):
            irregular = True  # rappel d'historique : la ligne lue par le shell n'est pas celle-ci
            i += 1
        elif c == "$" and (plain := _PLAIN_SUBSTITUTION_RE.match(command, i)) is not None:
            skeleton.append("x")  # une substitution faite de mots écrits en clair : un mot comme un autre
            i = plain.end()
        elif c == "$" and command[i + 1 : i + 2] in _CLOSERS and command[i + 1 : i + 2]:
            stack.append(_CLOSERS[command[i + 1]])
            skeleton.append(command[i : i + 2])
            i += 2
        elif c in "([" or (c == "{" and stack):
            stack.append(_CLOSERS[c])  # une accolade nue n'ouvre rien : groupe de commandes, ``{a,b}``
            skeleton.append(c)
            i += 1
        elif c in ")]" or (c == "}" and stack):
            if stack and stack[-1] == c:
                stack.pop()
            else:
                irregular = True  # motif de ``case``, crochet seul : la structure ne se lit pas
            skeleton.append(c)
            i += 1
        elif c == "`":
            if stack and stack[-1] == "`":
                stack.pop()
            else:
                stack.append("`")
            skeleton.append(c)
            i += 1
        elif c == "\n" and pending:
            # Le corps n'est lu que si le shell ouvre bien le heredoc : rien d'ouvert, et une
            # ligne — avec tout ce qui la précède — qu'il ne rejettera pas.
            unreadable = not safe(line_ends=True) or unreadable
            i += 1
            command_start = i
            for tag, tabbed, expands in pending:
                indent = r"\t*" if tabbed else ""
                closing = re.compile(rf"^{indent}{re.escape(tag)}$", re.MULTILINE).search(command, i)
                if closing is None:
                    break  # jamais fermé : la suite reste du code, donc inspectée
                bodies.append((i, closing.start(), closing.end(), expands))
                i = closing.end()
            pending.clear()
        else:
            skeleton.append(c)
            i += 1
            if c in ";|&\n" and not stack:
                command_start = i
    return _Layout(tuple(comment_spans), tuple(bodies), unreadable, irregular or bool(stack))


def _without_spans(text: str, spans: list[tuple[int, int]]) -> str:
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        out.append(text[cursor:start])
        cursor = max(cursor, end)
    out.append(text[cursor:])
    return "".join(out)


def _code_and_bodies(command: str, *, comments: bool) -> tuple[str, list[str], bool] | None:
    """Le code de *command* sans commentaires ni corps de heredoc, ces corps, et si ses textes cités se lisent.

    ``None`` si la ligne ne se lit pas. Le troisième élément est faux quand le balayage ne
    sait pas où finissent les textes cités : ce qui inspecte la ligne n'en efface alors aucun.
    """
    layout = _layout(command, comments=comments)
    if layout.unreadable:
        return None
    removed = [*layout.comments, *((start, closed) for start, _end, closed, _expands in layout.bodies)]
    bodies = [command[start:end] for start, end, _closed, _expands in layout.bodies]
    return _without_spans(command, removed), bodies, not layout.irregular


def strip_heredoc_bodies(command: str) -> str:
    """*command* sans les corps de heredoc que le shell ne lira comme du code sous aucune lecture.

    Pour ce qui inspecte la ligne (détection destructive, secrets, garde d'autorité) :
    un corps est une donnée, mais seulement s'il en est une sous les deux lectures d'un
    ``#``. Si la ligne ne se lit pas, rien n'est retiré : tout reste inspecté. Un corps
    que le shell développe (étiquette nue) et qui porte une substitution n'est pas retiré
    non plus : ce qu'il exécute en se développant doit rester vu.
    """
    if "<<" not in command:
        return command
    layouts = [_layout(command, comments=comments) for comments in _readings(command)]
    if any(layout.unreadable for layout in layouts):
        return command

    def data(layout: _Layout) -> list[tuple[int, int]]:
        return [
            (start, closed)
            for start, end, closed, expands in layout.bodies
            if not (expands and _ACTIVE_EXPANSION_RE.search(command, start, end))
        ]

    spans = data(layouts[0])
    for other in layouts[1:]:
        others = data(other)
        spans = [
            (max(start, other_start), min(end, other_end))
            for start, end in spans
            for other_start, other_end in others
            if max(start, other_start) < min(end, other_end)
        ]
    return _without_spans(command, spans)
