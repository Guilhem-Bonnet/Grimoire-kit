"""La vie d'un worker opencode dans le tick : lancement, battement, fin, arrêt, reprise (épique #1281, lot L4).

Le lanceur Claude Code lit ses sessions dans ``claude agents --json`` ; un worker opencode est un processus que le kit
a lancé lui-même (:class:`~grimoire.missions.worker_host.OpencodeWorkerHost`). Ce module en est le pendant, appelé
par :mod:`.launcher` et :mod:`.janitor` quand le lancement noté au ledger porte ``host: opencode`` :

- **lancement** (:func:`launch_opencode`) : route (fournisseur, modèle, clé), plan, garde du kit vérifiée, processus
  détaché, identité lue dans ``opencode session list``, inscription au journal d'autorité, rattachement au claim ;
- **battement** : un processus vivant dont le journal a parlé depuis moins de
  :data:`~grimoire.missions.worker_host.SILENCE_S` renouvelle le bail ; muet, il ne le renouvelle plus ;
- **fin** : un processus mort rend la tâche (:func:`~grimoire.missions.ended.release_ended`) ;
- **arrêt** : ``SIGTERM`` au groupe du processus ; la conversation reste dans opencode ;
- **reprise** : ``opencode run --session <id>`` sur le même worktree.

La reprise automatique après une erreur transitoire du fournisseur (#1158) lit le journal de Claude Code : elle ne
s'applique pas à un worker opencode, dont la session finie rend la tâche à la récolte.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.launcher import (
    LAUNCHED_EVENT,
    WORKER_ACTOR,
    WORKER_HALTED_EVENT,
    WORKER_RESUMED_EVENT,
    ExecResult,
    LaunchOutcome,
    WorkerLauncher,
    _seal_worker_for_dual_review,
    attest_session,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.worker_guard import new_opencode_home, pre_start_refusal, prepare_opencode_home
from grimoire.missions.worker_host import OPENCODE_HOST, OpencodeWorkerHost, SessionSpec, opencode_route, worker_log

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService
    from grimoire.missions.task_worktree import TaskWorktree

#: Un refus de la garde avant le lancement d'un worker opencode (motif fixe, jamais une sortie de programme).
GUARD_REFUSED_EVENT = "task.worker_guard_refused"
#: Refus de garde de suite avant que la tâche ne soit garée chez l'humain.
MAX_GUARD_REFUSALS = 3

__all__ = [
    "GUARD_REFUSED_EVENT",
    "MAX_GUARD_REFUSALS",
    "guard_refusals",
    "halt_opencode",
    "is_opencode",
    "launch_opencode",
    "opencode_marks",
    "release_opencode",
    "renew_opencode",
    "resume_opencode_worker",
]


def is_opencode(launch: Mapping[str, Any]) -> bool:
    return launch.get("host") == OPENCODE_HOST


def launch_opencode(launcher: WorkerLauncher, task: MissionTask, wt: TaskWorktree) -> LaunchOutcome:
    """Lance le worker opencode de *task* dans *wt* (claim posé, worktree préparé) ; mêmes issues que Claude Code.

    Pas de route (fournisseur fermé, clé absente, aucun modèle) : la tâche attend (``deferred``), elle ne retombe pas
    en silence sur Claude Code. Garde du kit non prouvée : rien n'est lancé (``skipped``, motif au ledger).
    """
    service, tid, role = launcher._service, task.id, launcher._policy.worker
    host = launcher._opencode
    route, why = opencode_route(
        service.home_root,
        role.provider,
        launcher._tier_for(task),
        task,
        model=role.model,
        busy_providers=launcher._busy_providers(),
        now=launcher._now,
    )
    if route is None:
        return launcher._deferred(tid, f"worker opencode sans route : {why}")
    name = f"grimoire-{tid}"
    # Un HOME du kit neuf pour ce seul lancement ; la garde passe avant toute commande opencode.
    kit_home = new_opencode_home(service.home_root, tid)
    if guard := prepare_opencode_home(kit_home, fresh=True) or host.guard_refusal(wt.path, route, kit_home=kit_home):
        return _guard_refused(launcher, task, guard)
    version = host.version(launcher._exec, wt.path, kit_home=kit_home)
    spec = SessionSpec(
        name=name,
        prompt=WorkerLauncher._prompt(tid, version, attached=True),
        model=route.qualified_model,
        permission_mode=role.permission_mode,
        lean=role.lean,
        opencode=route,
        kit_home=kit_home,
    )
    plan = host.plan(spec)
    if isinstance(plan, str):
        return launcher._deferred(tid, f"worker opencode : {plan}")
    if refusal := pre_start_refusal(wt.path, kit_home):  # rien n'a changé depuis la garde, juste avant le lancement
        return _guard_refused(launcher, task, refusal)
    log = worker_log(service.home_root, tid)
    started = host.start(plan, wt.path, log)
    if not started.pid:
        return launcher._failed(tid, f"opencode run n'a pas démarré : {started.error or 'aucun processus'}")
    pid = str(started.pid)
    session_id = host.resolve(launcher._exec, wt.path, pid, name, kit_home=kit_home)
    if not session_id:
        return launcher._abandon(
            tid,
            pid,
            wt.path,
            f"processus opencode {pid} lancé mais session {name} introuvable "
            "dans `opencode session list` : identité non résolue",
            count=True,
            host=host,
        )
    refused, code = attest_session(service, session_id, WORKER_ACTOR, tid)
    if refused:
        from grimoire.missions.mandate import CAP_EXCEEDED

        return launcher._abandon(tid, pid, wt.path, refused, count=code != CAP_EXCEEDED, host=host)
    _seal_worker_for_dual_review(service, tid, session_id, route.model, route.provider, launcher._policy)
    # opencode n'exporte pas d'identité aux commandes qu'il lance : le lanceur rattache la session lui-même.
    with contextlib.suppress(GrimoireError):
        service.attach_session(
            tid, session_id, session_host=host.session_host, actor=WORKER_ACTOR, host_version=version
        )
    current = service.require(tid).claim
    service.ledger.append_event(
        LAUNCHED_EVENT,
        tid,
        "task",
        WORKER_ACTOR,
        {
            "session": session_id,
            "session_id": session_id,
            "name": name,
            "model": route.model,
            "provider": route.provider,
            "provider_refused": "",
            "host": OPENCODE_HOST,
            "pid": pid,
            "log": str(log),
            "kit_home": str(kit_home),
            "model_source": route.source,
            "lean": role.lean,
            "permission_mode": role.permission_mode,
            "cwd": str(wt.path),
            "branch": wt.branch,
            "lease_epoch": current.lease_epoch if current is not None else 0,
        },
    )
    return LaunchOutcome(
        tid, "launched", f"session opencode {session_id} (pid {pid}) dans {wt.path}", session_id, route.model
    )


def guard_refusals(service: TaskService, task_id: str) -> int:
    """Les refus de garde consécutifs de la tâche depuis son dernier lancement ou sa dernière reprise."""
    count = 0
    for event in reversed(service.ledger.events_for(task_id)):
        if event.event_type in (LAUNCHED_EVENT, WORKER_RESUMED_EVENT):
            break
        if event.event_type == GUARD_REFUSED_EVENT:
            count += 1
    return count


def _guard_refused(launcher: WorkerLauncher, task: MissionTask, guard: str) -> LaunchOutcome:
    """Garde non prouvée : la tâche revient ``ready`` ; au :data:`MAX_GUARD_REFUSALS`-ième refus de suite, elle est
    garée chez l'humain avec le motif, plutôt que de revenir en file à chaque tick."""
    service, tid = launcher._service, task.id
    detail = f"worker opencode non lancé : {guard}"
    tried = guard_refusals(service, tid) + 1
    service.ledger.append_event(GUARD_REFUSED_EVENT, tid, "task", WORKER_ACTOR, {"reason": guard, "attempt": tried})
    claim = service.require(tid).claim
    if tried >= MAX_GUARD_REFUSALS and claim is not None:
        question = (
            f"La garde du kit a refusé {tried} fois de suite le worker opencode de {tid} ({guard}). "
            "Vérifier le worktree et relancer, ou abandonner ?"
        )
        try:
            service.ask(
                tid,
                question,
                "tick",
                kind="retry_exhausted",
                origin="tick",
                options=("réessayer", "abandonner"),
                recommended="réessayer",
                epoch=claim.lease_epoch,
            )
        except GrimoireError:
            pass
        else:
            return LaunchOutcome(tid, "skipped", f"{detail} — tâche garée après {tried} refus")
    service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=detail)
    return LaunchOutcome(tid, "skipped", detail)


