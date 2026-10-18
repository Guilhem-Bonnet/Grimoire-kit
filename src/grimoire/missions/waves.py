"""Vagues d'exécution d'un plan de tâches en DAG (W5-01).

Une vague regroupe les tâches dont toutes les dépendances ``blocks`` sont
closes ou dans une vague précédente : ce qu'on peut lancer en parallèle. Le
numéro d'une vague est son niveau absolu dans le DAG (une tâche vaut ``1 + max``
des niveaux de ses dépendances, closes comprises) : il ne change pas quand la
vague précédente se ferme, et c'est le même que celui du checkpoint
(:mod:`grimoire.missions.wave_checkpoint`). Le
calcul est pur — il lit des :class:`MissionTask`, n'écrit rien.

Deux tâches d'une même vague qui déclarent un même fichier se marchent dessus :
``file_overlaps`` le dit avant le lancement, là où le verrou de fichiers du
claim ne le dirait qu'au second claim.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from grimoire.missions.schemas import DependencyKind, MissionTask, TaskState

__all__ = ["compute_waves", "declared_files", "wave_levels"]

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


def _cycle_members(deps: dict[str, set[str]]) -> set[str]:
    """Les tâches qui appartiennent à un cycle : composantes fortement connexes (Tarjan, itératif) de plus d'un nœud, ou à boucle sur soi."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    members: set[str] = set()
    counter = 0
    for root in sorted(deps):
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(deps[root])))]
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in deps:
                    continue
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(deps[child]))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1 or node in deps[node]:
                    members.update(component)
    return members


def compute_waves(tasks: Iterable[MissionTask], *, only: Iterable[str] | None = None) -> dict[str, Any]:
    """Les vagues des tâches non terminées, leurs chevauchements de fichiers et ce qui ne peut pas être planifié.

    *tasks* est l'ensemble des tâches du ledger : une dépendance se résout
    contre lui tout entier, comme au claim (une dépendance vers une tâche
    close d'une autre mission est libre). *only* restreint le rapport aux
    tâches d'une mission, sans changer la façon dont leurs dépendances se
    résolvent ; les numéros de vague sont les niveaux absolus du plan complet
    (closes comprises, voir :func:`wave_levels`), une vague sans tâche de la
    mission est omise.

    Trois sortes de tâches restent hors de toute vague, sans jamais être
    déclarées libres :

    - ``unresolved`` : une dépendance directe vers une tâche inconnue ou
      annulée (la cause est nommée) ;
    - ``cyclic`` : les tâches qui appartiennent elles-mêmes à un cycle ;
    - ``blocked`` : les tâches qui attendent, directement ou de proche en
      proche, une tâche ``unresolved``, ``cyclic`` ou elle-même ``blocked``
      (les dépendances en cause sont nommées). Elles ne sont pas en cycle.
    """
    by_id = {t.id: t for t in tasks}
    pending = {tid: t for tid, t in by_id.items() if t.status not in _DONE}
    # Niveau absolu des tâches closes (W5-05) : le numéro d'une vague ne bouge pas quand la précédente se ferme,
    # sans quoi « vague 1 » désignerait deux ensembles selon qu'on lit `task waves` ou le checkpoint de la vague.
    closed_level = {tid: lv for tid, lv in wave_levels(by_id.values()).items() if by_id[tid].status is TaskState.CLOSED}
    floor: dict[str, int] = {}
    deps: dict[str, set[str]] = {}
    unresolved: dict[str, list[str]] = {}
    for tid, task in pending.items():
        waiting: set[str] = set()
        for dep in task.dependencies:
            if dep.kind is not DependencyKind.BLOCKS:
                continue
            target = by_id.get(dep.target)
            if target is not None and target.status is TaskState.CLOSED:
                floor[tid] = max(floor.get(tid, 0), closed_level.get(dep.target, 0))
                continue
            if target is None or target.status is TaskState.CANCELLED:
                unresolved.setdefault(tid, []).append(dep.target)
            else:
                waiting.add(dep.target)
        deps[tid] = waiting
    cyclic = _cycle_members(deps)
    # Propagation : ce qui attend une tâche inplanifiable est inplanifiable, sans être en cycle.
    stuck = set(unresolved) | cyclic
    blocked: dict[str, list[str]] = {}
    changed = True
    while changed:
        changed = False
        for tid in sorted(pending):
            if tid in stuck:
                continue
            causes = sorted(d for d in deps[tid] if d in stuck)
            if causes:
                blocked[tid] = causes
                stuck.add(tid)
                changed = True
    level: dict[str, int] = {}
    remaining = {tid for tid in pending if tid not in stuck}
    while True:
        ready = sorted(
            tid for tid in remaining if all(d in level for d in deps[tid])
        )
        if not ready:
            break
        for tid in ready:
            level[tid] = 1 + max(max((level[d] for d in deps[tid]), default=0), floor.get(tid, 0))
        remaining -= set(ready)
    scope = None if only is None else frozenset(only)

    def kept(tid: str) -> bool:
        return scope is None or tid in scope

    waves: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    for n in range(1, max(level.values(), default=0) + 1):
        members = sorted(tid for tid, lv in level.items() if lv == n and kept(tid))
        if not members:
            continue
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
        "unresolved": {tid: sorted(set(ts)) for tid, ts in sorted(unresolved.items()) if kept(tid)},
        "blocked": {tid: ts for tid, ts in sorted(blocked.items()) if kept(tid)},
        "cyclic": sorted(tid for tid in cyclic | remaining if kept(tid)),
    }


def wave_levels(tasks: Iterable[MissionTask]) -> dict[str, int]:
    """Le numéro de vague de chaque tâche du ledger, closes comprises (W5-05).

    :func:`compute_waves` retire les tâches closes : une vague entièrement
    fusionnée disparaît de son rapport, alors que c'est elle qu'un humain doit
    valider. Ici le niveau d'une tâche est ``1 + max`` des niveaux de ses
    dépendances ``blocks`` (1 sans dépendance), closes comprises, comme
    ``compute_waves`` pour les vagues encore ouvertes. Les tâches annulées, en
    cycle ou qui attendent une dépendance inconnue ou annulée n'ont pas de niveau.
    """
    by_id = {t.id: t for t in tasks if t.status is not TaskState.CANCELLED}
    deps = {
        tid: [d.target for d in t.dependencies if d.kind is DependencyKind.BLOCKS] for tid, t in by_id.items()
    }
    level: dict[str, int] = {}
    todo = set(by_id)
    while True:
        ready = sorted(tid for tid in todo if all(d in level for d in deps[tid]))
        if not ready:
            return level
        for tid in ready:
            level[tid] = 1 + max((level[d] for d in deps[tid]), default=0)
        todo -= set(ready)
