"""Gemini CLI's hook dialect: event names, tool classes and verdicts.

Gemini CLI exposes lifecycle hooks of its own (``.gemini/settings.json``,
``hooks`` key). Its vocabulary differs from the kit's neutral one at exactly
three points, and this module is the only place that knows them:

- **event names** — ``BeforeTool`` is a pending tool call, ``BeforeAgent`` an
  outgoing user message, ``AfterAgent``/``SessionEnd`` the end of a turn or a
  session, ``PreCompress`` a compaction about to happen;
- **tool names** — Gemini writes ``write_file``/``replace``/``read_file``/
  ``run_shell_command`` where the kit's policy engine classifies on its own
  Claude-shaped names (``Write``/``Edit``/``Read``/``Bash``). The table below is
  explicit on purpose; an unknown tool is routed by its payload
  (:func:`route_tool_call`) — a path field is a write on that path, a command
  field is a shell call — and refused when it carries neither, rather than
  being guessed into an apparently harmless read;
- **verdicts** — a refusal is a top-level ``{"decision": "deny", "reason": …}``
  on stdout, and session-start context is ``additionalContext``.

The decisions themselves stay untouched: :mod:`grimoire.hosts.decisions` does
not learn about Gemini, and this module embeds no rule.

Documentation read on geminicli.com/docs on 2026-11-12 (last announced version
v0.64.0). The host binary is not installed on the development machine, so every
payload this module accepts is a protocol fixture, not a capture. **Non
vérifié** : (1) un événement de fin de tour capable de bloquer — ``AfterAgent``
est documenté comme hook, pas comme point de blocage ; (2) le format des
fichiers de session (``transcript_path``).
"""

from __future__ import annotations

from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent

#: Gemini event name -> the neutral event the decisions already answer.
#: A ``BeforeTool`` refusal blocks the tool; ``BeforeAgent`` blocks the message;
#: ``SessionStart`` and ``PreCompress`` are advisory.
EVENT_BY_GEMINI_NAME: dict[str, HookEvent] = {
    "sessionstart": HookEvent.SESSION_START,
    "sessionend": HookEvent.STOP,
    "beforeagent": HookEvent.USER_PROMPT_SUBMIT,
    "afteragent": HookEvent.STOP,
    "beforetool": HookEvent.PRE_TOOL_USE,
    "aftertool": HookEvent.POST_TOOL_USE,
    "precompress": HookEvent.PRE_COMPACT,
}

#: Events Gemini documents as able to refuse the action. On these, an internal
#: error must exit 2 rather than let the action through silently.
BLOCKING_EVENTS: frozenset[HookEvent] = frozenset({HookEvent.PRE_TOOL_USE, HookEvent.USER_PROMPT_SUBMIT})

#: The kit's tool classes, as its policy engine classifies them.
TOOL_CLASS_READ = "Read"
TOOL_CLASS_WRITE = "Write"
TOOL_CLASS_EXECUTE = "Bash"

#: Gemini tool name -> kit tool class. Read and write names are listed so a
#: policy written for Claude Code applies unchanged; shell names map to
#: ``Bash`` because that is the class carrying a command line.
GEMINI_TOOL_CLASSES: dict[str, str] = {
    # Lecture
    "read_file": TOOL_CLASS_READ,
    "read_many_files": TOOL_CLASS_READ,
    "list_directory": TOOL_CLASS_READ,
    "glob": TOOL_CLASS_READ,
    "search_file_content": TOOL_CLASS_READ,
    "grep_search": TOOL_CLASS_READ,
    "google_web_search": TOOL_CLASS_READ,
    "web_fetch": TOOL_CLASS_READ,
    # Écriture de fichier
    "write_file": TOOL_CLASS_WRITE,
    "replace": TOOL_CLASS_WRITE,
    "write_todos": TOOL_CLASS_WRITE,
    "save_memory": TOOL_CLASS_WRITE,
    # Commande shell
    "run_shell_command": TOOL_CLASS_EXECUTE,
}

#: The class of a tool the table does not know: the shell, which can do
#: anything a read or a write can, so refusing conservatively stays possible.
UNKNOWN_TOOL_CLASS = TOOL_CLASS_EXECUTE

#: Fields that carry a pending call's target path. Gemini's documented tools
#: use ``file_path``; a third-party tool may name it ``absolute_path`` or
#: ``target``. All of them denote an intended write, never a read.
PATH_FIELDS: tuple[str, ...] = ("file_path", "absolute_path", "path", "target")

#: Fields that carry a command line (the shell class's own evidence).
COMMAND_FIELDS: tuple[str, ...] = ("command", "cmd", "commandLine", "script")


