"""Classify a pending tool call in host-neutral policy terms.

Shared by two decisions — :mod:`.tool_policy` (``PreToolUse``) and
:mod:`.evidence_trace` (``PostToolUse``) — and by neither of the other five.
Needs :mod:`grimoire.policies.schemas` for ``ActionKind``/``MutationClass``
(what :class:`ToolFacts` is made of) but never :mod:`grimoire.policies.engine`
— evaluating a request is :mod:`.tool_policy`'s job, not this module's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from grimoire.hosts.secrets import secret_patterns
from grimoire.policies.schemas import ActionKind, MutationClass

# ── Tool classification ──────────────────────────────────────────────────────
#
# Tool names are host vocabulary: Claude Code says ``Bash``/``Edit``/``Write``,
# VS Code says ``run_in_terminal``/``create_file``/``replace_string_in_file``,
# an MCP tool says whatever its server called it. Matching on substrings of the
# lowercased name covers all three without a per-host table to keep in sync.

_EXECUTE_MARKERS = ("bash", "shell", "terminal", "execute", "run_command", "runcommand", "process")
_WRITE_MARKERS = (
    "write",
    "edit",
    "create_file",
    "createfile",
    "replace_string",
    "apply_patch",
    "applypatch",
    "notebook",
)
_READ_MARKERS = ("read", "cat_file", "view", "open_file", "openfile")
_WEB_MARKERS = ("fetch", "websearch", "web_search", "browser", "navigate", "http")

#: Commands whose blast radius survives the session. Matched case-insensitively
#: on the command string; each is a shape that destroys work rather than a
#: specific tool.
#:
#: The force-push, hard-reset and discard-all entries anchor on ``(?=\s|$)``
#: rather than ``\b``: a trailing ``\b`` only asserts a word/non-word
#: transition, and ``-`` and ``.`` both count as non-word, so it treats a
#: hyphen or dot glued onto the flag as if it ended the shell token. That let
#: a branch name like ``docs/rejeu-lot-f-2026-09-17`` (containing ``-f-``)
#: read as ``git push -f``, and would equally let ``git checkout -- .gitignore``
#: read as discarding the whole tree. ``(?=\s|$)`` requires an actual token
#: boundary — whitespace or end of string — instead.
_DESTRUCTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "recursive/forced delete"),
    (
        r"\bgit\s+push\b.*(?:^|\s)"
        r"(--force|--force-with-lease(?:=\S+)?|--force-if-includes|-[a-z]*f[a-z]*)"
        r"(?=\s|$)",
        "force push",
    ),
    (r"\bgit\s+reset\s+--hard(?=\s|$)", "hard reset"),
    (r"\bgit\s+clean\s+-[a-z]*f", "forced clean"),
    (r"\bgit\s+checkout\s+--\s+\.(?=\s|$)", "discard all working-tree changes"),
    (r"\bdd\s+if=", "raw device write"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bchmod\s+-R\s+777\b", "world-writable recursive chmod"),
    (r"\bdrop\s+(table|database)\b", "schema drop"),
    (r"\btruncate\s+table\b", "table truncation"),
    (r"\bterraform\s+destroy\b", "infrastructure destruction"),
    (r"\bkubectl\s+delete\b", "cluster resource deletion"),
    (r"\bdocker\s+system\s+prune\b.*-a", "full docker prune"),
    # Windows (issue #761) : PowerShell et cmd n'écrivent pas ``rm -rf``. Chaque
    # motif reste dans un segment (``[^;|&\n]``) : un drapeau d'une autre
    # commande de la ligne ne compte pas. PowerShell accepte tout préfixe non
    # ambigu d'un paramètre (``-r``, ``-Rec``…) et ``-Recurse:$true``.
    (
        r"\b(?:remove-item|ri|rm|rmdir|rd|del|erase)\b[^;|&\n]*?\s"
        r"-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|fo(?:r(?:c(?:e)?)?)?)(?=\s|$|:|['\"])",
        "recursive/forced delete (PowerShell)",
    ),
    (r"\|\s*(?:remove-item|ri|del|erase)\b", "piped delete (PowerShell)"),
    (r"\b(?:rd|rmdir)\b[^;|&\n]*?\s/s(?=\s|$|['\"])", "recursive delete (cmd)"),
    (r"\b(?:del|erase)\b[^;|&\n]*?\s/[sq](?=\s|$|['\"])", "forced delete (cmd)"),
    (r"\b(?:format-volume|clear-disk|initialize-disk)\b", "disk format or wipe (PowerShell)"),
)

#: Leading verbs whose blast radius never survives the session — the read
#: half of the destructive/mutation split above. Matched against the first
#: *word* of a command (or of one ``&&``/``;``/``|`` segment of it), after
#: stripping ``VAR=value`` assignments and a leading ``sudo``/``command``/
#: ``time``/``nice``/``ionice``/``env`` — never against the whole line, and
#: never enough on its own: :func:`is_read_only_command` also refuses any
#: output redirection (``>``, ``>>``, ``| tee``), which is what tells
#: ``cat file`` from ``cat >> file`` apart. Deliberately narrow: an
#: unrecognised verb stays classified as a mutation (the pre-existing,
#: safe default), never the reverse.
_READ_ONLY_LEADING_COMMANDS = frozenset(
    {
        "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag",
        "find", "ls", "wc", "diff", "file", "stat", "pwd", "whoami", "printenv",
        "which", "type", "date", "ps", "df", "du", "tree", "od", "hexdump", "xxd",
        "sha256sum", "sha1sum", "md5sum", "echo", "printf", "true", "false",
        "basename", "dirname", "readlink", "realpath", "nproc", "uptime", "id", "uname",
    }
)

#: ``git`` subcommands kept out of this list on purpose because they have a
#: common mutating form (``git branch -d``, ``git remote add``, ``git tag
#: v1``, ``git config user.name ...``): treating them as always read-only
#: would be the exact defect this classifier exists to fix, just moved one
#: layer down. Only genuinely read-only-in-every-form subcommands qualify.
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe", "shortlog"}
)

#: ``find`` primaries that act instead of just reporting (issue #643): a bare
#: ``find`` only prints, but any of these delete, run an arbitrary command, or
#: prompt before doing so, so ``find`` in ``_READ_ONLY_LEADING_COMMANDS`` is
#: only correct once a segment is checked for these too.
_FIND_MUTATING_PRIMARIES = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

#: ``gh <resource> <verb> ...`` — the verb (second or third word) decides,
#: independently of the resource (``pr``, ``issue``, ``repo``, ``run``…):
#: ``gh pr view``, ``gh issue list``, ``gh run view`` never mutate.
_READ_ONLY_GH_VERBS = frozenset({"view", "list", "status", "diff", "show"})

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_LEADING_NEUTRAL_WORDS = frozenset({"sudo", "command", "time", "nice", "ionice", "env"})
#: Output redirection or a ``tee`` in the pipeline: the one shell-level
#: signal that turns an otherwise read-only pipeline into a write, checked
#: against the quote-stripped surface so a literal ``>`` inside a commit
#: message or a grep pattern is never mistaken for one.
_WRITE_REDIRECTION_RE = re.compile(r">>?(?!=)|\btee\b")


def _is_read_only_segment(segment: str) -> bool:
    words = segment.split()
    idx = 0
    while idx < len(words) and (_ENV_ASSIGNMENT_RE.match(words[idx]) or words[idx] in _LEADING_NEUTRAL_WORDS):
        idx += 1
    if idx >= len(words):
        return True  # nothing left but assignments/neutral words: nothing to mutate
    verb = words[idx]
    if verb == "git":
        sub = words[idx + 1] if idx + 1 < len(words) else ""
        return sub in _READ_ONLY_GIT_SUBCOMMANDS
    if verb == "gh":
        tail = words[idx + 1 : idx + 3]
        return any(word in _READ_ONLY_GH_VERBS for word in tail)
    if verb == "find":
        return not any(word in _FIND_MUTATING_PRIMARIES for word in words[idx + 1 :])
    return verb in _READ_ONLY_LEADING_COMMANDS


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
    redirection or ``tee`` anywhere in the line, or an unparseable segment
    all fall back to "not read-only" — the pre-existing (safe) behaviour.
    Only a command every one of whose ``&&``/``;``/``|``/``||`` segments is a
    *known* read verb, with no redirection anywhere in the line, is
    read-only. Quoted text is stripped first (:func:`command_surface`), the
    same pass the destructive-pattern check above already relies on, so a
    literal ``>`` or ``rm`` inside a commit message never flips the verdict.
    """
    if not command.strip():
        return False
    surface = command_surface(command)
    if _WRITE_REDIRECTION_RE.search(surface):
        return False
    segments = [seg.strip() for seg in re.split(r"&&|\|\||;|\|", surface)]
    segments = [seg for seg in segments if seg]
    return bool(segments) and all(_is_read_only_segment(seg) for seg in segments)


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

