"""Classify a pending tool call in host-neutral policy terms.

Shared by two decisions — :mod:`.tool_policy` (``PreToolUse``) and
:mod:`.evidence_trace` (``PostToolUse``) — and by neither of the other five.
Needs :mod:`grimoire.policies.schemas` for ``ActionKind``/``MutationClass``
(what :class:`ToolFacts` is made of) but never :mod:`grimoire.policies.engine`
— evaluating a request is :mod:`.tool_policy`'s job, not this module's.
"""

from __future__ import annotations

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
_DESTRUCTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "recursive/forced delete"),
    (r"\bgit\s+push\b.*(--force|-f)\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset"),
    (r"\bgit\s+clean\s+-[a-z]*f", "forced clean"),
    (r"\bgit\s+checkout\s+--\s+\.", "discard all working-tree changes"),
    (r"\bdd\s+if=", "raw device write"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bchmod\s+-R\s+777\b", "world-writable recursive chmod"),
    (r"\bdrop\s+(table|database)\b", "schema drop"),
    (r"\btruncate\s+table\b", "table truncation"),
    (r"\bterraform\s+destroy\b", "infrastructure destruction"),
    (r"\bkubectl\s+delete\b", "cluster resource deletion"),
    (r"\bdocker\s+system\s+prune\b.*-a", "full docker prune"),
)

#: Where a quoted string stops being data and becomes a command again: whatever
#: is handed to these is executed, so it stays under inspection.
_EVAL_INTRODUCER = re.compile(
    r"(?:\b(?:bash|sh|zsh|dash|ksh|ash)\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*c"
    r"|\beval|\bxargs(?:\s+-\S+)*|\bsu\s+-c|\bssh\s+\S+|\btimeout\s+\S+)\s*$"
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
    """
    out = command
    for match in _HEREDOC_OPEN_RE.finditer(command):
        tag = re.escape(match.group(2))
        body = re.compile(
            rf"({re.escape(match.group(0))}).*?^[ \t]*{tag}[ \t]*$",
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


@dataclass(frozen=True, slots=True)
class ToolFacts:
    """What a decision needs to know about a pending tool call."""

    kind: ActionKind
    mutation: MutationClass
    command: str = ""
    targets: tuple[str, ...] = ()
    destructive_reason: str = ""
    secret_target: str = ""

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


def classify_tool(tool_name: str, tool_input: dict[str, Any] | None = None) -> ToolFacts:
    """Describe a pending tool call in host-neutral policy terms."""
    payload = tool_input or {}
    name = tool_name.lower()
    command = _first_str(payload, "command", "cmd", "commandLine", "script")
    targets = _target_paths(payload)
    # Each candidate is matched on its own: joining them first turns the start
    # of a path into the middle of a blob, and ``.env`` stops looking like a
    # path anchor.
    candidates = [c.lower() for c in (command, *targets) if c]

    secret_target = ""
    for candidate in candidates:
        for pattern in secret_patterns():
            match = re.search(pattern, candidate)
            if match:
                # Report the fragment that matched, not the whole command line:
                # the refusal must name the credential, not echo the shell.
                secret_target = match.group(0).strip().strip("'\"=")
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
    elif is_write or (is_execute and command):
        mutation = MutationClass.MUTATION_CONTROLLED
    else:
        mutation = MutationClass.READ_ONLY
    if kind is ActionKind.TOOL_USE and not is_execute and not is_write:
        mutation = MutationClass.READ_ONLY

    return ToolFacts(
        kind=kind,
        mutation=mutation,
        command=command,
        targets=targets,
        destructive_reason=destructive_reason,
        secret_target=secret_target,
    )
