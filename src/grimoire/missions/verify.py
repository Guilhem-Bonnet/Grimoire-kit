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
from grimoire.missions.verifiability import Verifiability, classify

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask, TaskCheck

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

    @property
    def uncovered(self) -> tuple[str, ...]:
        """Les critères d'acceptation que les commandes ne couvrent pas (``()`` si tout l'est).

        Un verdict ``passed`` ne dit que « les commandes déclarées sont vertes » ;
        ces critères-là restent à preuve, et ``evidence_gate`` refuse la fermeture
        tant qu'ils le sont.
        """
        coverage = self.pack.coverage
        return coverage.acceptance_missing if coverage is not None else ()


def _reason(check: TaskCheck, result: CheckResult) -> str:
    if result.ok:
        return ""
    if result.unrunnable_reason is not None:
        return f"inexécutable : {result.unrunnable_reason}"
    if result.exit_code is None:
        delay = f" ({check.timeout_s:g} s)" if check.timeout_s is not None else ""
        return f"délai dépassé{delay}"
    if result.exit_code == check.expect_exit and check.expect_stdout_contains is not None:
        return f"code de sortie {result.exit_code} conforme, mais {check.expect_stdout_contains!r} absent de la sortie standard"
    return f"code de sortie {result.exit_code}, attendu {check.expect_exit}"


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
        expect_stdout_contains=tuple(c.expect_stdout_contains for c in task.checks),
        timeouts=tuple(c.timeout_s for c in task.checks),
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
                reason=_reason(check, result),
            )
        )
    # Le pack oppose les critères de la tâche à ce que les commandes ont prouvé
    # (``evidence_gate`` lit cette couverture). Seule une V0 en est dispensée :
    # tous ses critères sont mécaniques et ses commandes déclarées SONT le
    # verdict. Pour une V1/V2, une commande ne couvre pas « relue par un
    # humain » : le critère reste ``acceptance_missing`` et la fermeture est
    # refusée, au lieu d'un pack sans critère à opposer qui passait la gate.
    acceptance = () if classify(task) is Verifiability.V0 else task.acceptance
    service = EvidenceService(evidence_root)
    pack = service.create_pack(task.id, EvidenceProfile.LIGHT, items, acceptance=acceptance)
    verdict = service.verify(pack, extra_checks=tuple(verdict_checks))
    return VerifyOutcome(pack=pack, verdict=verdict, results=results)
