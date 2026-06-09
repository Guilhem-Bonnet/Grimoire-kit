"""Cockpit projections — read models built from MissionLedger, RuntimeKernel, and EvidenceService.

Projections are computed views; they never mutate state.
All data comes from the canonical sources (ledger, kernel, evidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, TaskState
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import WorkflowStatus

if TYPE_CHECKING:
    from grimoire.evidence.service import EvidenceService

__all__ = [
    "CockpitProjection",
    "CockpitProjectionBuilder",
    "EvidenceProjection",
    "IncidentProjection",
    "MemoryProjection",
    "MissionProjection",
    "PackProjection",
    "PolicyProjection",
    "TaskProjection",
    "VerdictProjection",
    "WorkflowProjection",
]


# ── I2 — Evidence & Verdict projections ──────────────────────────────────────

@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    pack_id: str
    task_id: str
    mission_id: str
    item_count: int
    profile: str
    created_at: str
    workflow_instance_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "item_count": self.item_count,
            "profile": self.profile,
            "workflow_instance_id": self.workflow_instance_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class VerdictProjection:
    verdict_id: str
    task_id: str
    evidence_pack_id: str
    verdict: str
    profile: str
    created_at: str
    decision_close: bool = False
    decision_reopen: bool = False
    decision_incident: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "task_id": self.task_id,
            "evidence_pack_id": self.evidence_pack_id,
            "verdict": self.verdict,
            "profile": self.profile,
            "decision_close": self.decision_close,
            "decision_reopen": self.decision_reopen,
            "decision_incident": self.decision_incident,
            "created_at": self.created_at,
        }


# ── I3 — Incident projections ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class IncidentProjection:
    incident_id: str
    mission_id: str
    task_id: str
    kind: str
    severity: str
    status: str
    summary: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "severity": self.severity,
            "status": self.status,
            "summary": self.summary,
            "created_at": self.created_at,
        }


# ── §9.10 — Policy / Memory / Pack projections ───────────────────────────────

@dataclass(frozen=True, slots=True)
class PolicyProjection:
    """Read model for the policy engine state at a point in time."""
    active_rule_count: int
    block_rate: float
    last_block_reason: str = ""
    total_evaluated: int = 0
    total_blocked: int = 0
    total_warned: int = 0
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_rule_count": self.active_rule_count,
            "block_rate": self.block_rate,
            "last_block_reason": self.last_block_reason,
            "total_evaluated": self.total_evaluated,
            "total_blocked": self.total_blocked,
            "total_warned": self.total_warned,
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryProjection:
    """Read model for the Memory OS state at a point in time."""
    layer_count: int
    total_item_count: int
    promotion_queue_size: int = 0
    recall_count_7d: int = 0
    promotion_count_7d: int = 0
    backend: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_count": self.layer_count,
            "total_item_count": self.total_item_count,
            "promotion_queue_size": self.promotion_queue_size,
            "recall_count_7d": self.recall_count_7d,
            "promotion_count_7d": self.promotion_count_7d,
            "backend": self.backend,
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True, slots=True)
class PackProjection:
    """Read model for a single pack's registry and activation state."""
    pack_name: str
    version: str
    status: str
    distribution: str
    activation_mode: str
    trust_tier: str = ""
    component_count: int = 0
    verified: bool = False
    installable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_name": self.pack_name,
            "version": self.version,
            "status": self.status,
            "distribution": self.distribution,
            "activation_mode": self.activation_mode,
            "trust_tier": self.trust_tier,
            "component_count": self.component_count,
            "verified": self.verified,
            "installable": self.installable,
        }


# ── Task / Mission / Workflow projections ─────────────────────────────────────

@dataclass(frozen=True, slots=True)
class TaskProjection:
    task_id: str
    title: str
    status: str
    task_type: str
    risk_profile: str
    mission_id: str
    claimed_by: str = ""
    workflow_instance_id: str = ""
    evidence_pack_id: str = ""
    open_incident_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "task_type": self.task_type,
            "risk_profile": self.risk_profile,
            "mission_id": self.mission_id,
            "claimed_by": self.claimed_by,
            "workflow_instance_id": self.workflow_instance_id,
            "evidence_pack_id": self.evidence_pack_id,
            "open_incident_ids": list(self.open_incident_ids),
        }


