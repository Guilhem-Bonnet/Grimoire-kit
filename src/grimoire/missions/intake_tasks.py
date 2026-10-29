"""De l'idée aux tâches prêtes : propositions complètes et création, pour le cockpit et le CLI.

``intake_proposal`` classe une demande mais ses ``TaskProposal`` n'ont ni
critère ni commande de vérification : aucune n'est créable telle quelle. Ce
module les complète par un gabarit de critère par classe de vérifiabilité
(V0 mécanique, V1 revue, V2 à démontrer), sans LLM et de façon déterministe,
puis crée les tâches retenues par ``TaskService.add`` — les mêmes gardes que
``grimoire task add`` et ``POST /api/workspace/tasks/add``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from grimoire.missions.dispatch_advice import model_tier_for, recommended_model_for
from grimoire.missions.intake import IntakeRequest, MissionIntakeService, TaskProposal
from grimoire.missions.schemas import RiskProfile, TaskState, TaskType
from grimoire.missions.verifiability import Verifiability, classify_criteria

__all__ = ["CRITICAL_RISKS", "create_from_proposals", "proposals_for"]

#: Les risques qu'on ne coche jamais d'office : un humain décide de les créer.
CRITICAL_RISKS = frozenset({RiskProfile.SECURITY_CRITICAL, RiskProfile.RELEASE})

#: Le gabarit de critère, par type de tâche ; la classe en découle (vérifiée par ``classify_criteria``).
_V0 = "Les tests passent : la commande de vérification retourne 0"
_V1 = "Revue : le résultat est relu et validé par un relecteur"
_V2 = "Le résultat est conforme à la demande"
_TEMPLATES: dict[TaskType, str] = {
    TaskType.IMPLEMENTATION: _V0,
    TaskType.TEST: _V0,
    TaskType.CLEANUP: _V0,
    TaskType.MIGRATION: _V0,
    TaskType.DOCUMENTATION: _V1,
    TaskType.ANALYSIS: _V1,
    TaskType.ARCHITECTURE: _V1,
    TaskType.SECURITY: _V1,
    TaskType.OPERATION: _V2,
}


def _risk_label(risk: RiskProfile) -> str:
    return "critical" if risk == RiskProfile.SECURITY_CRITICAL else risk.value


def _entry(proposal: TaskProposal) -> dict[str, Any]:
    acceptance = _TEMPLATES.get(proposal.task_type, _V2)
    klass = classify_criteria([acceptance])
    return {
        "title": proposal.title,
        "task_type": proposal.task_type.value,
        "risk_profile": _risk_label(proposal.risk_profile),
        "verifiability": klass.value,
        "model_tier": model_tier_for(klass),
        "recommended_model": recommended_model_for(klass),
        "acceptance": [f"{acceptance} — {proposal.title}" if klass is Verifiability.V2 else acceptance],
        "needs_check": klass is not Verifiability.V2,
        "rationale": proposal.rationale,
        "checked": proposal.risk_profile not in CRITICAL_RISKS,
    }


def proposals_for(text: str) -> list[dict[str, Any]]:
    """Les propositions complètes d'une demande : titre, type, risque, vérifiabilité, critère.

    Déterministe : la même demande rend toujours les mêmes propositions, dans le même ordre.
    Un texte blanc est refusé (``ValueError``).
    """
    if not text.strip():
        raise ValueError("l'intake attend le texte de la demande")
    result = MissionIntakeService().analyze(IntakeRequest(raw_text=text))
    return [_entry(p) for p in result.task_proposals]


def create_from_proposals(
    service: Any, items: Iterable[Mapping[str, Any]], *, actor: str, origin: str,
) -> list[dict[str, Any]]:
    """Crée les tâches de *items* (``title``, ``acceptance``, ``check``) par ``TaskService.add``.

    Une tâche V0/V1 n'est prête qu'avec une commande de vérification ; sans elle,
    ou si le gate refuse ``proposed → ready``, elle reste en ``proposed`` et la
    raison est rendue. Le refus d'une tâche n'empêche pas les suivantes.
    """
    from grimoire.core.exceptions import GrimoireMissionError
    from grimoire.missions.schemas import TaskCheck
    from grimoire.missions.service import TaskRefusedError

    out: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        raw = item.get("acceptance")
        acceptance = tuple(str(a).strip() for a in ([raw] if isinstance(raw, str) else raw or ()) if str(a).strip())
        command = str(item.get("check") or "").strip()
        if not title or not acceptance:
            raise ValueError("chaque tâche demande un titre et un critère d'acceptation")
        needs_check = classify_criteria(acceptance) is not Verifiability.V2
        checks = (TaskCheck(cmd=command),) if command and needs_check else ()
        try:
            added = service.add(title, acceptance, actor=actor, origin=origin, checks=checks)
        except GrimoireMissionError as exc:
            raise ValueError(str(exc)) from exc
        task, reason = added.task, ""
        if needs_check and not checks:
            reason = "commande de vérification manquante : la tâche reste proposée"
        else:
            try:
                task = service.transition(task.id, TaskState.READY, actor).task
            except TaskRefusedError as refused:
                reason = str(refused)
        out.append({
            "id": task.id, "title": task.title, "status": task.status.value, "reason": reason,
        })
    return out
