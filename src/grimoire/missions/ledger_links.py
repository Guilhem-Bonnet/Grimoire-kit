"""Retirer un lien de dépendance entre deux tâches (issue #906).

Constat du 2026-10-24 (refonte du cockpit) : ``GAO-p24-espace-001`` dépendait
de ``GAO-p14-provena-001``, annulée et remplacée par ``-002``, livrée et close.
Le claim exige des dépendances ``blocks`` closes ; ``cancelled`` est terminal
et aucune commande ne retirait un lien : le dépendant n'était plus réclamable
à vie.

Une dépendance annulée n'est **pas** tenue pour satisfaite : ce serait une
garde qui échoue ouvert, le travail qu'elle portait n'a pas été fait. On
retire le lien explicitement, avec un motif écrit dans le journal
(``task.unlinked``), puis on relie au remplaçant si besoin (``task link``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import DependencyKind, TaskDependency

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask


def unlink_tasks(
    ledger: MissionLedger,
    task_id: str,
    depends_on: str,
    kind: DependencyKind = DependencyKind.BLOCKS,
    *,
    reason: str,
    actor_id: str = "system",
) -> MissionTask:
    """Retire « *task_id* dépend de *depends_on* » ; refusé sans motif ou si le lien n'existe pas.

    Sous le verrou du journal, sur l'état relu, comme :meth:`MissionLedger.link_tasks`.
    Un lien absent est un refus et non un succès muet : une faute de frappe dans
    l'identifiant ne doit pas laisser croire que le blocage est levé.
    """
    from grimoire.missions.ledger import TASK_UNLINKED_EVENT, _ledger_file_lock

    motive = reason.strip()
    if not motive:
        raise GrimoireMissionError(f"Retrait refusé pour {task_id} : dites pourquoi le lien tombe (--reason)")
    with _ledger_file_lock(ledger._events_path):
        ledger._invalidate()
        ledger._load()
        task = ledger._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        if TaskDependency(kind, depends_on) not in task.dependencies:
            present = ", ".join(f"{d.target} ({d.kind.value})" for d in task.dependencies) or "aucune"
            raise GrimoireMissionError(
                f"Retrait refusé : {task_id} ne dépend pas de {depends_on} ({kind.value}) — dépendances : {present}"
            )
        ledger._append_event(
            TASK_UNLINKED_EVENT, task_id, "task", actor_id,
            {"task_id": task_id, "depends_on": depends_on, "kind": kind.value, "reason": motive}, lock_held=True,
        )
        ledger._load()
        return ledger._tasks[task_id]