#: Single- or double-quoted runs, the shape shell arguments take when they carry
#: prose: a commit message, a ``--description``, a log line.
_QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\"")

#: ``<<TAG`` / ``<<'TAG'`` / ``<<-TAG``, opening a body the command *writes*.
_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredoc_bodies(command: str) -> str:
    """Drop every heredoc body, keeping the redirection that opened it.

    A heredoc body is data the command writes to a file. The shell never runs
    it, so nothing inside it can be a destructive action.

    Issue #643: the body starts *after the opening line ends*, not right after
    ``<<TAG``. A redirection can follow ``<<TAG`` on that same line (``cat
    <<EOF > file``), and matching from ``<<TAG`` swallowed it along with the
    body it precedes — turning a classified write into a false read-only.
    """
    out = command
    for match in _HEREDOC_OPEN_RE.finditer(command):
        tag = re.escape(match.group(2))
        body = re.compile(
            rf"({re.escape(match.group(0))}[^\n]*\n).*?^[ \t]*{tag}[ \t]*$",
            re.DOTALL | re.MULTILINE,
        )
        out = body.sub(r"\1", out, count=1)
    return out


def command_surface(command: str) -> str:
    """The part of *command* the shell will execute, with carried data removed.

    Matching the destructive patterns against the whole command line meant the
    policy refused a heredoc that *documented* a dangerous command, and a commit
    message that merely named one — while the same words written through an
    editing tool passed, because those carry no command string at all. Reading
    data as if it were an action was the defect; the asymmetry was the symptom.

    Quoted text is dropped, *except* where a shell is about to run it: what
    follows ``bash -c``, ``eval`` or ``xargs`` is executed and stays inspected.
    """
    surface = _strip_heredoc_bodies(command)
    out: list[str] = []
    cursor = 0
    for quoted in _QUOTED_RE.finditer(surface):
        preceding = surface[cursor:quoted.start()]
        out.append(preceding)
        # Keep the quotes as a word boundary so ``rm -rf`` cannot be spliced
        # together out of two neighbouring fragments.
        out.append(quoted.group(0) if _EVAL_INTRODUCER.search(preceding.rstrip()) else " ")
        cursor = quoted.end()
    out.append(surface[cursor:])
    return "".join(out)


