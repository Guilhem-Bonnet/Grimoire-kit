"""Le rôle ``reviewer`` : une session distincte relit chaque tâche lancée qui arrive en revue (#876, lot 3, verrous 7 et 8).

Étape du tick, après le lanceur, active seulement si ``orchestrator.roles.reviewer.enabled`` (désactivé par défaut).
Pour chaque tâche que le lanceur a lancée (``task.session_launched``) et qui est en ``needs_verification`` :

1. **Lancement** — une fois par entrée en revue : ``claude --bg --name grimoire-review-<id> …`` dans le worktree de la
   tâche. La session est neuve : elle ne voit pas le raisonnement du worker, seulement les critères, le diff de la
   branche et le pack de preuve. Le modèle diffère de celui du worker quand la politique ne l'impose pas. Son vrai
   ``sessionId`` est résolu après le lancement dans ``claude agents --json``
   (:func:`~grimoire.missions.launcher.resolve_background_session` : ``--bg`` ignore ``--session-id``, #966) puis, sous
   mandat actif, inscrit au journal d'autorité en rôle ``reviewer`` pour la tâche
   (:func:`~grimoire.missions.launcher.attest_session`) : c'est ce qui atteste le reçu (verrou 7). Identité introuvable
   ou inscription refusée : la session est arrêtée. Le ledger n'en garde que l'empreinte (``session_sha256``).
2. **Jugement** — quand un reçu postérieur au lancement existe, et une seule fois par relecture :
   - écrit par la session du worker : **verdict forgé** — disjoncteur ouvert et, sous mandat, mandat révoqué
     (:meth:`~grimoire.missions.mandate.MandateStore.revoke_on_forged_verdict`) ;
   - d'une autre session que le relecteur lancé : refusé, rien ne bouge (un humain peut relire et accepter lui-même) ;
   - lancé sous mandat : le mandat doit être encore actif et la provenance tenir au journal d'autorité, sinon le reçu
     est refusé **définitivement** — une révocation entre le lancement et le jugement ne lève pas la garde ;
   - ``READY`` frais (:func:`~grimoire.missions.review_receipt.check_receipt`) : scellé au journal d'autorité
     (:mod:`.review_seal` : tête, arbre, pack et empreinte du reçu, qui doivent être ceux que le reçu déclare), puis
     noté ; la fusion et l'acceptation sont au merger (:mod:`.merger`, lot 4), qui comparent à ce sceau. ``READY`` périmé (l'arbre, le pack ou les critères ont changé) : relu de nouveau ;
   - ``NOT-READY`` : la tâche repart au worker (``needs_verification → running``, session du worker reprise par
     ``claude --bg --resume <son uuid>``, constats en note). Une reprise qui échoue remet la tâche en revue et sera
     retentée. Au cycle ``max_cycles`` (2 par défaut, compté depuis la dernière réponse humaine), ou sans session à
     reprendre, escalade : la tâche passe ``waiting(human)`` avec sa question.
   Un relecteur muet au-delà de :data:`REVIEW_TIMEOUT` est arrêté et relancé ; après :data:`MAX_RELAUNCHES` relances
   (muet ou périmé), l'escalade aussi. La session du relecteur est arrêtée une fois son reçu jugé.

Chaque étape est notée au ledger (``task.review_launched``, ``task.review_judged``, ``task.review_forged``) : le tick
est rejouable. Disjoncteur ouvert : rien n'est lancé ni repris. Les binaires sont injectables (*exec_*).

**Limites** : un cycle compte chaque ``NOT-READY``, que les checks soient verts (désaccord) ou rouges (accord sur un
échec) — le planner du lot 5 fera la différence en replanifiant. Les sessions tournent sous le même utilisateur du
système : un processus qui lit l'environnement ou la ligne de commande du relecteur (``/proc``), ou son reçu une fois
écrit, connaît son identifiant et peut le reprendre. L'attestation rend la falsification visible et coûteuse, pas
impossible ; seule une isolation par utilisateur du système la fermerait.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.breaker import read_breaker, record_failure, trip
from grimoire.missions.launcher import (
    _BG_ID,
    _TIER_ALIAS,
    _halt,
    attest_session,
    default_exec,
    launched_sessions,
    resolve_background_session,
    session_env,
)
from grimoire.missions.review_receipt import CRITERION_MET, NOT_READY, READY, check_receipt, read_receipt
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import find_task_worktree
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "MAX_RELAUNCHES",
    "REVIEWER_ACTOR",
    "REVIEW_FORGED_EVENT",
    "REVIEW_JUDGED_EVENT",
    "REVIEW_LAUNCHED_EVENT",
    "REVIEW_TIMEOUT",
    "ReviewOutcome",
    "ReviewStage",
    "entered_review_at",
    "session_digest",
]

REVIEWER_ACTOR = "reviewer"
REVIEW_LAUNCHED_EVENT = "task.review_launched"
REVIEW_JUDGED_EVENT = "task.review_judged"
REVIEW_FORGED_EVENT = "task.review_forged"
#: Au-delà, un relecteur sans reçu est arrêté et relancé.
REVIEW_TIMEOUT = timedelta(hours=2)
#: Relances d'une même entrée en revue (relecteur muet, reçu périmé) avant l'escalade à l'humain.
MAX_RELAUNCHES = 2
#: Verdicts notés qui appellent une nouvelle relecture, et ceux qui clôturent la relecture.
_RELAUNCH = frozenset({"timeout", "stale"})
#: Un autre modèle que celui du worker, quand la politique ne l'impose pas.
_ALTERNATE = {"opus": "sonnet", "sonnet": "opus", "haiku": "sonnet"}
_DEFAULT_MODEL = "sonnet"


def session_digest(session_id: str) -> str:
    """L'empreinte qu'un ledger lisible par le worker garde de l'identifiant du relecteur."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """Ce que l'étape a fait d'une tâche : ``action`` (launch, ready, stale, timeout, bounce, escalate, forged, refused)."""

    task_id: str
    action: str
    status: str
    detail: str = ""
    session: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail,
                "session": self.session, "model": self.model}


