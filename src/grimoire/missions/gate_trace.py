"""Le refus d'un gate, journalisé au TraceLedger (hors de ``service.py``, sous cliquet de taille)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.gates import GateVerdict
    from grimoire.missions.schemas import MissionTask, TaskState


def record_refusal(project_root: Path, task: MissionTask, target: TaskState, verdict: GateVerdict, actor: str) -> None:
    """Journaliser un gate rouge dans le TraceLedger — le journal d'observabilité, pas la source.

    Le Mission Ledger ne reçoit rien : un refus n'est pas un changement
    d'état. Mais `grimoire task trace` doit pouvoir montrer *pourquoi* une
    tâche n'a pas avancé, et c'est ici que le gateway de hooks écrit déjà
    ses refus. Best-effort : un journal inaccessible ne bloque pas le refus.
    """
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        TraceLedger(project_root / TRACES_DIR).record(
            run_id=f"task-gate-{task.id}",
            workflow_instance_id="",
            mission_id=task.mission_id,
            task_id=task.id,
            recipe_id="grimoire.task-gate",
            outcome=TraceOutcome.FAILURE,
            started_at=datetime.now(UTC).isoformat(),
            agent_id=actor,
            policy_verdicts=[
                {
                    "verdict_id": refusal.evidence,
                    "action_kind": f"task.transition:{task.status.value}->{target.value}",
                    "verdict": "block",
                }
                for refusal in verdict.refusals
            ],
            error_count=len(verdict.refusals),
            tags=["task.gate", verdict.transition_id],
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix du refus lui-même
        pass
