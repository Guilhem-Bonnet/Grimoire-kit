"""Service des tâches — la logique que le CLI et le serveur MCP partagent.

``grimoire task`` (issue #137) portait seul l'enchaînement « machine à états,
puis gate de preuve, puis écriture au ledger ». Exposer les mêmes gestes aux
agents par MCP (issue #138) exigeait soit de le recopier, soit de le sortir du
CLI. Il est ici, et les deux surfaces l'appellent : un gate contourné par l'une
le serait par l'autre, donc il n'y a qu'un endroit où il peut l'être.

Deux règles :

- **Le gate précède l'écriture.** Un refus après l'append laisserait dans un
  journal qui ne se réécrit pas un événement que rien ne justifie.
- **Le board suit le ledger.** Chaque écriture reprojette
  ``_grimoire/standard/task-board.yaml`` quand le projet est enrôlé. C'est ce
  qui rend un claim visible au hook SessionStart sans qu'un humain ait à
  relancer ``task board export``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError, GrimoireMissionError
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.core.standard_state import invalidate_cache
from grimoire.missions.board import board_status_of, build_board, write_board
from grimoire.missions.gates import GateRefusal, GateVerdict, check_transition
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.recall import DEFAULT_TOKEN_BUDGET, TaskRecall, build_task_recall, consolidate_task_memory
from grimoire.missions.schemas import MissionTask, TaskClaim, TaskState

if TYPE_CHECKING:
    from grimoire.core.agentic_standard import StandardRuntimeArtifact
    from grimoire.memory.manager import MemoryManager

__all__ = [
    "DEFAULT_LEDGER_RELPATH",
    "TaskMove",
    "TaskRefusedError",
    "TaskService",
]

#: Là où le ledger vit par défaut, relativement à la racine du projet.
DEFAULT_LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: Le board du standard, projection du ledger.
BOARD_RELPATH = STANDARD_DIR / "task-board.yaml"


class TaskRefusedError(GrimoireMissionError):
    """Le gate de preuve refuse la transition. Porte le verdict, pour qu'un
    appelant puisse le rendre tel quel — le refus nomme la preuve et le remède."""

    def __init__(self, task_id: str, verdict: GateVerdict) -> None:
        self.task_id = task_id
        self.verdict = verdict
        manque = "; ".join(str(r) for r in verdict.refusals)
        super().__init__(f"Gate « {verdict.transition_id} » refuse {task_id} : {manque}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": True,
            "task_id": self.task_id,
            "transition": self.verdict.transition_id,
            "strictness": self.verdict.strictness,
            "refusals": [
                {"evidence": r.evidence, "reason": r.reason, "remedy": r.remedy}
                for r in self.verdict.refusals
            ],
        }


@dataclass(frozen=True, slots=True)
class TaskMove:
    """Ce qu'une écriture a produit : la tâche après coup, d'où elle venait, ce
    que le gate a signalé sans bloquer, et le board reprojeté s'il l'a été."""

    task: MissionTask
    previous: TaskState
    verdict: GateVerdict
    board_path: Path | None

    @property
    def advisories(self) -> tuple[str, ...]:
        """Exigences non satisfaites qu'un profil permissif a laissé passer."""
        return tuple(str(r) for r in self.verdict.refusals)

    def to_dict(self) -> dict[str, Any]:
        data = self.task.to_dict()
        data["transition"] = f"{self.previous.value} → {self.task.status.value}"
        data["board"] = board_status_of(self.task.status)
        if self.advisories:
            data["advisories"] = list(self.advisories)
        if self.board_path is not None:
            data["board_path"] = str(self.board_path)
        return data