# ── Interpreted code (issue #724, §2) ────────────────────────────────────────
#
# ``python3 - <<'EOF' … shutil.rmtree("/x") … EOF`` used to pass with no
# warning while ``rm -rf /x`` was refused: :func:`_strip_heredoc_bodies`
# treats every heredoc body as data, but a body an *interpreter* reads on its
# stdin is executed. Same for ``python -c "…"``, ``node -e "…"``,
# ``perl -e``, ``ruby -e``, which :func:`command_surface` blanks as quoted
# prose. Those bodies are collected here, inspected against a per-language
# set of destructive calls plus the shell patterns (``os.system("rm -rf …")``),
# and the call is at least *named* as interpreted code when nothing matches.

#: The interpreter that will execute what follows (heredoc on stdin, or a
#: ``-c``/``-e`` body), and the language label it is reported under.
_INTERPRETERS: tuple[tuple[str, str], ...] = (
    (r"python[0-9.]*", "python"),
    (r"node|nodejs|deno|bun", "javascript"),
    (r"perl", "perl"),
    (r"ruby", "ruby"),
    (r"php", "php"),
    (r"bash|sh|zsh|dash|ksh|ash", "shell"),
)
_INTERPRETER_WORD = "|".join(pattern for pattern, _ in _INTERPRETERS)
#: ``<interp> [flags] -`` or ``<interp>`` as the last word before ``<<TAG`` /
#: after ``| `` on the opening line: the heredoc is its stdin.
_STDIN_INTERPRETER_RE = re.compile(
    rf"(?:^|[\s;&|(])(?:\S*/)?(?P<interp>{_INTERPRETER_WORD})(?:\s+-[A-Za-z]+)*(?:\s+-)?\s*$"
)
#: ``<interp> [flags] -c|-e <body>`` — the body is executed.
_INLINE_INTERPRETER_RE = re.compile(
    rf"(?:^|[\s;&|(])(?:\S*/)?(?P<interp>{_INTERPRETER_WORD})(?:\s+-[A-Za-z]+)*\s+-[A-Za-z]*[ce]\s*$"
)

