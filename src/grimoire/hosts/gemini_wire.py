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
  explicit on purpose: an unknown tool is mapped to the shell class, the
  strictest, rather than to an apparently harmless read;
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

    Unknown names — and an empty name, which the host only sends outside tool
    events — map to :data:`UNKNOWN_TOOL_CLASS`. The caller keeps the original
    name in ``HookInput.raw`` for the trace.
    """
    if not name.strip():
        return ""
    return GEMINI_TOOL_CLASSES.get(name.strip().lower(), UNKNOWN_TOOL_CLASS)


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
