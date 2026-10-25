"""Le rôle ``merger`` : la file série qui fusionne les tâches relues READY, puis les accepte (#876, lot 4, verrou 12).

Étape du tick, après le relecteur, active seulement si ``orchestrator.roles.merger.enabled`` (désactivé par défaut).
Du code, sans LLM : il ne lance aucune session, il ne fait que reprendre celle du worker quand la fusion échoue.

**Qui entre dans la file.** Une tâche lancée (``task.session_launched``), en ``needs_verification``, dont la dernière
relecture de l'entrée en revue en cours est jugée ``READY`` (``task.review_judged``), et pas encore fusionnée. L'ordre
de la file est celui des jugements ``READY``. Le merger n'ouvre pas de PR : c'est le worker qui l'ouvre ; sans PR, il
attend.

**À chaque tick, pour chaque tâche de la file**, dans cet ordre, chaque garde échouant fermée :

1. **la PR** (``gh pr view task/<id>``) : ouverte, pas en brouillon, vers la cible du worktree ; fusionnée, elle est
   constatée (étape 6) ; fermée sans fusion, refus ;
2. **le READY tient** : le reçu est celui qui a été jugé (pas réécrit depuis), le pack et les critères n'ont pas changé,
   l'arbre du worktree est propre (hors ce que le kit écrit lui-même) et son contenu est celui qui a été relu — ou
   celui qu'un réalignement du merger en a tiré (``task.merge_realigned`` garde l'empreinte relue et l'empreinte
   obtenue). Un arbre modifié non commité renvoie au worker ; un READY périmé rend la tâche à la relecture
   (``task.merge_requeued``, que le relecteur lit comme une nouvelle entrée en revue) ;
3. **la tête** : la tête locale de ``task/<id>`` est celle de la PR (ce qui est relu est ce qui sera fusionné) ; une
   tête réalignée et pas encore poussée est poussée ;
4. **la CI** (``gh pr checks``) : les checks requis par la protection de branche (``--required``), à défaut tous les
   checks hors ``ignored_checks`` (défaut ``codecov/*``). Rouge : retour au worker, comme un ``NOT-READY`` du lot 3
   (``claude --bg --resume`` de sa session). En attente, ou aucun check : on attend ;
5. **la cible** : branche en retard, ``origin/<cible>`` est fusionnée dans le worktree (jamais de rebase, jamais de push
   forcé). Un conflit limité à ``CHANGELOG.md`` est résolu par l'union des deux côtés (``git merge-file --union``),
   contrôlée : aucune ligne ``-`` dans ``git diff origin/<cible> -- CHANGELOG.md``, sinon retour au worker ; tout autre
   conflit renvoie au worker. Puis push, et on attend la nouvelle CI. Seules les ``realign_max`` premières tâches de la
   file sont réalignées (défaut 1 : le débit CI) ;
6. **la fusion**, pour la tête de file seulement (WIP 1) : ``gh pr merge <n> --squash --match-head-commit <tête>``,
   jamais ``--admin`` ni ``--auto``, **seulement** si :func:`~grimoire.missions.mandate.require_authority` l'autorise
   sous :func:`~grimoire.missions.mandate.acting_as_orchestrator` (mandat actif qui couvre ``merge`` et le type de la
   tâche, politique d'escalade qui délègue ``merge``, aucune catégorie gardée à l'humain) et si le reçu qui la fonde est
   attesté au journal d'autorité (verrou 7) ; sinon la tâche passe ``waiting(human)`` avec la question « fusionner ? ».
   La fusion n'est notée (``task.merged``) qu'une fois constatée sur la remote : PR ``MERGED`` et commit de fusion
   contenu dans ``origin/<cible>`` ; le geste est alors inscrit au journal d'autorité avec sa provenance (le reçu
   jugé READY, la tête fusionnée) ;
7. **l'acceptation** : sous un mandat qui couvre ``accept`` et une politique qui le délègue, par
   :func:`~grimoire.missions.human_acceptance.accept_task` (la garde du lot 1 : reçu READY d'un relecteur inscrit,
   distinct de l'auteur), puis clôture ; sinon ``waiting(human)`` « accepter ? ». Une tâche close, le janitor du lot 2
   nettoie (session, worktree, branche, créneau).

**Catégories gardées.** Une tâche dont le type, le profil ou les labels touchent une catégorie que la politique garde à
l'humain (``release``, ``secrets``, ``paid``, ``security``, :func:`~grimoire.missions.mandate.task_categories`) n'est
jamais fusionnée ni acceptée sous mandat, même si ``merge`` et ``accept`` sont délégués.

**La réponse humaine.** Une question du merger se répond par ``grimoire task answer`` (canal humain) ; la réponse réveille
la tâche (``ready``). Le lanceur ne la relance pas : si la réponse est « fusionner » ou « accepter », venue de l'origine
``human``, le merger reprend la tâche (``task.merge_resumed``), revérifie tout ce qui précède, et fait le geste au nom de
l'humain, la réponse en provenance. « reprendre » la rend au worker, « abandonner » l'annule.

Chaque étape est notée au ledger et n'est faite qu'une fois ; le tick est rejouable (une fusion faite et non notée est
constatée au tick suivant, jamais refaite). Un échec de ``gh`` ou de ``git`` est rapporté, compté au disjoncteur, et
rejoué au tick suivant ; une exception sur une tâche ne bloque pas les autres. Disjoncteur ouvert : rien n'est fait.

**Limites** : la réponse humaine relayée vaut ce que vaut la ligne ``task.human_answered`` du ledger (écrite par le canal
humain, mais le ledger n'est pas une zone protégée) — la voie sous mandat, elle, passe par le journal d'autorité ; le push
du réalignement fait tourner les hooks git du dépôt ; une PR sans aucun check attend indéfiniment (la CI verte est
exigée) ; l'union du CHANGELOG garde les deux côtés dans l'ordre de ``git merge-file``, sans reconstruire les sections.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.breaker import read_breaker, record_failure
from grimoire.missions.launcher import LAUNCHED_EVENT, default_exec, launched_sessions, session_env
from grimoire.missions.review_receipt import (
    READY,
    RECEIPT_FINGERPRINT_EXCLUDES,
    evidence_pack_digest,
    read_receipt,
    receipt_fingerprint,
)
from grimoire.missions.reviewer import REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT, entered_review_at
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.task_worktree import MERGED_EVENT, find_task_worktree
from grimoire.missions.task_worktree import _env as _git_env
from grimoire.missions.waiting import ABANDON, HUMAN_ANSWERED_EVENT, new_question_id

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.service import TaskService
    from grimoire.missions.task_worktree import TaskWorktree

__all__ = [
    "MERGER_ACTOR",
    "MERGE_ACCEPTED_EVENT",
    "MERGE_BOUNCED_EVENT",
    "MERGE_ESCALATED_EVENT",
    "MERGE_PUSHED_EVENT",
    "MERGE_QUEUED_EVENT",
    "MERGE_REALIGNED_EVENT",
    "MERGE_REQUESTED_EVENT",
    "MERGE_REQUEUED_EVENT",
    "MERGE_RESUMED_EVENT",
    "MergeStage",
    "MergerOutcome",
    "merger_holds",
]

MERGER_ACTOR = "merger"
MERGE_QUEUED_EVENT = "task.merge_queued"
MERGE_REQUEUED_EVENT = "task.merge_requeued"
MERGE_REALIGNED_EVENT = "task.merge_realigned"
MERGE_PUSHED_EVENT = "task.merge_pushed"
MERGE_BOUNCED_EVENT = "task.merge_bounced"
MERGE_REQUESTED_EVENT = "task.merge_requested"
MERGE_ESCALATED_EVENT = "task.merge_escalated"
MERGE_RESUMED_EVENT = "task.merge_resumed"
MERGE_ACCEPTED_EVENT = "task.merge_accepted"
CHANGELOG = "CHANGELOG.md"
#: La réponse humaine qui vaut le geste, par question du merger.
APPROVE = {"merge": "fusionner", "accept": "accepter"}
_OPTIONS = {
    "merge": ("fusionner", "reprendre", ABANDON),
    "accept": ("accepter", "reprendre", ABANDON),
    "bounce": ("reprendre", ABANDON),
}
#: Catégories propres aux gestes : le mandat et la politique les tranchent ; les autres sont celles de la tâche.
_GESTURE_CATEGORIES = frozenset({"merge", "accept"})
_EVIDENCE = Path("_grimoire-runtime-output") / "evidence"
_HOST = "grimoire-tick"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: ``gh pr checks`` : 0 tout passe, 1 un check échoue, 8 des checks attendent — le JSON vaut dans les trois cas.
_GH_CHECKS_EXIT = (0, 1, 8)
_RED_BUCKETS = frozenset({"fail", "cancel"})
_GREEN_BUCKETS = frozenset({"pass", "skipping"})
_PUSH_TIMEOUT_S = 600


@dataclass(frozen=True, slots=True)
class MergerOutcome:
    """Ce que l'étape a fait d'une tâche : ``action`` (queue, wait, stale, bounce, realign, push, merge, merged,
    unconfirmed, accept, close, escalate, resume, refused) et ``status`` (done, waiting, skipped, refused, failed)."""

    task_id: str
    action: str
    status: str
    detail: str = ""
    pr: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail, "pr": self.pr}


@dataclass(frozen=True, slots=True)
class _PullRequest:
    number: int
    state: str
    head: str
    base: str
    draft: bool
    merge_commit: str


def _git(cwd: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # arguments fixes, sans shell ; sans les variables qui détourneraient git -C
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=_git_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 124, "", f"git {args[0]} : {exc}")


def _git_bytes(cwd: Path, *args: str) -> tuple[int, bytes]:
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=120, check=False, env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return 124, b""
    return done.returncode, done.stdout


def _rev(cwd: Path, ref: str) -> str:
    done = _git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else ""


def _err(done: subprocess.CompletedProcess[str] | Any) -> str:
    return (str(done.stderr or "") or str(done.stdout or "")).strip()[:300] or f"sortie {done.returncode}"


def _index(events: list[LedgerEvent], event: LedgerEvent | None) -> int:
    if event is None:
        return -1
    return next((i for i, e in enumerate(events) if e.id == event.id), -1)


def _last(events: list[LedgerEvent], kind: str, after: int = -1) -> LedgerEvent | None:
    found = [e for e in events[after + 1:] if e.event_type == kind]
    return found[-1] if found else None


def _last_launch(events: list[LedgerEvent]) -> int:
    return max((i for i, e in enumerate(events) if e.event_type == LAUNCHED_EVENT), default=-1)


def _answer_to(events: list[LedgerEvent], escalation: LedgerEvent) -> LedgerEvent | None:
    """La réponse humaine (origine ``human``, sans mandat) à la question de *escalation*, s'il y en a une."""
    qid = escalation.payload.get("question_id")
    answers = [
        e for e in events[_index(events, escalation) + 1:]
        if e.event_type == HUMAN_ANSWERED_EVENT and e.payload.get("question_id") == qid
    ]
    if not answers:
        return None
    answer = answers[-1]
    return answer if answer.payload.get("origin") == ORIGIN_HUMAN and not answer.payload.get("mandate_id") else None


