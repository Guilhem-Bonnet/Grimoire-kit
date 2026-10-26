"""« Depuis ta dernière visite » — ce qui a bougé pendant ton absence (P53).

À traiter dit ce qui attend une décision **maintenant**. Il ne dit pas ce qui
s'est passé entre deux visites : une tâche livrée la nuit, un gate qui a
refusé une clôture, une tâche partie en attente. Ce module lit ces faits dans
les journaux qui les portent déjà, pour un projet ou pour tout le registre,
à partir d'un horodatage fourni par le client :

- **livré** : une transition ``task.transitioned`` vers ``closed`` du Mission
  Ledger postérieure à la visite (la dernière par tâche) ;
- **refusé** : un refus de gate de transition (``task.gate.refused``, recette
  :data:`grimoire.missions.trace.TASK_GATE_RECIPE` du TraceLedger), regroupé
  par tâche avec son nombre ;
- **en attente** : une tâche aujourd'hui ``waiting`` ou ``blocked`` qui y est
  entrée depuis la visite — la question posée ou la raison du blocage en
  dit pourquoi.

Refus délibérés :

- **la visite n'est jamais écrite ici** : elle est propre à une personne et
  à un navigateur, la coque la garde (``localStorage``). Cette route est une
  lecture pure ; sans ``since`` elle rend un résumé vide, ``visit: false`` ;
- **un horodatage illisible est un refus** (400), pas un « tout depuis
  toujours » qui noierait l'écran ;
- **une source illisible n'est pas un zéro** : le projet est rendu
  ``unreadable`` avec sa raison, comme dans la route d'attention ;
- **les fuseaux** : tout instant est comparé en UTC conscient
  (:func:`grimoire.missions.lease.parse_instant`), jamais en texte — un
  horodatage local du navigateur et un horodatage UTC du journal se comparent
  donc sans dérive d'une ou deux heures.

Les PR (P48) ne sont pas encore lues : chaque ligne renvoie à sa tâche.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.missions.lease import parse_instant

__all__ = ["DELIVERED", "KINDS", "LIMIT", "REFUSED", "SCHEMA_VERSION", "WAITING", "since_view", "summarize"]

SCHEMA_VERSION = "grimoire-workspace-since/v1"

DELIVERED = "delivered"
REFUSED = "refused"
WAITING = "waiting"
#: L'ordre d'affichage : ce qui est fini, ce qui a été refusé, ce qui attend.
KINDS: tuple[str, ...] = (DELIVERED, REFUSED, WAITING)

#: Lignes rendues par sorte ; au-delà, seul le compte est rendu (``more``).
LIMIT = 8

#: Le mot de l'état visé par un refus, lu dans ``action_kind``.
_STATE_WORDS = {
    "ready": "prête",
    "claimed": "réclamée",
    "running": "en cours",
    "needs_verification": "en revue",
    "closed": "livrée",
    "blocked": "bloquée",
    "waiting": "en attente",
    "failed": "en échec",
    "cancelled": "annulée",
}


def _instant(raw: str | None) -> datetime | None:
    return parse_instant(raw) if raw else None


def _target(verdicts: Iterable[Any]) -> str:
    """L'état visé par le refus (``task.transition:<de>-><vers>``), vide s'il n'est pas lisible."""
    for verdict in verdicts:
        kind = str(getattr(verdict, "action_kind", "") or "")
        if kind.startswith("task.transition:") and "->" in kind:
            return kind.rsplit("->", 1)[1]
    return ""


def _line(kind: str, at: datetime, task: Any, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "at": at.astimezone(UTC).isoformat(),
        "task_id": task.id,
        "title": task.title,
        "reason": reason,
        **extra,
    }


def _delivered(events: Iterable[Any], tasks: Mapping[str, Any], since: datetime) -> list[dict[str, Any]]:
    last: dict[str, datetime] = {}
    for event in events:
        if event.event_type != "task.transitioned" or event.payload.get("to_state") != "closed":
            continue
        at = _instant(event.created_at)
        if at is None or at <= since or event.entity_id not in tasks:
            continue
        if at > last.get(event.entity_id, since):
            last[event.entity_id] = at
    return [_line(DELIVERED, at, tasks[tid], "clôturée, preuve acceptée") for tid, at in last.items()]


def _refused(traces: Iterable[Any], tasks: Mapping[str, Any], since: datetime) -> list[dict[str, Any]]:
    from grimoire.missions.trace import TASK_GATE_RECIPE

    grouped: dict[str, tuple[datetime, Any, int]] = {}
    for trace in traces:
        if trace.recipe_id != TASK_GATE_RECIPE or trace.task_id not in tasks:
            continue
        at = _instant(trace.started_at)
        if at is None or at <= since:
            continue
        seen = grouped.get(trace.task_id)
        count = (seen[2] if seen else 0) + 1
        grouped[trace.task_id] = (at, trace, count) if seen is None or at > seen[0] else (seen[0], seen[1], count)
    out = []
    for task_id, (at, trace, count) in grouped.items():
        target = _target(trace.policy_verdicts)
        missing = ", ".join(pv.verdict_id for pv in trace.policy_verdicts) or "preuve manquante"
        if target == "closed":
            verb = "clôture refusée"
        elif target:
            verb = f"passage « {_STATE_WORDS.get(target, target)} » refusé"
        else:
            verb = "transition refusée"
        out.append(_line(REFUSED, at, tasks[task_id], f"{verb} — manque : {missing}", count=count))
    return out