@dataclass(frozen=True, slots=True)
class MissionProjection:
    mission_id: str
    title: str
    status: str
    risk_profile: str
    task_count: int = 0
    ready_count: int = 0
    running_count: int = 0
    blocked_count: int = 0
    needs_verification_count: int = 0
    closed_count: int = 0
    cancelled_count: int = 0
    open_incident_ids: tuple[str, ...] = ()
    active_workflow_instance_ids: tuple[str, ...] = ()
    tasks: tuple[TaskProjection, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "title": self.title,
            "status": self.status,
            "risk_profile": self.risk_profile,
            "task_count": self.task_count,
            "ready_count": self.ready_count,
            "running_count": self.running_count,
            "blocked_count": self.blocked_count,
            "needs_verification_count": self.needs_verification_count,
            "closed_count": self.closed_count,
            "cancelled_count": self.cancelled_count,
            "open_incident_ids": list(self.open_incident_ids),
            "active_workflow_instance_ids": list(self.active_workflow_instance_ids),
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass(frozen=True, slots=True)
class WorkflowProjection:
    wfi_id: str
    recipe_id: str
    recipe_version: str
    status: str
    mission_id: str
    task_id: str
    completed_steps: tuple[str, ...]
    pending_steps: tuple[str, ...]
    checkpoint_count: int = 0
    event_count: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "wfi_id": self.wfi_id,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "status": self.status,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "completed_steps": list(self.completed_steps),
            "pending_steps": list(self.pending_steps),
            "checkpoint_count": self.checkpoint_count,
            "event_count": self.event_count,
            "created_at": self.created_at,
        }


# ── Cockpit ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CockpitProjection:
    missions: tuple[MissionProjection, ...]
    active_workflows: tuple[WorkflowProjection, ...]
    verification_queue: tuple[str, ...]
    total_incident_count: int = 0
    open_mission_count: int = 0
    generated_at: str = ""
    # I2 — evidence & verdicts
    evidence_packs: tuple[EvidenceProjection, ...] = ()
    verdicts: tuple[VerdictProjection, ...] = ()
    # I3 — rich incident view
    incidents: tuple[IncidentProjection, ...] = ()
    # §9.10 — policy / memory / pack projections
    policy: PolicyProjection | None = None
    memory: MemoryProjection | None = None
    packs: tuple[PackProjection, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "open_mission_count": self.open_mission_count,
            "total_incident_count": self.total_incident_count,
            "verification_queue": list(self.verification_queue),
            "missions": [m.to_dict() for m in self.missions],
            "active_workflows": [w.to_dict() for w in self.active_workflows],
            "evidence_packs": [e.to_dict() for e in self.evidence_packs],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "incidents": [i.to_dict() for i in self.incidents],
            "policy": self.policy.to_dict() if self.policy is not None else None,
            "memory": self.memory.to_dict() if self.memory is not None else None,
            "packs": [p.to_dict() for p in self.packs],
        }


# ── Builder ───────────────────────────────────────────────────────────────────

