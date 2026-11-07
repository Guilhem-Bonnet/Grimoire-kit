"""Le constat d'une PR fusionnée hors du merger : le tick solde la tâche au lieu de la relancer (#1162).

La file du merger ne voit que les tâches qu'il a lancées (``task.session_launched``) et qui sont arrivées en
``needs_verification`` (:meth:`~grimoire.missions.merger.MergeStage._classify`). Une PR ``task/<id>`` fusionnée à la
main avec ``gh pr merge`` n'y entre jamais : la tâche reste ``ready``, ``claimed`` ou ``needs_verification`` et le
tick suivant la relançait pour un travail déjà livré.

Ici, avant l'admission, chaque tâche ouverte connue du ledger (un worktree noté, un claim, ou l'état
``needs_verification``) est confrontée à ce que GitHub dit de sa branche ``task/<id>``. ``MERGED`` avec un SHA de
fusion lisible : ``task.merged_externally`` garde la preuve et la tâche passe ``closed``, sans relecture ni session.
Tout le reste — pas de PR, PR ouverte, réponse illisible, SHA absent — ne clôt rien (échec fermé).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.launcher import Exec, default_exec
from grimoire.missions.ledger import EXTERNAL_MERGED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import TASK_BRANCH_PREFIX, find_task_worktree

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = ["EXTERNAL_MERGED_EVENT", "EXTERNAL_MERGE_ACTOR", "OPEN_STATES", "ExternalMergeOutcome", "reconcile_external_merges"]

#: L'auteur des gestes du tick (:meth:`~grimoire.missions.ledger.MissionLedger.wake` prend le même).
EXTERNAL_MERGE_ACTOR = "tick"
#: Les états d'une tâche encore ouverte : une PR fusionnée les solde tous.
OPEN_STATES = frozenset({
    TaskState.PROPOSED,
    TaskState.READY,
    TaskState.CLAIMED,
    TaskState.RUNNING,
    TaskState.BLOCKED,
    TaskState.NEEDS_VERIFICATION,
    TaskState.WAITING,
})
#: Les états d'où une PR peut exister sans worktree noté (claim gardé, tâche rendue à la revue).
_POLLABLE_WITHOUT_WORKTREE = frozenset({TaskState.NEEDS_VERIFICATION, TaskState.WAITING})
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_NO_PR = "no pull requests found"


@dataclass(frozen=True, slots=True)
class ExternalMergeOutcome:
    """Ce que le constat a fait : ``close`` (``done``), ou un refus d'agir (``failed``) qui ne bloque pas le tick."""

    task_id: str
    action: str
    status: str
    detail: str = ""
    pr: int = 0
    merged_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail,
            "pr": self.pr, "merged_sha": self.merged_sha,
        }


def _merger_owns(service: TaskService, task: MissionTask) -> bool:
    """Vrai quand le merger a déjà pris cette tâche dans sa file depuis sa dernière entrée en revue.

    Une PR qu'il a fusionnée (ou s'apprêtait à fusionner) se juge chez lui : sceau, reçu, gates et acceptation sous
    mandat. Le constat externe ne doit pas court-circuiter ces gardes — il ne solde que ce que la file n'a jamais vu.
    """
    from grimoire.missions.merger import MERGE_QUEUED_EVENT, MERGE_REQUESTED_EVENT
    from grimoire.missions.task_worktree import MERGED_EVENT

    events = service.ledger.events_for(task.id)
    entered = max(
        (
            i for i, e in enumerate(events)
            if e.event_type == "task.transitioned" and e.payload.get("to_state") == TaskState.NEEDS_VERIFICATION.value
        ),
        default=-1,
    )
    return any(e.event_type in (MERGE_QUEUED_EVENT, MERGE_REQUESTED_EVENT, MERGED_EVENT) for e in events[entered + 1:])


def _candidates(service: TaskService) -> list[MissionTask]:
    """Les tâches ouvertes dont une PR ``task/<id>`` peut exister, sans interroger ``gh`` pour tout le backlog.

    Un worktree noté au ledger, ou un claim tenu, ou une tâche en revue/attente suffit. Une tâche ``ready`` jamais
    lancée n'a pas de PR : la sonder à chaque tick coûterait un appel réseau par tâche dormante. Les tâches que le
    merger tient déjà dans sa file, et celles faites à la main (#994, PR sur une branche de travail, pas ``task/<id>``),
    sont laissées à leur relecture et à ses gardes.
    """
    try:
        from grimoire.missions.manual_review import manual_tasks

        manual = manual_tasks(service)
    except Exception:  # journal d'autorité illisible : rien n'est dit manuel, l'échec fermé est au relecteur
        manual = {}
    found = []
    for task in service.list_tasks():
        if task.status not in OPEN_STATES or task.id in manual:
            continue
        if not (task.claim is not None or task.status in _POLLABLE_WITHOUT_WORKTREE or find_task_worktree(service, task.id) is not None):
            continue
        if _merger_owns(service, task):
            continue
        found.append(task)
    return found


