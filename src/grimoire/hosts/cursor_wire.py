"""Le dialecte des hooks Cursor : noms d'événements, outils et verdicts.

Cursor lit ses hooks de projet dans ``.cursor/hooks.json`` (https://cursor.com/docs/hooks,
lue le 2026-11-21). Son vocabulaire diffère de celui du kit en quatre points, et ce
module est le seul à les connaître :

- **événements** — camelCase et plus fins que les neutres : ``preToolUse`` (tout
  outil), ``beforeShellExecution`` (une commande shell), ``beforeSubmitPrompt``,
  ``stop``, ``subagentStop``… (``beforeMCPExecution``/``beforeReadFile`` ne sont pas
  câblés : refusés comme événements inconnus). La commande écrite par l'émetteur porte le nom neutre (``--event PreToolUse``) ; le
  nom Cursor de l'appel est relu dans ``hook_event_name`` (champ commun documenté),
  ou déduit de la forme de la charge ;
- **outils** — ``Shell``, ``Read``, ``Write``, ``Grep``, ``Delete``, ``Task``,
  ``MCP:<outil>`` (valeurs de matcher documentées pour ``preToolUse``). La doc ne
  donne que l'entrée de ``Shell`` (``command``, ``working_directory``) : les clés
  de chemin des autres outils ne sont pas documentées, donc toutes les graphies
  plausibles sont lues, et un appel d'écriture sans chemin lisible est refusé
  plutôt que deviné ;
- **verdicts** — à plat : ``permission`` (``allow``/``deny``/``ask``),
  ``user_message``, ``agent_message``. ``ask`` est « accepté par le schéma mais
  non appliqué » sur ``preToolUse`` : il y est rendu en refus (échec fermé).
  ``beforeShellExecution`` l'applique. Une sortie qui
  ne respecte pas le schéma de l'événement bloque l'action : seuls les champs
  documentés pour chaque événement sont écrits ;
- **fin de tour** — ``stop`` ne refuse pas : un ``followup_message`` non vide est
  soumis comme message utilisateur suivant, dans la limite ``loop_limit`` (5 par
  défaut pour un hook Cursor, « per script »). ``loop_count`` compte les relances
  automatiques « for this conversation » : il ne dit pas *qui* a relancé, ni si
  c'est ce tour-ci. Un ``Stop`` n'est donc « continué » (``stop_hook_active`` chez
  Claude Code) que si le kit a lui-même émis la relance qui le précède : il la
  garde sur disque (:func:`record_followup`) et la retrouve (:func:`kit_relaunched`).

Code de sortie : la doc dit « 2 : bloque l'action, équivalent à ``permission:
"deny"`` », mais ne documente les messages (``user_message``, ``agent_message``)
que dans la sortie JSON d'un code 0. Le refus est donc rendu en JSON, code 0 :
même blocage, et un refus que l'utilisateur et l'agent peuvent lire.

**Non vérifié** : Cursor n'est pas installé sur le poste de développement et
aucune capture réelle n'existe. Chaque charge acceptée ici est une fixture de
protocole tirée de la doc, pas une capture ; la garde reste ``advisory`` dans les
capacités tant qu'aucun refus n'a été observé.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent

__all__ = [
    "ASK_EVENTS",
    "DEFAULT_DENY_REASON",
    "EVENT_BY_CURSOR_NAME",
    "LOOP_LIMIT",
    "cursor_event_name",
    "event_from_cursor",
    "followup_state_path",
    "kit_relaunched",
    "normalize_tool_call",
    "record_followup",
    "render",
    "session_id",
]

#: Nom d'événement Cursor (compacté, minuscules) -> événement neutre que les décisions savent juger.
EVENT_BY_CURSOR_NAME: dict[str, HookEvent] = {
    "sessionstart": HookEvent.SESSION_START,
    "beforesubmitprompt": HookEvent.USER_PROMPT_SUBMIT,
    "pretooluse": HookEvent.PRE_TOOL_USE,
    "beforeshellexecution": HookEvent.PRE_TOOL_USE,
    "posttooluse": HookEvent.POST_TOOL_USE,
    "posttoolusefailure": HookEvent.POST_TOOL_USE_FAILURE,
    "subagentstart": HookEvent.SUBAGENT_START,
    "subagentstop": HookEvent.SUBAGENT_STOP,
    "precompact": HookEvent.PRE_COMPACT,
    "stop": HookEvent.STOP,
}

#: Événements câblés où ``permission: "ask"`` est appliqué (doc : « allow, deny, or ask »).
#: Sur ``preToolUse`` (« accepted by the schema but not enforced »), une demande est
#: rendue en refus. ``beforeMCPExecution`` et ``beforeReadFile`` ne sont pas câblés :
#: un hook qui les enverrait tomberait sur un événement inconnu, refusé (échec fermé).
ASK_EVENTS: frozenset[str] = frozenset({"beforeshellexecution"})

#: Champs de sortie documentés par événement de permission. Un champ de plus serait
#: un écart de schéma, que Cursor traite comme un blocage.
_PERMISSION_FIELDS: dict[str, tuple[str, ...]] = {
    "pretooluse": ("permission", "user_message", "agent_message"),
    "beforeshellexecution": ("permission", "user_message", "agent_message"),
}

#: ``loop_limit`` documenté par défaut pour un hook Cursor (« default 5 »). L'émetteur
#: l'écrit explicitement ; le rendu ne relance jamais au-delà.
LOOP_LIMIT = 5

#: Raison d'un refus qui n'en formule pas : un refus muet ne dit pas quoi faire.
DEFAULT_DENY_REASON = "[Grimoire] action refusée par la politique du projet."

#: Préfixe d'une demande de confirmation rendue en refus (``ask`` non appliqué).
_ASK_AS_DENY = (
    "[Grimoire] confirmation demandée, mais Cursor n'applique pas `ask` sur cet événement : "
    "appel refusé par précaution. Relance-le après accord explicite de l'utilisateur. "
)

#: Outil Cursor -> classe d'outil du kit (celle que le moteur de politique sait juger).
_TOOL_CLASSES: dict[str, str] = {
    "shell": "Bash",
    "read": "Read",
    "write": "Write",
    # Une suppression est une mutation du chemin visé : jugée comme une écriture.
    "delete": "Write",
    "grep": "Grep",
    "task": "Task",
}

#: Outils dont l'entrée doit nommer un chemin pour être jugée.
_PATH_TOOLS = frozenset({"read", "write", "delete"})

#: Clés de chemin plausibles : la doc ne donne pas l'entrée de ``Read``/``Write``/``Delete``
#: (seuls ``beforeReadFile`` et ``afterFileEdit`` documentent ``file_path``).
PATH_FIELDS: tuple[str, ...] = ("file_path", "path", "target_file", "filePath", "targetFile", "absolute_path")


def _compact(name: str) -> str:
    return name.strip().replace("-", "").replace("_", "").replace(" ", "").lower()


def event_from_cursor(name: str) -> HookEvent | None:
    """Événement neutre d'un nom Cursor (``beforeShellExecution``), ou ``None``."""
    return EVENT_BY_CURSOR_NAME.get(_compact(name))


