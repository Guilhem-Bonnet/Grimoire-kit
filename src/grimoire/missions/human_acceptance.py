"""``grimoire task accept`` — le verdict humain d'une tâche à preuve déclarative (issue #865).

Constat du 2026-10-23 (Forge, kit 3.64.0) : ``task verify --evidence`` range un pack déclaratif
et un verdict ``skipped`` ; :func:`grimoire.missions.gates._resolve_review_gate` refuse alors la
clôture (« la décision revient à un humain »), mais aucun geste ne laissait cet humain trancher.
Une tâche ouverte sans ``--check`` ne pouvait donc jamais être fermée, par personne.

Le geste retenu, réservé au canal humain (même contrôle d'origine que ``task answer``) :

- la tâche doit être en revue (``needs_verification``) avec au moins un pack ;
- un pack d'attestation reprend les éléments du dernier pack et y ajoute l'acceptation, qui nomme
  chaque critère : la couverture (``EvidenceService.create_pack``) les compte couverts, sur la foi
  de l'humain et non d'une commande ;
- le verdict est ``passed``, ``created_by: human:<acteur>``, la raison en clair : il se distingue
  d'un vert mécanique dans le pack, dans le verdict et dans la trace ;
- l'acceptation est une relecture : elle écrit aussi le reçu ``READY``
  (:func:`grimoire.missions.review_receipt.write_receipt`, ``reviewer_id: human:<acteur>``), lié au
  pack d'attestation et à l'arbre présent, que le gate de clôture exige.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.evidence.schemas import (
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    VerdictDecision,
    VerdictResult,
    VerificationCheck,
    VerificationVerdict,
)
from grimoire.evidence.service import EvidenceService
from grimoire.missions.review_receipt import READY, ReviewReceipt, write_receipt
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.schemas import MissionTask

__all__ = ["HUMAN_VERIFIER_PREFIX", "HumanAcceptance", "accept_task"]

#: Préfixe du ``created_by`` d'un verdict humain : ``human:<acteur>``.
HUMAN_VERIFIER_PREFIX = "human:"


@dataclass(frozen=True, slots=True)
class HumanAcceptance:
    pack: EvidencePack
    verdict: VerificationVerdict
    receipt: ReviewReceipt

    def to_dict(self) -> dict[str, Any]:
        return {
            "human": True, "pack": self.pack.to_dict(), "verdict": self.verdict.to_dict(),
            "receipt": self.receipt.to_dict(),
        }


def accept_task(
    task: MissionTask, project_root: Path, evidence_root: Path, reason: str, actor: str, *, origin: str
) -> HumanAcceptance:
    """Range le pack d'attestation, le verdict ``passed`` humain et le reçu ``READY`` de *task* ; lève si refusé.

    *origin* vient du canal d'appel (cockpit, CLI interactive), jamais d'un champ que l'appelant
    remplit : un agent n'accepte pas son propre travail.
    """
    if origin != ORIGIN_HUMAN:
        raise GrimoireMissionError(
            f"Acceptation refusée pour {task.id} : origine {origin!r}. Seul le canal humain (cockpit, CLI "
            "interactive) accepte une preuve déclarative — un agent ne valide pas son propre travail"
        )
    if task.status is not TaskState.NEEDS_VERIFICATION:
        raise GrimoireMissionError(
            f"Acceptation refusée pour {task.id} : état {task.status.value}, il faut une tâche en revue "
            "(`task move --to needs_verification`)"
        )
    why = reason.strip()
    if not why:
        raise ValueError("`--reason` requis : une acceptation sans raison ne dit pas ce qui a été relu")
    service = EvidenceService(evidence_root)
    packs = service.list_packs(task.id)
    if not packs:
        raise GrimoireMissionError(
            f"Acceptation refusée pour {task.id} : aucun pack de preuve à accepter "
            f"(`grimoire task verify {task.id} --evidence <fichier>` d'abord)"
        )
    latest = packs[-1]
    criteria = " ; ".join(task.acceptance)
    attestation = EvidenceItem(
        f"human-{len(packs) + 1:02d}",
        EvidenceKind.DOC,
        f"{HUMAN_VERIFIER_PREFIX}{actor}",
        f"sha256-{sha256(why.encode('utf-8')).hexdigest()}",
        f"acceptation humaine par {actor} sur le pack {latest.id} : {why} — critères attestés : {criteria}",
    )
    pack = service.create_pack(task.id, latest.profile, [*latest.items, attestation], acceptance=task.acceptance)
    verdict = VerificationVerdict(
        id=f"ver-{task.id}-{len(service.list_verdicts(task.id)) + 1:03d}",
        task_id=task.id,
        evidence_pack_id=pack.id,
        verdict=VerdictResult.PASSED,
        profile=pack.profile,
        checks=(VerificationCheck("human-acceptance", VerdictResult.PASSED, why),),
        decision=VerdictDecision(close_task=True, reopen_task=False, create_incident=False),
        created_at=datetime.now(UTC).isoformat(),
        created_by=f"{HUMAN_VERIFIER_PREFIX}{actor}",
    )
    service.record_verdict(verdict)
    receipt = write_receipt(project_root, task.id, READY, reviewer_id=f"{HUMAN_VERIFIER_PREFIX}{actor}")
    return HumanAcceptance(pack, verdict, receipt)
