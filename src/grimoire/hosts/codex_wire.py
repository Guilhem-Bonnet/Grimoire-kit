"""Le dialecte des hooks d'OpenAI Codex CLI : charges, outils et verdicts.

Source unique : la page « Hooks » de Codex (https://learn.chatgpt.com/docs/hooks,
lue le 2026-11-21). Codex n'est pas installé sur le poste de développement :
chaque charge acceptée ici est une fixture de protocole tirée de cette page,
pas une capture. Ce qui suit est **documenté**, rien n'est **vérifié**.

Ce que la page dit, et que ce module traduit :

- les noms d'événements suivent la graphie de Claude Code (``PreToolUse``,
  ``Stop``…) et la charge est en snake_case (``hook_event_name``, ``tool_name``,
  ``tool_input``, ``stop_hook_active``) : :func:`grimoire.hosts.runtime.normalize_input`
  la lit déjà ;
- ``PreToolUse`` refuse par ``hookSpecificOutput.permissionDecision: "deny"`` et
  ``permissionDecisionReason``, réécrit l'entrée par ``"allow"`` plus
  ``updatedInput`` (pour ``Bash`` et ``apply_patch``, ``command`` en chaîne), ou
  par le code de sortie 2, la raison sur stderr ;
- ``"ask"`` n'est **pas** pris en charge sur ``PreToolUse`` : « These fail the hook
  run, and the tool call continues ». Rendre un ``ask`` tel quel laisserait donc
  passer l'outil ; il est rendu comme un refus qui dit pourquoi (échec fermé) ;
- ``Stop``/``SubagentStop`` : ``decision: "block"`` plus ``reason`` relance
  l'agent, la raison devenant une nouvelle consigne ; une sortie non JSON y est
  invalide ;
- ``SessionStart`` et ``UserPromptSubmit`` : ``hookSpecificOutput.additionalContext`` ;
- ``PreCompact`` : seuls les champs communs (``systemMessage``) ; ``continue:
  false`` y arrêterait la compaction, le kit ne l'émet jamais.

Outils : la page nomme ``Bash`` et ``apply_patch`` (« ``apply_patch`` also matches
``Edit`` or ``Write`` »). Le correctif d'``apply_patch`` arrive en chaîne dans
``tool_input.command`` ; lu tel quel, la politique y chercherait des commandes
shell. Chaque fichier qu'il touche devient donc un appel ``Write``/``Edit`` jugé à
part (:func:`patch_calls`), et le plus restrictif l'emporte. Une suppression ou
un déplacement est aussi jugé sous sa forme shell (``rm``, ``mv``) : sans cela, un
déplacement ou une suppression par ``apply_patch`` échappait à la règle qui
refuse le même effet par ``Bash``. **Supposé** : la grammaire des en-têtes
(``*** Add File:``, ``*** Update File:``, ``*** Delete File:``, ``*** Move to:``)
n'est pas sur la page des hooks ; c'est celle de l'outil ``apply_patch`` de Codex.
Un correctif dont aucun fichier ne se lit, ou qui porte une ligne de contrôle
``*** `` inconnue, est refusé (échec fermé).

Ce module ne décide rien et n'embarque aucune règle. Bibliothèque standard
seulement : il est importé à chaque appel de hook.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Any

from grimoire.hosts.copilot_wire import ToolCall
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent

__all__ = [
    "APPLY_PATCH",
    "DENY_EXIT",
    "PATCH_INPUT_KEYS",
    "is_pre_tool_refusal",
    "patch_calls",
    "refusal_reason",
    "render",
]

#: Code de sortie d'un refus ``PreToolUse`` : « Exit 2: Blocking. The reason goes
#: to stderr » (page Hooks). Le JSON reste sur stdout, il dit la même chose.
DENY_EXIT = 2

#: Nom de l'outil de modification de fichiers de Codex (page Hooks).
APPLY_PATCH = "apply_patch"

#: Champs qui peuvent porter le texte du correctif. ``command`` est le seul que la
#: page nomme (``updatedInput`` d'``apply_patch``) ; les deux autres sont tolérés.
PATCH_INPUT_KEYS: tuple[str, ...] = ("command", "input", "patch")

#: En-tête de fichier d'un correctif ``apply_patch`` (grammaire supposée, voir le
#: module), lu sur une ligne débarrassée de ses espaces de tête.
_PATCH_HEADER = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$")

#: Lignes de contrôle connues qui ne nomment aucun fichier.
_PATCH_MARKERS = frozenset({"*** Begin Patch", "*** End Patch", "*** End of File"})

#: Préfixe d'une ligne de contrôle : toute autre ligne qui le porte est inconnue.
_CONTROL_PREFIX = "*** "

#: Outils dont l'entrée peut porter un correctif : ``apply_patch`` « also matches
#: ``Edit`` or ``Write`` » (page Hooks) ; ils ne sont lus comme correctif que si
#: ``command`` en est un (:data:`_PATCH_BEGIN`).
_PATCH_ALIASES = frozenset({"edit", "write"})
_PATCH_BEGIN = "*** Begin Patch"

#: Raison d'un refus qui n'en porte pas : ``permissionDecisionReason`` vide ne dit rien.
DEFAULT_DENY_REASON = "[Grimoire] action refusée par la politique du projet."

#: Préfixe d'un ``ask`` rendu comme refus : Codex ne sait pas demander depuis un hook.
_ASK_PREFIX = (
    "[Grimoire] confirmation humaine requise, mais un hook Codex ne peut pas la demander "
    "(« ask » non pris en charge sur PreToolUse) : appel refusé. "
)

#: Événements dont le contexte part en ``hookSpecificOutput.additionalContext``.
_CONTEXT_EVENTS = frozenset({HookEvent.SESSION_START, HookEvent.USER_PROMPT_SUBMIT, HookEvent.POST_TOOL_USE})

#: Noms d'événement tels que Codex les écrit (page Hooks), pour ``hookEventName``.
_WIRE = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
}


def _patch_text(tool_input: Mapping[str, Any]) -> str:
    return next(
        (value for key in PATCH_INPUT_KEYS if isinstance(value := tool_input.get(key), str) and value.strip()),
        "",
    )


def _is_patch_call(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    name = tool_name.strip().lower()
    if name == APPLY_PATCH:
        return True
    command = tool_input.get("command")
    return name in _PATCH_ALIASES and isinstance(command, str) and command.lstrip().startswith(_PATCH_BEGIN)


class _FileOp:
    """Un fichier touché par le correctif : son en-tête, sa destination éventuelle, ses lignes ajoutées."""

    __slots__ = ("added", "kind", "move_to", "path")

    def __init__(self, kind: str, path: str) -> None:
        self.kind = kind
        self.path = path
        self.move_to = ""
        self.added: list[str] = []

    def calls(self) -> list[ToolCall]:
        body = "\n".join(self.added)
        if self.kind == "Add File":
            return [ToolCall("Write", {"file_path": self.path, "content": body})]
        if self.kind == "Delete File":
            # Le même effet que `rm` : jugé aussi sous cette forme, pour que la règle shell s'applique.
            return [
                ToolCall("Write", {"file_path": self.path, "content": ""}),
                ToolCall("Bash", {"command": f"rm -- {shlex.quote(self.path)}"}),
            ]
        if self.move_to:
            # Le contenu écrit arrive à la destination, et la source disparaît : l'effet d'un `mv`.
            return [
                ToolCall("Edit", {"file_path": self.move_to, "new_string": body}),
                ToolCall("Bash", {"command": f"mv -- {shlex.quote(self.path)} {shlex.quote(self.move_to)}"}),
            ]
        return [ToolCall("Edit", {"file_path": self.path, "new_string": body})]


def patch_calls(tool_name: str, tool_input: Mapping[str, Any]) -> tuple[tuple[ToolCall, ...], str] | None:
    """Les effets d'un appel ``apply_patch``, fichier par fichier, et le défaut qui le rend illisible.

    ``None`` : l'appel n'est pas un correctif, il se juge tel quel. Un ``Edit`` ou
    un ``Write`` dont ``command`` commence par ``*** Begin Patch`` en est un (Codex
    les fait correspondre à ``apply_patch``). Sinon ``(appels, problème)`` :

    - un fichier ajouté devient un ``Write`` (``content`` = lignes ajoutées) ;
    - un fichier modifié, un ``Edit`` (``new_string`` = lignes ajoutées) ;
    - un fichier déplacé (``*** Move to:``), un ``Edit`` de la **destination**
      avec les lignes ajoutées, plus le ``mv -- source destination`` équivalent ;
    - un fichier supprimé, un ``Write`` vide plus le ``rm -- chemin`` équivalent.

    ``problème`` est non vide quand le correctif manque, ne nomme aucun fichier,
    porte un ``*** Move to:`` hors d'un ``*** Update File:`` ou une ligne de
    contrôle inconnue : la garde refuse alors l'appel.
    """
    if not _is_patch_call(tool_name, tool_input):
        return None
    text = _patch_text(tool_input)
    if not text:
        return (), "apply_patch sans correctif lisible (ni `command`, ni `input`, ni `patch`)"
    ops: list[_FileOp] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_CONTROL_PREFIX):
            # En-tête reconnu même indenté ; toute autre ligne de contrôle refuse le correctif.
            if stripped in _PATCH_MARKERS:
                continue
            header = _PATCH_HEADER.match(stripped)
            if header is None:
                return (), f"apply_patch avec une ligne de contrôle inconnue : {stripped[:80]!r}"
            kind, path = header.group(1), header.group(2).strip()
            if kind != "Move to":
                ops.append(_FileOp(kind, path))
                continue
            current = ops[-1] if ops else None
            if current is None or current.kind != "Update File" or current.move_to or current.added:
                return (), "apply_patch avec un `*** Move to:` qui ne suit pas un `*** Update File:`"
            current.move_to = path
        elif line.startswith("+") and ops:
            ops[-1].added.append(line[1:])
    if not ops:
        return (), "apply_patch dont aucun en-tête de fichier ne se lit"
    return tuple(call for op in ops for call in op.calls()), ""


def refusal_reason(decision: Decision) -> str:
    """La raison lisible d'un refus ``PreToolUse`` (``ask`` compris, rendu comme refus)."""
    reason = decision.reason or DEFAULT_DENY_REASON
    return f"{_ASK_PREFIX}{reason}" if decision.outcome is Outcome.ASK else reason