def cursor_event_name(payload: dict[str, Any]) -> str:
    """Le nom Cursor compacté de l'appel : ``hook_event_name``, sinon la forme de la charge.

    Une charge sans nom mais avec une ``command`` au premier niveau et sans
    ``tool_name`` est celle de ``beforeShellExecution`` (seul événement documenté à
    porter la commande hors de ``tool_input``).
    """
    name = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    if isinstance(name, str) and name.strip():
        return _compact(name)
    if isinstance(payload.get("command"), str) and not payload.get("tool_name"):
        return "beforeshellexecution"
    return ""


def _path_of(tool_input: dict[str, Any]) -> str:
    for key in PATH_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def normalize_tool_call(payload: dict[str, Any], event: HookEvent) -> tuple[str, dict[str, Any]]:
    """``(classe d'outil du kit, entrée)`` d'un appel Cursor ; ``("", …)`` s'il est injugeable.

    Un nom vide sur ``PRE_TOOL_USE`` est ce que la garde refuse déjà (« aucun nom
    d'outil exploitable ») : c'est le chemin d'échec fermé, pas une autorisation.
    """
    sub = cursor_event_name(payload)
    raw_input = payload.get("tool_input")
    if sub == "beforeshellexecution":
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            return "", {}
        tool_input: dict[str, Any] = {"command": command}
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            tool_input["working_directory"] = cwd
        return "Bash", tool_input
    name = str(payload.get("tool_name") or "").strip()
    tool_input = _parse_json_object(raw_input)
    if not name:
        return "", tool_input
    key = name.lower()
    if key.startswith("mcp:"):
        return name, tool_input
    klass = _TOOL_CLASSES.get(key)
    if klass is None:
        # Outil que la doc ne nomme pas : jugé sous son nom par le moteur (classement par
        # sous-chaînes), comme un outil MCP ailleurs.
        return name, tool_input
    if klass == "Bash":
        command = tool_input.get("command")
        if event is HookEvent.PRE_TOOL_USE and (not isinstance(command, str) or not command.strip()):
            return "", tool_input
        return klass, tool_input
    if key in _PATH_TOOLS:
        path = _path_of(tool_input)
        if not path:
            if event is HookEvent.PRE_TOOL_USE:
                # Une écriture ou une lecture sans chemin lisible n'est pas jugeable.
                return "", tool_input
            return klass, tool_input
        return klass, {**tool_input, "file_path": path}
    return klass, tool_input


