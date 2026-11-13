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
# corps, ``EOF``, puis ``)"``. Toute autre substitution qui porte un guillemet, un
# antislash, une parenthèse ou une redirection rend la ligne irrégulière : s'il s'y
# trouve un ``<<`` hors texte cité, elle est illisible.
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
    #: ``(début du corps, fin du corps, fin de la ligne de fermeture, développé)`` de chaque
    #: heredoc. Développé : l'étiquette est nue, le shell développe le corps avant de le passer.
    bodies: tuple[tuple[int, int, int, bool], ...]
    #: La ligne ne se lit pas : un ``<<`` là où ce balayage ne sait pas ce que le shell en fait.
    unreadable: bool


def _readings(command: str) -> tuple[bool, ...]:
    """Les lectures d'un ``#`` sous lesquelles juger *command* : avec commentaires, puis sans."""
    return (True, False) if "#" in command else (False,)


#: Devant ``<<``, ce qui en fait une redirection : un blanc ou un opérateur de commande.
_BEFORE_A_HEREDOC = frozenset(" \t\n;|&")
#: Sur la ligne d'un ``<<``, un mot qui lit sa suite autrement qu'une commande simple.
_NOT_A_SIMPLE_COMMAND_RE = re.compile(r"(?:^|[\s;|&])(?:let|case)(?=\s)")
_CLOSERS = {"(": ")", "[": "]", "{": "}"}
#: ``"$(cat <<'EOF'`` en fin de ligne : le message écrit par heredoc.
_QUOTED_HEREDOC_RE = re.compile(r"\"\$\(cat[ \t]+<<(-?)(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2[ \t]*\n")
#: Ce qui doit suivre sa ligne de fermeture : la fin de la substitution et du texte cité.
_QUOTED_HEREDOC_END_RE = re.compile(r"\n?[ \t]*\)\"")
#: Une substitution entre guillemets doubles dont le contenu ne porte ni guillemet, ni
#: antislash, ni parenthèse, ni redirection : le texte cité finit bien où le balayage le voit.
_PLAIN_SUBSTITUTION_RE = re.compile(r"\$\([^'\"\\`()<>]*\)")


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


def _quoted_heredoc(command: str, i: int) -> tuple[int, int, int, bool, int] | None:
    """Le message écrit par heredoc ouvert en *i*, ou ``None`` : corps, fermeture, développé, indice suivant.

    La première ligne qui commence par l'étiquette doit être la fermeture exacte : un
    shell qui fermerait sur ``EOF)`` ne finit pas le corps où ce balayage le finit.
    """
    opening = _QUOTED_HEREDOC_RE.match(command, i)
    if opening is None:
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
        if span[j] == "`":
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
    operators = 0  # ``<<`` rencontrés hors texte cité
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
        elif c in "'\"":
            written = _quoted_heredoc(command, i) if c == '"' and not stack and not pending else None
            if written is not None:
                operators += 1  # une irrégularité ailleurs sur la ligne rend aussi ce corps illisible
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
            i += 3
        elif command.startswith("<<", i):
            operators += 1
            opening = _HEREDOC_OPEN_RE.match(command, i)
            line = command[command.rfind("\n", 0, i) + 1 : i]
            if (
                opening is None
                or stack
                or (i > 0 and command[i - 1] not in _BEFORE_A_HEREDOC)
                or _NOT_A_SIMPLE_COMMAND_RE.search(line)
            ):
                unreadable = True
                i += 2
            else:
                pending.append((opening.group(2), opening.group(0).startswith("<<-"), not opening.group(1)))
                i = opening.end()
        elif c == "$" and command.startswith(_DOLLAR_QUOTES, i):
            irregular = True  # guillemets que ce balayage ne décode pas
            i += 1
        elif c == "$" and command[i + 1 : i + 2] in _CLOSERS and command[i + 1 : i + 2]:
            stack.append(_CLOSERS[command[i + 1]])
            i += 2
        elif c in "([" or (c == "{" and stack):
            stack.append(_CLOSERS[c])  # une accolade nue n'ouvre rien : groupe de commandes, ``{a,b}``
            i += 1
        elif c in ")]" or (c == "}" and stack):
            if stack and stack[-1] == c:
                stack.pop()
            else:
                irregular = True  # motif de ``case``, crochet seul : la structure ne se lit pas
            i += 1
        elif c == "`":
            if stack and stack[-1] == "`":
                stack.pop()
            else:
                stack.append("`")
            i += 1
        elif c == "\n" and pending:
            if stack:
                unreadable = True  # le corps commencerait à l'intérieur d'une construction ouverte
            i += 1
            for tag, tabbed, expands in pending:
                indent = r"\t*" if tabbed else ""
                closing = re.compile(rf"^{indent}{re.escape(tag)}$", re.MULTILINE).search(command, i)
                if closing is None:
                    break  # jamais fermé : la suite reste du code, donc inspectée
                bodies.append((i, closing.start(), closing.end(), expands))
                i = closing.end()
            pending.clear()
        else:
            i += 1
    irregular = irregular or bool(stack)
    return _Layout(tuple(comment_spans), tuple(bodies), unreadable or (irregular and operators > 0))


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
    removed = [*layout.comments, *((start, closed) for start, _end, closed, _expands in layout.bodies)]
    return _without_spans(command, removed), [command[start:end] for start, end, _closed, _expands in layout.bodies]


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