class CockpitProjectionBuilder:
    """Build cockpit projections from MissionLedger, RuntimeKernel, and optionally EvidenceService.

    Usage::

        builder = CockpitProjectionBuilder(
            ledger=MissionLedger(Path("...")),
            kernel=RuntimeKernel(Path("...")),
            evidence=EvidenceService(Path("...")),   # optional
        )
        cockpit = builder.build()
    """

    def __init__(
        self,
        ledger: MissionLedger,
        kernel: RuntimeKernel,
        evidence: EvidenceService | None = None,
    ) -> None:
        self._ledger = ledger
        self._kernel = kernel
        self._evidence = evidence

    def build(self, mission_id: str | None = None) -> CockpitProjection:
        missions_raw = self._ledger.list_missions()
        if mission_id:
            missions_raw = [m for m in missions_raw if m.id == mission_id]

        mission_projections: list[MissionProjection] = []
        all_verification_queue: list[str] = []
        all_evidence_packs: list[EvidenceProjection] = []
        all_verdicts: list[VerdictProjection] = []
        all_incidents: list[IncidentProjection] = []
        total_incidents = 0

        for mission in missions_raw:
            tasks = self._ledger.list_tasks(mission.id)
            incidents_open = [i for i in self._ledger.open_incidents() if i.mission_id == mission.id]

            # I3 — build rich incident projections
            for inc in incidents_open:
                all_incidents.append(IncidentProjection(
                    incident_id=inc.id,
                    mission_id=inc.mission_id,
                    task_id=inc.task_id,
                    kind=inc.kind,
                    severity=inc.severity.value,
                    status=inc.status.value,
                    summary=inc.summary,
                    created_at=inc.created_at,
                ))

            task_projections: list[TaskProjection] = []
            active_wfi_ids: list[str] = []

            for task in tasks:
                task_incidents = [i.id for i in incidents_open if i.task_id == task.id]
                wfi_id = ""
                wfis = self._kernel.list_instances(task_id=task.id)
                active = sorted(
                    [w for w in wfis if w.status not in (WorkflowStatus.ABORTED, WorkflowStatus.VERIFIED)],
                    key=lambda w: w.created_at,
                )
                if active:
                    wfi_id = active[-1].id
                    if wfi_id not in active_wfi_ids:
                        active_wfi_ids.append(wfi_id)

                # I2 — evidence packs and verdicts per task
                latest_pack_id = ""
                if self._evidence is not None:
                    packs = self._evidence.list_packs(task_id=task.id)
                    for pack in packs:
                        all_evidence_packs.append(EvidenceProjection(
                            pack_id=pack.id,
                            task_id=pack.task_id,
                            mission_id=mission.id,
                            item_count=len(pack.items),
                            profile=pack.profile.value,
                            workflow_instance_id=pack.workflow_instance_id,
                            created_at=pack.created_at,
                        ))
                    if packs:
                        latest_pack_id = packs[-1].id

                    verdict = self._evidence.get_latest_verdict(task.id)
                    if verdict is not None:
                        all_verdicts.append(VerdictProjection(
                            verdict_id=verdict.id,
                            task_id=verdict.task_id,
                            evidence_pack_id=verdict.evidence_pack_id,
                            verdict=verdict.verdict.value,
                            profile=verdict.profile.value,
                            created_at=verdict.created_at,
                            decision_close=verdict.decision.close_task,
                            decision_reopen=verdict.decision.reopen_task,
                            decision_incident=verdict.decision.create_incident,
                        ))

                task_projections.append(TaskProjection(
                    task_id=task.id,
                    title=task.title,
                    status=task.status.value,
                    task_type=task.type.value,
                    risk_profile=task.risk_profile.value,
                    mission_id=mission.id,
                    claimed_by=task.claim.actor_id if task.claim else "",
                    workflow_instance_id=wfi_id,
                    evidence_pack_id=latest_pack_id,
                    open_incident_ids=tuple(task_incidents),
                ))

                if task.status == TaskState.NEEDS_VERIFICATION:
                    all_verification_queue.append(task.id)

            total_incidents += len(incidents_open)

            mission_projections.append(MissionProjection(
                mission_id=mission.id,
                title=mission.title,
                status=mission.status.value,
                risk_profile=mission.risk_profile.value,
                task_count=len(tasks),
                ready_count=sum(1 for t in tasks if t.status == TaskState.READY),
                running_count=sum(1 for t in tasks if t.status == TaskState.RUNNING),
                blocked_count=sum(1 for t in tasks if t.status == TaskState.BLOCKED),
                needs_verification_count=sum(1 for t in tasks if t.status == TaskState.NEEDS_VERIFICATION),
                closed_count=sum(1 for t in tasks if t.status == TaskState.CLOSED),
                cancelled_count=sum(1 for t in tasks if t.status == TaskState.CANCELLED),
                open_incident_ids=tuple(i.id for i in incidents_open),
                active_workflow_instance_ids=tuple(active_wfi_ids),
                tasks=tuple(task_projections),
            ))

        active_wfis = [
            w for w in self._kernel.list_instances()
            if w.status in (WorkflowStatus.RUNNING, WorkflowStatus.CHECKPOINTED, WorkflowStatus.PAUSED, WorkflowStatus.BLOCKED)
        ]
        workflow_projections = [self._build_workflow_projection(w.id) for w in active_wfis]

        open_missions = sum(1 for m in mission_projections if m.status == MissionState.OPEN.value)

        return CockpitProjection(
            missions=tuple(mission_projections),
            active_workflows=tuple(workflow_projections),
            verification_queue=tuple(all_verification_queue),
            total_incident_count=total_incidents,
            open_mission_count=open_missions,
            generated_at=datetime.now(tz=UTC).isoformat(),
            evidence_packs=tuple(all_evidence_packs),
            verdicts=tuple(all_verdicts),
            incidents=tuple(all_incidents),
        )

    def _build_workflow_projection(self, wfi_id: str) -> WorkflowProjection:
        wfi = self._kernel.get_instance(wfi_id)
        if wfi is None:
            return WorkflowProjection(
                wfi_id=wfi_id, recipe_id="", recipe_version="", status="unknown",
                mission_id="", task_id="", completed_steps=(), pending_steps=(),
            )
        events = self._kernel.get_run_events(wfi_id)
        return WorkflowProjection(
            wfi_id=wfi.id,
            recipe_id=wfi.recipe_id,
            recipe_version=wfi.recipe_version,
            status=wfi.status.value,
            mission_id=wfi.mission_id,
            task_id=wfi.task_id,
            completed_steps=(),
            pending_steps=(),
            checkpoint_count=len(wfi.checkpoint_refs),
            event_count=len(events),
            created_at=wfi.created_at,
        )


def build_cockpit_from_paths(
    ledger_root: Path,
    kernel_root: Path,
    *,
    evidence_root: Path | None = None,
    mission_id: str | None = None,
) -> CockpitProjection:
    """Convenience factory: build a CockpitProjection from directory paths."""
    from grimoire.evidence.service import EvidenceService
    ledger = MissionLedger(ledger_root)
    kernel = RuntimeKernel(kernel_root)
    evidence = EvidenceService(evidence_root) if evidence_root is not None else None
    return CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build(mission_id=mission_id)