def session_id(payload: dict[str, Any]) -> str:
    """L'identifiant de conversation : ``conversation_id`` est commun à tous les événements d'agent."""
    for key in ("conversation_id", "session_id", "sessionId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_id(value: str) -> str:
    value = value.strip() or "unknown"
    return value if _SAFE_ID.match(value) else "h-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def followup_state_path(project_root: Path, conversation_id: str) -> Path:
    """La dernière relance émise par le kit pour *conversation_id*, à côté des ``policy-holds-*``."""
    from grimoire.core.standard_generation import RUNS_DIR

    return project_root / RUNS_DIR / f"cursor-followup-{_safe_id(conversation_id)}.json"


def _generation(payload: dict[str, Any]) -> str:
    value = payload.get("generation_id")
    return value.strip() if isinstance(value, str) else ""


def kit_relaunched(project_root: Path, payload: dict[str, Any]) -> bool:
    """Ce ``stop`` suit-il une relance que **le kit** a émise dans cette conversation ?

    Vrai seulement si l'état disque de la conversation existe, que la relance a été
    émise au ``loop_count`` immédiatement précédent (``loop_count`` courant - 1), et
    que ce n'est pas le même tour rejoué (même ``generation_id``, quand Cursor
    l'envoie). Une relance d'un autre hook, un ``loop_count`` hérité d'une relance
    ancienne ou un état illisible : pas de continuation, la garde juge normalement.
    """
    conversation = session_id(payload)
    count = _loop_count(payload)
    if not conversation or count < 1:
        return False
    try:
        state = json.loads(followup_state_path(project_root, conversation).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict) or state.get("conversation_id") != conversation:
        return False
    emitted = state.get("loop_count")
    if not isinstance(emitted, int) or isinstance(emitted, bool) or emitted != count - 1:
        return False
    generation = _generation(payload)
    return not (generation and state.get("generation_id") == generation)


def record_followup(project_root: Path, payload: dict[str, Any], *, emitted: bool) -> None:
    """Garder (``emitted``) ou effacer la relance du kit pour la conversation de *payload*.

    Au mieux : un disque qui refuse n'empêche jamais de rendre le verdict ; la
    conséquence est seulement qu'un ``stop`` suivant sera jugé comme un premier.
    """
    conversation = session_id(payload)
    if not conversation:
        return
    path = followup_state_path(project_root, conversation)
    try:
        if not emitted:
            path.unlink(missing_ok=True)
            return
        from grimoire.core.fsio import atomic_write

        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "conversation_id": conversation,
            "generation_id": _generation(payload),
            "loop_count": _loop_count(payload),
            "emitted_at": datetime.now(UTC).isoformat(),
        }
        atomic_write(path, json.dumps(state, ensure_ascii=False))
    except OSError:
        return