#: What a body must contain to be a destructive action, per language family
#: (``*`` applies to every body: an embedded shell string).
_INTERPRETED_DESTRUCTIVE: tuple[tuple[str, str, str], ...] = (
    ("python", r"\bshutil\s*\.\s*rmtree\s*\(", "recursive delete (shutil.rmtree)"),
    ("python", r"\bos\s*\.\s*(?:remove|unlink|rmdir|removedirs)\s*\(", "file delete (os.remove/unlink/rmdir)"),
    ("python", r"\.\s*(?:unlink|rmdir)\s*\(", "file delete (Path.unlink/rmdir)"),
    ("python", r"\bsend2trash\s*\(", "file delete (send2trash)"),
    (
        "javascript",
        r"(?:\bfs(?:\.promises)?|\))\s*\.\s*(?:rm|rmSync|rmdir|rmdirSync|unlink|unlinkSync)\s*\(",
        "file delete (fs.rm*)",
    ),
    ("javascript", r"\brimraf\b", "recursive delete (rimraf)"),
    ("javascript", r"\bDeno\s*\.\s*remove(?:Sync)?\s*\(", "file delete (Deno.remove)"),
    ("perl", r"\b(?:unlink|rmdir|rmtree|remove_tree)\b", "file delete (unlink/rmtree)"),
    ("ruby", r"\bFileUtils\s*\.\s*(?:rm_rf|rm_r|rm_f|rm|remove_dir|rmtree)\b", "recursive delete (FileUtils.rm_rf)"),
    ("ruby", r"\b(?:File|Dir)\s*\.\s*(?:delete|unlink|rmdir)\b", "file delete (File.delete)"),
    ("php", r"\b(?:unlink|rmdir)\s*\(", "file delete (unlink/rmdir)"),
)


def _label_for(interp: str) -> str:
    for pattern, label in _INTERPRETERS:
        if re.fullmatch(pattern, interp):
            return label
    return interp


def interpreted_bodies(command: str) -> tuple[tuple[str, str], ...]:
    """``(language, body)`` for every piece of *command* an interpreter will run.

    Heredocs whose opening line ends in an interpreter reading stdin
    (``python3 - <<'EOF'``, ``cat <<EOF | node``), and ``-c``/``-e`` bodies
    of the same interpreters. A heredoc redirected to a file (``cat > f
    <<EOF``) is data and is not returned.
    """
    found: list[tuple[str, str]] = []
    for match in _HEREDOC_OPEN_RE.finditer(command):
        line_end = command.find("\n", match.end())
        if line_end < 0:
            continue
        opening = command[:match.start()]
        rest_of_line = command[match.end():line_end]
        tag = re.escape(match.group(2))
        body_re = re.compile(rf"^(.*?)^[ \t]*{tag}[ \t]*$", re.DOTALL | re.MULTILINE)
        body_match = body_re.match(command, line_end + 1)
        if body_match is None:
            continue
        body = body_match.group(1)
        consumer = _STDIN_INTERPRETER_RE.search(opening.rstrip())
        if consumer is None:
            piped = re.search(r"\|\s*(.*)$", rest_of_line)
            consumer = _STDIN_INTERPRETER_RE.search(piped.group(1).rstrip()) if piped else None
        if consumer is not None and body.strip():
            found.append((_label_for(consumer.group("interp")), body))
    surface_source = _strip_heredoc_bodies(command)
    cursor = 0
    for quoted in _QUOTED_RE.finditer(surface_source):
        preceding = surface_source[cursor:quoted.start()]
        cursor = quoted.end()
        inline = _INLINE_INTERPRETER_RE.search(preceding.rstrip())
        inner = quoted.group(0)[1:-1]
        if inline is not None and inner.strip():
            found.append((_label_for(inline.group("interp")), inner))
    return tuple(found)


