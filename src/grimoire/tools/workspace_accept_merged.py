"""« Tout accepter » (GAO-cockpit-tout-001) : fermer en un geste les tâches que le merger a laissées à l'humain.

Le 2026-11-07, treize tâches attendaient « Accepter … ? » dans le cockpit : une PR
qui touche un chemin réservé demande la fusion, puis ``grimoire task accept``.
Cette route fait le second geste pour toutes les tâches qu'elle peut prouver
prêtes, et **jamais** pour les autres.

Une tâche est candidate si et seulement si :

- son état est ``needs_verification`` ;
- un ``task.merged`` est noté au ledger depuis le dernier lancement ;
- la dernière question ouverte est celle que le merger pose pour l'acceptation
  (``task.merge_escalated`` avec ``gesture: accept``, ou son texte
  « Accepter <id> (PR #N fusionnée) ? ») ;
- le reçu de revue du worktree de la tâche porte le verdict ``READY``.

Le geste est celui d'un humain : la route exige la clé de contrôle
(``~/.grimoire/control/cockpit-key``), comme l'octroi du mandat et le pilote
automatique — le jeton de session, injecté dans toute page servie sur la
loopback, ne suffit pas à fermer des tâches. Chaque acceptation passe par
:func:`grimoire.missions.human_acceptance.accept_task`, avec les racines de
:func:`~grimoire.missions.human_acceptance.acceptance_roots` (#1169 : la preuve
est dans le worktree quand elle y a été rangée). Une tâche qui échoue est
rapportée, les autres continuent : c'est un lot, pas une transaction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from grimoire.missions.human_acceptance import accept_task, acceptance_roots
from grimoire.missions.launcher import LAUNCHED_EVENT
from grimoire.missions.mandate import verify_control_key
from grimoire.missions.questions import OPEN, records_of, status_of
from grimoire.missions.review_receipt import READY, read_receipt
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.questions import QuestionRecord
    from grimoire.missions.schemas import LedgerEvent, MissionTask

__all__ = ["ACTOR", "accept_merged", "accept_merged_candidates", "accept_merged_view"]

#: L'acteur inscrit au ledger : le canal, jamais un nom fourni par le corps.
ACTOR = "cockpit"

#: La question d'acceptation posée par le merger (``merger._ask_human``) : le
#: préfixe suffit à reconnaître le geste, l'identifiant est vérifié à part.
_ACCEPT_TEXT = re.compile(r"^\s*Accepter\s+(?P<task>\S+)\s+\(PR\s+#(?P<pr>\d+)\s+fusionnée\)\s*\?")


def _last_launch_index(events: list[LedgerEvent]) -> int:
    """L'indice du dernier lancement, ou ``-1`` : une tâche faite à la main n'en a pas."""
    return max((i for i, e in enumerate(events) if e.event_type == LAUNCHED_EVENT), default=-1)


def _merged_after_launch(events: list[LedgerEvent]) -> LedgerEvent | None:
    """Le dernier ``task.merged`` depuis le dernier lancement, sinon ``None``.

    Même règle que le merger (``_accept``) : une fusion notée avant un nouveau
    lancement ne dit rien du travail présent.
    """
    after = _last_launch_index(events)
    merged = [e for e in events[after + 1 :] if e.event_type == MERGED_EVENT]
    return merged[-1] if merged else None


def _last_open_question(task: MissionTask) -> QuestionRecord | None:
    """La dernière question encore ouverte du registre, sinon ``None``."""
    opened = [record for record in records_of(task) if status_of(task, record) == OPEN]
    return opened[-1] if opened else None


def _is_merger_accept_question(task: MissionTask, question: QuestionRecord, events: list[LedgerEvent]) -> bool:
    """Vrai si *question* est celle que le merger pose pour l'acceptation de *task*.

    L'événement ``task.merge_escalated`` fait foi ; le texte de la question sert
    de repli pour un ledger qui porterait la question sans son événement.
    """
    from grimoire.missions.merger import MERGE_ESCALATED_EVENT

    if any(
        e.event_type == MERGE_ESCALATED_EVENT
        and e.payload.get("gesture") == "accept"
        and e.payload.get("question_id") == question.id
        for e in events
    ):
        return True
    match = _ACCEPT_TEXT.match(question.question.text or "")
    return match is not None and match.group("task") == task.id


def _question_pr(question: QuestionRecord, merged: LedgerEvent) -> int:
    """Le numéro de PR que la question nomme, sinon celui de la fusion notée."""
    match = _ACCEPT_TEXT.match(question.question.text or "")
    if match is not None:
        return int(match.group("pr"))
    return int(merged.payload.get("pr") or 0)


def _ready_receipt(proof_root: Path, task_id: str) -> bool:
    """Vrai si le reçu de revue existe et porte ``READY`` — un reçu illisible n'est pas un READY."""
    try:
        receipt = read_receipt(proof_root, task_id)
    except ValueError:
        return False
    return receipt is not None and receipt.verdict == READY


