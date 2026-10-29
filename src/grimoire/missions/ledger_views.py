"""Valeurs que le ledger rend pour les baux et la récolte (``LeaseView``, ``HolderCheck``, ``ReapDecision``, ``ReapReport``).

Réexportées par :mod:`grimoire.missions.ledger`, où elles vivaient.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LeaseView:
    """L'état du bail d'une tâche tenue : ``valid``, ``lost`` ou ``unknown`` (horloge incohérente)."""

    task_id: str
    status: str
    reason: str
    epoch: int
    actor_id: str
    host: str
    session_id: str = ""
    #: Version de l'hôte de la session (K3) ; ``""`` = inconnue, donc hôte non fencé.
    host_version: str = ""


@dataclass(frozen=True, slots=True)
class HolderCheck:
    """Le verdict du contrôle d'epoch d'un titulaire — ``source`` : ``lease`` (cache) ou ``ledger``."""

    ok: bool
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class ReapDecision:
    """Ce que la récolte fait d'un bail perdu : ``ready`` (relancée) ou ``defer`` (cause nommée)."""

    task_id: str
    action: str
    cause: str
    epoch: int
    attempts: int
    retry_at: str = ""
    written: bool = False
    #: La question posée à l'humain quand la cause est ``resume_unfenced`` (K3) ; ``""`` sinon.
    question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "cause": self.cause,
            "epoch": self.epoch,
            "attempts": self.attempts,
            "retry_at": self.retry_at,
            "written": self.written,
            **({"question": self.question} if self.question else {}),
        }


@dataclass(frozen=True, slots=True)
class ReapReport:
    """Le résultat d'une récolte : ``clock`` vaut ``unknown`` quand rien n'a pu être jugé."""

    clock: str
    decisions: tuple[ReapDecision, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"clock": self.clock, "decisions": [d.to_dict() for d in self.decisions]}
