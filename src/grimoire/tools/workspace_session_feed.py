"""Bloc Activité (P69v2) — le fil de l'agent, dérivé du ledger, jamais inventé.

Un même fil chronologique pour deux inspecteurs : celui d'une tâche
(``GET /api/workspace/tasks/<id>/feed``) et celui d'une session
(``GET /api/workspace/sessions/<id>/feed`` — l'union des fils des tâches que la
session tient). Les entrées viennent de deux journaux déjà écrits, aucun
nouveau :

- le Mission Ledger : claim et transitions (``task.transitioned``, avec le
  claim et sa raison), questions/réponses (``task.question_asked``,
  ``task.human_answered``, ``task.question_resolved``), notes de progression
  (``task.progress_noted``), rattachement et lancement de session ;
- le TraceLedger : les refus de porte (``task.gate.refused``), par la même
  projection que la timeline (:func:`grimoire.missions.trace._trace_entries`)
  — une porte rouge fait partie de la vie de la tâche.

Lecture seule stricte. Une tâche inconnue lève (404 côté transport) ; une
session sans claim rend un fil vide qui le dit, jamais un fil deviné. La
commande ``claude logs`` vient de :mod:`grimoire.missions.session_link` — un
hôte sans commande connue n'en affiche aucune.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.missions.service import TaskService
from grimoire.missions.session_link import logs_command, session_fields
from grimoire.missions.trace import _trace_entries

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.schemas import LedgerEvent

__all__ = ["FEED_KINDS", "FEED_LIMIT", "SCHEMA_VERSION", "session_feed", "task_feed"]

SCHEMA_VERSION = "grimoire-workspace-session-feed/v1"
#: Entrées rendues par fil — les plus récentes ; ``total`` dit ce qui existe.
FEED_LIMIT = 20

#: Les événements du Mission Ledger qui racontent la vie de la tâche à un
#: humain : claim et transitions, portes, questions/réponses, progression.
#: Les battements de bail (``task.lease_renewed``) n'y sont pas : un bail
#: renouvelé n'est pas une progression (même doctrine que ``liveness``).
FEED_KINDS = frozenset({
    "task.created",
    "task.transitioned",
    "task.session_attached",
    "task.session_launched",
    "task.session_launch_failed",
    "task.question_asked",
    "task.human_answered",
    "task.question_resolved",
    "task.progress_noted",
})

#: ``to_state`` qui, en soi, disent un arrêt (même règle que la timeline).
_STOPPED = {"blocked", "failed"}

_STATE_LABEL = {
    "proposed": "proposée", "ready": "prête", "claimed": "réclamée", "running": "en cours",
    "waiting": "en attente", "blocked": "bloquée", "needs_verification": "en revue",
    "closed": "close", "cancelled": "annulée", "failed": "échouée", "archived": "archivée",
}


def _clip(text: str, limit: int = 120) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _label(event: LedgerEvent, *, with_session: bool = True) -> tuple[str, bool]:
    """``(libellé, échec)`` d'un événement du ledger — le fait, en une ligne.

    ``with_session=False`` : pas d'identifiant de session dans le libellé —
    la ligne sur la carte (P74 : plus d'UUID sur une carte).
    """
    payload = event.payload
    kind = event.event_type
    if kind == "task.created":
        return f"ouverte par {event.actor_id}", False
    if kind == "task.transitioned":
        to_state = str(payload.get("to_state", ""))
        label = f"passée {_STATE_LABEL.get(to_state, to_state)} par {event.actor_id}"
        claim = payload.get("claim")
        if with_session and isinstance(claim, dict) and claim.get("session_id"):
            label += f" (session {str(claim['session_id'])[:8]})"
        reason = _clip(str(payload.get("reason", "") or ""))
        if reason:
            label += f" — {reason}"
        return label, to_state in _STOPPED
    if kind == "task.session_attached":
        return f"session {str(payload.get('session_id', ''))[:8]} rattachée au claim", False
    if kind == "task.session_launched":
        model = str(payload.get("model", "") or "")
        return f"session {payload.get('session', '?')} lancée" + (f" ({model})" if model else ""), False
    if kind == "task.session_launch_failed":
        return f"lancement de session échoué — {_clip(str(payload.get('detail', '') or ''))}", True
    if kind == "task.question_asked":
        question = payload.get("question")
        text = question.get("text", "") if isinstance(question, dict) else ""
        return f"question posée : {_clip(text)}", False
    if kind == "task.human_answered":
        return f"réponse reçue : {_clip(str(payload.get('answer', '') or ''))}", False
    if kind == "task.question_resolved":
        return "question résolue", False
    if kind == "task.progress_noted":
        return f"progression : {_clip(str(payload.get('note', '') or ''))}", False
    return kind, False


def _ledger_entries(service: TaskService, task_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in service.ledger.list_events(task_id):
        if event.event_type not in FEED_KINDS:
            continue
        label, failure = _label(event)
        out.append({
            "at": event.created_at, "kind": event.event_type, "actor": event.actor_id,
            "label": label, "failure": failure, "task_id": task_id,
        })
    return out


def _gate_entries(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Les refus de porte de *task_id* — la même projection que la timeline."""
    traces = project_root / TRACES_DIR
    if not (traces / "traces.jsonl").is_file():
        return []
    return [
        {"at": e.at, "kind": e.kind, "actor": "", "label": e.summary, "failure": e.failure, "task_id": task_id}
        for e in _trace_entries(traces, task_id)
        if e.source == "gate"
    ]


def _entries(project_root: Path, service: TaskService, task_id: str) -> list[dict[str, Any]]:
    merged = _ledger_entries(service, task_id) + _gate_entries(project_root, task_id)
    merged.sort(key=lambda e: e["at"])  # tri stable : à date égale, l'ordre d'écriture
    return merged


def task_feed(project_root: Path, task_id: str, *, limit: int = FEED_LIMIT) -> dict[str, Any]:
    """Le fil d'activité d'une tâche, du plus ancien au plus récent (les *limit* derniers)."""
    root = project_root.resolve()
    service = TaskService(root)
    task = service.require(task_id)
    entries = _entries(root, service, task_id)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "task_id": task.id,
        "session": session_fields(task),
        "entries": entries[-limit:],
        "total": len(entries),
    }