def _candidate_row(service: TaskService, task: MissionTask) -> dict[str, Any] | None:
    """La ligne de *task* si elle réunit les quatre conditions, sinon ``None``.

    Un ledger, un reçu ou un worktree illisible n'est pas une candidate : la
    route rend la liste de ce qui se prouve, pas une erreur d'affichage.
    """
    try:
        events = service.ledger.events_for(task.id)
        merged = _merged_after_launch(events)
        if merged is None:
            return None
        question = _last_open_question(task)
        if question is None or not _is_merger_accept_question(task, question, events):
            return None
        _evidence_root, proof_root = acceptance_roots(service, task.id)
        if not _ready_receipt(proof_root, task.id):
            return None
    except Exception:
        return None
    return {
        "task_id": task.id,
        "title": task.title,
        "pr": _question_pr(question, merged),
        "question_id": question.id,
    }


def accept_merged_candidates(project_root: Path) -> list[dict[str, Any]]:
    """Les tâches que « Tout accepter » fermerait maintenant, sans rien écrire.

    Une ligne : ``task_id``, ``title``, ``pr``, ``question_id``. Une tâche dont
    la preuve, le reçu ou le ledger ne se lisent pas reste hors de la liste :
    la candidate est celle qu'on peut prouver prête, pas celle qu'on espère.
    """
    service = TaskService(project_root.resolve())
    if not service.has_ledger:
        return []
    rows: list[dict[str, Any]] = []
    for task in service.list_tasks(status=TaskState.NEEDS_VERIFICATION.value):
        row = _candidate_row(service, task)
        if row is not None:
            rows.append(row)
    return rows


def accept_merged_view(project_root: Path, _query: Any) -> dict[str, Any]:
    """La lecture de la route GET : candidates et compte, pour le libellé du bouton."""
    rows = accept_merged_candidates(project_root)
    return {"candidates": rows, "count": len(rows)}


def _reason(pr: int) -> str:
    """Le motif fixe : il nomme le lot (et son canal), et la PR, jamais une justification inventée par tâche."""
    return f"Tout accepter (lot cockpit) : PR #{pr} fusionnée, relue READY"


def accept_merged(project_root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Accepte et ferme les candidates de *project_root* ; rend les acceptées et les refusées.

    Sans clé de contrôle valide : :class:`PermissionError` (403), rien n'est écrit.
    Chaque refus est attribué à sa tâche ; les autres continuent.
    """
    if not verify_control_key(body.get("control_key")):
        raise PermissionError("clé de contrôle absente ou invalide : accepter en lot est un geste d'humain")
    root = project_root.resolve()
    service = TaskService(root)
    accepted: list[str] = []
    refused: list[dict[str, str]] = []
    for row in accept_merged_candidates(root):
        task_id = str(row["task_id"])
        try:
            task = service.require(task_id)
            evidence_root, proof_root = acceptance_roots(service, task_id)
            reason = _reason(int(row.get("pr") or 0))
            outcome = accept_task(
                task,
                root,
                evidence_root,
                reason,
                ACTOR,
                origin=ORIGIN_HUMAN,
                proof_root=proof_root,
            )
            service.comment(
                task_id,
                f"Acceptation humaine ({outcome.verdict.id}, pack {outcome.pack.id}) : {reason}",
                ACTOR,
                origin=ORIGIN_HUMAN,
            )
            # La clôture relit la preuve et le reçu là où l'acceptation vient de les écrire : dans
            # le worktree noté, un TaskService posé là retrouve le ledger du projet par son marqueur.
            TaskService(proof_root).transition(task_id, TaskState.CLOSED, ACTOR, reason)
        except Exception as exc:  # un refus par tâche, jamais l’arrêt du lot
            refused.append({"task_id": task_id, "reason": str(exc) or type(exc).__name__})
        else:
            accepted.append(task_id)
    return {"accepted": accepted, "refused": refused}
