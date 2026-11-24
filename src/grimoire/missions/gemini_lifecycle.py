"""La vie d'un worker Gemini CLI dans le tick : lancement et reprise (GAO-orchestrateu-001).

Le pendant de :mod:`.worker_lifecycle` (opencode) pour :class:`~.gemini_host.GeminiWorkerHost` : un processus que le
kit lance lui-même, suivi par son pid et son journal. Le battement, la fin et l'arrêt passent par les mêmes fonctions
que pour opencode (:func:`~.worker_lifecycle.renew_opencode`, :func:`~.worker_lifecycle.release_opencode`,
:func:`~.worker_lifecycle.halt_opencode`), qui ne demandent à l'hôte que ``state`` et ``stop``.

- **lancement** (:func:`launch_gemini`) : modèle (politique, sinon table palier x hôte ``dispatch.models.gemini``),
  plan, processus détaché, identité lue dans l'événement ``init``, inscription au journal d'autorité, rattachement ;
- **reprise** (:func:`resume_gemini_worker`) : ``gemini --resume <id>`` sur le même worktree, frein revérifié.

Le frein (refus ``BeforeTool`` prouvé pour la version exacte) est vérifié par le lanceur **avant** le claim, puis de
nouveau à chaque reprise : sans preuve, rien ne part et la raison est dite.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.gemini_host import GeminiWorkerHost, gemini_log, gemini_worktree_refusal
from grimoire.missions.launcher import (
    LAUNCHED_EVENT,
    WORKER_ACTOR,
    WORKER_RESUMED_EVENT,
    ExecResult,
    LaunchOutcome,
    WorkerLauncher,
    _seal_worker_for_dual_review,
    attest_session,
)
from grimoire.missions.worker_host import GEMINI_HOST, SessionSpec

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService
    from grimoire.missions.task_worktree import TaskWorktree

__all__ = ["gemini_tier_model", "launch_gemini", "resume_gemini_worker"]


def gemini_tier_model(root: Path, tier: str) -> str:
    """Le modèle Gemini du palier *tier* (table du projet, sinon défaut du kit) ; ``""`` si aucun."""
    from grimoire.hosts.model_table import load_model_table, resolve_model

    if not tier:
        return ""
    return resolve_model(tier, GEMINI_HOST, table=load_model_table(root)).model or ""


def _log_offset(log: Path) -> int:
    try:
        return log.stat().st_size
    except OSError:
        return 0


def launch_gemini(launcher: WorkerLauncher, task: MissionTask, wt: TaskWorktree, version: str) -> LaunchOutcome:
    """Lance le worker Gemini de *task* dans *wt* (claim posé, worktree préparé, frein prouvé pour *version*).

    Une tâche qui vise un chemin sensible part au palier ``strong`` de Gemini. Un palier sans modèle : la tâche
    attend (``deferred``) plutôt que de tourner en silence sur le modèle par défaut de l'hôte.
    """
    service, tid, role = launcher._service, task.id, launcher._policy.worker
    host = launcher._gemini
    sensitive = launcher.routing_for(task)
    tier = "strong" if sensitive.strong else launcher._tier_for(task)
    model = (
        gemini_tier_model(service.home_root, tier)
        if sensitive.strong
        else (role.model or gemini_tier_model(service.home_root, tier))
    )
    if tier and not model:
        return launcher._deferred(tid, f"aucun modèle Gemini pour le palier {tier} (table `dispatch.models.gemini`)")
    name = f"grimoire-{tid}"
    spec = SessionSpec(
        name=name,
        prompt=WorkerLauncher._prompt(tid, version, attached=True),
        model=model,
        permission_mode=role.permission_mode,
    )
    plan = host.plan(spec)
    if isinstance(plan, str):
        return launcher._deferred(tid, f"worker Gemini CLI : {plan}")
    log = gemini_log(service.home_root, tid)
    offset = _log_offset(log)
    started = host.start(plan, wt.path, log)
    if not started.pid:
        return launcher._failed(tid, f"gemini n'a pas démarré : {started.error or 'aucun processus'}")
    pid = str(started.pid)
    session_id = host.resolve(launcher._exec, wt.path, pid, name, log=log, since=offset)
    if not session_id:
        return launcher._abandon(
            tid,
            pid,
            wt.path,
            f"processus gemini {pid} lancé mais aucun événement init dans son flux : identité non résolue",
            count=True,
            host=host,
        )
    refused, code = attest_session(service, session_id, WORKER_ACTOR, tid)
    if refused:
        from grimoire.missions.mandate import CAP_EXCEEDED

        return launcher._abandon(tid, pid, wt.path, refused, count=code != CAP_EXCEEDED, host=host)
    _seal_worker_for_dual_review(service, tid, session_id, model, "", launcher._policy)
    # Aucune identité de session n'est exportée aux commandes (non vérifié) : le lanceur rattache la session lui-même.
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
            "model": model,
            "provider": "",
            "provider_refused": "",
            "host": GEMINI_HOST,
            "host_version": version,
            "pid": pid,
            "log": str(log),
            "log_offset": offset,
            "lean": False,
            "permission_mode": role.permission_mode,
            "cwd": str(wt.path),
            "branch": wt.branch,
            "lease_epoch": current.lease_epoch if current is not None else 0,
            **({"sensitive_route": sensitive.to_dict()} if sensitive.strong else {}),
        },
    )
    detail = f"session gemini {session_id} (pid {pid}) dans {wt.path}"
    if sensitive.strong:
        detail += f" — palier fort ({model}) : {sensitive.reason}"
    return LaunchOutcome(tid, "launched", detail, session_id, model)


def resume_gemini_worker(
    service: TaskService,
    exec_: Exec,
    task_id: str,
    launch: Mapping[str, Any],
    message: str,
    *,
    permission_mode: str = "",
    cause: str = "",
    host: GeminiWorkerHost | None = None,
) -> ExecResult:
    """Rend la tâche à son worker Gemini : ``gemini --resume <id>`` sur le même worktree.

    Le frein est revérifié pour la version installée à cet instant, et le hook du kit dans le worktree ; une reprise
    refusée ou qui ne démarre pas rend un échec lisible, comme une reprise Claude Code ratée.
    """
    host = host or GeminiWorkerHost()
    cwd = Path(str(launch.get("cwd") or "."))
    session_id = str(launch.get("session_id") or "")
    if not session_id:
        return ExecResult(1, "", "reprise gemini refusée : aucune session notée au lancement")
    version = host.version(exec_, cwd)
    if refusal := host.proof_refusal(version, service.project_root) or gemini_worktree_refusal(cwd):
        return ExecResult(1, "", f"reprise gemini refusée : {refusal}")
    plan = host.plan(
        SessionSpec(
            name=f"grimoire-{task_id}",
            prompt=message,
            model=str(launch.get("model") or ""),
            permission_mode=permission_mode or str(launch.get("permission_mode") or ""),
            resume=session_id,
        )
    )
    if isinstance(plan, str):
        return ExecResult(1, "", f"reprise gemini refusée : {plan}")
    log = gemini_log(service.home_root, task_id)
    offset = _log_offset(log)
    started = host.start(plan, cwd, log)
    if not started.pid:
        return ExecResult(1, "", f"gemini n'a pas démarré : {started.error or 'aucun processus'}")
    payload: dict[str, Any] = {
        "session": session_id,
        "fresh": False,
        "name": f"grimoire-{task_id}",
        "host": GEMINI_HOST,
        "host_version": version,
        "pid": str(started.pid),
        "log": str(log),
        "log_offset": offset,
    }
    if cause:
        payload["cause"] = cause
    service.ledger.append_event(WORKER_RESUMED_EVENT, task_id, "task", WORKER_ACTOR, payload)
    return ExecResult(0, str(started.pid), "")