#: A non-shell body hands a string to a shell through one of these; only then
#: are the shell patterns read inside it — a python string that merely
#: *documents* ``rm -rf`` (a changelog written through a heredoc) is data.
_EXEC_CALL_RE = re.compile(
    r"\b(?:os\.system|os\.popen|subprocess\.\w+|execSync|spawnSync|execFileSync|child_process\.\w+|"
    r"system|exec|spawn|popen|shell_exec|passthru|backtick)\s*\("
)


def interpreted_destructive_reason(bodies: tuple[tuple[str, str], ...]) -> str:
    """The first destructive call found in interpreted *bodies*, or ``""``."""
    for language, body in bodies:
        for lang, pattern, label in _INTERPRETED_DESTRUCTIVE:
            if lang == language and re.search(pattern, body):
                return f"{label} — code {language} interprété"
        if language == "shell" or _EXEC_CALL_RE.search(body):
            for pattern, label in _DESTRUCTIVE_PATTERNS:
                if re.search(pattern, body, flags=re.IGNORECASE):
                    return f"{label} — code {language} interprété"
    return ""


#: Same shape as ``_EVAL_INTRODUCER`` but reaching further: also matches the
#: python interpreters (whose ``-c`` argument is executed as python, not
#: shell — ``_EVAL_INTRODUCER`` deliberately leaves them out, so
#: :func:`command_surface` still blanks a ``python -c "…"`` argument like any
#: other quoted prose). Used only by :func:`extract_c_bodies` below, never by
#: :func:`command_surface` itself.
_C_BODY_INTRODUCER = re.compile(
    r"(?:\b(?:bash|sh|zsh|dash|ksh|ash|python3?)\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*c|\beval)\s*$"
)


def extract_c_bodies(command: str) -> tuple[str, ...]:
    """The argument bodies of every ``-c``/``eval`` call in *command*.

    Not part of :func:`command_surface`: that function keeps classifying a
    ``bash -c "…"`` or ``python -c "…"`` call as a single opaque command for
    the destructive-pattern and read-only checks above, unchanged. This
    exists solely for :mod:`.tool_policy`'s untrusted-memory comparison
    (issue #645 follow-up): a command a planted content spelled out
    unwrapped (``curl … | sh``) still carries the exact same inner text once
    an agent wraps it (``bash -c "curl … | sh"``, ``python -c "…os.system('curl
    … | sh')"``, ``eval "curl … | sh"``) — the comparison needs to see past
    that wrapping, even though nothing else here should.

    Heredoc bodies are dropped first, same as :func:`command_surface`: a
    ``-c``/``eval`` mentioned only inside documentary data must never surface
    here either.
    """
    surface_source = _strip_heredoc_bodies(command)
    bodies: list[str] = []
    cursor = 0
    for quoted in _QUOTED_RE.finditer(surface_source):
        preceding = surface_source[cursor:quoted.start()]
        cursor = quoted.end()
        if _C_BODY_INTRODUCER.search(preceding.rstrip()):
            inner = quoted.group(0)[1:-1]
            if inner.strip():
                bodies.append(inner)
                # One level of nesting: ``python -c "…os.system('curl … | sh')…"``
                # carries the actual shell text one quote layer further in —
                # everything inside a ``-c``/``eval`` argument is executed or
                # evaluated, so a literal string inside it is worth comparing
                # on its own too, not only as part of the whole body.
                for nested in _QUOTED_RE.finditer(inner):
                    nested_inner = nested.group(0)[1:-1]
                    if nested_inner.strip():
                        bodies.append(nested_inner)
    return tuple(bodies)


