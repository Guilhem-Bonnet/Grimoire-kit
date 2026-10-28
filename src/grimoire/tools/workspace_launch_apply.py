"""Apply d'assignation (P47 lot 3) — lancer la session que le plan a montrée, jamais une autre.

Contrat plan→apply d'ADR-008. ``POST /api/workspace/tasks/<id>/launch`` porte
l'``empreinte`` du plan affiché (:func:`workspace_launch_preflight.
plan_fingerprint`) ; l'apply recalcule le préflight ici et maintenant, refuse
si l'empreinte diffère (l'état a changé depuis le plan), puis lance par
:class:`grimoire.missions.launcher.WorkerLauncher` — le même lanceur que le
tick, avec toutes ses gardes (rôle activé, disjoncteur, WIP, plafond du jour,
mandat), jamais une recomposition. Idempotent par tâche : une tâche déjà
lancée et encore vivante rend sa session existante, sans rien relancer.

Journal (ajout seul, :func:`workspace_write_guard.journal_event`) :
``apply_started`` puis ``apply_effect`` dans
``_grimoire-runtime-output/cockpit/launch-apply.jsonl`` du projet. Fail-closed
à l'entrée : un journal non inscriptible refuse le lancement avant tout effet ;
après l'effet, une trace qui échoue ne défait pas la session lancée, elle est
dite dans la réponse. Un refus est une réponse (``lance: false`` et sa
raison), jamais une exception — la garde d'écriture commune (ADR-008 D3) est
déjà passée côté serveur, comme pour toute écriture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.missions import launcher
from grimoire.missions.launcher import Exec, default_exec, launched_sessions
from grimoire.tools.workspace_launch_preflight import launch_preflight_full
from grimoire.tools.workspace_write_guard import journal_event

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

__all__ = ["launch_apply"]

#: Le journal de l'apply, dans le runtime du projet — à côté de ``write-guard.jsonl``.
_JOURNAL = ("_grimoire-runtime-output", "cockpit", "launch-apply.jsonl")


def _refus(task_id: str, raison: str, **extra: Any) -> dict[str, Any]:
    return {"task_id": task_id, "lance": False, "raison": raison, **extra}


def launch_apply(
    project_root: Path,
    task_id: str,
    body: dict[str, Any],
    *,
    now: datetime | None = None,
    exec_: Exec = default_exec,
    claude_config: Path | None = None,
    files: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Lance la session planifiée sur *task_id* si l'empreinte du corps est encore celle de l'état.

    *exec_* est le point d'entrée des processus externes du lanceur (``claude``),
    injectable comme dans :class:`WorkerLauncher`. Une tâche inconnue lève
    :class:`GrimoireMissionError` (404 côté transport), tout autre refus est
    une réponse ``lance: false`` avec sa raison.
    """
    # Le même *exec_* sonde gh au préflight et lance claude ensuite : un seul point d'entrée des processus.
    pre = launch_preflight_full(
        project_root, task_id, now=now, exec_=exec_, claude_config=claude_config, files=files,
    )
    service, task = pre.service, pre.task
    # Idempotence avant tout : une tâche déjà lancée et vivante rend sa session, rien n'est relancé.
    existing = launched_sessions(service).get(task.id)
    if existing is not None and task.status in launcher._LIVE:
        return _refus(
            task.id, "session déjà lancée pour cette tâche — rien n'est relancé",
            deja_lancee=True, session=str(existing.get("session", "")),
            session_id=str(existing.get("session_id", "")), worktree=str(existing.get("cwd", "")),
        )
    empreinte = str(body.get("empreinte") or "")
    if not empreinte:
        return _refus(task.id, "empreinte du plan requise : rouvre le panneau Assigner (ADR-008, le plan engage l'apply)")
    if empreinte != pre.payload["empreinte"]:
        return _refus(
            task.id, "l'état a changé depuis le plan montré — rouvre le panneau pour un plan à jour",
            empreinte=pre.payload["empreinte"],
        )
    if not pre.payload["pret"]:
        return _refus(task.id, f"plan pas prêt : {pre.payload['raison']}")
    if pre.policy.policy is None:
        return _refus(task.id, f"politique d'orchestration illisible : {pre.policy.error}")
    admit = next((a for a in pre.plan.admitted if a.task_id == task.id), None)
    if admit is None:
        return _refus(task.id, "la tâche n'est plus admise par le plan de file — rouvre le panneau")
    journal = project_root.resolve().joinpath(*_JOURNAL)
    try:
        journal_event(journal, {"event": "apply_started", "task_id": task.id, "empreinte": empreinte})
    except OSError as exc:
        # Fail-closed : pas de trace, pas d'effet — même doctrine que la garde d'écriture.
        return _refus(task.id, f"journal de l'apply non inscriptible ({journal}) : lancement refusé — {exc}")
    worker = launcher.WorkerLauncher(service, pre.policy.policy, exec_=exec_, now=now)
    outcome = worker((admit,))[0]
    note = ""
    try:
        journal_event(journal, {"event": "apply_effect", "task_id": task.id, **outcome.to_dict()})
    except OSError as exc:
        # L'effet a eu lieu : on ne le défait pas, on dit que sa trace manque.
        note = f" (trace apply_effect non écrite : {exc})"
    detail = outcome.detail + note
    return {
        "task_id": task.id,
        "lance": outcome.status == "launched",
        "statut": outcome.status,
        "raison": "" if outcome.status == "launched" else detail,
        "detail": detail,
        "session": outcome.session,
        "modele": outcome.model,
    }
