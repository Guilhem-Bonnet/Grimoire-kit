"""Vagues d'exécution d'un plan de tâches en DAG (W5-01).

Une vague regroupe les tâches dont toutes les dépendances ``blocks`` sont
closes ou dans une vague précédente : ce qu'on peut lancer en parallèle. Le
calcul est pur — il lit des :class:`MissionTask`, n'écrit rien.

Deux tâches d'une même vague qui déclarent un même fichier se marchent dessus :
``file_overlaps`` le dit avant le lancement, là où le verrou de fichiers du
claim ne le dirait qu'au second claim.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from grimoire.missions.schemas import DependencyKind, MissionTask, TaskState

__all__ = ["compute_waves", "declared_files"]

_DONE = frozenset({TaskState.CLOSED, TaskState.CANCELLED})


def declared_files(task: MissionTask) -> tuple[str, ...]:
    """Les fichiers qu'une tâche déclare toucher : ceux de son claim, puis ``extra["files"]``."""
    files: list[str] = []
    if task.claim is not None:
        files.extend(task.claim.exclusive_files)
    raw = task.extra.get("files", ())
    if isinstance(raw, (list, tuple)):
        files.extend(str(f) for f in raw if str(f).strip())
    return tuple(dict.fromkeys(files))


def compute_waves(tasks: Iterable[MissionTask]) -> dict[str, Any]:
    """Les vagues des tâches non terminées, leurs chevauchements de fichiers et les tâches en cycle.

    Une dépendance vers une tâche inconnue ou annulée garde sa tâche hors de
    toute vague (``unresolved``) : rien n'invente qu'elle serait libre.
    """
    by_id = {t.id: t for t in tasks}
    pending = {tid: t for tid, t in by_id.items() if t.status not in _DONE}
    deps: dict[str, set[str]] = {}
    unresolved: dict[str, list[str]] = {}
    for tid, task in pending.items():
        waiting: set[str] = set()
        for dep in task.dependencies:
            if dep.kind is not DependencyKind.BLOCKS:
                continue
            target = by_id.get(dep.target)
            if target is not None and target.status is TaskState.CLOSED:
                continue
            if target is None or target.status is TaskState.CANCELLED:
                unresolved.setdefault(tid, []).append(dep.target)
            else:
                waiting.add(dep.target)
        deps[tid] = waiting
    level: dict[str, int] = {}
    remaining = {tid for tid in pending if tid not in unresolved}
    while True:
        ready = sorted(
            tid for tid in remaining if all(d in level for d in deps[tid])
        )
        if not ready:
            break
        for tid in ready:
            level[tid] = 1 + max((level[d] for d in deps[tid]), default=0)
        remaining -= set(ready)
    waves: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    for n in range(1, max(level.values(), default=0) + 1):
        members = sorted(tid for tid, lv in level.items() if lv == n)
        waves.append({"wave": n, "tasks": members})
        owners: dict[str, list[str]] = {}
        for tid in members:
            for path in declared_files(pending[tid]):
                owners.setdefault(path, []).append(tid)
        overlaps.extend(
            {"wave": n, "file": path, "tasks": ids} for path, ids in sorted(owners.items()) if len(ids) > 1
        )
    return {
        "waves": waves,
        "file_overlaps": overlaps,
        "unresolved": {tid: sorted(set(ts)) for tid, ts in sorted(unresolved.items())},
        "cyclic": sorted(remaining - set(unresolved)),
    }
