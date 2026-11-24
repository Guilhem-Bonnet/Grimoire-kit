"""Les deux dialectes des hooks GitHub Copilot, tels qu'observés (épique #1280, P1-0).

VS Code fait tourner deux harness Copilot : « Copilot » (celui de la CLI, la
cible durable) et « Local » (déprécié, retiré en février 2027). Leurs hooks ne
reçoivent pas la même charge utile :

- harness Copilot, événement en camelCase (``preToolUse``…) : clés camelCase
  (``sessionId``, ``toolName``, ``toolArgs``…). Le journal de session de l'hôte
  montre en outre un ``preToolUse`` qui porte une **liste** ``toolCalls``
  (``id``, ``name``, ``args`` en chaîne JSON) au lieu d'un couple
  ``toolName``/``toolArgs`` (fixtures ``tests/fixtures/hosts/copilot/copilot-harness/``) ;
- harness Local, et harness Copilot configuré en PascalCase (``PreToolUse``) :
  clés snake_case (``session_id``, ``hook_event_name``, ``tool_name``…). La
  documentation GitHub dit que la casse suit celle du nom d'événement
  configuré : une charge snake_case ne distingue donc pas les deux harness.

Ce module ne décide rien : il lit. La détection rend ce que la forme permet
d'affirmer, et rien de plus (camelCase ⇒ harness Copilot ; snake_case ⇒
indéterminé). Module feuille, bibliothèque standard seulement : il est importé
à chaque appel de hook.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NamedTuple

from grimoire.hosts.events import HookEvent

__all__ = [
    "EVENT_ALIASES",
    "STOP_BLOCK_LIMIT",
    "PayloadDialect",
    "ToolCall",
    "claude_code_payload",
    "copilot_harness_payload",
    "detect_dialect",
    "event_from_alias",
    "inspect_tool_calls",
    "parse_tool_args",
    "single_tool_call",
    "tool_calls",
]


class PayloadDialect(StrEnum):
    """Ce que la forme d'une charge utile Copilot dit du harness qui l'a envoyée."""

    #: Clés camelCase : harness Copilot (CLI), hook déclaré en camelCase.
    #: Le harness Local n'envoie jamais de camelCase (référence des hooks Local de VS Code).
    COPILOT = "copilot"
    #: Clés snake_case : harness Local, ou harness Copilot dont le hook est
    #: déclaré en PascalCase. La forme seule ne les départage pas.
    SNAKE = "snake"
    #: Ni l'une ni l'autre, ou les deux à la fois : rien n'est affirmé.
    UNKNOWN = "unknown"


#: Clés propres au dialecte camelCase (vues dans le journal de session de l'hôte).
_CAMEL_KEYS = frozenset(
    {
        "sessionId",
        "toolName",
        "toolArgs",
        "toolCalls",
        "toolResult",
        "initialPrompt",
        "transcriptPath",
        "customInstructions",
        "stopHookActive",
        "hookEventName",
    }
)

#: Clés propres au dialecte snake_case (documentées pour le harness Local).
_SNAKE_KEYS = frozenset(
    {
        "session_id",
        "hook_event_name",
        "tool_name",
        "tool_input",
        "tool_use_id",
        "tool_response",
        "transcript_path",
        # Pas `stop_hook_active` : la référence GitHub l'écrit en snake_case dans
        # l'entrée camelCase d'`agentStop` aussi, il ne dit rien du dialecte.
        "initial_prompt",
        "custom_instructions",
    }
)


def detect_dialect(payload: Mapping[str, Any]) -> PayloadDialect:
    """Le dialecte de *payload*, d'après ses seules clés de premier niveau."""
    keys = set(payload)
    camel = bool(keys & _CAMEL_KEYS)
    snake = bool(keys & _SNAKE_KEYS)
    if camel and not snake:
        return PayloadDialect.COPILOT
    if snake and not camel:
        return PayloadDialect.SNAKE
    return PayloadDialect.UNKNOWN


#: Agent cloud Copilot (#1280, P3) : aucune marque dans la charge. La référence des hooks
#: (https://docs.github.com/en/copilot/reference/hooks-reference, lue le 2026-11-23)
#: décrit la même entrée pour la CLI et l'agent cloud ; seuls `agentId`/`agentType`/
#: `parentAgentId` s'ajoutent, et pour un sous-agent, quelle que soit la surface. Le
#: bac à sable cloud se reconnaît à son environnement (`COPILOT_AGENT_PROMPT`), pas à la
#: charge. La garde ne rend donc jamais `deny` à la place de `ask` d'après la charge :
#: ce serait deviner. Et c'est inutile là où ça compterait — la même référence dit que
#: l'hôte traite `ask` comme `deny` sous l'agent cloud « because no user is available ».