def _waiting(events: Iterable[Any], tasks: Mapping[str, Any], since: datetime) -> list[dict[str, Any]]:
    from grimoire.missions.schemas import TaskState

    held = {TaskState.WAITING.value, TaskState.BLOCKED.value}
    entered: dict[str, tuple[datetime, str]] = {}
    for event in events:
        task = tasks.get(event.entity_id)
        if event.event_type != "task.transitioned" or task is None or task.status.value not in held:
            continue
        if event.payload.get("to_state") != task.status.value:
            continue
        at = _instant(event.created_at)
        if at is None:
            continue
        if event.entity_id not in entered or at > entered[event.entity_id][0]:
            entered[event.entity_id] = (at, str(event.payload.get("reason") or ""))
    out = []
    for task_id, (at, reason) in entered.items():
        if at <= since:
            continue
        task = tasks[task_id]
        waiting = getattr(task, "waiting", None)
        question = getattr(waiting, "question", None) if waiting is not None else None
        if task.status is TaskState.WAITING:
            why = str(getattr(question, "text", "") or "") or reason or "en attente"
            word = "question posée" if question is not None else "en attente"
        else:
            why, word = reason or "bloquée", "bloquée"
        out.append(_line(WAITING, at, task, why, state=task.status.value, word=word))
    return out


def summarize(
    tasks: Iterable[Any], events: Iterable[Any], traces: Iterable[Any], since: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Fonction pure : les lignes de chaque sorte postérieures à *since*, la plus récente d'abord."""
    by_id = {task.id: task for task in tasks}
    events = list(events)
    lines = {
        DELIVERED: _delivered(events, by_id, since),
        REFUSED: _refused(traces, by_id, since),
        WAITING: _waiting(events, by_id, since),
    }
    for kind in KINDS:
        lines[kind].sort(key=lambda line: line["at"], reverse=True)
    return lines


def _read_traces(root: Path) -> list[Any]:
    from grimoire.core.standard_generation import TRACES_DIR

    path = root / TRACES_DIR
    if not path.is_dir():
        return []
    from grimoire.traces.ledger import TraceLedger

    return TraceLedger(path).list_traces()


def _read_project(entry: Mapping[str, Any], since: datetime) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    from grimoire.missions.service import TaskService

    slug = str(entry.get("slug", ""))
    raw = str(entry.get("path", "") or "")
    row: dict[str, Any] = {"slug": slug, "name": str(entry.get("name", "")) or slug, "state": "ok", "reason": None}
    empty: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    if not raw or not Path(raw).is_dir():
        row.update(state="unreadable", reason="dossier introuvable" if raw else "chemin absent du registre")
        return row, empty
    try:
        service = TaskService(Path(raw))
        if not service.has_ledger:
            row.update(state="no_ledger", reason="aucun Mission Ledger")
            return row, empty
        lines = summarize(service.list_tasks(), service.ledger.list_events(), _read_traces(Path(raw)), since)
    except Exception as exc:  # frontière : un journal abîmé n'efface ni le projet ni les autres
        row.update(state="unreadable", reason=f"journal illisible : {exc}")
        return row, empty
    project = {"slug": row["slug"], "name": row["name"]}
    for kind in KINDS:
        for line in lines[kind]:
            line["project"] = project
    return row, lines


def since_view(
    project_root: Path,
    *,
    since: str | None,
    portfolio: bool = False,
    slug: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/attention/since``.

    *since* est l'instant de la dernière visite, ISO 8601 (``Z`` ou décalage ;
    sans décalage, UTC). Absent : aucune visite connue, résumé vide et
    ``visit: false``. Illisible : ``ValueError`` (un 400). Mêmes portées que
    la route d'attention (*portfolio*, *slug*).
    """
    from grimoire.tools.workspace_attention import _entries

    moment = now or datetime.now(UTC)
    base: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "scope": "portfolio" if portfolio else "project",
        "visit": False,
        "since": None,
        "sections": [{"id": kind, "count": 0, "more": 0, "items": []} for kind in KINDS],
        "count": 0,
        "projects": [],
    }
    if not since:
        return base
    visit = parse_instant(since)
    if visit is None:
        raise ValueError(f"since illisible : {since!r} (attendu : un instant ISO 8601)")
    rows: list[dict[str, Any]] = []
    merged: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    for entry in _entries(project_root, portfolio=portfolio, slug=slug):
        row, lines = _read_project(entry, visit)
        rows.append(row)
        for kind in KINDS:
            merged[kind].extend(lines[kind])
    sections: list[dict[str, Any]] = []
    total = 0
    for kind in KINDS:
        found = sorted(merged[kind], key=lambda line: line["at"], reverse=True)
        sections.append({"id": kind, "count": len(found), "more": max(len(found) - LIMIT, 0), "items": found[:LIMIT]})
        total += len(found)
    base.update(
        visit=True,
        since=visit.astimezone(UTC).isoformat(),
        sections=sections,
        count=total,
        projects=rows,
    )
    return base