class UnjudgeableInputError(ValueError):
    """A hook payload the wire layer refuses to translate into a decision.

    Raised only for Gemini, and only where the alternative is silently letting
    an action through: an event the host did not name, a tool event without a
    tool, or a tool with neither a command line nor a target path. The hook
    entry points turn it into exit code 2 on a blocking event (fail closed).
    """


#: Reason carried when a decision refuses without wording one. Gemini requires
#: ``reason`` on a denial, and an empty one would be a refusal nobody can read.
DEFAULT_DENY_REASON = "[Grimoire] action refusée par la politique du projet."

#: Events whose decision context is injected into the conversation.
_CONTEXT_EVENTS = frozenset({HookEvent.SESSION_START, HookEvent.USER_PROMPT_SUBMIT})


def event_from_gemini(name: str) -> HookEvent | None:
    """Translate a Gemini event name (``BeforeTool``) or ``None`` if unknown."""
    key = name.strip().replace("-", "").replace("_", "").replace(" ", "").lower()
    return EVENT_BY_GEMINI_NAME.get(key)


def canonical_tool_name(name: str) -> str:
    """Map a Gemini tool name to the kit's tool class.

    Unknown names map to :data:`UNKNOWN_TOOL_CLASS`; an empty name — which the
    host only sends outside tool events — stays empty. On a tool event, prefer
    :func:`route_tool_call`, which reads the payload and refuses to guess.
    The caller keeps the original name in ``HookInput.raw`` for the trace.
    """
    if not name.strip():
        return ""
    return GEMINI_TOOL_CLASSES.get(name.strip().lower(), UNKNOWN_TOOL_CLASS)


def route_tool_call(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Route a pending ``BeforeTool`` call to a kit class, or refuse to guess.

    A known Gemini tool maps through :data:`GEMINI_TOOL_CLASSES`. For a tool the
    table does not know, the *payload* decides the class rather than the name:

    - a path field (:data:`PATH_FIELDS`) means a file write on that path — the
      path is copied to ``file_path`` so the policy engine and the authority
      guard see the target they already know how to read;
    - a command field (:data:`COMMAND_FIELDS`) means a shell command, as before;
    - neither means the call cannot be judged: :class:`UnjudgeableInputError` is
      raised, and the hook fails closed instead of letting it through.

    An empty ``tool_name`` on a tool event is the same refusal: there is no
    class to route to. The caller decides what an unjudgeable input means
    (exit 2 on a blocking event).
    """
    name = tool_name.strip()
    if not name:
        raise UnjudgeableInputError("tool_name vide ou absent sur un événement d'outil")
    known = GEMINI_TOOL_CLASSES.get(name.lower())
    if known is not None:
        return known, tool_input
    target = next(
        (
            value.strip()
            for key in PATH_FIELDS
            if isinstance(value := tool_input.get(key), str) and value.strip()
        ),
        "",
    )
    if target:
        declared = tool_input.get("file_path")
        file_path = declared if isinstance(declared, str) and declared.strip() else target
        return TOOL_CLASS_WRITE, {**tool_input, "file_path": file_path}
    if any(isinstance(tool_input.get(key), str) and tool_input[key].strip() for key in COMMAND_FIELDS):
        return UNKNOWN_TOOL_CLASS, tool_input
    raise UnjudgeableInputError(
        f"outil inconnu « {name} » sans commande ni chemin : aucune classe ne peut être déduite"
    )


def is_refusal(decision: Decision) -> bool:
    """True when *decision* asks the host to refuse the pending action."""
    return decision.outcome in {Outcome.DENY, Outcome.ASK, Outcome.BLOCK}


def render(decision: Decision, hook: HookInput) -> dict[str, Any]:
    """Render *decision* in the JSON Gemini reads on stdout.

    A refusal on a blocking event becomes ``decision``/``reason``. ``ASK`` is
    rendered as a denial because Gemini exposes no way to ask the human from a
    hook: letting the action through would be the opposite of the kit's
    fail-closed rule. A non-refusal carries context with ``additionalContext``
    on the events that consume it. Everything else stays an empty object, which
    Gemini reads as "no verdict".

    ``AfterAgent``/``SessionEnd`` are mapped to the kit's ``STOP`` decision, but
    their blocking behaviour is **non vérifié** : no ``decision`` is emitted for
    them, so a stop-gate refusal is recorded but not claimed as enforced.
    """
    if hook.event in BLOCKING_EVENTS and is_refusal(decision):
        return {"decision": "deny", "reason": decision.reason or DEFAULT_DENY_REASON}
    if hook.event in _CONTEXT_EVENTS and decision.context:
        return {"additionalContext": decision.context}
    return {}
