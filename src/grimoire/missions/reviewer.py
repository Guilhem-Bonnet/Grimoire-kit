"""Le rôle ``reviewer`` : une session distincte relit chaque tâche lancée qui arrive en revue (#876, lot 3, verrous 7 et 8).

Étape du tick, après le lanceur, active seulement si ``orchestrator.roles.reviewer.enabled`` (désactivé par défaut).
Pour chaque tâche que le lanceur a lancée (``task.session_launched``) et qui est en ``needs_verification`` :

1. **Lancement** — une fois par entrée en revue : ``claude --bg --name grimoire-review-<id> --session-id <uuid> …`` dans
   le worktree de la tâche. La session est neuve : elle ne voit pas le raisonnement du worker, seulement les critères,
   le diff de la branche et le pack de preuve. Le modèle diffère de celui du worker quand la politique ne l'impose pas.
   Sous mandat actif, l'identifiant est inscrit au journal d'autorité en rôle ``reviewer`` pour la tâche *avant* le
   lancement (:func:`~grimoire.missions.launcher.attest_session`) : c'est ce qui atteste le reçu (verrou 7).
2. **Jugement** — quand un reçu postérieur au lancement existe : sa session doit être celle du relecteur lancé.
   - Écrit par la session du worker (ou en son nom) : **verdict forgé** — disjoncteur ouvert et, sous mandat, mandat
     révoqué (:meth:`~grimoire.missions.mandate.MandateStore.revoke_on_forged_verdict`).
   - D'une autre session : refusé, rien ne bouge (un humain peut relire et accepter lui-même).
   - ``READY`` et frais (:func:`~grimoire.missions.review_receipt.check_receipt`) : noté ; l'acceptation et la fusion
     sont les lots suivants.
   - ``NOT-READY`` : la tâche repart au worker (``needs_verification → running``), avec les constats en note et la
     session du worker reprise (``claude --bg --resume <son uuid>``) ; au cycle ``max_cycles`` (2 par défaut), ou sans
     session à reprendre, escalade : la tâche passe ``waiting(human)`` avec sa question (désaccord persistant).
   La session du relecteur est arrêtée une fois le reçu jugé.

Chaque étape est notée au ledger (``task.review_launched``, ``task.review_judged``, ``task.review_forged``) : le tick
est rejouable, un reçu n'est jugé qu'une fois. Les binaires sont injectables (*exec_*), comme pour le lanceur.

**Limites** : un cycle compte chaque ``NOT-READY``, que les checks soient verts (désaccord) ou rouges (accord sur un
échec) — le planner du lot 5 fera la différence en replanifiant ; l'identifiant de session d'un processus se lit dans
son environnement, qu'un processus du même utilisateur peut lire (``/proc``) : l'attestation rend la falsification
visible et coûteuse, pas impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.breaker import read_breaker, record_failure, trip
from grimoire.missions.launcher import (
    _BG_ID,
    _TIER_ALIAS,
    WORKER_ACTOR,
    attest_session,
    default_exec,
    launched_sessions,
    session_env,
)
from grimoire.missions.review_receipt import CRITERION_MET, NOT_READY, READY, check_receipt, read_receipt
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import find_task_worktree

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "REVIEWER_ACTOR",
    "REVIEW_FORGED_EVENT",
    "REVIEW_JUDGED_EVENT",
    "REVIEW_LAUNCHED_EVENT",
    "ReviewOutcome",
    "ReviewStage",
]

REVIEWER_ACTOR = "reviewer"
REVIEW_LAUNCHED_EVENT = "task.review_launched"
REVIEW_JUDGED_EVENT = "task.review_judged"
REVIEW_FORGED_EVENT = "task.review_forged"
#: Un autre modèle que celui du worker, quand la politique ne l'impose pas.
_ALTERNATE = {"opus": "sonnet", "sonnet": "opus", "haiku": "sonnet"}
_DEFAULT_MODEL = "sonnet"


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """Ce que l'étape a fait d'une tâche : ``action`` (launch, ready, bounce, escalate, forged, refused) et ``status``."""

    task_id: str
    action: str
    status: str
    detail: str = ""
    session: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail,
                "session": self.session, "model": self.model}


def _family(model: str) -> str:
    lowered = model.lower()
    return next((alias for alias in _ALTERNATE if alias in lowered), lowered)


class ReviewStage:
    """L'étape ``reviewer`` du tick : lance les relectures dues, juge les reçus rendus."""

    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec, claude_bin: str = "claude"
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin

    # -- lecture du ledger ---------------------------------------------------

    def _events(self, task_id: str, kind: str) -> list[LedgerEvent]:
        return [e for e in self._service.ledger.events_for(task_id) if e.event_type == kind]

    def _entered_review_at(self, task_id: str) -> str:
        moves = [
            e.created_at for e in self._service.ledger.events_for(task_id)
            if e.event_type == "task.transitioned" and e.payload.get("to_state") == TaskState.NEEDS_VERIFICATION.value
        ]
        return moves[-1] if moves else ""

    def _open_reviews(self) -> int:
        """Les relectures lancées et pas encore jugées, sur des tâches encore en revue : le WIP du relecteur."""
        count = 0
        for task_id in launched_sessions(self._service):
            task = self._service.ledger.get_task(task_id)
            if task is None or task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            judged = {e.payload.get("review") for e in self._events(task_id, REVIEW_JUDGED_EVENT)}
            count += sum(1 for e in self._events(task_id, REVIEW_LAUNCHED_EVENT) if e.id not in judged)
        return count

    # -- étape ---------------------------------------------------------------

    def __call__(self) -> tuple[ReviewOutcome, ...]:
        if not self._policy.reviewer.enabled:
            return ()
        outcomes: list[ReviewOutcome] = []
        for task_id, launch in launched_sessions(self._service).items():
            task = self._service.ledger.get_task(task_id)
            if task is None or task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            entered = self._entered_review_at(task_id)
            reviews = [e for e in self._events(task_id, REVIEW_LAUNCHED_EVENT) if e.created_at >= entered]
            if not reviews:
                outcomes.append(self._launch(task, launch))
                continue
            review = reviews[-1]
            if any(e.payload.get("review") == review.id for e in self._events(task_id, REVIEW_JUDGED_EVENT)):
                continue
            judged = self._judge(task, launch, review)
            if judged is not None:
                outcomes.append(judged)
        return tuple(outcomes)

    # -- lancement -----------------------------------------------------------

    def _model(self, worker_model: str) -> str:
        role = self._policy.reviewer
        if role.model:
            return role.model
        chosen = _TIER_ALIAS.get(role.tier, "")
        if worker_model and (not chosen or _family(chosen) == _family(worker_model)):
            return _ALTERNATE.get(_family(worker_model), _DEFAULT_MODEL)
        return chosen or _DEFAULT_MODEL

    def _launch(self, task: MissionTask, launch: dict[str, Any]) -> ReviewOutcome:
        tid, role = task.id, self._policy.reviewer
        root = self._service.home_root
        breaker = read_breaker(root)
        if breaker.open:
            return ReviewOutcome(tid, "launch", "skipped", f"disjoncteur ouvert : {breaker.reason}")
        open_reviews = self._open_reviews()
        if open_reviews >= role.wip:
            return ReviewOutcome(tid, "launch", "skipped", f"WIP du relecteur atteint ({open_reviews}/{role.wip})")
        cwd = Path(str(launch.get("cwd") or ""))
        worktree = find_task_worktree(self._service, tid)
        if not launch.get("cwd") or not cwd.is_dir() or worktree is None:
            return ReviewOutcome(tid, "launch", "failed", "worktree de la tâche introuvable : rien à relire")
        session_id, refused = attest_session(self._service, REVIEWER_ACTOR, tid)
        if refused:
            return ReviewOutcome(tid, "launch", "skipped", refused)
        model = self._model(str(launch.get("model") or ""))
        argv = [self._claude, "--bg", "--name", f"grimoire-review-{tid}", "--session-id", session_id, "--model", model,
                "--permission-mode", role.permission_mode, self._prompt(tid, worktree.base_sha, model)]
        result = self._exec(argv, cwd, session_env(session_id))
        match = _BG_ID.search(result.stdout) if result.returncode == 0 else None
        if match is None:
            detail = (result.stderr or result.stdout).strip()[:300] or f"sortie {result.returncode}"
            state = record_failure(root, self._policy.breaker_threshold, f"{tid} : relecteur non lancé : {detail}")
            return ReviewOutcome(tid, "launch", "failed", f"claude --bg a échoué : {detail}" + (" — disjoncteur ouvert" if state.open else ""))
        self._service.ledger.append_event(
            REVIEW_LAUNCHED_EVENT, tid, "task", REVIEWER_ACTOR,
            {"task_id": tid, "session": match.group(1), "session_id": session_id, "model": model,
             "worker_model": str(launch.get("model") or ""), "cwd": str(cwd)},
        )
        return ReviewOutcome(tid, "launch", "launched", f"relecteur {match.group(1)} dans {cwd}", match.group(1), model)

    @staticmethod
    def _prompt(tid: str, base_sha: str, model: str) -> str:
        return (
            f"Tu es le relecteur de la tâche {tid}. Tu ne vois pas le raisonnement du worker et tu ne le cherches pas : "
            f"tu juges le résultat. Lis les critères d'acceptation (`grimoire task show {tid}`), le diff de la branche "
            f"(`git diff {base_sha}...HEAD`) et le dernier pack de preuve, puis rejoue les commandes qui montrent chaque "
            "critère. Ne modifie aucun fichier suivi du dépôt. Écris ton verdict dans "
            f"`_grimoire-output/reviews/{tid}/verdict.json` (schéma grimoire-review-verdict/v1 : `verdict` READY ou "
            "NOT-READY ; `criteria`, un objet par critère avec `index` à partir de 1, `status` met, unmet ou "
            "unverifiable, `evidence_cmd` et `note` ; `findings` facultatifs), puis lance "
            f"`grimoire task review {tid} --criteria _grimoire-output/reviews/{tid}/verdict.json --reviewer reviewer "
            f"--model {model}`. READY seulement si chaque critère est tenu et montré."
        )

    # -- jugement ------------------------------------------------------------

    def _judge(self, task: MissionTask, launch: dict[str, Any], review: LedgerEvent) -> ReviewOutcome | None:
        tid = task.id
        root = Path(str(review.payload.get("cwd") or launch.get("cwd") or ""))
        try:
            receipt = read_receipt(root, tid)
        except ValueError as exc:
            return ReviewOutcome(tid, "refused", "refused", f"reçu illisible : {exc}")
        if receipt is None or receipt.written_at < review.created_at:
            return None  # la relecture n'a pas encore rendu son reçu
        expected = str(review.payload.get("session_id") or "")
        workers = {str(launch.get("session_id") or ""), task.claim.session_id if task.claim else ""} - {""}
        # Forgé : la session du worker, ou l'acteur qui tient la tâche. Le propriétaire (un humain, souvent) qui relit
        # son propre travail n'est pas un faux : son reçu est seulement refusé plus bas, comme toute autre session.
        if receipt.reviewer_session in workers or receipt.reviewer_id in ({task.claim.actor_id if task.claim else "", WORKER_ACTOR} - {""}):
            return self._forged(task, review, receipt)
        if receipt.reviewer_session != expected:
            return ReviewOutcome(
                tid, "refused", "refused",
                f"reçu d'une autre session ({receipt.reviewer_session or 'aucune'}) que le relecteur lancé ({expected})",
            )
        refused = self._mandate_refusal(task, receipt)
        if refused is not None:
            return refused
        if receipt.verdict == READY:
            refusal = check_receipt(root, task)
            if refusal is not None:
                return ReviewOutcome(tid, "ready", "refused", str(refusal))
            self._record_judged(task, review, READY, 0)
            self._stop(review)
            return ReviewOutcome(tid, "ready", "done", f"READY, {len(receipt.criteria_met)} critère(s) tenu(s)", expected)
        return self._bounce(task, launch, review, receipt)

    def _mandate_refusal(self, task: MissionTask, receipt: ReviewReceipt) -> ReviewOutcome | None:
        """Sous mandat actif, la provenance du reçu au journal d'autorité (verrou 7) ; ``None`` hors mandat ou si elle tient."""
        from grimoire.missions.mandate import MandateRefusedError, MandateStore

        store = MandateStore(self._service.home_root)
        if not store.state().active:
            return None
        try:
            store.check_receipt_provenance(task, receipt)
        except MandateRefusedError as exc:
            if exc.code == "receipt_self_review":
                return self._forged(task, None, receipt)
            return ReviewOutcome(task.id, "refused", "refused", str(exc))
        return None

    def _forged(self, task: MissionTask, review: LedgerEvent | None, receipt: ReviewReceipt) -> ReviewOutcome:
        """Un reçu écrit par le worker, ou en son nom : disjoncteur ouvert, mandat révoqué. Jamais rejoué."""
        from grimoire.missions.mandate import MandateStore

        tid = task.id
        detail = f"reçu {receipt.verdict} de {receipt.reviewer_id!r} (session {receipt.reviewer_session or 'aucune'}) : écrit par le worker"
        trip(self._service.home_root, f"{tid} : verdict forgé — {detail}", source=REVIEWER_ACTOR)
        store = MandateStore(self._service.home_root)
        revoked = store.revoke_on_forged_verdict(tid, detail) if store.state().active else None
        self._service.ledger.append_event(
            REVIEW_FORGED_EVENT, tid, "task", REVIEWER_ACTOR,
            {"task_id": tid, "detail": detail, "mandate_revoked": revoked.id if revoked is not None else ""},
        )
        if review is not None:
            self._record_judged(task, review, "forged", 0)
        return ReviewOutcome(tid, "forged", "done", detail + (f" — mandat {revoked.id} révoqué" if revoked is not None else ""))

    def _record_judged(self, task: MissionTask, review: LedgerEvent, verdict: str, cycle: int) -> None:
        self._service.ledger.append_event(
            REVIEW_JUDGED_EVENT, task.id, "task", REVIEWER_ACTOR,
            {"task_id": task.id, "review": review.id, "verdict": verdict, "cycle": cycle},
        )

    def _stop(self, review: LedgerEvent) -> None:
        short = str(review.payload.get("session") or "")
        if short:
            self._exec([self._claude, "stop", short], Path(str(review.payload.get("cwd") or ".")), None)

    def _bounce(self, task: MissionTask, launch: dict[str, Any], review: LedgerEvent, receipt: ReviewReceipt) -> ReviewOutcome:
        """``NOT-READY`` : retour au worker avec les constats, ou escalade au cycle ``max_cycles``."""
        tid, max_cycles = task.id, self._policy.reviewer.max_cycles
        cycle = 1 + sum(1 for e in self._events(tid, REVIEW_JUDGED_EVENT) if e.payload.get("verdict") == NOT_READY)
        notes = _findings_text(receipt)
        worker_session = str(launch.get("session_id") or "")
        ledger = self._service.ledger
        try:
            ledger.transition_task(
                tid, TaskState.RUNNING, actor_id=REVIEWER_ACTOR, reason=f"relecture NOT-READY (cycle {cycle}/{max_cycles})"
            )
        except GrimoireError as exc:
            return ReviewOutcome(tid, "bounce", "failed", f"retour au worker refusé : {exc}")
        self._record_judged(task, review, NOT_READY, cycle)
        self._stop(review)
        if cycle >= max_cycles or not worker_session:
            why = f"{cycle} relecture(s) NOT-READY" if worker_session else "aucune session de worker à reprendre"
            try:
                self._service.ask(
                    tid,
                    f"Désaccord persistant sur {tid} ({why}) : {notes[:600]} — reprendre la tâche ou l'abandonner ?",
                    REVIEWER_ACTOR,
                    kind="choice",
                    options=("reprendre", "abandonner"),
                )
            except (GrimoireError, ValueError) as exc:
                return ReviewOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc}) : la tâche reste en cours")
            return ReviewOutcome(tid, "escalate", "done", f"escalade à l'humain après {why}")
        self._service.comment(tid, f"Relecture NOT-READY (cycle {cycle}/{max_cycles}) : {notes}", REVIEWER_ACTOR)
        argv = [self._claude, "--bg", "--resume", worker_session]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(
            f"La relecture de {tid} rend NOT-READY : {notes} Corrige, revérifie avec les checks de la tâche, "
            f"puis remets-la en revue (`grimoire task move {tid} --to needs_verification`)."
        )
        result = self._exec(argv, Path(str(launch.get("cwd") or ".")), session_env(worker_session))
        if result.returncode != 0:
            state = record_failure(self._service.home_root, self._policy.breaker_threshold, f"{tid} : reprise du worker : {result.stderr[:200]}")
            return ReviewOutcome(tid, "bounce", "failed", "worker non repris" + (" — disjoncteur ouvert" if state.open else ""))
        return ReviewOutcome(tid, "bounce", "done", f"retour au worker (cycle {cycle}/{max_cycles})", worker_session)


def _findings_text(receipt: ReviewReceipt) -> str:
    parts = [
        f"critère {c.index} {c.status} ({c.criterion})" + (f" — `{c.evidence_cmd}`" if c.evidence_cmd else "")
        + (f" : {c.note}" if c.note else "")
        for c in receipt.criteria if c.status != CRITERION_MET
    ]
    parts += [f"{f.summary}" + (f" — `{f.evidence_cmd}`" if f.evidence_cmd else "") for f in receipt.findings]
    return "; ".join(parts) or "aucun constat détaillé"