#: Clés que seul Claude Code envoie à ses hooks : ``permission_mode`` est un champ
#: commun de toutes ses entrées d'outil (https://code.claude.com/docs/en/hooks,
#: « Common input fields »), ``effort``/``prompt_id``/``scratchpad_dir`` aussi.
#: Aucune n'apparaît dans les entrées documentées par GitHub (harness Copilot,
#: camelCase comme PascalCase) ni dans celles du harness Local.
_CLAUDE_CODE_KEYS = frozenset({"permission_mode", "effort", "prompt_id", "scratchpad_dir"})


def claude_code_payload(payload: Mapping[str, Any]) -> bool:
    """*payload* porte-t-il une clé que seul Claude Code envoie ?"""
    return bool(_CLAUDE_CODE_KEYS & set(payload))


def copilot_harness_payload(payload: Mapping[str, Any]) -> bool:
    """*payload* vient-il d'un harness Copilot, d'après une marque sourcée de sa forme ?

    Sert quand la commande du hook dit ``--host claude`` : Copilot exécute aussi
    les hooks de ``.claude/settings.json`` (référence GitHub : « Cross-tool
    ``.claude/settings.json`` and ``.claude/settings.local.json`` files in the
    repository are also read »). Deux marques :

    - le dialecte camelCase (:func:`detect_dialect`), que Claude Code n'envoie jamais ;
    - une charge snake_case qui porte ``timestamp`` : la référence GitHub le donne
      dans l'entrée PascalCase (« ISO 8601 string ») et l'entrée de Claude Code ne
      l'a pas (« Common input fields »). Une charge qui porte aussi une clé propre
      à Claude Code (:func:`claude_code_payload`) n'est jamais attribuée à Copilot.

    Une charge snake_case sans l'une ni l'autre reste indécidable : ``False``.
    """
    if claude_code_payload(payload):
        return False
    dialect = detect_dialect(payload)
    if dialect is PayloadDialect.COPILOT:
        return True
    return dialect is PayloadDialect.SNAKE and "timestamp" in payload


#: Refus consécutifs d'un ``agentStop`` que le harness Copilot honore : « After 8
#: consecutive ``block`` continuations, the CLI overrides the hook and ends the
#: turn anyway » (https://docs.github.com/en/copilot/reference/hooks-reference).
#: Le harness Local ne documente aucune borne. Le kit ne compte jamais au-delà :
#: la chaîne de refus du ``Stop`` (``done_gate._STOP_CAPS``) plafonne en dessous
#: ou à cette valeur, et le rendu Copilot n'émet pas un refus de plus.
STOP_BLOCK_LIMIT = 8


#: Noms d'événements camelCase de Copilot que l'orthographe Claude Code ne
#: retrouve pas par simple compactage (``preToolUse`` → ``pretooluse`` suffit,
#: ``userPromptSubmitted`` non).
EVENT_ALIASES: dict[str, HookEvent] = {
    "userpromptsubmitted": HookEvent.USER_PROMPT_SUBMIT,
    "agentstop": HookEvent.STOP,
}


def event_from_alias(name: str) -> HookEvent | None:
    """L'événement neutre d'un nom propre à Copilot, ou ``None``."""
    key = name.strip().replace("-", "").replace("_", "").replace(" ", "").lower()
    return EVENT_ALIASES.get(key)


def parse_tool_args(raw: Any) -> dict[str, Any]:
    """Arguments d'un outil Copilot : objet, ou chaîne JSON d'un objet ; ``{}`` sinon."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def tool_calls(payload: Mapping[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Tous les appels d'outil qu'une charge camelCase annonce, dans l'ordre.

    ``toolCalls`` (liste) prime ; à défaut, le couple ``toolName``/``toolArgs``.
    Une entrée sans nom est ignorée : elle ne désigne aucun outil.
    """
    raw_calls = payload.get("toolCalls")
    if isinstance(raw_calls, list):
        calls: list[tuple[str, dict[str, Any]]] = []
        for entry in raw_calls:
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("name") or entry.get("toolName") or "")
            if name:
                calls.append((name, parse_tool_args(entry.get("args", entry.get("toolArgs")))))
        return tuple(calls)
    name = str(payload.get("toolName") or "")
    if name:
        return ((name, parse_tool_args(payload.get("toolArgs"))),)
    return ()