class TaskService:
    """Lire, réclamer et déplacer les tâches d'un projet, gates compris."""

    def __init__(
        self,
        project_root: Path,
        ledger_root: Path = DEFAULT_LEDGER_RELPATH,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.ledger_root = ledger_root if ledger_root.is_absolute() else self.project_root / ledger_root
        self._ledger: MissionLedger | None = None
        # ``None`` explicite (mémoire injectée sans backend) et « pas encore
        # résolue » sont deux états différents : sans ce booléon, un manager
        # None passé au constructeur serait re-résolu depuis la config à
        # chaque appel.
        self._memory: MemoryManager | None = memory_manager
        self._memory_resolved = memory_manager is not None

    @property
    def ledger(self) -> MissionLedger:
        if self._ledger is None:
            self._ledger = MissionLedger(self.ledger_root)
        return self._ledger

    @property
    def has_ledger(self) -> bool:
        return (self.ledger_root / "events.jsonl").is_file()

    @property
    def memory(self) -> MemoryManager | None:
        """Le manager mémoire du projet, résolu au premier accès — ``None`` sans config.

        Un projet sans ``project-context.yaml`` n'a pas de mémoire à consulter :
        le rappel et la consolidation se dégradent alors à ce que le ledger
        seul sait, plutôt que d'échouer.
        """
        if not self._memory_resolved:
            self._memory = self._load_memory()
            self._memory_resolved = True
        return self._memory

    def _load_memory(self) -> MemoryManager | None:
        config_path = self.project_root / "project-context.yaml"
        if not config_path.is_file():
            return None
        try:
            from grimoire.core.config import GrimoireConfig
            from grimoire.memory.manager import MemoryManager as _MemoryManager

            config = GrimoireConfig.from_yaml(config_path)
            return _MemoryManager.from_config(config, project_root=self.project_root)
        except Exception:  # la mémoire est une couche optionnelle du service
            return None

    # ── Lecture ────────────────────────────────────────────────────────────

    def require(self, task_id: str) -> MissionTask:
        task = self.ledger.get_task(task_id)
        if task is None:
            raise GrimoireMissionError(f"Tâche inconnue : {task_id}")
        return task

    def list_tasks(self, mission_id: str | None = None, status: str | None = None) -> list[MissionTask]:
        tasks = self.ledger.list_tasks(mission_id)
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        return tasks

    def list_ready(self, mission_id: str | None = None) -> list[MissionTask]:
        """Les tâches qu'un agent peut réclamer maintenant."""
        return self.list_tasks(mission_id, TaskState.READY.value)

    def gate(self, task: MissionTask, target: TaskState) -> GateVerdict:
        """Le verdict du gate pour cette transition, sans rien écrire."""
        return check_transition(self.project_root, task, board_status_of(task.status), board_status_of(target))

    # ── Écriture ───────────────────────────────────────────────────────────

    def claim(
        self, task_id: str, actor: str, host: str = "local", files: tuple[str, ...] = ()
    ) -> TaskMove:
        """ready → claimed, si le gate `ready_to_in_progress` l'accorde et si aucun
        autre claim actif ne réserve déjà l'un de *files*."""
        claim = TaskClaim.new(actor_id=actor, host_id=host, exclusive_files=files)
        return self.transition(task_id, TaskState.CLAIMED, actor, claim=claim)

    def transition(
        self,
        task_id: str,
        target: TaskState,
        actor: str,
        reason: str = "",
        *,
        claim: TaskClaim | None = None,
    ) -> TaskMove:
        """Déplace une tâche : machine à états, verrou de fichiers, gate, puis ledger, puis board.

        Lève :class:`TaskRefusedError` sur un verrou de fichiers ou un gate bloquant,
        :class:`GrimoireMissionError` sur une tâche inconnue ou une transition que la
        machine à états refuse. Dans tous les cas, rien n'a été écrit.
        """
        task = self.require(task_id)
        if claim is not None:
            self._check_exclusive_files(task, target, claim)
        verdict = self.gate(task, target)
        if verdict.blocked:
            self._record_refusal(task, target, verdict, actor)
            raise TaskRefusedError(task_id, verdict)
        moved = self.ledger.transition_task(task_id, target, actor_id=actor, reason=reason, claim=claim)
        self._consolidate(moved, target, actor=actor, reason=reason)
        return TaskMove(task=moved, previous=task.status, verdict=verdict, board_path=self.project_board())

    def _check_exclusive_files(self, task: MissionTask, target: TaskState, claim: TaskClaim) -> None:
        """Refuse un claim dont un fichier exclusif est déjà réservé ailleurs.

        Une autre tâche CLAIMED ou RUNNING (« in_progress » au board) dont le
        claim n'a pas expiré et réserve l'un des mêmes fichiers bloque celui-ci —
        deux agents n'écrivent jamais le même fichier en même temps. Un claim
        expiré ne bloque plus personne : le temps a suffi, sans geste humain.
        """
        if not claim.exclusive_files:
            return
        requested = set(claim.exclusive_files)
        for other in self.ledger.list_tasks():
            if other.id == task.id or other.status not in (TaskState.CLAIMED, TaskState.RUNNING):
                continue
            held = other.claim
            if held is None or not held.exclusive_files or held.is_expired():
                continue
            overlap = requested & set(held.exclusive_files)
            if not overlap:
                continue
            fichier = sorted(overlap)[0]
            refusal = GateRefusal(
                evidence=fichier,
                reason=f"réservé par {other.id} ({held.actor_id}) jusqu'à {held.expires_at or 'sans expiration'}",
                remedy="attendre l'expiration du claim ou réclamer une autre tâche",
            )
            verdict = GateVerdict(transition_id="exclusive_files", strictness="hard_fail", refusals=(refusal,))
            self._record_refusal(task, target, verdict, claim.actor_id)
            raise TaskRefusedError(task.id, verdict)

    def _consolidate(self, task: MissionTask, target: TaskState, *, actor: str, reason: str) -> None:
        """Clôture ou blocage : ce que la roadmap Memory OS autorise à écrire.

        Rien d'autre n'appelle :func:`consolidate_task_memory` — c'est le seul
        point d'écriture, comme :meth:`project_board` l'est pour le board.
        Best-effort : la transition est déjà au ledger, qui est la source ;
        une mémoire indisponible n'y change rien.
        """
        if target not in (TaskState.CLOSED, TaskState.BLOCKED, TaskState.FAILED):
            return
        # best-effort : la transition est déjà au ledger, qui est la source ;
        # une mémoire indisponible n'y change rien.
        with contextlib.suppress(Exception):
            consolidate_task_memory(self.memory, self.ledger, task, target, actor=actor, reason=reason)

    def _record_refusal(self, task: MissionTask, target: TaskState, verdict: GateVerdict, actor: str) -> None:
        """Journaliser un gate rouge dans le TraceLedger — le journal d'observabilité, pas la source.

        Le Mission Ledger ne reçoit rien : un refus n'est pas un changement
        d'état. Mais `grimoire task trace` doit pouvoir montrer *pourquoi* une
        tâche n'a pas avancé, et c'est ici que le gateway de hooks écrit déjà
        ses refus. Best-effort : un journal inaccessible ne bloque pas le refus.
        """
        try:
            from datetime import UTC, datetime

            from grimoire.core.standard_generation import TRACES_DIR
            from grimoire.traces.ledger import TraceLedger
            from grimoire.traces.schemas import TraceOutcome

            TraceLedger(self.project_root / TRACES_DIR).record(
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

    def project_board(self) -> Path | None:
        """Reprojette le board du standard depuis le ledger, si le projet est enrôlé.

        Un projet sans ``_grimoire/standard/`` n'a pas de board à tenir à jour ;
        on n'en crée pas un pour lui. L'échec d'écriture n'annule pas la
        transition — elle est déjà au ledger, qui est la source — mais il ne
        doit pas non plus passer pour un succès : on rend ``None``.
        """
        if not (self.project_root / STANDARD_DIR).is_dir():
            return None
        dest = self.project_root / BOARD_RELPATH
        try:
            write_board(dest, build_board(self.ledger, project=self.project_root.name))
        except (OSError, GrimoireError):
            return None
        # Le hook lit la tâche ``in_progress`` du board via un cache dérivé,
        # invalidé par empreinte fichier (grimoire.core.standard_state) ; sans
        # cet appel, la reprojection ne serait visible qu'au hook *suivant*.
        invalidate_cache(self.project_root)
        return dest

    # ── Contexte ───────────────────────────────────────────────────────────

    def context(self, task_id: str) -> StandardRuntimeArtifact:
        """Le context bundle d'une tâche réelle — une tâche inconnue est refusée avant tout calcul."""
        from grimoire.core.agentic_standard import build_context_bundle

        self.require(task_id)
        return build_context_bundle(self.project_root, task_id=task_id)

    def recall(self, task_id: str, *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> TaskRecall:
        """Ce que le projet sait de *task_id* et de ses voisines — le même rappel partout.

        Une tâche inconnue est refusée avant tout calcul, comme :meth:`context`.
        """
        self.require(task_id)
        return build_task_recall(
            self.project_root, task_id,
            ledger_root=self.ledger_root, memory_manager=self.memory, token_budget=token_budget,
        )