@dataclass(frozen=True, slots=True)
class ToolFacts:
    """What a decision needs to know about a pending tool call."""

    kind: ActionKind
    mutation: MutationClass
    command: str = ""
    targets: tuple[str, ...] = ()
    destructive_reason: str = ""
    secret_target: str = ""
    #: Languages whose code this call executes from a heredoc or a ``-c``/``-e``
    #: body (``"python"``, ``"javascript, shell"``…), ``""`` when none (#724).
    #: Only the known destructive calls are inspected inside; the policy
    #: names the rest as interpreted code it did not read.
    interpreted_code: str = ""
    #: Governance profile guard (issue tracked in ``fix/profile-downgrade-guard``):
    #: ``None`` when this call does not mutate
    #: ``_grimoire/standard/standard-profile.yaml`` at all. ``""`` when it does,
    #: but the proposed new ``profile:`` value cannot be read with confidence
    #: (a shell mutation — ``sed -i``, redirection, ``cp`` — whose command line
    #: is not a reliable source for the file's *resulting* content). Otherwise
    #: the declared profile id read from a ``Write``/``Edit`` call's own
    #: ``content``/``new_string``, ready for :mod:`.tool_policy` to compare
    #: against the project's current profile — this module has no notion of
    #: "current", only of "proposed".
    standard_profile_write: str | None = None
    #: Même contrat pour ``_grimoire/standard/profile-floor.yaml`` (W2-07) : la
    #: valeur du champ ``floor:`` proposée par un ``Write``/``Edit``, ``""`` pour
    #: une commande shell dont le résultat ne se lit pas, ``None`` sans écriture.
    profile_floor_write: str | None = None

    @property
    def family(self) -> str:
        """Neutral tool family, matching :attr:`HookSpec.matcher` values."""
        if self.kind is ActionKind.FILE_WRITE:
            return "write"
        if self.kind is ActionKind.NETWORK:
            return "network"
        if self.kind is ActionKind.SECRET_ACCESS:
            return "secret"
        if self.command:
            return "execute"
        return "read"


def policy_tool_detail(facts: ToolFacts) -> str:
    """The detail a policy pattern matches beyond the bare tool name.

    ``docs/hosts.md`` documents ``tool_pattern`` values shaped like Claude
    Code's own permission syntax — ``Bash(rm:*)``, ``Bash(git push:*)``,
    ``Write(_grimoire/standard/*)`` — where the parenthesised body is
    matched against *this* string, not against ``tool_name`` (see
    :func:`grimoire.policies.temporal.tool_pattern_matches`, the actual
    comparison). Reuses the exact fields :func:`classify_tool` already
    derives for the destructive-command and secret-target checks above,
    rather than re-deriving a command or target path here: the full shell
    command line when there is one (a ``Bash``-shaped call), else the first
    target file (a ``Write``/``Edit``/``Read``-shaped call), else ``""`` —
    an MCP tool call with no established argument convention yet can only
    match the bare-name form or a parenthesised pattern whose body is
    exactly ``"*"``.
    """
    if facts.command:
        return facts.command
    if facts.targets:
        return facts.targets[0]
    return ""


#: Clés sans effet sur ce que la commande fait, par outil : seules ignorées.
#: Bash natif : ``description``, ``timeout`` ; Copilot ``run_in_terminal`` :
#: ``explanation``, ``goal`` (texte d'affichage, reformulé à chaque appel).
_VOLATILE_KEYS: dict[str, frozenset[str]] = {
    "Bash": frozenset({"description", "timeout"}),
    "run_in_terminal": frozenset({"explanation", "goal"}),
}
_COMMAND_KEYS = ("command", "cmd", "commandLine", "script")


