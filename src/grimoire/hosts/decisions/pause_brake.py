"""``PreToolUse`` et ``UserPromptSubmit`` face à une pause (K11, épopée #705, plan §4.4).

Une pause ``drain`` n'arrête personne : les agents en cours finissent leur tâche, seule l'admission est
fermée (:mod:`grimoire.missions.pause`). Une pause ``hold`` est plus dure, mais seulement là où on peut la tenir :

- **hôte fencé** (le refus d'outil est prouvé pour sa version, ``hosts.capabilities``) : le frein refuse les
  outils mutants avec « pause : dépose ton point de reprise ». Le **premier** refus ouvre **une** écriture,
  ``grimoire task wait <tâche> --reason paused`` (ou l'action MCP ``wait`` avec ``to=paused``), tant que la tâche
  est encore ``claimed`` ou ``running``. Une fois garée, tout outil mutant reste refusé et le ``Stop`` est permis
  (la session qui se gare n'est plus jugée). Les lectures restent permises ;
- **hôte sans frein** : ``hold`` se dégrade en ``drain``. Rien n'est refusé, la consigne « pause demandée : gare-toi »
  est injectée au tour suivant, et l'état le dit (« hold dégradé en drain (hôte sans frein) »).

Le frein ne juge que les sessions qui tiennent une tâche (journal de session, comme le frein du bail) : une
session sans tâche n'est pas un agent qu'on a admis. Un drapeau illisible vaut ``drain`` : il ne refuse rien,
mais il se voit. Une exception n'est jamais un ``allow`` silencieux : l'appel est rendu à l'utilisateur (``ask``).

Limites écrites : la pause ne reprend pas un effet distant déjà parti (``gh``, ``git push``) ; une session qui lance
du code arbitraire contourne n'importe quelle garde de commande ; le bail d'une tâche garée reste tenu jusqu'au
réveil par le tick (K6), il n'est pas rendu au dépôt du point de reprise.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.lease_brake import _lease_holder, _simple_grimoire_task
from grimoire.hosts.decisions.tool_facts import ToolFacts, command_surface
from grimoire.missions.pause import HOLD, PauseState, effective_mode, home_root, read_pause
from grimoire.policies.schemas import MutationClass

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["pause_brake", "pause_context", "pause_lifted_notices"]

_WAIT_PAUSED = re.compile(r"^\s*(?:\S*/)?grimoire\s+task\s+wait\s+(?P<id>[^\s;&|<>]+)(?P<rest>[^;&|<>\n]*)")
_PAUSED_REASON = re.compile(r"--reason[ =]paused(?:\s|$)")
#: Les lectures du kit que la pause laisse passer même quand l'outil ne les classe pas en lecture seule.
_READS = re.compile(
    r"^\s*(?:\S*/)?grimoire\s+(?:task\s+(?:show|list|context|decisions)|orchestrate\s+status|queue\s+plan|status)\b"
)


def _is_fenced(hook: HookInput) -> bool:
    """Le refus d'outil est-il prouvé pour l'hôte de ce hook, à sa version ? Sans hôte nommé, non."""
    from grimoire.hosts.capabilities import is_fenced, resolve_host
    from grimoire.hosts.host_version import detect_host_version

    host_id = resolve_host(hook.host) if hook.host else None
    if host_id is None:
        return False
    return is_fenced(host_id, detect_host_version(host_id, hook.raw))


def _task_status(root: Path, task_id: str) -> str:
    from grimoire.core.standard_state import LEDGER_RELPATH
    from grimoire.missions.ledger import MissionLedger

    task = MissionLedger(root / LEDGER_RELPATH).get_task(task_id)
    return task.status.value if task is not None else ""


#: Les outils MCP du kit qui écrivent dans le ledger : ``classify_tool`` ne reconnaît pas leur nom comme une
#: écriture (un outil MCP inconnu est une lecture), or un ``hold`` doit pouvoir les refuser.
_MCP_TASK_WRITERS = ("task_claim", "task_update", "task_add")


def _mcp_task_write(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") and tool_name.endswith(_MCP_TASK_WRITERS)


def _checkpoint_call(hook: HookInput, facts: ToolFacts, task_id: str) -> bool:
    """L'appel est-il le point de reprise de CETTE tâche : ``task wait <id> --reason paused``, rien d'enchaîné ?"""
    if facts.command:
        if not _simple_grimoire_task(facts.command):
            return False
        match = _WAIT_PAUSED.match(command_surface(facts.command))
        return bool(match and match.group("id") == task_id and _PAUSED_REASON.search(match.group("rest")))
    name = hook.tool_name or ""
    if name.startswith("mcp__") and name.endswith("task_update"):
        args = hook.tool_input
        return args.get("action") == "wait" and args.get("to") == "paused" and args.get("task_id") == task_id
    return False


def _deny(hook: HookInput, task_id: str, why: str, kind: str) -> Decision:
    return Decision(
        outcome=Outcome.DENY,
        reason=(
            f"[Grimoire pause] {hook.tool_name or 'action'} refusé — {why}. "
            f"Dépose ton point de reprise pour {task_id} : `grimoire task wait {task_id} --reason paused` "
            "(une seule écriture permise), puis arrête-toi ; les lectures restent permises. "
            "Limite : un effet distant déjà parti (gh, push) n'est pas repris par ce frein."
        ),
        detail={"tool": hook.tool_name, "pause_brake": kind, "task_id": task_id},
    )


def pause_brake(hook: HookInput, facts: ToolFacts) -> Decision | None:
    """Le refus d'un ``hold`` pour cet appel d'outil, ou ``None`` (pas de pause, ``drain``, hôte sans frein, lecture)."""
    if facts.mutation is MutationClass.READ_ONLY and not _mcp_task_write(hook.tool_name or ""):
        return None
    try:
        task_id, _epoch = _lease_holder(hook)
        if not task_id:
            return None
        root = home_root(hook.project_root)
        if read_pause(root).mode_for(task_id) != HOLD or not _is_fenced(hook):
            return None
        if facts.command and _READS.match(command_surface(facts.command)):
            return None
        if _checkpoint_call(hook, facts, task_id):
            if _task_status(root, task_id) in ("claimed", "running"):
                return None
            return _deny(hook, task_id, "pause : le point de reprise est déjà déposé, une seule écriture était permise", "checkpoint_used")
        return _deny(hook, task_id, "pause : dépose ton point de reprise", "hold")
    except Exception as exc:
        return Decision(
            outcome=Outcome.ASK,
            reason=(
                f"[Grimoire pause] frein de pause en erreur — {type(exc).__name__}: {exc}. "
                f"La pause n'a pas pu être vérifiée pour {hook.tool_name or 'cet appel'} : à toi de décider."
            ),
            detail={"tool": hook.tool_name, "pause_brake": "error", "error": f"{type(exc).__name__}: {exc}"},
        )


def pause_context(hook: HookInput, task_id: str) -> str:
    """La consigne de pause injectée au tour de la session (drain, hold, hold dégradé, drapeau illisible) ; ``""`` sans pause.

    Best-effort comme tout contexte de hook : une erreur de lecture rend ``""``, jamais une session cassée.
    """
    try:
        state: PauseState = read_pause(home_root(hook.project_root))
        mode = state.mode_for(task_id)
        if mode is None:
            return ""
        lines = [f"[Grimoire pause] {state.reason_for(task_id)}."]
        lines.extend(f"[Grimoire pause] attention : {line}." for line in state.attention())
        applied, degraded = effective_mode(mode, fenced=_is_fenced(hook))
        park = f"`grimoire task wait {task_id} --reason paused`" if task_id and task_id != "bootstrap" else "`grimoire task wait <tâche> --reason paused`"
        if applied == HOLD:
            lines.append(
                f"Pause hold : le prochain outil mutant sera refusé ; dépose ton point de reprise avec {park}, puis arrête-toi."
            )
        elif degraded:
            lines.append(f"Pause demandée : gare-toi avec {park} dès que tu peux le faire sans casser un effet en cours ({degraded}).")
        else:
            lines.append("Pause drain : ne réclame aucune nouvelle tâche ; termine celle en cours.")
        return "\n".join(lines)
    except Exception:
        return ""


def pause_lifted_notices(project_root: Path, session_id: str) -> str:
    """Annonce à *session_id* les tâches qu'elle avait garées en pause et que le tick a rendues ``ready`` : elle peut les re-claimer.

    Rejeu du journal : une tâche est « rendue » pour la session quand elle lui a été rattachée, puis qu'un
    réveil de cause ``pause levée`` est venu, sans nouveau rattachement depuis. Seules les tâches encore
    ``ready`` sont annoncées. Best-effort : ``""`` sur tout échec.
    """
    session = session_id.strip()
    if not session:
        return ""
    try:
        from grimoire.core.standard_state import LEDGER_RELPATH
        from grimoire.missions.ledger import SESSION_ATTACHED_EVENT, WOKEN_KEY, MissionLedger
        from grimoire.missions.pause import PAUSE_LIFTED_CAUSE

        root = home_root(project_root)
        if not (root / LEDGER_RELPATH / "events.jsonl").is_file():
            return ""
        ledger = MissionLedger(root / LEDGER_RELPATH)
        holder: dict[str, str] = {}
        lifted: set[str] = set()
        for evt in ledger.list_events():
            tid = evt.entity_id
            if evt.event_type == SESSION_ATTACHED_EVENT:
                holder[tid] = str(evt.payload.get("session_id", ""))
                if holder[tid] == session:
                    lifted.discard(tid)
            elif evt.event_type == "task.transitioned":
                woken = evt.payload.get(WOKEN_KEY)
                if isinstance(woken, dict) and woken.get("cause") == PAUSE_LIFTED_CAUSE and holder.get(tid) == session:
                    lifted.add(tid)
        lines = []
        for tid in sorted(lifted):
            task = ledger.get_task(tid)
            if task is not None and task.status.value == "ready":
                lines.append(
                    f"[Grimoire] Pause levée : ta tâche {tid} est de nouveau ready ; tu peux la re-claimer "
                    f"(`grimoire task claim {tid}`), elle repart de sa branche."
                )
        return "\n".join(lines[:3])
    except Exception:
        return ""
