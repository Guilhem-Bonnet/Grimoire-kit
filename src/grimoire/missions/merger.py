"""Le rôle ``merger`` : la file série qui fusionne les tâches relues READY, puis les accepte (#876, lot 4, verrou 12).

Étape du tick, après le relecteur, active seulement si ``orchestrator.roles.merger.enabled`` (désactivé par défaut).
Du code, sans LLM : il ne lance aucune session, il ne fait que reprendre celle du worker quand la fusion échoue.

**Qui entre dans la file.** Une tâche lancée (``task.session_launched``), en ``needs_verification``, dont la dernière
relecture de l'entrée en revue en cours est jugée ``READY`` (``task.review_judged``), et pas encore fusionnée. L'ordre
de la file est celui des jugements ``READY``. Le merger n'ouvre pas de PR : c'est le worker qui l'ouvre ; sans PR, il
attend.

**À chaque tick, pour chaque tâche de la file**, dans cet ordre, chaque garde échouant fermée :

1. **le worktree** : celui du ledger, sur la branche ``task/<id>``, dans ce dépôt (même dossier git commun) ;
2. **la PR** (``gh pr view task/<id>``) : ouverte, pas en brouillon, vers la cible du worktree ; fusionnée, elle est
   constatée (étape 7) ; fermée sans fusion, refus ;
3. **le READY tient** : l'arbre du worktree est propre (hors ce que le kit écrit lui-même), et l'état présent est celui
   que le relecteur du tick a **scellé au journal d'autorité** au jugement (:mod:`.review_seal` : octets du reçu, pack,
   tête et arbre), ou un descendant noté au même journal par un réalignement du merger. Jamais le fichier reçu seul :
   le worker l'écrit. Arbre sale : retour au worker ; sinon nouvelle relecture (``task.merge_requeued``, que le
   relecteur lit comme une nouvelle entrée en revue) ;
4. **la tête** : la tête locale de ``task/<id>`` est celle de la PR ; une tête réalignée pas encore poussée est poussée ;
5. **la CI** (``gh pr checks``) : les checks requis par la protection de branche (``--required``) ; seulement si gh dit
   qu'aucun n'est requis, tous les checks hors ``ignored_checks`` (défaut ``codecov/*``). Vert = au moins un check
   ``pass``, aucun autre état que ``pass``/``skipping``, **et le même verdict au tick précédent** pour cette tête (les
   checks d'un push tout juste fait ne sont pas encore inscrits). Rouge : retour au worker, comme un ``NOT-READY`` du
   lot 3 (``claude --bg --resume``). En attente, aucun check ou gh en échec : on attend ;
6. **la cible** : branche en retard, ``origin/<cible>`` est fusionnée dans le worktree (jamais de rebase, jamais de push
   forcé). Un conflit limité à ``CHANGELOG.md`` est résolu par l'union des deux côtés (``git merge-file --union``),
   contrôlée : aucune ligne ``-`` dans ``git diff origin/<cible> -- CHANGELOG.md`` ; tout autre conflit renvoie au
   worker. Puis push, et on attend la nouvelle CI. Seules les ``realign_max`` premières tâches de la file sont réalignées
   (défaut 1 : le débit CI) ;
7. **la fusion**, pour la tête de file seulement (WIP 1) : ``gh pr merge <n> --squash --match-head-commit <tête>``,
   jamais ``--admin`` ni ``--auto``, **seulement** si :func:`~grimoire.missions.mandate.require_authority` l'autorise
   sous :func:`~grimoire.missions.mandate.acting_as_orchestrator` (mandat actif qui couvre ``merge`` et le type de la
   tâche, politique qui délègue ``merge``), si aucune catégorie de la tâche n'est gardée à l'humain, si la PR ne touche
   aucun chemin protégé (la politique, les workflows, CODEOWNERS, le code des gardes, plus ``protected_paths``) et si le
   reçu est attesté au journal d'autorité (verrou 7). Sinon, et si le dépôt refuse la fusion, une question **non
   bloquante** « fusionner ? » est posée une fois : la tâche reste en revue, l'humain fusionne lui-même et le tick le
   constate. ``task.merged`` n'est noté que constaté sur la remote (PR ``MERGED``, commit de fusion contenu dans
   ``origin/<cible>``) ; le geste est inscrit au journal d'autorité avant, une fois, avec sa provenance ;
8. **l'acceptation** : la fusion revérifiée sur la remote et le sceau du READY tenu, sous un mandat qui couvre
   ``accept`` et une politique qui le délègue, par :func:`~grimoire.missions.human_acceptance.accept_task` (reçu READY
   d'un relecteur inscrit, distinct de l'auteur, état scellé), puis clôture ; sinon une question non bloquante
   « accepter ? » donne la commande ``grimoire task accept`` à lancer dans le worktree. Une tâche close, le janitor du
   lot 2 nettoie.

Le merger ne lit aucune réponse humaine du ledger : un geste qu'il ne fait pas sous mandat, l'humain le fait lui-même
(fusion sur la remote, ``task accept``), par un canal qui vérifie son origine.

**Git sans hooks.** Chaque ``git`` du merger tourne avec ``core.hooksPath`` vers ``os.devnull``, ``core.fsmonitor``
désactivé, et ``--no-verify`` sur ``merge``, ``commit`` et ``push`` : un hook du dépôt, que le worker peut réécrire,
n'est jamais exécuté par le tick.

Chaque étape est notée au ledger et n'est faite qu'une fois ; le tick est rejouable (une fusion faite et non notée est
constatée au tick suivant, jamais refaite ; une acceptation inscrite au journal et non notée n'est pas refaite). Un échec
technique de ``gh`` ou de ``git`` est rapporté, compté au disjoncteur, et rejoué ; un refus de fusion du dépôt (revue
exigée, protection) ne l'est pas. Une exception sur une tâche ne bloque pas les autres. Disjoncteur ouvert : rien n'est
fait.

**Limites** : le journal d'autorité vit sous le même utilisateur que les sessions (sa garde refuse les écritures d'agent
par les outils, pas un script arbitraire) ; la configuration git du dépôt (pilotes de fusion et filtres déclarés par
``.gitattributes``) reste lue ; une PR sans aucun check attend indéfiniment ; l'union du CHANGELOG garde les deux côtés
dans l'ordre de ``git merge-file`` ; une réponse ``task.human_answered`` forgée au ledger remet à zéro le compte des
retours au worker (coût, pas autorité) ; le classement « refus du dépôt » contre « panne » lit le texte d'erreur de gh.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.breaker import read_breaker, record_failure
from grimoire.missions.launcher import LAUNCHED_EVENT, default_exec, launched_sessions, session_env
from grimoire.missions.review_receipt import READY, RECEIPT_FINGERPRINT_EXCLUDES, read_receipt, receipt_fingerprint
from grimoire.missions.reviewer import REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT, entered_review_at
from grimoire.missions.schemas import ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.task_worktree import MERGED_EVENT, TASK_BRANCH_PREFIX, find_task_worktree
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
    "DEFAULT_PROTECTED_PATHS",
    "MERGER_ACTOR",
    "MERGE_ACCEPTED_EVENT",
    "MERGE_BOUNCED_EVENT",
    "MERGE_CI_EVENT",
    "MERGE_ESCALATED_EVENT",
    "MERGE_PUSHED_EVENT",
    "MERGE_QUEUED_EVENT",
    "MERGE_REALIGNED_EVENT",
    "MERGE_REQUESTED_EVENT",
    "MERGE_REQUEUED_EVENT",
    "MergeStage",
    "MergerOutcome",
]

MERGER_ACTOR = "merger"
MERGE_QUEUED_EVENT = "task.merge_queued"
MERGE_REQUEUED_EVENT = "task.merge_requeued"
MERGE_REALIGNED_EVENT = "task.merge_realigned"
MERGE_PUSHED_EVENT = "task.merge_pushed"
MERGE_BOUNCED_EVENT = "task.merge_bounced"
MERGE_CI_EVENT = "task.merge_ci"
MERGE_REQUESTED_EVENT = "task.merge_requested"
MERGE_ESCALATED_EVENT = "task.merge_escalated"
MERGE_ACCEPTED_EVENT = "task.merge_accepted"
CHANGELOG = "CHANGELOG.md"
#: Ce qui garde les gardes : une PR qui y touche n'est jamais fusionnée ni acceptée sous mandat (``protected_paths`` s'ajoute).
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "_grimoire/standard/*",
    ".github/workflows/*",
    "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
    "src/grimoire/missions/mandate.py", "src/grimoire/missions/merger.py", "src/grimoire/missions/reviewer.py",
    "src/grimoire/missions/review_seal.py", "src/grimoire/missions/review_receipt.py",
    "src/grimoire/missions/review_verdict.py", "src/grimoire/missions/human_acceptance.py",
    "src/grimoire/missions/launcher.py", "src/grimoire/missions/janitor.py", "src/grimoire/missions/breaker.py",
    "src/grimoire/missions/orchestrator_policy.py", "src/grimoire/missions/orchestrate.py",
    "src/grimoire/missions/check_gate.py", "src/grimoire/missions/gates.py",
    "src/grimoire/hosts/decisions/authority_guard.py",
)
#: Catégories propres aux gestes : le mandat et la politique les tranchent ; les autres sont celles de la tâche.
_GESTURE_CATEGORIES = frozenset({"merge", "accept"})
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: ``gh pr checks`` : 0 tout passe, 1 un check échoue, 8 des checks attendent — le JSON vaut dans les trois cas.
_GH_CHECKS_EXIT = (0, 1, 8)
_RED_BUCKETS = frozenset({"fail", "cancel"})
_GREEN_BUCKETS = frozenset({"pass", "skipping"})
#: Ce qui fait d'un échec de ``gh pr merge`` une panne (comptée) plutôt qu'un refus du dépôt (escaladé).
_TECHNICAL = ("could not resolve", "timeout", "timed out", "connection", "network", "http 5", "error connecting",
              "authentication", "gh auth login", "rate limit", "unexpected eof", "binaire introuvable", "délai")
_PUSH_TIMEOUT_S = 600
#: Aucun hook, aucun moniteur de fichiers : rien de l'arbre que le worker écrit n'est exécuté par le tick.
_GIT_SAFE = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false")
_EVIDENCE = Path("_grimoire-runtime-output") / "evidence"


@dataclass(frozen=True, slots=True)
class MergerOutcome:
    """Ce que l'étape a fait d'une tâche : ``action`` (queue, wait, stale, bounce, realign, push, merge, unconfirmed,
    accept, close, escalate, human, refused) et ``status`` (done, waiting, skipped, refused, failed)."""

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
        return subprocess.run(  # arguments fixes, sans shell, sans hooks ; sans les variables qui détourneraient git -C
            ["git", *_GIT_SAFE, "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, env=_git_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 124, "", f"git {args[0]} : {exc}")


def _git_bytes(cwd: Path, *args: str) -> tuple[int, bytes]:
    try:
        done = subprocess.run(["git", *_GIT_SAFE, "-C", str(cwd), *args], capture_output=True, timeout=120, check=False,
                              env=_git_env())
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


class MergeStage:
    """L'étape ``merger`` du tick : accepte les tâches fusionnées, avance la file."""

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
        merged, queue = self._classify(launched)
        breaker = read_breaker(self._home)
        if breaker.open:
            why = f"disjoncteur ouvert : {breaker.reason}"
            return tuple(MergerOutcome(tid, "wait", "skipped", why) for tid in (*merged, *(t for t, _ in queue)))
        outcomes = [self._guarded(tid, lambda t: self._accept(t)) for tid in merged]
        position = 0  # le rang des seules tâches qui restent dans la file : une tête refusée ou à l'humain ne bloque pas
        for tid, judged in queue:
            outcome = self._guarded(tid, lambda t, j=judged, p=position: self._advance(t, j, p, launched[t]))
            outcomes.append(outcome)
            if (outcome.status in ("waiting", "failed") and outcome.action != "human") or outcome.action in ("realign", "push"):
                position += 1
        return tuple(outcomes)

    def _guarded(self, tid: str, step: Any) -> MergerOutcome:
        """Une exception sur une tâche devient un échec rapporté et compté : les autres tâches suivent leur chemin."""
        try:
            outcome: MergerOutcome = step(tid)
        except Exception as exc:
            return self._failed(tid, "error", f"{type(exc).__name__}: {exc}")
        return outcome

    def _classify(self, launched: dict[str, dict[str, Any]]) -> tuple[list[str], list[tuple[str, LedgerEvent]]]:
        """``(fusionnées à accepter, file ordonnée)``."""
        merged: list[str] = []
        queue: list[tuple[str, LedgerEvent]] = []
        for tid in launched:
            task = self._service.ledger.get_task(tid)
            if task is None or task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            events = self._service.ledger.events_for(tid)
            if _last(events, MERGED_EVENT, _last_launch(events)) is not None:
                merged.append(tid)
                continue
            judged = self._ready_judgment(tid, events)
            if judged is not None:
                queue.append((tid, judged))
        queue.sort(key=lambda item: (item[1].created_at, item[0]))
        return merged, queue

    def _ready_judgment(self, tid: str, events: list[LedgerEvent]) -> LedgerEvent | None:
        """Le ``task.review_judged`` READY de la dernière relecture de l'entrée en revue en cours."""
        entered = entered_review_at(self._service, tid)
        reviews = [e for e in events if e.event_type == REVIEW_LAUNCHED_EVENT and e.created_at >= entered]
        if not reviews:
            return None
        judged = [e for e in events if e.event_type == REVIEW_JUDGED_EVENT and e.payload.get("review") == reviews[-1].id]
        return judged[-1] if judged and judged[-1].payload.get("verdict") == READY else None

    def _worktree(self, tid: str) -> tuple[TaskWorktree | None, str]:
        """Le worktree du ledger, s'il est bien celui de ``task/<id>`` dans ce dépôt ; sinon la raison du refus."""
        wt = find_task_worktree(self._service, tid)
        if wt is None or not wt.path.is_dir():
            return None, "worktree de la tâche introuvable"
        if wt.branch != TASK_BRANCH_PREFIX + tid:
            return None, f"branche {wt.branch!r} notée au ledger : seule task/{tid} est fusionnée"
        common = _git(wt.path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        mine = _git(self._home, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        if not common or not mine or Path(common).resolve() != Path(mine).resolve():
            return None, f"{wt.path} n'est pas un worktree de ce dépôt"
        if _git(wt.path, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() != wt.branch:
            return None, f"{wt.path} n'est pas sur {wt.branch}"
        return wt, ""

    # -- la file ---------------------------------------------------------------

    def _advance(self, tid: str, judged: LedgerEvent, position: int, launch: dict[str, Any]) -> MergerOutcome:
        task = self._service.require(tid)
        wt, refused = self._worktree(tid)
        if wt is None:
            return MergerOutcome(tid, "refused", "refused", f"{refused} : rien à fusionner")
        events = self._service.ledger.events_for(tid)
        if not any(e.event_type == MERGE_QUEUED_EVENT and e.payload.get("judged") == judged.id for e in events):
            self._note(MERGE_QUEUED_EVENT, tid, {"judged": judged.id, "review": judged.payload.get("review"), "position": position + 1})
        pr, error = self._view(wt.branch)
        if error:
            return self._failed(tid, "wait", f"gh pr view {wt.branch} : {error}")
        if pr is None:
            return MergerOutcome(tid, "wait", "waiting", f"aucune PR pour {wt.branch} : le worker l'ouvre")
        if pr.state == "MERGED":
            return self._confirm(task, wt, judged, pr)
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
        if kind or receipt is None:
            return self._requeue(task, judged, why, pr.number)
        local = _rev(wt.path, f"refs/heads/{wt.branch}")
        if local != pr.head:
            realigned = _last(events, MERGE_REALIGNED_EVENT, _index(events, judged))
            if realigned is not None and realigned.payload.get("to") == local and realigned.payload.get("from") == pr.head:
                return self._push(tid, wt, local, pr.number)  # la tête locale est scellée (étape 3) : seul le push manquait
            return MergerOutcome(tid, "wait", "waiting", f"tête locale {local[:12]} ≠ tête de la PR {pr.head[:12]}", pr.number)
        ci, detail = self._stable_ci(tid, wt.branch, local)
        if ci == "unknown":
            return self._failed(tid, "wait", f"CI illisible : {detail}", pr.number)
        if ci == "red":
            return self._bounce(task, launch, "ci_red", detail, pr.number)
        if ci != "green":
            return MergerOutcome(tid, "wait", "waiting", f"CI : {detail}", pr.number)
        fetched = _git(self._home, "fetch", "-q", self._remote, wt.into)
        if fetched.returncode != 0:
            return self._failed(tid, "wait", f"git fetch {self._remote} {wt.into} : {_err(fetched)}", pr.number)
        target = f"refs/remotes/{self._remote}/{wt.into}"
        if _git(wt.path, "merge-base", "--is-ancestor", target, local).returncode != 0:
            if position >= self._policy.merger.realign_max:
                return MergerOutcome(tid, "wait", "waiting", f"en retard sur {wt.into}, en file (position {position + 1}) : "
                                     f"{self._policy.merger.realign_max} réalignement(s) à la fois", pr.number)
            return self._realign(task, wt, local, judged, launch, pr.number)
        if position > 0:
            return MergerOutcome(tid, "wait", "waiting", f"prête, en file (position {position + 1}) : une fusion à la fois", pr.number)
        return self._merge(task, wt, judged, receipt, pr, local)

    def _fresh(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent) -> tuple[ReviewReceipt | None, str, str]:
        """``(reçu, genre, raison)`` : genre ``""`` si l'état présent est celui du READY scellé, sinon ``stale``,
        ``dirty`` (modifications non commitées) ou ``error`` (git muet)."""
        from grimoire.missions.review_seal import verify

        excludes = [f":(exclude){p}" for p in ("_grimoire-output", *RECEIPT_FINGERPRINT_EXCLUDES)]
        status = _git(wt.path, "status", "--porcelain", "--untracked-files=all", "--", ".", *excludes)
        if status.returncode != 0:
            return None, "error", f"git status du worktree : {_err(status)}"
        if status.stdout.strip():
            changed = ", ".join(line[3:] for line in status.stdout.splitlines()[:5])
            return None, "dirty", f"modifications non commitées dans le worktree ({changed}) : ce qui serait fusionné n'est pas ce qui a été relu"
        refusal = verify(self._home, task, wt.path, review_id=str(judged.payload.get("review") or ""))
        if refusal:
            return None, "stale", refusal
        try:
            receipt = read_receipt(wt.path, task.id)
        except ValueError as exc:
            return None, "stale", f"reçu illisible ({exc})"
        if receipt is None or receipt.verdict != READY:
            return None, "stale", "le reçu scellé n'est plus READY"
        if receipt.criteria and tuple(c.criterion for c in receipt.criteria) != tuple(task.acceptance):
            return None, "stale", "les critères ont changé depuis le READY"
        return receipt, "", ""

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

    def _checks(self, branch: str, *extra: str) -> tuple[str, list[dict[str, Any]]]:
        """``("ok"|"none"|"error", checks)`` : ``none`` seulement quand gh dit lui-même qu'il n'y a aucun check."""
        result = self._exec(["gh", "pr", "checks", branch, "--json", "name,bucket", *extra], self._home, None)
        said = result.stderr.lower()
        if result.returncode == 1 and not result.stdout.strip() and "checks reported" in said and "no " in said:
            return "none", []
        if result.returncode not in _GH_CHECKS_EXIT or not result.stdout.strip():
            return "error", []
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            return "error", []
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return "error", []
        return "ok", rows

    def _ci(self, branch: str) -> tuple[str, str]:
        """``(green|red|pending|unknown, détail)`` : les checks requis, ou tous hors ignorés si gh dit qu'aucun n'est requis."""
        state, required = self._checks(branch, "--required")
        if state == "error":
            return "unknown", "gh pr checks --required en échec : on n'en conclut rien"
        if state == "ok" and required:
            rows, scope = required, "checks requis"
        else:
            state, every = self._checks(branch)
            if state == "error":
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
        if "pass" not in buckets:
            return "pending", f"{len(rows)} {scope} tous sautés : rien n'a tourné"
        return "green", f"{len(rows)} {scope} verts"

    def _stable_ci(self, tid: str, branch: str, head: str) -> tuple[str, str]:
        """Le verdict CI, vert seulement s'il l'était déjà au tick précédent pour cette tête (``task.merge_ci``)."""
        verdict, detail = self._ci(branch)
        events = self._service.ledger.events_for(tid)
        seen = next((e for e in reversed(events) if e.event_type == MERGE_CI_EVENT and e.payload.get("head") == head), None)
        was_green = seen is not None and seen.payload.get("verdict") == "green"
        if verdict == "green" and not was_green:
            self._note(MERGE_CI_EVENT, tid, {"head": head, "verdict": "green", "detail": detail})
            return "pending", f"{detail} — à confirmer au tick suivant (checks d'un push tout juste inscrits ?)"
        if verdict != "green" and was_green:
            self._note(MERGE_CI_EVENT, tid, {"head": head, "verdict": verdict, "detail": detail})
        return verdict, detail

    # -- réalignement --------------------------------------------------------

    def _realign(
        self, task: MissionTask, wt: TaskWorktree, local: str, judged: LedgerEvent, launch: dict[str, Any], pr: int,
    ) -> MergerOutcome:
        """``origin/<cible>`` fusionnée dans le worktree (jamais de rebase) ; conflit limité au CHANGELOG : union contrôlée."""
        from grimoire.missions.review_seal import seal_realignment

        tid = task.id
        target = f"refs/remotes/{self._remote}/{wt.into}"
        target_sha = _rev(wt.path, target)
        done = _git(wt.path, "merge", "--no-ff", "--no-edit", "--no-verify", "-m",
                    f"Merge {wt.into} into {wt.branch} (merger, #876)", target)
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
            committed = _git(wt.path, "commit", "--no-edit", "--no-verify", "-q")
            if committed.returncode != 0:
                _git(wt.path, "merge", "--abort")
                return self._failed(tid, "realign", f"git commit du réalignement : {_err(committed)}", pr)
            union = True
        head, tree = _rev(wt.path, "HEAD"), receipt_fingerprint(wt.path)
        review = str(judged.payload.get("review") or "")
        seal_realignment(self._home, tid, review, local, head, tree)  # au journal d'autorité : ce que la fraîcheur lit
        self._note(MERGE_REALIGNED_EVENT, tid, {
            "from": local, "to": head, "into": wt.into, "into_sha": target_sha, "changelog_union": union, "review": review,
            "fingerprint_after": tree,
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
        """Pousse *head* sur ``task/<id>`` : jamais forcé, sans hook ; un push non fast-forward est refusé par la remote."""
        if wt.branch != TASK_BRANCH_PREFIX + tid:  # pragma: no cover - _worktree l'a déjà refusé
            return MergerOutcome(tid, "push", "refused", f"branche {wt.branch!r} : seule task/{tid} est poussée", pr)
        done = _git(wt.path, "push", "-q", "--no-verify", self._remote, f"{head}:refs/heads/{wt.branch}", timeout=_PUSH_TIMEOUT_S)
        if done.returncode != 0:
            return self._failed(tid, "push", f"git push {wt.branch} : {_err(done)}", pr)
        self._note(MERGE_PUSHED_EVENT, tid, {"head": head, "branch": wt.branch})
        return MergerOutcome(tid, "push", "done", f"{wt.branch} poussée à {head[:12]}", pr)

    # -- fusion --------------------------------------------------------------

    def _kept_to_human(self, task: MissionTask, changed: list[str]) -> str:
        """Pourquoi ce geste reste humain quoi que dise le mandat : catégorie gardée, chemin protégé ; ``""`` sinon."""
        from grimoire.missions.mandate import load_escalation, task_categories

        policy = load_escalation(self._home)
        guarded = sorted(c for c in task_categories(task) if c not in _GESTURE_CATEGORIES and not policy.delegates(c))
        if guarded:
            return f"catégorie gardée à l'humain : {', '.join(guarded)}"
        patterns = (*DEFAULT_PROTECTED_PATHS, *self._policy.merger.protected_paths)
        touched = sorted(p for p in changed if any(fnmatch.fnmatchcase(p, pat) for pat in patterns))
        if touched:
            return f"la PR touche des chemins protégés (ce qui garde les gardes) : {', '.join(touched[:8])}"
        return ""

    def _changed(self, cwd: Path, *spec: str) -> list[str] | None:
        done = _git(cwd, "diff", "--name-only", "-z", "--no-renames", *spec)
        return None if done.returncode != 0 else [p for p in done.stdout.split("\0") if p]

    def _merge(
        self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, receipt: ReviewReceipt, pr: _PullRequest, local: str,
    ) -> MergerOutcome:
        from grimoire.missions.mandate import (
            MandateRefusedError,
            MandateStore,
            acting_as_orchestrator,
            require_authority,
        )

        tid = task.id
        changed = self._changed(wt.path, f"refs/remotes/{self._remote}/{wt.into}...{local}")
        if changed is None:
            return self._failed(tid, "merge", "git diff de la PR illisible : chemins protégés non vérifiables", pr.number)
        kept = self._kept_to_human(task, changed)
        if kept:
            return self._ask_human(task, wt, "merge", judged.id, kept, pr.number)
        provenance = {"kind": "receipt", "ref": f"{tid}:review={judged.payload.get('review')}:head={local[:12]}"}
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
            return self._ask_human(task, wt, "merge", judged.id, str(exc), pr.number)
        self._note(MERGE_REQUESTED_EVENT, tid, {
            "pr": pr.number, "head": local, "origin": ORIGIN_ORCHESTRATOR, "mandate_id": auth.mandate_id, "provenance": provenance,
        })
        result = self._exec(["gh", "pr", "merge", str(pr.number), "--squash", "--match-head-commit", local], self._home, None)
        if result.returncode != 0:
            said = (result.stderr or result.stdout).strip()[:300]
            if result.returncode in (124, 126, 127) or any(w in said.lower() for w in _TECHNICAL):
                return self._failed(tid, "merge", f"gh pr merge #{pr.number} : {said}", pr.number)
            # Le dépôt refuse (revue exigée, protection, file de fusion) : ni une panne, ni un geste à forcer.
            return self._ask_human(task, wt, "merge", judged.id, f"le dépôt refuse la fusion ({said})", pr.number)
        after, error = self._view(wt.branch)
        if error or after is None:
            return self._failed(tid, "unconfirmed", f"fusion demandée, non constatée : {error or 'PR introuvable'}", pr.number)
        return self._confirm(task, wt, judged, after)

    def _merged_on_remote(self, wt: TaskWorktree, pr: _PullRequest) -> tuple[str, str]:
        """``("yes"|"no"|"error", détail)`` : PR ``MERGED`` et commit de fusion contenu dans ``origin/<cible>``."""
        if pr.state != "MERGED" or not _SHA.fullmatch(pr.merge_commit):
            return "no", f"PR #{pr.number} {pr.state} : fusion non constatée"
        fetched = _git(self._home, "fetch", "-q", self._remote, wt.into)
        if fetched.returncode != 0:
            return "error", f"git fetch {self._remote} {wt.into} : {_err(fetched)}"
        target = f"refs/remotes/{self._remote}/{wt.into}"
        if _git(self._home, "merge-base", "--is-ancestor", pr.merge_commit, target).returncode != 0:
            return "no", f"commit de fusion {pr.merge_commit[:12]} absent de {target}"
        return "yes", ""

    def _confirm(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, pr: _PullRequest) -> MergerOutcome:
        """``task.merged`` seulement si la remote le dit ; le geste sous mandat au journal d'autorité d'abord."""
        tid = task.id
        merged, why = self._merged_on_remote(wt, pr)
        if merged == "error":
            return self._failed(tid, "unconfirmed", why, pr.number)
        if merged == "no":
            return MergerOutcome(tid, "unconfirmed", "waiting", f"{why}, revue au tick suivant", pr.number)
        events = self._service.ledger.events_for(tid)
        requested = next((e for e in reversed(events) if e.event_type == MERGE_REQUESTED_EVENT and e.payload.get("head") == pr.head), None)
        self._record_gesture(tid, requested)  # dédoublonné : une coupure avant ``task.merged`` le retrouve inscrit
        self._service.ledger.append_event(MERGED_EVENT, tid, "task", MERGER_ACTOR, {
            "into": wt.into, "base_sha": wt.base_sha, "head_sha": pr.head, "merged_sha": pr.merge_commit, "pr": pr.number,
            "via": "gh", "judged": judged.id,
            "origin": str(requested.payload.get("origin", "")) if requested else "remote",
            "mandate_id": str(requested.payload.get("mandate_id", "")) if requested else "",
        })
        accepted = self._accept(tid)
        return MergerOutcome(tid, accepted.action, accepted.status, f"PR #{pr.number} fusionnée à {pr.merge_commit[:12]} ; {accepted.detail}", pr.number)

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

    def _accept(self, tid: str) -> MergerOutcome:
        """La fusion revérifiée sur la remote (le ledger ne suffit pas), le sceau tenu, puis l'acceptation sous mandat."""
        from grimoire.missions.human_acceptance import accept_task
        from grimoire.missions.mandate import MandateRefusedError, MandateStore, acting_as_orchestrator
        from grimoire.missions.review_seal import verify

        task = self._service.require(tid)
        wt, refused = self._worktree(tid)
        if wt is None:
            return MergerOutcome(tid, "accept", "refused", f"{refused} : rien à accepter")
        events = self._service.ledger.events_for(tid)
        noted = _last(events, MERGED_EVENT, _last_launch(events))
        if noted is None:  # pragma: no cover - l'appelant le garantit
            return MergerOutcome(tid, "accept", "refused", "aucune fusion constatée")
        if _last(events, MERGE_ACCEPTED_EVENT, _index(events, noted)) is not None:
            return self._close(tid, wt, int(noted.payload.get("pr") or 0))
        pr, error = self._view(wt.branch)
        if error or pr is None:
            return self._failed(tid, "accept", f"gh pr view {wt.branch} : {error or 'PR introuvable'}")
        merged, why = self._merged_on_remote(wt, pr)
        if merged != "yes":
            return (self._failed(tid, "accept", why, pr.number) if merged == "error"
                    else MergerOutcome(tid, "accept", "refused", f"task.merged au ledger, mais {why}", pr.number))
        provenance = {"kind": "verdict", "ref": f"{tid}:merged={pr.merge_commit[:12]}:judged={noted.payload.get('judged')}"}
        store = MandateStore(self._home)
        if any(g.get("gesture") == "accept" and g.get("provenance") == provenance for g in store.gestures_taken(tid)):
            # Coupure après ``accept_task`` (geste inscrit, gardes passées) : ni second pack, ni second verdict, ni second geste.
            self._note(MERGE_ACCEPTED_EVENT, tid, {"origin": ORIGIN_ORCHESTRATOR, "replayed": True})
            return self._close(tid, wt, pr.number)
        local = _rev(wt.path, f"refs/heads/{wt.branch}")
        reasons = []
        if pr.head != local:
            reasons.append("la tête fusionnée n'est pas celle du worktree relu")
        if sealed := verify(self._home, task, wt.path):
            reasons.append(sealed)
        changed = self._changed(self._home, f"{pr.merge_commit}^1", pr.merge_commit)
        if changed is None:
            return self._failed(tid, "accept", "git diff du commit de fusion illisible", pr.number)
        if kept := self._kept_to_human(task, changed):
            reasons.append(kept)
        if reasons:
            return self._ask_human(task, wt, "accept", pr.merge_commit, " ; ".join(reasons), pr.number)
        reason = f"PR #{pr.number} fusionnée à {pr.merge_commit[:12]}, relue READY (sceau tenu), CI verte"
        try:
            with acting_as_orchestrator():
                outcome = accept_task(task, self._home, wt.path / _EVIDENCE, reason, MERGER_ACTOR, origin=ORIGIN_ORCHESTRATOR,
                                      provenance=provenance, proof_root=wt.path)
        except (MandateRefusedError, GrimoireError, ValueError) as exc:
            return self._ask_human(task, wt, "accept", pr.merge_commit, str(exc), pr.number)
        self._note(MERGE_ACCEPTED_EVENT, tid, {
            "verdict": outcome.verdict.id, "pack": outcome.pack.id, "origin": ORIGIN_ORCHESTRATOR,
            "mandate_id": outcome.verdict.created_by.removeprefix(f"{ORIGIN_ORCHESTRATOR}:"),
        })
        return self._close(tid, wt, pr.number)

    def _close(self, tid: str, wt: TaskWorktree, pr: int) -> MergerOutcome:
        """Clôture par le service ouvert dans le worktree : ses gates y lisent le reçu et la preuve."""
        from grimoire.missions.service import TaskService

        try:
            TaskService(wt.path).transition(tid, TaskState.CLOSED, MERGER_ACTOR, f"fusionnée (PR #{pr}) et acceptée")
        except (GrimoireError, ValueError) as exc:
            return MergerOutcome(tid, "close", "failed", f"acceptée, clôture refusée : {exc}", pr)
        return MergerOutcome(tid, "accept", "done", f"acceptée et close (PR #{pr}) : le janitor nettoie", pr)

    # -- retours et questions ------------------------------------------------

    def _ask_human(self, task: MissionTask, wt: TaskWorktree, gesture: str, anchor: str, why: str, pr: int) -> MergerOutcome:
        """Une question **non bloquante**, une fois par geste et par READY (ou par fusion) : la tâche reste en revue.

        Le merger ne fait pas ce geste ; l'humain le fait lui-même, par un canal qui vérifie son origine : la fusion
        sur la remote (le tick la constate), ``task accept`` dans le worktree. Aucune réponse n'est lue ici.
        """
        from grimoire.missions.questions import QuestionSpec

        tid = task.id
        if gesture == "merge":
            text = (f"Fusionner la PR #{pr} de task/{tid} ? Le merger ne la fusionne pas : {why}. Fusionne-la toi-même "
                    f"(`gh pr merge {pr} --squash`) si elle te convient : le tick constatera la fusion.")
        else:
            text = (f"Accepter {tid} (PR #{pr} fusionnée) ? Le merger ne l'accepte pas : {why}. Accepte-la toi-même : "
                    f"`grimoire task accept {tid} --project-root {wt.path} --reason \"…\"`.")
        key = f"merger:{gesture}:{anchor}"
        try:
            asked = self._service.ask_batch(tid, [QuestionSpec(text, kind="merge" if gesture == "merge" else "choice",
                                                               blocking=False, key=key)], MERGER_ACTOR)
        except (GrimoireError, ValueError) as exc:
            return MergerOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc})", pr)
        if not asked.plan.asked:
            return MergerOutcome(tid, "human", "waiting", f"« {APPROVE_WORD[gesture]} ? » posée, l'humain décide : {why}", pr)
        self._note(MERGE_ESCALATED_EVENT, tid, {"gesture": gesture, "question_id": asked.plan.asked[0].id, "why": why[:600],
                                                "pr": pr, "anchor": anchor, "blocking": False})
        return MergerOutcome(tid, "escalate", "done", f"« {APPROVE_WORD[gesture]} ? » posée à l'humain : {why}", pr)

    def _requeue(self, task: MissionTask, judged: LedgerEvent, why: str, pr: int) -> MergerOutcome:
        """Un READY qui ne tient plus : la tâche repart à la relecture (une nouvelle entrée en revue), une fois par READY."""
        events = self._service.ledger.events_for(task.id)
        if _last(events, MERGE_REQUEUED_EVENT, _index(events, judged)) is None:
            if self._cycle(events, MERGE_REQUEUED_EVENT) >= self._policy.merger.max_cycles:
                return self._escalate(task, f"READY périmé à répétition ({why})", pr)
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
            escalated = self._escalate(task, why, pr)
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

    def _escalate(self, task: MissionTask, why: str, pr: int) -> MergerOutcome:
        """Échecs répétés : ``needs_verification → running → waiting(human)`` « reprendre ou abandonner »."""
        tid, ledger = task.id, self._service.ledger
        qid = new_question_id()
        self._note(MERGE_ESCALATED_EVENT, tid, {"gesture": "bounce", "question_id": qid, "why": why[:600], "pr": pr, "blocking": True})
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason=f"escalade : {why[:200]}")
        except GrimoireError as exc:
            return MergerOutcome(tid, "escalate", "failed", f"escalade refusée : {exc}", pr)
        try:
            self._service.ask(tid, f"La fusion de {tid} échoue à répétition ({why}) — reprendre la tâche ou l'abandonner ?",
                              MERGER_ACTOR, kind="choice", options=("reprendre", ABANDON), question_id=qid)
        except (GrimoireError, ValueError) as exc:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="escalade non posée")
            return MergerOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc}) : tâche remise en revue", pr)
        return MergerOutcome(tid, "escalate", "done", f"« reprendre ou abandonner ? » posée à l'humain : {why}", pr)

    # -- journal -------------------------------------------------------------

    def _note(self, kind: str, tid: str, payload: dict[str, Any]) -> None:
        self._service.ledger.append_event(kind, tid, "task", MERGER_ACTOR, {"task_id": tid, **payload})

    def _failed(self, tid: str, action: str, detail: str, pr: int = 0) -> MergerOutcome:
        """Un échec technique de ``gh`` ou de ``git`` : rapporté, compté au disjoncteur, rejoué au tick suivant."""
        state = record_failure(self._home, self._policy.breaker_threshold, f"{tid} : merger ({action}) : {detail}")
        return MergerOutcome(tid, action, "failed", detail + (" — disjoncteur ouvert" if state.open else ""), pr)


APPROVE_WORD = {"merge": "fusionner", "accept": "accepter"}
