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
from typing import Any

from grimoire.hosts.events import HookEvent

__all__ = [
    "EVENT_ALIASES",
    "PayloadDialect",
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
        "stop_hook_active",
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


def inspect_tool_calls(payload: Mapping[str, Any]) -> tuple[tuple[tuple[str, dict[str, Any]], ...], str] | None:
    """Les appels d'une charge camelCase et, s'il y en a un, le défaut qui la rend injugeable.

    ``None`` : la charge n'a pas la forme camelCase d'un appel d'outil (ni
    ``toolCalls`` ni ``toolName``/``toolArgs``) — elle relève d'un autre dialecte.
    Sinon ``(appels, problème)`` : ``problème`` est vide quand chaque appel a un
    nom et des arguments lisibles. La garde refuse une charge à problème : un
    appel qu'on ne sait pas lire n'est pas un appel autorisé (échec fermé).
    """
    if "toolCalls" in payload:
        raw_calls = payload["toolCalls"]
        if not isinstance(raw_calls, list) or not raw_calls:
            return (), "`toolCalls` n'est pas une liste d'appels non vide"
        calls: list[tuple[str, dict[str, Any]]] = []
        for index, entry in enumerate(raw_calls):
            if not isinstance(entry, Mapping):
                return tuple(calls), f"appel {index + 1} : pas un objet"
            name = entry.get("name", entry.get("toolName"))
            if not isinstance(name, str) or not name.strip():
                return tuple(calls), f"appel {index + 1} : nom d'outil absent"
            args, problem = _strict_args(entry.get("args", entry.get("toolArgs")))
            if problem:
                return tuple(calls), f"appel {index + 1} ({name}) : {problem}"
            calls.append((name, args))
        return tuple(calls), ""
    if "toolName" in payload or "toolArgs" in payload:
        name = payload.get("toolName")
        if not isinstance(name, str) or not name.strip():
            return (), "nom d'outil absent"
        args, problem = _strict_args(payload.get("toolArgs"))
        if problem:
            return (), f"{name} : {problem}"
        return ((name, args),), ""
    return None


def _strict_args(raw: Any) -> tuple[dict[str, Any], str]:
    """Arguments lisibles ou la raison pour laquelle ils ne le sont pas (absents = aucun argument)."""
    if raw is None:
        return {}, ""
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