def entered_review_at(service: TaskService, task_id: str) -> str:
    """La dernière remise en revue par le worker ; celles du relecteur (reprise ou escalade ratée) n'en sont pas.

    Lot 4 : le merger qui remet une tâche en revue (reprise du worker ratée, question non posée) n'en fait pas une
    nouvelle entrée ; un READY qu'il trouve périmé en est une (``task.merge_requeued``) : il appelle une relecture.
    """
    from grimoire.missions.merger import MERGE_REQUEUED_EVENT, MERGER_ACTOR

    moves = [
        e.created_at for e in service.ledger.events_for(task_id)
        if (e.event_type == "task.transitioned" and e.payload.get("to_state") == TaskState.NEEDS_VERIFICATION.value
            and e.payload.get("actor_id") not in (REVIEWER_ACTOR, MERGER_ACTOR))
        or e.event_type == MERGE_REQUEUED_EVENT
    ]
    return moves[-1] if moves else ""


def _family(model: str) -> str:
    lowered = model.lower()
    return next((alias for alias in _ALTERNATE if alias in lowered), lowered)


def _parse(stamp: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class ReviewStage:
    """L'étape ``reviewer`` du tick : lance les relectures dues, juge les reçus rendus."""

    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec, claude_bin: str = "claude",
        now: datetime | None = None,
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin
        self._now = now

    # -- lecture du ledger ---------------------------------------------------

    def _events(self, task_id: str, kind: str) -> list[LedgerEvent]:
        return [e for e in self._service.ledger.events_for(task_id) if e.event_type == kind]

    def _entered_review_at(self, task_id: str) -> str:
        return entered_review_at(self._service, task_id)

    def _verdicts(self, task_id: str) -> dict[str, str]:
        """Relecture (id de ``task.review_launched``) → verdict noté."""
        return {str(e.payload.get("review")): str(e.payload.get("verdict")) for e in self._events(task_id, REVIEW_JUDGED_EVENT)}

    def _open_reviews(self) -> int:
        """Les relectures lancées et pas encore jugées, sur des tâches encore en revue : le WIP du relecteur."""
        count = 0
        for task_id in launched_sessions(self._service):
            task = self._service.ledger.get_task(task_id)
            if task is None or task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            judged = self._verdicts(task_id)
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
            outcome = self._step(task, launch)
            if outcome is not None:
                outcomes.append(outcome)
        return tuple(outcomes)

    def _step(self, task: MissionTask, launch: dict[str, Any]) -> ReviewOutcome | None:
        entered = self._entered_review_at(task.id)
        reviews = [e for e in self._events(task.id, REVIEW_LAUNCHED_EVENT) if e.created_at >= entered]
        verdicts = self._verdicts(task.id)
        if not reviews:
            return self._launch(task, launch)
        last = reviews[-1]
        if last.id not in verdicts:
            return self._judge(task, launch, last)
        if verdicts[last.id] not in _RELAUNCH:
            return None  # jugée : READY (lots suivants), forgée ou refusée — rien de plus à faire ici
        relaunched = sum(1 for r in reviews if verdicts.get(r.id) in _RELAUNCH)
        if relaunched > MAX_RELAUNCHES:
            return self._escalate(task, f"{relaunched} relecture(s) sans reçu jugeable (muettes ou périmées)", "")
        return self._launch(task, launch)

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
        from grimoire.missions.mandate import MandateStore

        tid, role = task.id, self._policy.reviewer
        root = self._service.home_root
        breaker = read_breaker(root)
        if breaker.open:
            return ReviewOutcome(tid, "launch", "skipped", f"disjoncteur ouvert : {breaker.reason}")
        open_reviews = self._open_reviews()
        if open_reviews >= role.wip:
            return ReviewOutcome(tid, "launch", "skipped", f"WIP du relecteur atteint ({open_reviews}/{role.wip})")
        cwd = Path(str(launch.get("cwd") or ""))
        try:
            worktree = find_task_worktree(self._service, tid)
        except (KeyError, ValueError, TypeError):
            worktree = None
        if not launch.get("cwd") or not cwd.is_dir() or worktree is None:
            return ReviewOutcome(tid, "launch", "failed", "worktree de la tâche introuvable : rien à relire")
        from grimoire.core.safe_git import same_repo
        from grimoire.missions.review_seal import branch_head, seal_review_launch

        if unsafe := same_repo(root, cwd):  # un worktree qui n'est pas de ce dépôt (.git réécrit) n'est pas relu
            return ReviewOutcome(tid, "launch", "failed", f"worktree refusé : {unsafe}")
        head = branch_head(root, tid)
        under_mandate = MandateStore(root).state().active
        model = self._model(str(launch.get("model") or ""))
        argv = [self._claude, "--bg", "--name", f"grimoire-review-{tid}", "--model", model,
                "--permission-mode", role.permission_mode, self._prompt(tid, worktree.base_sha, model)]
        result = self._exec(argv, cwd, session_env())
        match = _BG_ID.search(result.stdout) if result.returncode == 0 else None
        if match is None:
            detail = (result.stderr or result.stdout).strip()[:300] or f"sortie {result.returncode}"
            state = record_failure(root, self._policy.breaker_threshold, f"{tid} : relecteur non lancé : {detail}")
            return ReviewOutcome(tid, "launch", "failed", f"claude --bg a échoué : {detail}" + (" — disjoncteur ouvert" if state.open else ""))
        short = match.group(1)
        # #966 : l'identité du relecteur se lit après le lancement ; c'est elle qui s'inscrit et se scelle.
        session_id = resolve_background_session(self._exec, cwd, short, self._claude)
        if not session_id:
            halted = _halt(self._exec, cwd, short, self._claude)
            detail = f"relecteur {short} lancé mais introuvable dans `claude agents` : identité non résolue{halted}"
            state = record_failure(root, self._policy.breaker_threshold, f"{tid} : {detail}")
            return ReviewOutcome(tid, "launch", "failed", detail + (" — disjoncteur ouvert" if state.open else ""))
        refused = attest_session(self._service, session_id, REVIEWER_ACTOR, tid)
        if refused:
            return ReviewOutcome(tid, "launch", "skipped", refused + _halt(self._exec, cwd, short, self._claude))
        try:  # la tête relue, au journal d'autorité : un commit posé pendant la relecture ne sera pas scellé
            seal_review_launch(root, tid, session_digest(session_id), head)
            launch_sealed = True
        except Exception:  # journal rompu : le jugement refusera de sceller (échec fermé)
            launch_sealed = False
        self._service.ledger.append_event(
            REVIEW_LAUNCHED_EVENT, tid, "task", REVIEWER_ACTOR,
            {"task_id": tid, "session": short, "session_sha256": session_digest(session_id), "model": model,
             "worker_model": str(launch.get("model") or ""), "cwd": str(cwd), "under_mandate": under_mandate,
             "head": head, "launch_sealed": launch_sealed},
        )
        return ReviewOutcome(tid, "launch", "launched", f"relecteur {short} dans {cwd}", short, model)

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

    def _timed_out(self, review: LedgerEvent) -> bool:
        started = _parse(review.created_at)
        return started is not None and (self._now or datetime.now(UTC)) - started > REVIEW_TIMEOUT

    def _judge(self, task: MissionTask, launch: dict[str, Any], review: LedgerEvent) -> ReviewOutcome | None:
        from grimoire.core.safe_git import same_repo

        tid = task.id
        root = Path(str(review.payload.get("cwd") or launch.get("cwd") or ""))
        if unsafe := same_repo(self._service.home_root, root):  # rien n'est jugé dans un worktree d'un autre dépôt
            return ReviewOutcome(tid, "refused", "failed", f"worktree refusé : {unsafe}")
        try:
            receipt = read_receipt(root, tid)
        except ValueError as exc:
            receipt, unreadable = None, str(exc)
        else:
            unreadable = ""
        fresh = receipt is not None and receipt.written_at >= review.created_at
        if receipt is not None and fresh:
            workers = {str(launch.get("session_id") or ""), task.claim.session_id if task.claim else ""} - {""}
            if receipt.reviewer_session in workers:
                return self._forged(task, review, receipt)
            if session_digest(receipt.reviewer_session) == review.payload.get("session_sha256"):
                return self._judge_attested(task, launch, review, receipt, root)
        if self._timed_out(review):
            self._record_judged(task, review, "timeout", 0)
            self._stop(review)
            return ReviewOutcome(tid, "timeout", "done", f"aucun reçu du relecteur en {REVIEW_TIMEOUT} : arrêté, sera relancé")
        if unreadable:
            return ReviewOutcome(tid, "refused", "refused", f"reçu illisible : {unreadable}")
        if receipt is not None and fresh:
            return ReviewOutcome(tid, "refused", "refused", "reçu d'une autre session que le relecteur lancé : ignoré")
        return None  # la relecture n'a pas encore rendu son reçu

    def _judge_attested(
        self, task: MissionTask, launch: dict[str, Any], review: LedgerEvent, receipt: ReviewReceipt, root: Path
    ) -> ReviewOutcome:
        """Le reçu vient de la session du relecteur lancé : provenance sous mandat, puis verdict."""
        tid = task.id
        refused = self._provenance_refusal(task, review, receipt)
        if refused:
            self._record_judged(task, review, "refused", 0)
            self._stop(review)
            return ReviewOutcome(tid, "refused", "done", refused)
        if receipt.verdict == READY:
            refusal = check_receipt(root, task)
            if refusal is not None:
                self._record_judged(task, review, "stale", 0)
                self._stop(review)
                return ReviewOutcome(tid, "stale", "done", f"READY non tenu ({refusal}) : relu de nouveau")
            sealed, why = self._seal(task, review, receipt, root)
            if why:
                self._record_judged(task, review, "stale", 0)
                self._stop(review)
                return ReviewOutcome(tid, "stale", "done", f"READY non tenu ({why}) : relu de nouveau")
            self._record_judged(task, review, READY, 0, sealed)
            self._stop(review)
            return ReviewOutcome(tid, "ready", "done", f"READY, {len(receipt.criteria_met)} critère(s) tenu(s)")
        return self._bounce(task, launch, review, receipt)

    def _seal(self, task: MissionTask, review: LedgerEvent, receipt: ReviewReceipt, root: Path) -> tuple[dict[str, Any], str]:
        """Lot 4 : scelle le READY au journal d'autorité (tête, arbre, pack, empreinte du reçu), copie au ledger.

        Un arbre ou un pack qui ne sont pas ceux du reçu : ``("…", raison)``, le READY est relu. Un journal qui refuse
        l'écriture : le READY est noté non scellé, et ni la fusion ni l'acceptation sous mandat ne s'y appuieront.
        """
        from grimoire.missions.review_seal import seal_judgment

        try:
            entry = seal_judgment(self._service.home_root, task.id, review.id, root, receipt,
                                  str(review.payload.get("session_sha256") or ""))
        except ValueError as exc:
            return {}, str(exc)
        except Exception as exc:  # journal rompu : rien n'est scellé, la garde en aval refusera
            return {"sealed": False, "seal_error": f"{type(exc).__name__}: {exc}"[:300]}, ""
        keys = ("head", "tree", "tree_fingerprint", "evidence_pack_sha256", "receipt_sha256")
        return {"sealed": True, "seal": {k: entry.get(k, "") for k in keys}, "seal_seq": entry.get("seq")}, ""

    def _provenance_refusal(self, task: MissionTask, review: LedgerEvent, receipt: ReviewReceipt) -> str:
        """Une relecture lancée sous mandat s'atteste au journal d'autorité au jugement aussi ; ``""`` si elle tient.

        Mandat révoqué ou expiré depuis le lancement, journal illisible, provenance refusée : refus. Ce n'est pas
        un faux (la session est celle du relecteur), mais la garde ne s'ouvre pas pour autant.
        """
        from grimoire.missions.mandate import MandateRefusedError, MandateStore

        store = MandateStore(self._service.home_root)
        state = store.state()
        if not review.payload.get("under_mandate"):
            return ""
        if not state.active:
            return f"relecture lancée sous mandat, mandat inactif au jugement ({state.reason or 'aucun'}) : provenance non attestée"
        try:
            store.check_receipt_provenance(task, receipt)
        except MandateRefusedError as exc:
            return f"provenance refusée au journal d'autorité : {exc}"
        return ""

    def _forged(self, task: MissionTask, review: LedgerEvent, receipt: ReviewReceipt) -> ReviewOutcome:
        """Un reçu écrit par la session du worker : disjoncteur ouvert, mandat révoqué. Jugé une fois."""
        from grimoire.missions.mandate import MandateStore

        tid = task.id
        detail = f"reçu {receipt.verdict} écrit par la session du worker ({receipt.reviewer_id!r})"
        trip(self._service.home_root, f"{tid} : verdict forgé — {detail}", source=REVIEWER_ACTOR)
        store = MandateStore(self._service.home_root)
        revoked = store.revoke_on_forged_verdict(tid, detail) if store.state().active else None
        self._service.ledger.append_event(
            REVIEW_FORGED_EVENT, tid, "task", REVIEWER_ACTOR,
            {"task_id": tid, "detail": detail, "mandate_revoked": revoked.id if revoked is not None else ""},
        )
        self._record_judged(task, review, "forged", 0)
        self._stop(review)
        return ReviewOutcome(tid, "forged", "done", detail + (f" — mandat {revoked.id} révoqué" if revoked is not None else ""))

    def _record_judged(
        self, task: MissionTask, review: LedgerEvent, verdict: str, cycle: int, extra: dict[str, Any] | None = None,
    ) -> None:
        self._service.ledger.append_event(
            REVIEW_JUDGED_EVENT, task.id, "task", REVIEWER_ACTOR,
            {"task_id": task.id, "review": review.id, "verdict": verdict, "cycle": cycle, **(extra or {})},
        )

    def _stop(self, review: LedgerEvent) -> None:
        short = str(review.payload.get("session") or "")
        if short:
            self._exec([self._claude, "stop", short], Path(str(review.payload.get("cwd") or ".")), None)

    def _cycle(self, task_id: str) -> int:
        """Le rang de ce ``NOT-READY`` depuis la dernière réponse humaine : l'humain qui dit « reprendre » remet à zéro."""
        answered = [e.created_at for e in self._service.ledger.events_for(task_id) if e.event_type == HUMAN_ANSWERED_EVENT]
        since = answered[-1] if answered else ""
        return 1 + sum(
            1 for e in self._events(task_id, REVIEW_JUDGED_EVENT) if e.payload.get("verdict") == NOT_READY and e.created_at > since
        )

    def _bounce(self, task: MissionTask, launch: dict[str, Any], review: LedgerEvent, receipt: ReviewReceipt) -> ReviewOutcome:
        """``NOT-READY`` : retour au worker avec les constats, ou escalade au cycle ``max_cycles``."""
        tid, max_cycles = task.id, self._policy.reviewer.max_cycles
        breaker = read_breaker(self._service.home_root)
        if breaker.open:
            return ReviewOutcome(tid, "bounce", "skipped", f"disjoncteur ouvert : {breaker.reason}")
        cycle, notes = self._cycle(tid), _findings_text(receipt)
        worker_session = str(launch.get("session_id") or "")
        if cycle >= max_cycles or not worker_session:
            why = f"{cycle} relecture(s) NOT-READY" if worker_session else "aucune session de worker à reprendre"
            escalated = self._escalate(task, why, notes)
            if escalated.status == "done":
                self._record_judged(task, review, NOT_READY, cycle)
                self._stop(review)
            return escalated
        ledger = self._service.ledger
        try:
            ledger.transition_task(
                tid, TaskState.RUNNING, actor_id=REVIEWER_ACTOR, reason=f"relecture NOT-READY (cycle {cycle}/{max_cycles})"
            )
        except GrimoireError as exc:
            return ReviewOutcome(tid, "bounce", "failed", f"retour au worker refusé : {exc}")
        argv = [self._claude, "--bg", "--resume", worker_session]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(
            f"La relecture de {tid} rend NOT-READY : {notes} Corrige, revérifie avec les checks de la tâche, "
            f"puis remets-la en revue (`grimoire task move {tid} --to needs_verification`)."
        )
        result = self._exec(argv, Path(str(launch.get("cwd") or ".")), session_env())
        if result.returncode != 0:
            # Personne ne reprend la tâche : elle revient en revue, la reprise sera retentée au tick suivant.
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=REVIEWER_ACTOR, reason="reprise du worker échouée")
            state = record_failure(self._service.home_root, self._policy.breaker_threshold, f"{tid} : reprise du worker : {result.stderr[:200]}")
            return ReviewOutcome(tid, "bounce", "failed", "worker non repris, tâche remise en revue" + (" — disjoncteur ouvert" if state.open else ""))
        self._record_judged(task, review, NOT_READY, cycle)
        self._stop(review)
        self._service.comment(tid, f"Relecture NOT-READY (cycle {cycle}/{max_cycles}) : {notes}", REVIEWER_ACTOR)
        return ReviewOutcome(tid, "bounce", "done", f"retour au worker (cycle {cycle}/{max_cycles})")

    def _escalate(self, task: MissionTask, why: str, notes: str) -> ReviewOutcome:
        """``needs_verification → running → waiting(human)`` avec la question ; une question non posée remet en revue."""
        tid, ledger = task.id, self._service.ledger
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=REVIEWER_ACTOR, reason=f"escalade : {why}")
        except GrimoireError as exc:
            return ReviewOutcome(tid, "escalate", "failed", f"escalade refusée : {exc}")
        detail = f" : {notes[:600]}" if notes else ""
        try:
            self._service.ask(
                tid, f"Désaccord persistant sur {tid} ({why}){detail} — reprendre la tâche ou l'abandonner ?",
                REVIEWER_ACTOR, kind="choice", options=("reprendre", "abandonner"),
            )
        except (GrimoireError, ValueError) as exc:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=REVIEWER_ACTOR, reason="escalade non posée")
            return ReviewOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc}) : tâche remise en revue")
        return ReviewOutcome(tid, "escalate", "done", f"escalade à l'humain après {why}")


def _findings_text(receipt: ReviewReceipt) -> str:
    parts = [
        f"critère {c.index} {c.status} ({c.criterion})" + (f" — `{c.evidence_cmd}`" if c.evidence_cmd else "")
        + (f" : {c.note}" if c.note else "")
        for c in receipt.criteria if c.status != CRITERION_MET
    ]
    parts += [f"{f.summary}" + (f" — `{f.evidence_cmd}`" if f.evidence_cmd else "") for f in receipt.findings]
    return "; ".join(parts) or "aucun constat détaillé"