def approval_fingerprint(tool_name: str, tool_input: dict[str, Any], facts: ToolFacts) -> str:
    """Empreinte de l'action pour une règle ``require_approval`` (W1-09).

    Toujours l'entrée canonique complète de l'outil (JSON trié : toutes les
    cibles, tous les arguments, le contenu), de sorte qu'un autre hôte, dépôt,
    répertoire, cible ou contenu redemande. Quand l'appel porte une commande,
    sa valeur y est remplacée par la commande normalisée (blancs hors
    guillemets) ; ``description``/``timeout`` (Bash natif) et
    ``explanation``/``goal`` (Copilot ``run_in_terminal``) sont ignorés, tout
    autre argument compte. Calculée au PreToolUse puis, sans identifiant d'appel,
    recalculée au PostToolUse depuis son ``tool_input`` : un hook qui réécrit
    l'entrée entre les deux (``git status`` -> ``rtk git status``) la change.
    Avec un ``tool_use_id``, le PostToolUse reprend donc l'empreinte retenue au
    PreToolUse (``temporal.remember_pending_approval``) au lieu de la recalculer.
    """
    from grimoire.policies.temporal import action_fingerprint, normalize_command

    payload = dict(tool_input)
    for key in _VOLATILE_KEYS.get(tool_name, ()):
        payload.pop(key, None)
    if facts.command:
        for key in _COMMAND_KEYS:
            if payload.get(key) == facts.command:
                payload[key] = normalize_command(facts.command)
                break
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return action_fingerprint(tool_name, canonical, normalize=False)


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _target_paths(tool_input: dict[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for key in ("file_path", "filePath", "path", "notebook_path", "target_file", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                value = edit.get("file_path") or edit.get("filePath")
                if isinstance(value, str):
                    paths.append(value)
    return tuple(dict.fromkeys(paths))


#: The one file the profile-downgrade guard watches — deliberately just this
#: path, not the rest of ``_grimoire/standard/`` (``policies.yaml``,
#: ``task-board.yaml``, ...), which this guard has no opinion on.
_STANDARD_PROFILE_SUFFIX = "_grimoire/standard/standard-profile.yaml"

#: A YAML ``profile:`` mapping entry, quoted or not — the one field this
#: guard reads. Deliberately a regex, not a YAML parse: the value only ever
#: needs comparing against known profile ids, and a full parse is exactly the
#: "heavy import on the PreToolUse path" this package avoids elsewhere.
_PROFILE_FIELD_RE = re.compile(r'(?m)^[ \t]*profile[ \t]*:[ \t]*["\']?([A-Za-z0-9_-]+)')


#: Le plancher de profil (W2-07) : même garde, fichier voisin, champ ``floor:``.
_PROFILE_FLOOR_SUFFIX = "_grimoire/standard/profile-floor.yaml"
_FLOOR_FIELD_RE = re.compile(r'(?m)^[ \t]*floor[ \t]*:[ \t]*["\']?([A-Za-z0-9_-]+)')


def _targets_standard_profile_file(path: str) -> bool:
    """*path* is (or resolves to) the profile file — a ``Write``/``Edit`` target."""
    return path.replace("\\", "/").rstrip("/").endswith(_STANDARD_PROFILE_SUFFIX)


def _command_mentions_standard_profile_file(command: str) -> bool:
    """The profile file is named anywhere on *command*'s line.

    Looser than :func:`_targets_standard_profile_file` on purpose: a shell
    command names its target as one argument among others (flags, a ``sed``
    expression, a redirection), never as the whole string.
    """
    return _STANDARD_PROFILE_SUFFIX in command.replace("\\", "/")


def _declared_profile(text: str) -> str | None:
    match = _PROFILE_FIELD_RE.search(text)
    return match.group(1) if match else None


def classify_tool(tool_name: str, tool_input: dict[str, Any] | None = None) -> ToolFacts:
    """Describe a pending tool call in host-neutral policy terms."""
    payload = tool_input or {}
    name = tool_name.lower()
    command = _first_str(payload, "command", "cmd", "commandLine", "script")
    targets = _target_paths(payload)
    # Each candidate is matched on its own: joining them first turns the start
    # of a path into the middle of a blob, and ``.env`` stops looking like a
    # path anchor. The command is read *raw* on purpose — ``cat ".env"`` must
    # stay a secret read — except for heredoc bodies the command only writes
    # (``gh issue create --body-file - <<EOF``: data, not a read, #724) ; a
    # body an interpreter executes is read on its own below. Case is kept and
    # the match is case-insensitive instead of lower-casing: the env-file
    # suffix rule relies on it (``.env.CLAUDE_CODE_TMPDIR`` is a jq field).
    interpreted = interpreted_bodies(command) if command else ()
    candidates = [c for c in (_strip_heredoc_bodies(command) if command else "", *targets) if c]
    candidates.extend(body for _, body in interpreted)

    secret_target = ""
    for candidate in candidates:
        for pattern in secret_patterns():
            match = re.search(pattern, candidate, flags=re.IGNORECASE)
            if match:
                # Report the fragment that matched, not the whole command line:
                # the refusal must name the credential, not echo the shell.
                secret_target = match.group(0).strip().strip("'\"=").lower()
                break
        if secret_target:
            break

    destructive_reason = ""
    if command:
        surface = command_surface(command)
        for pattern, label in _DESTRUCTIVE_PATTERNS:
            if re.search(pattern, surface, flags=re.IGNORECASE):
                destructive_reason = label
                break
        if not destructive_reason and interpreted:
            destructive_reason = interpreted_destructive_reason(interpreted)
    interpreted_code = ", ".join(sorted({language for language, _ in interpreted}))

    is_execute = bool(command) or any(marker in name for marker in _EXECUTE_MARKERS)
    is_write = any(marker in name for marker in _WRITE_MARKERS)
    is_web = any(marker in name for marker in _WEB_MARKERS)
    is_read = any(marker in name for marker in _READ_MARKERS)

    if secret_target and (is_read or is_execute or not name):
        kind = ActionKind.SECRET_ACCESS
    elif is_write:
        kind = ActionKind.FILE_WRITE
    elif is_web:
        kind = ActionKind.NETWORK
    elif is_execute:
        kind = ActionKind.TOOL_USE
    else:
        kind = ActionKind.TOOL_USE

    if destructive_reason:
        mutation = MutationClass.DESTRUCTIVE
    elif is_write or (is_execute and command and not is_read_only_command(command)):
        mutation = MutationClass.MUTATION_CONTROLLED
    else:
        mutation = MutationClass.READ_ONLY
    if kind is ActionKind.TOOL_USE and not is_execute and not is_write:
        mutation = MutationClass.READ_ONLY

    # Profile-downgrade guard: narrow on purpose (see ``ToolFacts.standard_profile_write``)
    # — only this one file, only its ``profile:`` field, ``None`` unless this
    # call actually mutates it.
    standard_profile_write: str | None = None
    if is_write and any(_targets_standard_profile_file(t) for t in targets):
        proposed_content = _first_str(payload, "content", "new_string")
        standard_profile_write = _declared_profile(proposed_content) if proposed_content else None
    elif (
        not is_write
        and command
        and mutation is not MutationClass.READ_ONLY
        and _command_mentions_standard_profile_file(command)
    ):
        # A shell mutation's command line is not a reliable source for the
        # file's resulting content (``sed -i``'s expression, redirected
        # ``echo``/heredoc bodies, ``cp`` from elsewhere) — "" marks it as
        # targeted-but-unreadable rather than guessing a direction.
        standard_profile_write = ""

    profile_floor_write: str | None = None
    if is_write and any(t.replace("\\", "/").rstrip("/").endswith(_PROFILE_FLOOR_SUFFIX) for t in targets):
        floor_text = _first_str(payload, "content", "new_string")
        floor_match = _FLOOR_FIELD_RE.search(floor_text) if floor_text else None
        profile_floor_write = floor_match.group(1) if floor_match else ""
    elif (
        not is_write
        and command
        and mutation is not MutationClass.READ_ONLY
        and _PROFILE_FLOOR_SUFFIX in command.replace("\\", "/")
    ):
        profile_floor_write = ""

    return ToolFacts(
        kind=kind,
        mutation=mutation,
        command=command,
        targets=targets,
        destructive_reason=destructive_reason,
        secret_target=secret_target,
        interpreted_code=interpreted_code,
        standard_profile_write=standard_profile_write,
        profile_floor_write=profile_floor_write,
    )