def opencode_marks(service: TaskService, task_id: str) -> dict[str, Any]:
    """Le dernier lancement ou la dernière reprise opencode de la tâche (``pid``, ``log``, ``session_id``…)."""
    found: dict[str, Any] = {}
    for event in service.ledger.events_for(task_id):
        if event.event_type in (LAUNCHED_EVENT, WORKER_RESUMED_EVENT) and event.payload.get("pid"):
            found = {**found, **dict(event.payload)}
    return found


def renew_opencode(
    service: TaskService,
    held: Mapping[str, int],
    host: OpencodeWorkerHost,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Renouvelle le bail des tâches tenues (*held* : id → epoch) dont le worker opencode travaille encore."""
    renewed: list[str] = []
    for tid, epoch in held.items():
        if host.state(opencode_marks(service, tid)) != "busy":
            continue  # mort ou muet : la récolte juge comme avant
        try:
            service.ledger.renew_lease(tid, epoch, actor_id="tick", now=now)
        except GrimoireError:
            continue
        renewed.append(tid)
    return renewed


def release_opencode(
    service: TaskService,
    held: Mapping[str, int],
    host: OpencodeWorkerHost,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Rend ``ready`` les tâches tenues (*held* : id → epoch) dont le processus opencode est mort."""
    from grimoire.missions.ended import release_ended

    released: list[str] = []
    for tid, epoch in held.items():
        if host.state(opencode_marks(service, tid)) != "ended":
            continue
        try:
            decision = release_ended(service.ledger, tid, epoch, actor_id="tick", now=now)
        except GrimoireError:
            continue
        if decision is not None:
            released.append(tid)
    return released


def halt_opencode(service: TaskService, task_id: str, status: str, host: OpencodeWorkerHost) -> bool:
    """Arrête le worker opencode d'une tâche en revue ou en attente ; vrai si l'arrêt est noté au ledger."""
    marks = opencode_marks(service, task_id)
    pid = str(marks.get("pid") or "")
    gone = host.state(marks) == "ended"
    if not gone and not host.stop(pid):
        return False
    service.ledger.append_event(
        WORKER_HALTED_EVENT,
        task_id,
        "task",
        WORKER_ACTOR,
        {"session": str(marks.get("session_id") or ""), "pid": pid, "status": status, "already_gone": gone},
    )
    return True


def resume_opencode_worker(
    service: TaskService,
    exec_: Exec,
    task_id: str,
    launch: Mapping[str, Any],
    message: str,
    *,
    permission_mode: str = "",
    cause: str = "",
    host: OpencodeWorkerHost | None = None,
) -> ExecResult:
    """Rend la tâche à son worker opencode : ``opencode run --session <id>`` sur le même worktree.

    La route est recalculée (la clé n'est jamais notée au ledger) et la garde du kit revérifiée ; une reprise
    refusée ou qui ne démarre pas rend un échec lisible, comme une reprise Claude Code ratée.
    """
    host = host or OpencodeWorkerHost()
    cwd = Path(str(launch.get("cwd") or "."))
    task = service.require(task_id)
    route, why = opencode_route(
        service.home_root, str(launch.get("provider") or ""), "", task, model=str(launch.get("model") or "")
    )
    if route is None:
        return ExecResult(1, "", f"reprise opencode impossible : {why}")
    if not launch.get("kit_home"):
        return ExecResult(1, "", "reprise opencode refusée : lancement sans HOME du kit noté")
    kit_home = Path(str(launch["kit_home"]))  # celui de ce worker : sa base de sessions y vit
    if guard := prepare_opencode_home(kit_home) or host.guard_refusal(cwd, route, kit_home=kit_home):
        return ExecResult(1, "", f"reprise opencode refusée : {guard}")
    plan = host.plan(
        SessionSpec(
            name=f"grimoire-{task_id}",
            prompt=message,
            model=route.qualified_model,
            permission_mode=permission_mode or str(launch.get("permission_mode") or ""),
            resume=str(launch.get("session_id") or ""),
            opencode=route,
            kit_home=kit_home,
        )
    )
    if isinstance(plan, str):
        return ExecResult(1, "", f"reprise opencode refusée : {plan}")
    if refusal := pre_start_refusal(cwd, kit_home):
        return ExecResult(1, "", f"reprise opencode refusée : {refusal}")
    started = host.start(plan, cwd, worker_log(service.home_root, task_id))
    if not started.pid:
        return ExecResult(1, "", f"opencode run n'a pas démarré : {started.error or 'aucun processus'}")
    payload: dict[str, Any] = {
        "session": str(launch.get("session_id") or ""),
        "fresh": False,
        "name": f"grimoire-{task_id}",
        "host": OPENCODE_HOST,
        "pid": str(started.pid),
        "kit_home": str(kit_home),
    }
    if cause:
        payload["cause"] = cause
    service.ledger.append_event(WORKER_RESUMED_EVENT, task_id, "task", WORKER_ACTOR, payload)
    return ExecResult(0, str(started.pid), "")
