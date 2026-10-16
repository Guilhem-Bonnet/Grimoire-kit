"""``grimoire task verify`` — le producteur de pack et de verdict (issue #772, D9).

Constat : le kit savait *ranger* un pack de preuve et un verdict
(``evidence.service``) mais rien ne les produisait pour une tâche du ledger.
``GAO-kanban-agent-001`` s'est clôturée en revue sans pack ni verdict, et une
tâche « sûre (tests) » classait V0 sans qu'aucune commande existe à lancer.

Ce module lance les commandes déclarées de la tâche (``task.checks``), en fait
un pack (une pièce ``test`` par commande, empreinte de la sortie) et rend le
verdict de ``EvidenceService.verify`` — jamais un « passé » sans commande :
une tâche qui n'en déclare aucune est refusée, pas validée à vide.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.evidence.schemas import (
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceProfile,
    VerdictResult,
    VerificationCheck,
    VerificationVerdict,
)
from grimoire.evidence.service import EvidenceService
from grimoire.missions.dispatch import CheckResult, _run_checks

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = ["NoDeclaredChecksError", "VerifyOutcome", "verify_task"]


class NoDeclaredChecksError(ValueError):
    """La tâche ne déclare aucune commande : il n'y a rien à vérifier mécaniquement."""


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    pack: EvidencePack
    verdict: VerificationVerdict
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return self.verdict.verdict is VerdictResult.PASSED


def _reason(task_check_exit: int, result: CheckResult) -> str:
    if result.ok:
        return ""
    if result.unrunnable_reason is not None:
        return f"inexécutable : {result.unrunnable_reason}"
    if result.exit_code is None:
        return "délai dépassé"
    return f"code de sortie {result.exit_code}, attendu {task_check_exit}"


def verify_task(task: MissionTask, project_root: Path, evidence_root: Path) -> VerifyOutcome:
    """Lance les ``checks`` de *task*, écrit le pack et le verdict, rend les deux.

    Chaque commande donne une pièce de preuve ``test`` ET un contrôle du
    verdict : un code de sortie inattendu, un délai dépassé ou une commande
    inexécutable (binaire absent) rend le verdict ``failed`` — jamais
    ``passed`` par défaut.
    """
    if not task.checks:
        raise NoDeclaredChecksError(f"{task.id} ne déclare aucune commande (checks) : rien à vérifier mécaniquement")
    results = _run_checks(
        tuple(c.cmd for c in task.checks),
        project_root=project_root,
        expect_exits=tuple(c.expect_exit for c in task.checks),
    )
    items: list[EvidenceItem] = []
    verdict_checks: list[VerificationCheck] = []
    for index, (check, result) in enumerate(zip(task.checks, results, strict=True), start=1):
        summary = f"{check.cmd} -> exit {result.exit_code} (attendu {check.expect_exit})"
        items.append(
            EvidenceItem.from_text(
                f"check-{index:02d}",
                EvidenceKind.TEST,
                f"{summary}\n{result.output_excerpt}",
                uri=f"check:{check.cmd}",
                summary=summary,
            )
        )
        verdict_checks.append(
            VerificationCheck(
                id=f"command-{index:02d}",
                result=VerdictResult.PASSED if result.ok else VerdictResult.FAILED,
                reason=_reason(check.expect_exit, result),
            )
        )
    service = EvidenceService(evidence_root)
    pack = service.create_pack(task.id, EvidenceProfile.LIGHT, items)
    verdict = service.verify(pack, extra_checks=tuple(verdict_checks))
    return VerifyOutcome(pack=pack, verdict=verdict, results=results)