def session_feed(project_root: Path, session_id: str, *, limit: int = FEED_LIMIT) -> dict[str, Any]:
    """Le fil d'une session : l'union des fils des tâches que son claim tient.

    Une session qui ne tient aucun claim rend un fil vide avec sa raison — le
    ledger ne sait rien d'elle, on ne devine pas.
    """
    root = project_root.resolve()
    service = TaskService(root)
    held = [
        t for t in service.ledger.list_tasks()
        if t.claim is not None and str(getattr(t.claim, "session_id", "") or "") == session_id
    ]
    entries: list[dict[str, Any]] = []
    host = ""
    for task in held:
        host = host or str(getattr(task.claim, "session_host", "") or "")
        entries.extend(_entries(root, service, task.id))
    entries.sort(key=lambda e: e["at"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "session_id": session_id,
        "tasks": [t.id for t in held],
        "logs_command": logs_command(host, session_id),
        "entries": entries[-limit:],
        "total": len(entries),
        "note": "" if held else "aucun claim ne rattache cette session : le ledger ne raconte rien d'elle",
    }


#: Les entrées de carte : celles du fil SANS les événements de session — une
#: carte ne porte aucun identifiant de session (P74), l'inspecteur les garde.
_CARD_KINDS = FEED_KINDS - {"task.session_attached", "task.session_launched", "task.session_launch_failed"}


def last_feed_labels(service: TaskService, task_ids: set[str]) -> dict[str, dict[str, str]]:
    """Task id → dernière entrée du fil (``{at, label}``), en UNE passe du ledger.

    Pour la ligne sur la carte (P69v2) : seuls les événements du Mission Ledger
    comptent ici — pas les portes du TraceLedger, qu'on ne relit pas par carte —
    et jamais un identifiant de session (P74) : les libellés de carte n'en
    portent pas et les événements de session sont laissés à l'inspecteur.
    """
    found: dict[str, dict[str, str]] = {}
    if not task_ids:
        return found
    for event in service.ledger.list_events():
        if event.entity_id in task_ids and event.event_type in _CARD_KINDS:
            label, _failure = _label(event, with_session=False)
            found[event.entity_id] = {"at": event.created_at, "label": label}
    return found
