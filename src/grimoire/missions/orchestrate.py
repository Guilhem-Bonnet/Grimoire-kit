"""Le tick d'orchestration et ce qu'une pause en fait (K11, épopée #705, plan §4.4 et §4.5).

Le tick n'est pas résident : c'est une fonction qu'un déclencheur appelle (CLI, session, minuteur).
Il ne fait aucun appel de LLM. Sa première lecture est celle des drapeaux de pause (:mod:`.pause`),
**avant** d'ouvrir le ledger : sous une pause qui porte sur tout le périmètre, le tick sort sans avoir
chargé le ledger ni rien dispatché. Les échéances ``waiting`` continuent de courir (une date reste une date),
mais personne ne les rend tant que la pause dure ; la sortie le dit.

Une pause qui ne porte que sur des tâches nommées laisse le tick tourner : ces tâches sont différées à
l'admission et leurs attentes ``paused`` ne sont pas rendues, les autres suivent leur chemin.

Hors pause, le tick récolte les baux perdus, rend les attentes levées (dont les ``waiting(paused)`` dont la
pause a pris fin, dans l'ordre de la file), puis calcule le plan d'admission. Le dispatch est un point d'accroche
(*dispatcher*), appelé avec les seules tâches admises ; K12 y branche l'exécution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.pause import PauseState, home_root, read_pause

if TYPE_CHECKING:
    from grimoire.missions.admission import Admit, QueuePlan
    from grimoire.missions.service import TaskService, WakeDecision

__all__ = ["TickReport", "run_tick"]


@dataclass(frozen=True, slots=True)
class TickReport:
    """Ce qu'un tick a fait. ``status`` : ``paused`` (sorti sur un drapeau), ``idle`` (aucun ledger) ou ``ran``."""

    status: str
    pause: PauseState
    attention: tuple[str, ...] = ()
    woken: tuple[WakeDecision, ...] = ()
    reaped: tuple[Any, ...] = ()
    plan: QueuePlan | None = None
    dispatched: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pause": self.pause.to_dict(),
            "attention": list(self.attention),
            "notes": list(self.notes),
            "woken": [d.to_dict() for d in self.woken],
            "reaped": [d.to_dict() if hasattr(d, "to_dict") else str(d) for d in self.reaped],
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "dispatched": list(self.dispatched),
        }


def run_tick(
    project_root: Path,
    *,
    ledger_root: Path | None = None,
    apply: bool = True,
    now: datetime | None = None,
    service_factory: Callable[..., TaskService] | None = None,
    dispatcher: Callable[[tuple[Admit, ...]], None] | None = None,
    control_home: Path | None = None,
) -> TickReport:
    """Un tick. Sous une pause de tout le périmètre : les drapeaux sont lus, le ledger ne l'est pas, rien n'est dispatché.

    *service_factory* est appelé avec ``(project_root[, ledger_root])`` pour ouvrir le service : c'est ce qu'une
    pause évite. *dispatcher* reçoit les tâches admises, jamais sous pause et jamais à blanc (``apply=False``).
    """
    state = read_pause(home_root(project_root), control_home=control_home)
    if state.global_stop:
        return TickReport(
            "paused", state, state.attention(),
            notes=("les échéances waiting continuent de courir ; elles ne sont pas rendues tant que la pause dure",),
        )

    if service_factory is None:
        from grimoire.missions.service import TaskService

        service_factory = TaskService
    service = service_factory(project_root) if ledger_root is None else service_factory(project_root, ledger_root)
    if not service.has_ledger:
        return TickReport("idle", state, state.attention(), notes=("aucun ledger : rien à faire",))

    reaped = tuple(service.reap(apply=apply).decisions)
    woken = tuple(service.wake_due(apply=apply, now=now, pause=state))
    plan = service.queue_plan(now=now, pause=state)
    dispatched: tuple[str, ...] = ()
    if apply and dispatcher is not None and plan.admitted and not plan.capacity.errors:
        dispatcher(plan.admitted)
        dispatched = tuple(a.task_id for a in plan.admitted)
    notes = tuple(f"{t} : en pause ({state.mode_for(t)})" for t in sorted({tid for f in state.flags for tid in f.tasks}))
    return TickReport("ran", state, state.attention(), woken, reaped, plan, dispatched, notes)