def _loop_count(payload: dict[str, Any]) -> int:
    count = payload.get("loop_count")
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0


def _permission(decision: Decision, sub: str) -> dict[str, Any]:
    """La sortie d'un événement de permission : champs documentés seulement."""
    fields = _PERMISSION_FIELDS.get(sub, _PERMISSION_FIELDS["pretooluse"])
    reason = decision.reason
    if decision.outcome is Outcome.ASK:
        if sub in ASK_EVENTS:
            permission = "ask"
        else:
            permission = "deny"
            reason = _ASK_AS_DENY + (reason or "")
    elif decision.is_refusal:
        permission = "deny"
        reason = reason or DEFAULT_DENY_REASON
    else:
        permission = "allow"
    out: dict[str, Any] = {"permission": permission}
    if reason and permission != "allow":
        out["user_message"] = reason
        if "agent_message" in fields:
            out["agent_message"] = reason
    elif decision.context and "agent_message" in fields:
        # Avertissement non bloquant : le motif part à l'agent, sans refus.
        out["agent_message"] = decision.context
    return out


def render(decision: Decision, hook: HookInput) -> dict[str, Any]:
    """*decision* dans le JSON que Cursor lit sur stdout pour cet événement.

    - ``PRE_TOOL_USE`` : ``permission``/``user_message``/``agent_message`` ; ``ask``
      gardé là où Cursor l'applique, refus ailleurs ;
    - ``STOP`` : un refus de clôture devient ``followup_message`` (relance), seulement
      sur un tour terminé normalement (``status`` absent ou ``completed``) et tant que
      ``loop_count`` reste sous :data:`LOOP_LIMIT` ;
    - ``SUBAGENT_STOP`` : même règle, mais la décision du kit n'y refuse jamais —
      un ``followup_message`` relancerait le sous-agent ;
    - ``SESSION_START``, ``POST_TOOL_USE``, ``POST_TOOL_USE_FAILURE`` : ``additional_context`` ;
    - ``PRE_COMPACT`` : ``user_message`` (observationnel) quand une capsule est écrite.

    Tout le reste rend ``{}`` : Cursor poursuit comme sans hook.
    """
    if decision.abstain and hook.event is not HookEvent.PRE_TOOL_USE:
        return {}
    raw = hook.raw
    if hook.event is HookEvent.PRE_TOOL_USE:
        if decision.abstain:
            # Pas d'avis, mais une sortie sans `permission` pourrait être lue comme hors
            # schéma, ce qui bloque l'action : `allow` ne couvre jamais le refus d'un
            # autre hook (« deny beats ask, ask beats allow »).
            return {"permission": "allow"}
        return _permission(decision, cursor_event_name(raw) or "pretooluse")
    if hook.event in {HookEvent.STOP, HookEvent.SUBAGENT_STOP}:
        status = raw.get("status")
        finished = status in (None, "", "completed")
        if decision.outcome is Outcome.BLOCK and finished and _loop_count(raw) < LOOP_LIMIT:
            return {"followup_message": decision.reason or DEFAULT_DENY_REASON}
        return {}
    if hook.event in {HookEvent.SESSION_START, HookEvent.POST_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE}:
        return {"additional_context": decision.context} if decision.context else {}
    if hook.event is HookEvent.PRE_COMPACT and decision.context:
        error = str(decision.detail.get("capsule_error") or "")
        return {
            "user_message": (
                f"[Grimoire] capsule de gouvernance non écrite avant compaction ({error})."
                if error
                else "[Grimoire] capsule de gouvernance écrite avant compaction."
            )
        }
    return {}