def is_pre_tool_refusal(decision: Decision) -> bool:
    """Le verdict ``PreToolUse`` doit-il refuser l'appel ? ``ask`` compris (voir le module)."""
    return decision.outcome in {Outcome.DENY, Outcome.BLOCK, Outcome.ASK}


def render(decision: Decision, hook: HookInput) -> dict[str, Any]:
    """*decision* dans le JSON que Codex lit sur stdout pour cet événement.

    Seuls les champs que la page documente pour l'événement sont émis : un champ
    non pris en charge sur ``PreToolUse`` fait échouer le hook et laisse passer
    l'outil. Un objet vide vaut « pas de verdict ».
    """
    if hook.event is HookEvent.PRE_TOOL_USE:
        specific: dict[str, Any] = {"hookEventName": _WIRE[HookEvent.PRE_TOOL_USE]}
        if is_pre_tool_refusal(decision):
            specific["permissionDecision"] = "deny"
            specific["permissionDecisionReason"] = refusal_reason(decision)
            return {"hookSpecificOutput": specific}
        if decision.updated_input is not None:
            # « Rewrite input: permissionDecision "allow" with updatedInput » ; un
            # `allow` nu n'est pas émis : sans sortie, Codex poursuit déjà.
            specific["permissionDecision"] = "allow"
            specific["updatedInput"] = dict(decision.updated_input)
        if decision.context:
            specific["additionalContext"] = decision.context
        return {"hookSpecificOutput": specific} if len(specific) > 1 else {}

    if hook.event in {HookEvent.STOP, HookEvent.SUBAGENT_STOP}:
        if decision.outcome is Outcome.BLOCK:
            return {"decision": "block", "reason": decision.reason or DEFAULT_DENY_REASON}
        # Aucun `additionalContext` documenté sur Stop : le message système est le seul canal visible.
        return {"systemMessage": decision.context} if decision.context else {}

    if hook.event is HookEvent.PRE_COMPACT:
        if not decision.context:
            return {}
        capsule_error = str(decision.detail.get("capsule_error") or "")
        return {
            "systemMessage": (
                f"[Grimoire] capsule de gouvernance non écrite avant compaction ({capsule_error}) : "
                "le contexte de tâche ne survivra pas à la compaction."
                if capsule_error
                else "[Grimoire] capsule de gouvernance écrite avant compaction."
            )
        }

    if hook.event in _CONTEXT_EVENTS and decision.context:
        return {"hookSpecificOutput": {"hookEventName": _WIRE[hook.event], "additionalContext": decision.context}}
    return {}