class ToolCall(NamedTuple):
    """Un appel d'outil lu dans une charge camelCase."""

    name: str
    args: dict[str, Any]
    #: ``id`` de l'entrée ``toolCalls`` ; vide pour le couple ``toolName``/``toolArgs``.
    call_id: str = ""


#: Clés d'appel d'outil d'un autre dialecte que le camelCase de Copilot. Une charge
#: qui en porte en même temps que ``toolCalls``/``toolName``/``toolArgs`` dit deux
#: choses différentes de l'appel : la garde la refuse comme ambiguë.
_OTHER_CALL_KEYS = frozenset({"tool_name", "tool_input", "tool", "input", "arguments", "toolInput"})


def inspect_tool_calls(payload: Mapping[str, Any]) -> tuple[tuple[ToolCall, ...], str] | None:
    """Les appels d'une charge camelCase et, s'il y en a un, le défaut qui la rend injugeable.

    ``None`` : la charge n'a pas la forme camelCase d'un appel d'outil (ni
    ``toolCalls`` ni ``toolName``/``toolArgs``) — elle relève d'un autre dialecte.
    Sinon ``(appels, problème)`` : ``problème`` est vide quand chaque appel a un
    nom et des arguments lisibles et que la charge ne décrit l'appel qu'une fois.
    La garde refuse une charge à problème : un appel qu'on ne sait pas lire, ou
    qu'on peut lire de deux façons, n'est pas un appel autorisé (échec fermé).
    """
    has_list = "toolCalls" in payload
    has_pair = "toolName" in payload or "toolArgs" in payload
    if not has_list and not has_pair:
        return None
    other = sorted(_OTHER_CALL_KEYS & set(payload))
    if other or (has_list and has_pair):
        named = ", ".join(other or ["toolName/toolArgs"])
        return (), f"charge ambiguë : `toolCalls`/`toolName`/`toolArgs` et {named} décrivent l'appel deux fois"
    if has_list:
        raw_calls = payload["toolCalls"]
        if not isinstance(raw_calls, list) or not raw_calls:
            return (), "`toolCalls` n'est pas une liste d'appels non vide"
        calls: list[ToolCall] = []
        for index, entry in enumerate(raw_calls, start=1):
            if not isinstance(entry, Mapping):
                return tuple(calls), f"appel {index} : pas un objet"
            if "name" in entry and "toolName" in entry:
                return tuple(calls), f"appel {index} : `name` et `toolName` à la fois"
            name = entry.get("name", entry.get("toolName"))
            if not isinstance(name, str) or not name.strip():
                return tuple(calls), f"appel {index} : nom d'outil absent"
            if "args" in entry and "toolArgs" in entry:
                return tuple(calls), f"appel {index} ({name}) : `args` et `toolArgs` à la fois"
            key = "args" if "args" in entry else "toolArgs"
            args, problem = _strict_args(entry.get(key), present=key in entry)
            if problem:
                return tuple(calls), f"appel {index} ({name}) : {problem}"
            call_id = entry.get("id")
            calls.append(ToolCall(name, args, call_id if isinstance(call_id, str) else ""))
        return tuple(calls), ""
    name = payload.get("toolName")
    if not isinstance(name, str) or not name.strip():
        return (), "nom d'outil absent"
    args, problem = _strict_args(payload.get("toolArgs"), present="toolArgs" in payload)
    if problem:
        return (), f"{name} : {problem}"
    return (ToolCall(name, args),), ""


def _strict_args(raw: Any, *, present: bool) -> tuple[dict[str, Any], str]:
    """Arguments lisibles ou la raison pour laquelle ils ne le sont pas.

    Absents : aucun argument. Présents mais ``null`` : illisibles, pas « aucun ».
    """
    if not present:
        return {}, ""
    if raw is None:
        return {}, "arguments illisibles (null)"
    if isinstance(raw, dict):
        return dict(raw), ""
    if isinstance(raw, str):
        if not raw.strip():
            return {}, ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}, "arguments illisibles (JSON invalide)"
        if isinstance(parsed, dict):
            return dict(parsed), ""
        return {}, f"arguments illisibles ({type(parsed).__name__}, pas un objet)"
    return {}, f"arguments illisibles ({type(raw).__name__})"


def single_tool_call(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """L'appel d'outil d'une charge camelCase qui en annonce exactement un, sinon ``None``.

    Un lot de plusieurs appels n'est pas réduit à son premier : juger le premier
    et laisser passer les autres serait pire que de ne rien lire.
    """
    calls = tool_calls(payload)
    return calls[0] if len(calls) == 1 else None