def _approval_pending(events: list[LedgerEvent]) -> tuple[LedgerEvent, LedgerEvent] | None:
    """``(escalade, réponse)`` quand la dernière question du merger a reçu son « oui » humain, pas encore repris."""
    escalation = _last(events, MERGE_ESCALATED_EVENT)
    if escalation is None or str(escalation.payload.get("gesture")) not in APPROVE:
        return None
    answer = _answer_to(events, escalation)
    if answer is None or str(answer.payload.get("answer", "")).strip().lower() != APPROVE[str(escalation.payload["gesture"])]:
        return None
    if _last(events, MERGE_RESUMED_EVENT, _index(events, answer)) is not None:
        return None
    return escalation, answer


def merger_holds(service: TaskService, task_id: str) -> bool:
    """Vrai quand la tâche, réveillée par le « oui » humain à une question du merger, attend que le merger la reprenne.

    Le lanceur ne la relance pas : sans cela, la réponse « fusionner » rendrait la tâche à un nouveau worker.
    """
    task = service.ledger.get_task(task_id)
    if task is None or task.status is not TaskState.READY:
        return False
    return _approval_pending(service.ledger.events_for(task_id)) is not None


class MergeStage:
    """L'étape ``merger`` du tick : reprend les tâches approuvées, accepte les fusionnées, avance la file."""

    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec,
        claude_bin: str = "claude", remote: str = "origin", now: Any = None,
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin
        self._remote = remote
        self._now = now
        self._home = service.home_root

    # -- étape ---------------------------------------------------------------

    def __call__(self) -> tuple[MergerOutcome, ...]:
        if not self._policy.merger.enabled:
            return ()
        launched = launched_sessions(self._service)
        held, merged, queue = self._classify(launched)
        breaker = read_breaker(self._home)
        if breaker.open:
            why = f"disjoncteur ouvert : {breaker.reason}"
            return tuple(MergerOutcome(tid, "wait", "skipped", why) for tid in (*held, *merged, *(t for t, _ in queue)))
        outcomes: list[MergerOutcome] = []
        for tid in held:
            outcomes.append(self._guarded(tid, lambda t: self._resume(t)))
        for tid in merged:
            outcomes.append(self._guarded(tid, lambda t: self._accept(t, launched[t])))
        position = 0  # le rang dans la file des seules tâches qui y restent : une tête refusée ne bloque pas les suivantes
        for tid, judged in queue:
            outcome = self._guarded(tid, lambda t, j=judged, p=position: self._advance(t, j, p, launched[t]))
            outcomes.append(outcome)
            if outcome.status in ("waiting", "failed") or outcome.action in ("realign", "push"):
                position += 1
        return tuple(outcomes)

    def _guarded(self, tid: str, step: Any) -> MergerOutcome:
        """Une exception sur une tâche devient un échec rapporté et compté : les autres tâches suivent leur chemin."""
        try:
            outcome: MergerOutcome = step(tid)
        except Exception as exc:
            return self._failed(tid, "error", f"{type(exc).__name__}: {exc}")
        return outcome

    def _classify(self, launched: dict[str, dict[str, Any]]) -> tuple[list[str], list[str], list[tuple[str, LedgerEvent]]]:
        """``(reprises dues, fusionnées à accepter, file ordonnée)``."""
        held: list[str] = []
        merged: list[str] = []
        queue: list[tuple[str, LedgerEvent]] = []
        for tid in launched:
            task = self._service.ledger.get_task(tid)
            if task is None:
                continue
            events = self._service.ledger.events_for(tid)
            if task.status in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
                if self._resumable(task, events):
                    held.append(tid)
                continue
            if task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            if _last(events, MERGED_EVENT, _last_launch(events)) is not None:
                merged.append(tid)
                continue
            judged = self._ready_judgment(tid, events)
            if judged is not None:
                queue.append((tid, judged))
        queue.sort(key=lambda item: (item[1].created_at, item[0]))
        return held, merged, queue

    def _resumable(self, task: MissionTask, events: list[LedgerEvent]) -> bool:
        if _approval_pending(events) is None:
            return False
        if task.status is TaskState.READY:
            return True
        return task.claim is not None and task.claim.actor_id == MERGER_ACTOR  # une reprise interrompue à mi-chemin

    def _ready_judgment(self, tid: str, events: list[LedgerEvent]) -> LedgerEvent | None:
        """Le ``task.review_judged`` READY de la dernière relecture de l'entrée en revue en cours."""
        entered = entered_review_at(self._service, tid)
        reviews = [e for e in events if e.event_type == REVIEW_LAUNCHED_EVENT and e.created_at >= entered]
        if not reviews:
            return None
        judged = [e for e in events if e.event_type == REVIEW_JUDGED_EVENT and e.payload.get("review") == reviews[-1].id]
        return judged[-1] if judged and judged[-1].payload.get("verdict") == READY else None

    # -- la file ---------------------------------------------------------------

    def _advance(self, tid: str, judged: LedgerEvent, position: int, launch: dict[str, Any]) -> MergerOutcome:
        task = self._service.require(tid)
        wt = find_task_worktree(self._service, tid)
        if wt is None or not wt.path.is_dir():
            return MergerOutcome(tid, "refused", "refused", "worktree de la tâche introuvable : rien à fusionner")
        events = self._service.ledger.events_for(tid)
        if not any(e.event_type == MERGE_QUEUED_EVENT and e.payload.get("judged") == judged.id for e in events):
            self._note(MERGE_QUEUED_EVENT, tid, {"judged": judged.id, "review": judged.payload.get("review"), "position": position + 1})
        pr, error = self._view(wt.branch)
        if error:
            return self._failed(tid, "wait", f"gh pr view {wt.branch} : {error}")
        if pr is None:
            return MergerOutcome(tid, "wait", "waiting", f"aucune PR pour {wt.branch} : le worker l'ouvre")
        if pr.state == "MERGED":
            return self._confirm(task, wt, judged, pr, launch)
        if pr.state != "OPEN":
            return MergerOutcome(tid, "refused", "refused", f"PR #{pr.number} {pr.state} sans fusion", pr.number)
        if pr.draft:
            return MergerOutcome(tid, "wait", "waiting", f"PR #{pr.number} en brouillon", pr.number)
        if pr.base != wt.into:
            return MergerOutcome(tid, "refused", "refused", f"PR #{pr.number} vers {pr.base}, pas vers la cible {wt.into}", pr.number)
        receipt, kind, why = self._fresh(task, wt, judged)
        if kind == "error":
            return self._failed(tid, "wait", why, pr.number)
        if kind == "dirty":
            return self._bounce(task, launch, "dirty", why, pr.number)
        if kind == "stale" or receipt is None:
            return self._requeue(task, judged, why, pr.number)
        local = _rev(wt.path, f"refs/heads/{wt.branch}")
        if local != pr.head:
            realigned = _last(events, MERGE_REALIGNED_EVENT, _index(events, judged))
            if realigned is not None and realigned.payload.get("to") == local and realigned.payload.get("from") == pr.head:
                return self._push(tid, wt, local, pr.number)
            return MergerOutcome(tid, "wait", "waiting", f"tête locale {local[:12]} ≠ tête de la PR {pr.head[:12]}", pr.number)
        ci, detail = self._ci(wt.branch)
        if ci == "unknown":
            return self._failed(tid, "wait", f"CI illisible : {detail}", pr.number)
        if ci == "red":
            return self._bounce(task, launch, "ci_red", detail, pr.number)
        if ci == "pending":
            return MergerOutcome(tid, "wait", "waiting", f"CI en attente : {detail}", pr.number)
        fetched = _git(self._home, "fetch", "-q", self._remote, wt.into)
        if fetched.returncode != 0:
            return self._failed(tid, "wait", f"git fetch {self._remote} {wt.into} : {_err(fetched)}", pr.number)
        target = f"refs/remotes/{self._remote}/{wt.into}"
        if _git(wt.path, "merge-base", "--is-ancestor", target, local).returncode != 0:
            if position >= self._policy.merger.realign_max:
                return MergerOutcome(tid, "wait", "waiting", f"en retard sur {wt.into}, en file (position {position + 1}) : "
                                     f"{self._policy.merger.realign_max} réalignement(s) à la fois", pr.number)
            return self._realign(task, wt, local, receipt, launch, pr.number)
        if position > 0:
            return MergerOutcome(tid, "wait", "waiting", f"prête, en file (position {position + 1}) : une fusion à la fois", pr.number)
        return self._merge(task, wt, judged, receipt, pr, local, launch)

    def _fresh(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent) -> tuple[ReviewReceipt | None, str, str]:
        """``(reçu, genre, raison)`` : genre ``""`` si le READY jugé tient pour l'arbre présent, sinon ``stale``,
        ``dirty`` (modifications non commitées) ou ``error`` (git muet)."""
        try:
            receipt = read_receipt(wt.path, task.id)
        except ValueError as exc:
            return None, "stale", f"reçu illisible ({exc})"
        if receipt is None or receipt.verdict != READY:
            return None, "stale", "le reçu jugé READY n'est plus là"
        if receipt.written_at > judged.created_at:
            return None, "stale", "reçu réécrit depuis le jugement READY"
        if receipt.evidence_pack_sha256 != evidence_pack_digest(wt.path, task.id):
            return None, "stale", "le pack de preuve a changé depuis le READY"
        if receipt.criteria and tuple(c.criterion for c in receipt.criteria) != tuple(task.acceptance):
            return None, "stale", "les critères ont changé depuis le READY"
        excludes = [f":(exclude){p}" for p in ("_grimoire-output", *RECEIPT_FINGERPRINT_EXCLUDES)]
        status = _git(wt.path, "status", "--porcelain", "--untracked-files=all", "--", ".", *excludes)
        if status.returncode != 0:
            return None, "error", f"git status du worktree : {_err(status)}"
        if status.stdout.strip():
            changed = ", ".join(line[3:] for line in status.stdout.splitlines()[:5])
            return None, "dirty", f"modifications non commitées dans le worktree ({changed}) : ce qui serait fusionné n'est pas ce qui a été relu"
        current = receipt_fingerprint(wt.path)
        if current == receipt.tree_fingerprint:
            return receipt, "", ""
        events = self._service.ledger.events_for(task.id)
        realigned = [e for e in events[_index(events, judged) + 1:] if e.event_type == MERGE_REALIGNED_EVENT]
        if any(e.payload.get("reviewed_fingerprint") == receipt.tree_fingerprint and e.payload.get("fingerprint_after") == current
               for e in realigned):
            return receipt, "", ""
        return None, "stale", "l'arbre a changé depuis le READY (autrement que par un réalignement du merger)"

    # -- remote --------------------------------------------------------------

    def _view(self, branch: str) -> tuple[_PullRequest | None, str]:
        result = self._exec(
            ["gh", "pr", "view", branch, "--json", "number,state,headRefOid,baseRefName,isDraft,mergeCommit"], self._home, None,
        )
        if result.returncode != 0:
            if "no pull requests found" in result.stderr.lower():
                return None, ""
            return None, f"sortie {result.returncode} : {result.stderr.strip()[:200]}"
        try:
            data = json.loads(result.stdout)
            commit = data.get("mergeCommit") or {}
            pr = _PullRequest(
                int(data["number"]), str(data["state"]).upper(), str(data.get("headRefOid") or ""),
                str(data.get("baseRefName") or ""), bool(data.get("isDraft")),
                str(commit.get("oid") or "") if isinstance(commit, dict) else "",
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return None, "réponse de gh pr view illisible"
        if not _SHA.fullmatch(pr.head):
            return None, "gh pr view ne donne pas la tête de la PR (headRefOid)"
        return pr, ""

    def _checks(self, branch: str, *extra: str) -> list[dict[str, Any]] | None:
        result = self._exec(["gh", "pr", "checks", branch, "--json", "name,bucket", *extra], self._home, None)
        if result.returncode == 1 and not result.stdout.strip() and "no checks" in result.stderr.lower():
            return []
        if result.returncode not in _GH_CHECKS_EXIT or not result.stdout.strip():
            return None
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return None
        return rows

    def _ci(self, branch: str) -> tuple[str, str]:
        """``(green|red|pending|unknown, détail)`` : les checks requis par la protection de branche, à défaut tous hors ignorés."""
        required = self._checks(branch, "--required")
        if required:
            rows, scope = required, "checks requis"
        else:
            every = self._checks(branch)
            if every is None:
                return "unknown", "gh pr checks illisible ou en échec"
            ignored = self._policy.merger.ignored_checks
            rows = [r for r in every if not any(fnmatch.fnmatchcase(str(r.get("name", "")), pat) for pat in ignored)]
            scope = f"checks hors {', '.join(ignored)}" if ignored else "tous les checks"
        if not rows:
            return "pending", f"aucun check rapporté ({scope}) : la CI verte est exigée"
        buckets = [str(r.get("bucket", "")) for r in rows]
        red = [str(r.get("name")) for r, b in zip(rows, buckets, strict=True) if b in _RED_BUCKETS]
        if red:
            return "red", f"{len(red)}/{len(rows)} {scope} en échec : {', '.join(red[:5])}"
        if any(b not in _GREEN_BUCKETS for b in buckets):
            return "pending", f"{sum(1 for b in buckets if b not in _GREEN_BUCKETS)}/{len(rows)} {scope} en cours"
        return "green", f"{len(rows)} {scope} verts"

    # -- réalignement --------------------------------------------------------

    def _realign(
        self, task: MissionTask, wt: TaskWorktree, local: str, receipt: ReviewReceipt, launch: dict[str, Any], pr: int,
    ) -> MergerOutcome:
        """``origin/<cible>`` fusionnée dans le worktree (jamais de rebase) ; conflit limité au CHANGELOG : union contrôlée."""
        tid = task.id
        target = f"refs/remotes/{self._remote}/{wt.into}"
        target_sha = _rev(wt.path, target)
        done = _git(wt.path, "merge", "--no-ff", "--no-edit", "-m", f"Merge {wt.into} into {wt.branch} (merger, #876)", target)
        union = False
        if done.returncode != 0:
            conflicted = [p for p in _git(wt.path, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if p.strip()]
            if not conflicted:
                _git(wt.path, "merge", "--abort")
                return self._failed(tid, "realign", f"git merge {target} : {_err(done)}", pr)
            if conflicted != [CHANGELOG]:
                _git(wt.path, "merge", "--abort")
                return self._bounce(task, launch, "conflict", f"conflit avec {wt.into} sur {', '.join(conflicted[:10])}", pr)
            refused = self._union_changelog(wt.path, target)
            if refused:
                _git(wt.path, "merge", "--abort")
                return self._bounce(task, launch, "conflict", refused, pr)
            committed = _git(wt.path, "commit", "--no-edit", "-q")
            if committed.returncode != 0:
                _git(wt.path, "merge", "--abort")
                return self._failed(tid, "realign", f"git commit du réalignement : {_err(committed)}", pr)
            union = True
        head = _rev(wt.path, "HEAD")
        self._note(MERGE_REALIGNED_EVENT, tid, {
            "from": local, "to": head, "into": wt.into, "into_sha": target_sha, "changelog_union": union,
            "reviewed_fingerprint": receipt.tree_fingerprint, "fingerprint_after": receipt_fingerprint(wt.path),
        })
        pushed = self._push(tid, wt, head, pr)
        if pushed.status != "done":
            return pushed
        how = " (CHANGELOG : union des deux côtés)" if union else ""
        return MergerOutcome(tid, "realign", "done", f"{wt.into} fusionnée dans {wt.branch}{how}, poussée : la CI repart", pr)

    def _union_changelog(self, wt: Path, target: str) -> str:
        """Résout le conflit du CHANGELOG par l'union des deux côtés ; ``""`` si aucune ligne de la cible n'est perdue."""
        stages: dict[int, bytes] = {}
        for stage in (1, 2, 3):
            code, body = _git_bytes(wt, "show", f":{stage}:{CHANGELOG}")
            if code != 0 and stage != 1:
                return f"{CHANGELOG} absent d'un côté de la fusion : pas d'union possible"
            stages[stage] = body if code == 0 else b""
        scratch = Path(_git(wt, "rev-parse", "--absolute-git-dir").stdout.strip()) / "grimoire-union"
        scratch.mkdir(parents=True, exist_ok=True)
        ours, base, theirs = scratch / "ours", scratch / "base", scratch / "theirs"
        ours.write_bytes(stages[2])
        base.write_bytes(stages[1])
        theirs.write_bytes(stages[3])
        merged = _git(wt, "merge-file", "--union", "-L", "branche", "-L", "base", "-L", "cible", str(ours), str(base), str(theirs))
        if merged.returncode < 0 or merged.returncode > 127:
            return f"git merge-file --union : {_err(merged)}"
        (wt / CHANGELOG).write_bytes(ours.read_bytes())
        added = _git(wt, "add", "--", CHANGELOG)
        if added.returncode != 0:
            return f"git add {CHANGELOG} : {_err(added)}"
        diff = _git(wt, "diff", "--cached", target, "--", CHANGELOG)
        lost = [ln for ln in diff.stdout.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        if diff.returncode != 0 or lost:
            why = f"{len(lost)} ligne(s) de la cible perdue(s), dont {lost[0][1:80]!r}" if lost else _err(diff)
            return f"union du {CHANGELOG} refusée : {why}"
        return ""

    def _push(self, tid: str, wt: TaskWorktree, head: str, pr: int) -> MergerOutcome:
        """Pousse *head* sur ``task/<id>`` : jamais forcé, un push non fast-forward est refusé par la remote."""
        done = _git(wt.path, "push", "-q", self._remote, f"{head}:refs/heads/{wt.branch}", timeout=_PUSH_TIMEOUT_S)
        if done.returncode != 0:
            return self._failed(tid, "push", f"git push {wt.branch} : {_err(done)}", pr)
        self._note(MERGE_PUSHED_EVENT, tid, {"head": head, "branch": wt.branch})
        return MergerOutcome(tid, "push", "done", f"{wt.branch} poussée à {head[:12]}", pr)

    # -- fusion --------------------------------------------------------------

    def _guarded_categories(self, task: MissionTask) -> list[str]:
        """Les catégories de la tâche que la politique garde à l'humain (hors ``merge``/``accept``, tranchés par le mandat)."""
        from grimoire.missions.mandate import load_escalation, task_categories

        policy = load_escalation(self._home)
        return sorted(c for c in task_categories(task) if c not in _GESTURE_CATEGORIES and not policy.delegates(c))

    def _human_approval(self, events: list[LedgerEvent], gesture: str, after: int) -> LedgerEvent | None:
        """La reprise (``task.merge_resumed``) d'un « oui » humain à *gesture*, postérieure à *after* et pas encore consommée."""
        resumed = _last(events, MERGE_RESUMED_EVENT, after)
        if resumed is None or resumed.payload.get("gesture") != gesture:
            return None
        consumed = MERGED_EVENT if gesture == "merge" else MERGE_ACCEPTED_EVENT  # un gh en échec ne la consomme pas
        return None if _last(events, consumed, _index(events, resumed)) is not None else resumed

    def _merge(
        self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, receipt: ReviewReceipt, pr: _PullRequest,
        local: str, launch: dict[str, Any],
    ) -> MergerOutcome:
        from grimoire.missions.mandate import (
            MandateRefusedError,
            MandateStore,
            acting_as_orchestrator,
            require_authority,
        )

        tid = task.id
        events = self._service.ledger.events_for(tid)
        provenance = {"kind": "receipt", "ref": f"{tid}:review={judged.payload.get('review')}:head={local[:12]}"}
        human = self._human_approval(events, "merge", _index(events, judged))
        mandate_id = ""
        if human is not None and human.payload.get("head") == local:
            provenance = {"kind": "rule", "ref": f"réponse humaine {human.payload.get('question_id')}"}
            origin = ORIGIN_HUMAN
        else:
            guarded = self._guarded_categories(task)
            if guarded:
                return self._escalate(task, "merge", f"catégorie gardée à l'humain : {', '.join(guarded)}", pr.number, local)
            try:
                with acting_as_orchestrator():
                    auth = require_authority(
                        self._home, "merge", task, ORIGIN_ORCHESTRATOR, what=f"Fusion refusée pour {tid}",
                        provenance=provenance, record=False,
                    )
                if auth is None:  # pragma: no cover - l'origine est orchestrator : jamais None
                    raise MandateRefusedError("guard_error", "autorité sans mandat")
                MandateStore(self._home).check_receipt_provenance(task, receipt)  # verrou 7 : la relecture qui fonde la fusion
            except (MandateRefusedError, GrimoireError) as exc:
                return self._escalate(task, "merge", str(exc), pr.number, local)
            mandate_id, origin = auth.mandate_id, ORIGIN_ORCHESTRATOR
        self._note(MERGE_REQUESTED_EVENT, tid, {
            "pr": pr.number, "head": local, "origin": origin, "mandate_id": mandate_id, "provenance": provenance,
        })
        result = self._exec(["gh", "pr", "merge", str(pr.number), "--squash", "--match-head-commit", local], self._home, None)
        if result.returncode != 0:
            return self._failed(tid, "merge", f"gh pr merge #{pr.number} : {(result.stderr or result.stdout).strip()[:300]}", pr.number)
        after, error = self._view(wt.branch)
        if error or after is None:
            return self._failed(tid, "unconfirmed", f"fusion demandée, non constatée : {error or 'PR introuvable'}", pr.number)
        return self._confirm(task, wt, judged, after, launch)

    def _confirm(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, pr: _PullRequest, launch: dict[str, Any]) -> MergerOutcome:
        """``task.merged`` seulement si la remote le dit : PR ``MERGED``, commit de fusion contenu dans la cible."""
        tid = task.id
        if pr.state != "MERGED" or not _SHA.fullmatch(pr.merge_commit):
            return MergerOutcome(tid, "unconfirmed", "waiting", f"PR #{pr.number} {pr.state} : fusion non constatée, revue au tick suivant", pr.number)
        fetched = _git(self._home, "fetch", "-q", self._remote, wt.into)
        if fetched.returncode != 0:
            return self._failed(tid, "unconfirmed", f"git fetch {self._remote} {wt.into} : {_err(fetched)}", pr.number)
        target = f"refs/remotes/{self._remote}/{wt.into}"
        if _git(self._home, "merge-base", "--is-ancestor", pr.merge_commit, target).returncode != 0:
            return MergerOutcome(tid, "unconfirmed", "waiting", f"commit de fusion {pr.merge_commit[:12]} absent de {target}", pr.number)
        local = _rev(wt.path, f"refs/heads/{wt.branch}")
        _receipt, kind, _why = self._fresh(task, wt, judged)
        reviewed = pr.head == local and not kind
        events = self._service.ledger.events_for(tid)
        requested = next((e for e in reversed(events) if e.event_type == MERGE_REQUESTED_EVENT and e.payload.get("head") == pr.head), None)
        # Le geste au journal d'autorité d'abord (dédoublonné) : une coupure avant ``task.merged`` le retrouve inscrit.
        self._record_gesture(tid, requested)
        self._service.ledger.append_event(MERGED_EVENT, tid, "task", MERGER_ACTOR, {
            "into": wt.into, "base_sha": wt.base_sha, "head_sha": pr.head, "merged_sha": pr.merge_commit, "pr": pr.number,
            "via": "gh", "reviewed": reviewed, "judged": judged.id,
            "origin": str(requested.payload.get("origin", "")) if requested else "remote",
            "mandate_id": str(requested.payload.get("mandate_id", "")) if requested else "",
        })
        accepted = self._accept(tid, launch)
        merged = f"PR #{pr.number} fusionnée à {pr.merge_commit[:12]}"
        return MergerOutcome(tid, accepted.action, accepted.status, f"{merged} ; {accepted.detail}", pr.number)

    def _record_gesture(self, tid: str, requested: LedgerEvent | None) -> None:
        """Inscrit la fusion prise sous mandat au journal d'autorité, une fois, une fois la fusion constatée."""
        from grimoire.missions.mandate import Authorization, MandateStore

        if requested is None or not requested.payload.get("mandate_id"):
            return
        provenance = {str(k): str(v) for k, v in dict(requested.payload.get("provenance") or {}).items()}
        store = MandateStore(self._home)
        if any(g.get("gesture") == "merge" and g.get("provenance") == provenance for g in store.gestures_taken(tid)):
            return
        store.record_gesture(Authorization(str(requested.payload["mandate_id"]), "merge", tid, provenance))

    # -- acceptation ---------------------------------------------------------

    def _accept(self, tid: str, launch: dict[str, Any]) -> MergerOutcome:
        from grimoire.missions.human_acceptance import accept_task
        from grimoire.missions.mandate import MandateRefusedError, acting_as_orchestrator

        task = self._service.require(tid)
        wt = find_task_worktree(self._service, tid)
        if wt is None or not wt.path.is_dir():
            return MergerOutcome(tid, "accept", "refused", "worktree de la tâche introuvable : rien à accepter")
        events = self._service.ledger.events_for(tid)
        merged = _last(events, MERGED_EVENT, _last_launch(events))
        if merged is None:  # pragma: no cover - l'appelant le garantit
            return MergerOutcome(tid, "accept", "refused", "aucune fusion constatée")
        pr = int(merged.payload.get("pr") or 0)
        if _last(events, MERGE_ACCEPTED_EVENT, _index(events, merged)) is None:
            human = self._human_approval(events, "accept", _index(events, merged))
            reason = f"PR #{pr} fusionnée à {str(merged.payload.get('merged_sha'))[:12]}, relue READY, CI verte"
            try:
                if human is not None:
                    actor = str(human.payload.get("actor") or "human")
                    outcome = accept_task(
                        task, self._home, wt.path / _EVIDENCE, f"{reason} — réponse humaine {human.payload.get('question_id')}",
                        actor, origin=ORIGIN_HUMAN, proof_root=wt.path,
                    )
                    origin, mandate = ORIGIN_HUMAN, ""
                else:
                    guarded = self._guarded_categories(task)
                    if guarded:
                        return self._escalate(task, "accept", f"catégorie gardée à l'humain : {', '.join(guarded)}", pr)
                    if not merged.payload.get("reviewed"):
                        return self._escalate(task, "accept", "la tête fusionnée n'est pas celle que la relecture READY couvre", pr)
                    provenance = {"kind": "verdict", "ref": f"{tid}:merged={str(merged.payload.get('merged_sha'))[:12]}:judged={merged.payload.get('judged')}"}
                    with acting_as_orchestrator():
                        outcome = accept_task(
                            task, self._home, wt.path / _EVIDENCE, reason, MERGER_ACTOR, origin=ORIGIN_ORCHESTRATOR,
                            provenance=provenance, proof_root=wt.path,
                        )
                    origin, mandate = ORIGIN_ORCHESTRATOR, outcome.verdict.created_by.removeprefix(f"{ORIGIN_ORCHESTRATOR}:")
            except (MandateRefusedError, GrimoireError, ValueError) as exc:
                return self._escalate(task, "accept", str(exc), pr)
            self._note(MERGE_ACCEPTED_EVENT, tid, {"verdict": outcome.verdict.id, "pack": outcome.pack.id, "origin": origin, "mandate_id": mandate})
        return self._close(tid, wt, pr)

    def _close(self, tid: str, wt: TaskWorktree, pr: int) -> MergerOutcome:
        """Clôture par le service ouvert dans le worktree : ses gates y lisent le reçu et la preuve."""
        from grimoire.missions.service import TaskService

        try:
            TaskService(wt.path).transition(tid, TaskState.CLOSED, MERGER_ACTOR, f"fusionnée (PR #{pr}) et acceptée")
        except (GrimoireError, ValueError) as exc:
            return MergerOutcome(tid, "close", "failed", f"acceptée, clôture refusée : {exc}", pr)
        return MergerOutcome(tid, "accept", "done", f"acceptée et close (PR #{pr}) : le janitor nettoie", pr)

    # -- retours et escalades --------------------------------------------------

    def _requeue(self, task: MissionTask, judged: LedgerEvent, why: str, pr: int) -> MergerOutcome:
        """Un READY qui ne tient plus : la tâche repart à la relecture (une nouvelle entrée en revue), une fois par READY."""
        events = self._service.ledger.events_for(task.id)
        if _last(events, MERGE_REQUEUED_EVENT, _index(events, judged)) is None:
            if self._cycle(events, MERGE_REQUEUED_EVENT) >= self._policy.merger.max_cycles:
                return self._escalate(task, "bounce", f"READY périmé à répétition ({why})", pr)
            self._note(MERGE_REQUEUED_EVENT, task.id, {"judged": judged.id, "why": why})
        return MergerOutcome(task.id, "stale", "done", f"READY périmé ({why}) : relu de nouveau", pr)

    @staticmethod
    def _cycle(events: list[LedgerEvent], kind: str) -> int:
        """Le rang de ce retour depuis la dernière réponse humaine (« reprendre » remet à zéro)."""
        answered = max((i for i, e in enumerate(events) if e.event_type == HUMAN_ANSWERED_EVENT), default=-1)
        return 1 + sum(1 for e in events[answered + 1:] if e.event_type == kind)

    def _bounce(self, task: MissionTask, launch: dict[str, Any], reason: str, detail: str, pr: int) -> MergerOutcome:
        """CI rouge, conflit, arbre sale : retour au worker (reprise de sa session), ou escalade au cycle ``max_cycles``."""
        tid, max_cycles = task.id, self._policy.merger.max_cycles
        events = self._service.ledger.events_for(tid)
        cycle, worker = self._cycle(events, MERGE_BOUNCED_EVENT), str(launch.get("session_id") or "")
        if cycle >= max_cycles or not worker:
            why = f"{detail} — {cycle} retour(s) de fusion" if worker else f"{detail} — aucune session de worker à reprendre"
            escalated = self._escalate(task, "bounce", why, pr)
            if escalated.status == "done":
                self._note(MERGE_BOUNCED_EVENT, tid, {"reason": reason, "detail": detail[:600], "cycle": cycle, "escalated": True})
            return escalated
        ledger = self._service.ledger
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason=f"fusion refusée ({reason}, cycle {cycle}/{max_cycles})")
        except GrimoireError as exc:
            return MergerOutcome(tid, "bounce", "failed", f"retour au worker refusé : {exc}", pr)
        argv = [self._claude, "--bg", "--resume", worker]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(
            f"La fusion de {tid} est refusée : {detail}. Corrige dans ce worktree, commite, pousse sur task/{tid} "
            f"(jamais de push forcé), revérifie avec les checks de la tâche, puis remets-la en revue "
            f"(`grimoire task move {tid} --to needs_verification`)."
        )
        result = self._exec(argv, Path(str(launch.get("cwd") or ".")), session_env(worker))
        if result.returncode != 0:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="reprise du worker échouée")
            return self._failed(tid, "bounce", f"worker non repris ({_err(result)}), tâche remise en revue", pr)
        self._note(MERGE_BOUNCED_EVENT, tid, {"reason": reason, "detail": detail[:600], "cycle": cycle})
        self._service.comment(tid, f"Fusion refusée (cycle {cycle}/{max_cycles}) : {detail}", MERGER_ACTOR)
        return MergerOutcome(tid, "bounce", "done", f"retour au worker ({reason}, cycle {cycle}/{max_cycles}) : {detail}", pr)

    def _escalate(self, task: MissionTask, gesture: str, why: str, pr: int, head: str = "") -> MergerOutcome:
        """``needs_verification → running → waiting(human)`` avec la question ; une question non posée remet en revue."""
        tid, ledger = task.id, self._service.ledger
        qid = new_question_id()
        if gesture == "merge":
            text = (f"Fusionner la PR #{pr} de task/{tid} ? Le merger ne la fusionne pas lui-même : {why}. "
                    "« fusionner » la fait fusionner au tick suivant (tout est revérifié), « reprendre » la rend au worker, "
                    "« abandonner » annule la tâche.")
        elif gesture == "accept":
            text = (f"Accepter {tid} (PR #{pr} fusionnée) ? Le merger ne l'accepte pas lui-même : {why}. "
                    "« accepter » l'accepte et la ferme au tick suivant, « reprendre » la rend au worker.")
        else:
            text = f"La fusion de {tid} échoue à répétition ({why}) — reprendre la tâche ou l'abandonner ?"
        # Noté avant la question : la réponse qui réveille la tâche retrouve toujours la question du merger.
        self._note(MERGE_ESCALATED_EVENT, tid, {"gesture": gesture, "question_id": qid, "why": why[:600], "pr": pr, "head": head})
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason=f"escalade : {why[:200]}")
        except GrimoireError as exc:
            return MergerOutcome(tid, "escalate", "failed", f"escalade refusée : {exc}", pr)
        try:
            self._service.ask(tid, text, MERGER_ACTOR, kind="merge" if gesture == "merge" else "choice",
                              options=_OPTIONS[gesture], question_id=qid)
        except (GrimoireError, ValueError) as exc:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="escalade non posée")
            return MergerOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc}) : tâche remise en revue", pr)
        return MergerOutcome(tid, "escalate", "done", f"« {APPROVE.get(gesture, 'reprendre')} ? » posée à l'humain : {why}", pr)

    def _resume(self, tid: str) -> MergerOutcome:
        """Le « oui » humain reçu : la tâche réveillée revient en revue, aux mains du merger (jamais d'un nouveau worker)."""
        events = self._service.ledger.events_for(tid)
        pending = _approval_pending(events)
        if pending is None:  # pragma: no cover - l'appelant le garantit
            return MergerOutcome(tid, "resume", "skipped", "aucune réponse à reprendre")
        escalation, answer = pending
        service, ledger = self._service, self._service.ledger
        try:
            if service.require(tid).status is TaskState.READY:
                service.claim(tid, MERGER_ACTOR, host=_HOST)
            if service.require(tid).status is TaskState.CLAIMED:
                ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason="reprise par le merger")
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="reprise par le merger")
        except GrimoireError as exc:
            return self._failed(tid, "resume", f"reprise refusée : {exc}")
        gesture = str(escalation.payload.get("gesture"))
        self._note(MERGE_RESUMED_EVENT, tid, {
            "gesture": gesture, "question_id": escalation.payload.get("question_id"), "answer_event": answer.id,
            "actor": answer.payload.get("actor_id") or answer.actor_id, "head": escalation.payload.get("head", ""),
        })
        return MergerOutcome(tid, "resume", "done", f"réponse humaine « {APPROVE[gesture]} » : reprise par le merger", int(escalation.payload.get("pr") or 0))

    # -- journal -------------------------------------------------------------

    def _note(self, kind: str, tid: str, payload: dict[str, Any]) -> None:
        self._service.ledger.append_event(kind, tid, "task", MERGER_ACTOR, {"task_id": tid, **payload})

    def _failed(self, tid: str, action: str, detail: str, pr: int = 0) -> MergerOutcome:
        """Un échec de ``gh`` ou de ``git`` : rapporté, compté au disjoncteur, rejoué au tick suivant."""
        state = record_failure(self._home, self._policy.breaker_threshold, f"{tid} : merger ({action}) : {detail}")
        return MergerOutcome(tid, action, "failed", detail + (" — disjoncteur ouvert" if state.open else ""), pr)