def _frozen_slug(service: TaskService, task_id: str) -> str:
    """Le dépôt GitHub figé au lancement de la tâche, ou ``""`` (projet sans remote GitHub reconnaissable)."""
    try:
        from grimoire.missions.review_seal import frozen_repository

        pin = frozen_repository(service.home_root, task_id)
    except Exception:  # journal d'autorité illisible : on interroge le dépôt local, rien n'est figé
        return ""
    return str((pin or {}).get("repo") or "")


def _pr_state(exec_: Exec, repo: Path, branch: str, slug: str = "") -> tuple[dict[str, Any] | None, str]:
    """``(vue de la PR, "")`` ; ``(None, "")`` sans PR ; ``(None, raison)`` quand la réponse n'est pas exploitable.

    *slug* (dépôt figé au lancement) est visé par ``-R`` : jamais le dépôt qu'une configuration locale désignerait.
    """
    argv = ["gh", "pr", "view", branch, "--json", "state,number,mergeCommit,baseRefName,headRefOid"]
    if slug:
        argv += ["-R", slug]
    result = exec_(argv, repo, None)
    if result.returncode != 0:
        said = (result.stderr or result.stdout).strip()
        if _NO_PR in said.lower():
            return None, ""
        return None, f"gh pr view {branch} a échoué ({result.returncode}) : {said[:200]}"
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None, f"réponse de gh pr view {branch} illisible"
    if not isinstance(data, dict):
        return None, f"réponse de gh pr view {branch} illisible : pas un objet"
    return data, ""


def _merge_sha(data: dict[str, Any]) -> str:
    merge = data.get("mergeCommit")
    return str(merge.get("oid") or "") if isinstance(merge, dict) else ""


def reconcile_external_merges(service: TaskService, *, exec_: Exec = default_exec) -> tuple[ExternalMergeOutcome, ...]:
    """Solde les tâches ouvertes dont la PR ``task/<id>`` est ``MERGED`` ; rend ce qui a été fait ou refusé.

    L'appelant place cette étape avant l'admission : une tâche close n'est ni dispatchée, ni relue. L'arrêt de la
    session et le rangement (worktree, branche) restent au janitor, qui voit la tâche désormais terminale.
    Disjoncteur ouvert : rien n'est interrogé, comme pour le lanceur et le merger — le tick est dégradé, pas aveugle.
    """
    from grimoire.missions.breaker import read_breaker

    if read_breaker(service.home_root).open:
        return ()
    outcomes: list[ExternalMergeOutcome] = []
    for task in _candidates(service):
        branch = TASK_BRANCH_PREFIX + task.id
        data, error = _pr_state(exec_, service.home_root, branch, _frozen_slug(service, task.id))
        if error:
            outcomes.append(ExternalMergeOutcome(task.id, "close", "failed", error))
            continue
        if data is None or str(data.get("state") or "").upper() != "MERGED":
            continue
        number = data.get("number")
        pr = number if isinstance(number, int) and not isinstance(number, bool) else 0
        sha = _merge_sha(data)
        if not _SHA.fullmatch(sha):
            outcomes.append(ExternalMergeOutcome(
                task.id, "close", "failed", f"PR #{pr} fusionnée sans commit de fusion lisible : clôture refusée", pr,
            ))
            continue
        into = str(data.get("baseRefName") or "")
        head = str(data.get("headRefOid") or "")
        try:
            moved = service.ledger.close_merged_externally(
                task.id, pr=pr, merged_sha=sha, branch=branch, into=into, head_sha=head, actor_id=EXTERNAL_MERGE_ACTOR,
            )
        except Exception as exc:  # une tâche qui résiste ne casse pas le tick : elle est rapportée
            outcomes.append(ExternalMergeOutcome(task.id, "close", "failed", f"{type(exc).__name__}: {exc}", pr, sha))
            continue
        reason = f"PR #{pr} fusionnée hors merger à {sha[:12]} (task.merged_externally)"
        service._consolidate(moved, TaskState.CLOSED, actor=EXTERNAL_MERGE_ACTOR, reason=reason)
        outcomes.append(ExternalMergeOutcome(
            task.id, "close", "done", f"PR #{pr} fusionnée à {sha[:12]} : tâche soldée sans relecture", pr, sha,
        ))
    return tuple(outcomes)
