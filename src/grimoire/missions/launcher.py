"""Le lanceur du rôle ``worker`` : une session Claude Code détachée par tâche admise (#876, lot 2, verrous 1 et 13).

Avant ce module, le tick calculait un plan d'admission et ne lançait rien : son point d'accroche *dispatcher*
n'avait aucun branchement. :class:`WorkerLauncher` en est un. Pour chaque tâche admise il pose, dans l'ordre :

1. le **worktree** ``task/<id>`` (:func:`~grimoire.missions.task_worktree.create_task_worktree`, base épinglée) ;
2. le **contexte** de la tâche (``context-bundle``) ;
3. le **claim**, donc le bail (``ready → claimed``, acteur ``worker``) ;
4. le **lancement** : ``claude --bg --name grimoire-<id> [--model …] [--permission-mode …] "<consigne>"``, lancé *dans*
   le worktree. ``--bg`` rend aussitôt la main et imprime l'identifiant court que ``claude stop|logs|attach`` prennent ;
   il est noté au ledger (``task.session_launched``), c'est lui que le janitor arrêtera.

Les gardes, toutes fermées : disjoncteur ouvert, politique illisible, rôle désactivé, WIP atteint, plafond de
sessions du jour atteint — rien n'est lancé, et la sortie dit pourquoi. Un lancement qui échoue rend le claim
(``claimed → ready``), note ``task.session_launch_failed`` et compte pour le disjoncteur (:mod:`.breaker`).

**Verrou 13.** Un worker est une session ``--bg`` à part entière : son budget, ses compteurs et son journal sont les
siens. Le lanceur ne passe jamais par l'outil Agent (aucun ``--agent``/``--agents`` dans la ligne de commande) : un
sous-agent partagerait le budget et les compteurs de la session parente.

Le binaire est injectable (*exec_*), c'est ainsi que les tests tournent contre un faux ``claude``.

**Identité de session (lot 3, verrou 7).** Le lanceur fixe l'identifiant de la session (``--session-id <uuid>`` et
``GRIMOIRE_SESSION_ID``) au lieu de le lire après coup : sous un mandat actif, il l'inscrit au journal d'autorité
(:meth:`~grimoire.missions.mandate.MandateStore.register_session`) *avant* le lancement, en rôle ``worker``. C'est ce
qui permet de refuser un reçu de revue écrit par la session qui a fait le travail. Une inscription refusée (plafond de
sessions du mandat) ne lance rien. Sans mandat, rien ne s'inscrit et le lancement est celui du lot 2.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.missions.breaker import read_breaker, record_failure
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import create_task_worktree

if TYPE_CHECKING:
    from grimoire.missions.admission import Admit
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.service import TaskService

__all__ = [
    "LAUNCHED_EVENT",
    "LAUNCH_FAILED_EVENT",
    "WORKER_ACTOR",
    "ExecResult",
    "LaunchOutcome",
    "WorkerLauncher",
    "attest_session",
    "default_exec",
    "launched_sessions",
    "live_background_sessions",
    "renew_live_workers",
    "session_env",
]

LAUNCHED_EVENT = "task.session_launched"
LAUNCH_FAILED_EVENT = "task.session_launch_failed"
WORKER_ACTOR = "worker"
_HOST = "claude-code-cli"
_EXEC_TIMEOUT_S = 60.0
#: ``backgrounded · 0d310bc4 (…)`` : la ligne que ``claude --bg`` imprime (vérifié avec Claude Code 2.1.309).
_BG_ID = re.compile(r"backgrounded\s*\S?\s*([0-9a-f]{8})\b")
#: Palier de la politique → alias de modèle que ``claude --model`` accepte.
_TIER_ALIAS = {"cheap": "haiku", "mid": "sonnet", "strong": "opus"}


@dataclass(frozen=True, slots=True)
class ExecResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


#: ``(argv, cwd, env)`` → résultat. Le seul point d'entrée des processus externes (claude, gh) : injectable.
Exec = Callable[[Sequence[str], Path, Mapping[str, str] | None], ExecResult]


def default_exec(argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
    """Lance *argv* sans shell. Un binaire absent ou un délai dépassé est un échec lisible (127, 124), jamais une exception."""
    from grimoire.core.safe_git import session_environ  # une session ne reçoit pas la neutralisation git du tick

    full_env = session_environ(env)
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            list(argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_EXEC_TIMEOUT_S, check=False, env=full_env,
        )
    except FileNotFoundError:
        return ExecResult(127, "", f"{argv[0]} : binaire introuvable")
    except subprocess.TimeoutExpired:
        return ExecResult(124, "", f"{argv[0]} : délai de {_EXEC_TIMEOUT_S:g} s dépassé")
    except OSError as exc:
        return ExecResult(126, "", f"{argv[0]} : {exc}")
    return ExecResult(done.returncode, done.stdout or "", done.stderr or "")


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """``launched`` (session détachée posée), ``skipped`` (une garde a refusé, rien n'est écrit) ou ``failed``."""

    task_id: str
    status: str
    detail: str = ""
    session: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status, "detail": self.detail, "session": self.session, "model": self.model}


def launched_sessions(service: TaskService) -> dict[str, dict[str, Any]]:
    """Task id → payload du dernier ``task.session_launched`` (le ledger est la seule source)."""
    found: dict[str, dict[str, Any]] = {}
    for event in service.ledger.list_events():
        if event.event_type == LAUNCHED_EVENT:
            found[event.entity_id] = dict(event.payload)
    return found


def live_background_sessions(exec_: Exec, cwd: Path, claude_bin: str = "claude") -> frozenset[tuple[str, str]] | None:
    """Les sessions de fond au travail (``claude agents --json``), en ``(id court, nom)`` ; ``None`` si la lecture échoue.

    Seul ``busy`` compte : une session ``idle`` a fini son tour (question posée, travail rendu sans transition) et ne
    travaille plus ; la tenir pour vivante repousserait sans fin le plafond dur du claim.
    """
    result = exec_([claude_bin, "agents", "--json"], cwd, None)
    if result.returncode != 0:
        return None
    try:
        raw = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(raw, list):
        return None
    return frozenset(
        (str(a["id"]), str(a.get("name", ""))) for a in raw
        if isinstance(a, dict) and a.get("kind") == "background" and a.get("status") == "busy" and a.get("id")
    )


def renew_live_workers(
    service: TaskService, exec_: Exec, claude_bin: str = "claude", *, now: datetime | None = None
) -> tuple[str, ...]:
    """Le battement des workers (#961) : renouvelle le bail de chaque tâche tenue dont la session lancée vit encore.

    Le bail n'est écrit qu'au claim : sans ce geste, un worker qui travaille plus que le TTL du bail (20 min) est
    récolté et garé en ``waiting(human)`` alors que sa session tourne. Une session absente, ou une lecture de
    ``claude agents`` qui échoue, ne renouvelle rien : la récolte juge comme avant (échec fermé).
    """
    sessions = launched_sessions(service)
    held: dict[str, tuple[dict[str, Any], int]] = {}
    for tid, payload in sessions.items():
        task = service.ledger.get_task(tid)
        # Le lancement doit être celui du claim courant : une session d'un claim récolté puis repris ne le tient pas.
        if task is not None and task.status in (TaskState.CLAIMED, TaskState.RUNNING) and task.claim is not None \
                and payload.get("lease_epoch") == task.claim.lease_epoch:
            held[tid] = (payload, task.claim.lease_epoch)
    if not held:
        return ()
    alive = live_background_sessions(exec_, service.home_root, claude_bin)
    if alive is None:
        return ()
    renewed: list[str] = []
    for tid, (payload, epoch) in held.items():
        if (str(payload.get("session", "")), f"grimoire-{tid}") not in alive:
            continue
        try:
            service.ledger.renew_lease(tid, epoch, actor_id="tick", now=now)
        except GrimoireError:
            continue  # epoch périmé ou bail rendu entre-temps : rien à renouveler
        renewed.append(tid)
    return tuple(renewed)


def _git_out(cwd: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def session_env(session_id: str) -> dict[str, str]:
    """L'environnement d'une session de fond : sans surveillance, et l'identifiant que ses commandes ``grimoire`` liront."""
    from grimoire.core.standard_state import SESSION_ENV

    return {"GRIMOIRE_UNATTENDED": "1", SESSION_ENV: session_id}


def attest_session(service: TaskService, role: str, task_id: str) -> tuple[str, str]:
    """``(identifiant, refus)`` d'une session à lancer : un uuid neuf, inscrit au journal d'autorité sous mandat actif.

    Sans mandat actif (ou journal illisible à la lecture), l'identifiant n'est pas inscrit : rien n'est attesté, rien
    n'est refusé — aucun geste ne sera pris sous mandat. Un refus d'inscription (plafond du mandat, rôle déjà tenu) ou
    une écriture du journal qui échoue rend ``("", raison)`` : la session n'est pas lancée (échec fermé).
    """
    from grimoire.missions.mandate import MandateRefusedError, MandateStore

    session = str(uuid.uuid4())
    try:
        store = MandateStore(service.home_root)
        if not store.state().active:
            return session, ""
        store.register_session(session, role, task_id)
    except MandateRefusedError as exc:
        return "", f"session {role} non inscrite au journal d'autorité : {exc}"
    except Exception as exc:  # une garde qui échoue ne lance rien
        return "", f"session {role} non inscrite : journal d'autorité en erreur ({type(exc).__name__}: {exc})"
    return session, ""


def _freeze_repository(service: TaskService, task_id: str) -> str:
    """Lot 4 : fige le dépôt GitHub de la tâche au journal d'autorité, avant le worker ; ``""`` ou la raison d'un refus."""
    from grimoire.missions.review_seal import freeze_repository

    try:
        freeze_repository(service.home_root, task_id)
    except Exception as exc:  # journal d'autorité rompu : le worker n'est pas lancé (échec fermé)
        return f"dépôt de la tâche non figé au journal d'autorité ({type(exc).__name__}: {exc})"
    return ""


_LIVE = (TaskState.CLAIMED, TaskState.RUNNING, TaskState.BLOCKED, TaskState.NEEDS_VERIFICATION, TaskState.WAITING)


def _day(stamp: str) -> str:
    return stamp[:10]


class WorkerLauncher:
    """Le *dispatcher* du tick : appelé avec les seules tâches admises, rend ce qu'il a fait de chacune."""

    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec,
        claude_bin: str = "claude", now: datetime | None = None,
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin
        self._now = now

    # -- gardes -------------------------------------------------------------

    def _active(self) -> int:
        """Les sessions de worker vivantes : tâches portant un lancement et tenant encore leur claim.

        Une session qui attend (``blocked``, ``waiting``, ``needs_verification``) est vivante : elle compte.
        """
        sessions = launched_sessions(self._service)
        return sum(1 for tid in sessions if self._service.require(tid).status in _LIVE)

    def _launched_today(self) -> int:
        today = _day((self._now or datetime.now(UTC)).isoformat())
        return sum(
            1 for e in self._service.ledger.list_events()
            if e.event_type in (LAUNCHED_EVENT, LAUNCH_FAILED_EVENT) and _day(e.created_at) == today
        )

    def _model_for(self, admit: Admit) -> str:
        role = self._policy.worker
        if role.model:
            return role.model
        route = admit.route
        if route is not None and route.outcome == "route" and route.model and route.provider.lower() in {"anthropic", "claude", "claude-code"}:
            return route.model
        return _TIER_ALIAS.get(role.tier, "")

    # -- lancement ----------------------------------------------------------

    def __call__(self, admitted: tuple[Admit, ...]) -> tuple[LaunchOutcome, ...]:
        role = self._policy.worker
        root = self._service.home_root
        outcomes: list[LaunchOutcome] = []
        for admit in admitted:
            tid = admit.task_id
            if not role.enabled:
                outcomes.append(LaunchOutcome(tid, "skipped", "rôle worker désactivé"))
                continue
            breaker = read_breaker(root)
            if breaker.open:
                outcomes.append(LaunchOutcome(tid, "skipped", f"disjoncteur ouvert : {breaker.reason}"))
                continue
            active = self._active()
            if active >= role.wip:
                outcomes.append(LaunchOutcome(tid, "skipped", f"WIP du worker atteint ({active}/{role.wip})"))
                continue
            today = self._launched_today()
            if today >= role.sessions_per_day:
                outcomes.append(LaunchOutcome(tid, "skipped", f"plafond de sessions du jour atteint ({today}/{role.sessions_per_day})"))
                continue
            outcomes.append(self._launch(admit))
        return tuple(outcomes)

    def _launch(self, admit: Admit) -> LaunchOutcome:
        service, tid = self._service, admit.task_id
        base, into, why = self._base()
        if not base:
            return LaunchOutcome(tid, "failed", f"préparation refusée : {why}")
        try:
            wt = create_task_worktree(service, tid, repo=service.home_root, base=base, into=into, actor=WORKER_ACTOR)
            service.context(tid)
            service.claim(tid, WORKER_ACTOR, host=_HOST)
        except (GrimoireError, ValueError, OSError) as exc:
            # Ni le claude ni la tâche ne sont en cause du côté « session » : rien n'est compté au disjoncteur.
            return LaunchOutcome(tid, "failed", f"préparation refusée : {exc}")
        # Inscrite après la préparation : une préparation qui échoue ne consomme pas le plafond de sessions du mandat.
        session_id, refused = attest_session(service, WORKER_ACTOR, tid)
        if not refused:
            refused = _freeze_repository(service, tid)
        if refused:
            service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=refused)
            return LaunchOutcome(tid, "skipped", refused)
        model = self._model_for(admit)
        result = self._exec(self._argv(tid, model, session_id), wt.path, session_env(session_id))
        match = _BG_ID.search(result.stdout) if result.returncode == 0 else None
        if match is None:
            detail = (result.stderr or result.stdout).strip()[:300] or f"sortie {result.returncode}, aucun identifiant de session"
            return self._failed(tid, f"claude --bg a échoué ({result.returncode}) : {detail}")
        session = match.group(1)
        current = service.require(tid).claim
        service.ledger.append_event(
            LAUNCHED_EVENT, tid, "task", WORKER_ACTOR,
            {
                "session": session, "session_id": session_id, "name": f"grimoire-{tid}", "model": model,
                "cwd": str(wt.path), "branch": wt.branch,
                "lease_epoch": current.lease_epoch if current is not None else 0,
            },
        )
        return LaunchOutcome(tid, "launched", f"session {session} dans {wt.path}", session, model)

    def _base(self) -> tuple[str, str, str]:
        """``(base, cible, raison)`` : la clé ``orchestrator.base``, à défaut ``origin/HEAD`` ; jamais la branche extraite.

        Les worktrees partent de la pointe du remote de cette branche (celle où les PR se fusionnent), à défaut de
        la branche locale ; la cible de fusion est la branche locale du même nom.
        """
        repo = self._service.home_root
        name = self._policy.base
        if not name:
            head = _git_out(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
            name = head.removeprefix("origin/") if head.startswith("origin/") else ""
        if not name:
            return "", "", (
                "ni `orchestrator.base` dans la politique ni `origin/HEAD` : base introuvable "
                "(`git remote set-head origin -a` ou poser la clé) — le lanceur ne prend jamais la branche extraite"
            )
        remote_ref = f"refs/remotes/origin/{name}"
        base = remote_ref if _git_out(repo, "rev-parse", "--verify", "--quiet", f"{remote_ref}^{{commit}}") else f"refs/heads/{name}"
        return base, name, ""

    def _failed(self, tid: str, detail: str) -> LaunchOutcome:
        """Rend le claim, note l'échec, et le compte au disjoncteur."""
        service = self._service
        try:
            service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=f"lancement échoué : {detail}")
        except GrimoireError as exc:
            detail += f" (claim non rendu : {exc})"
        service.ledger.append_event(LAUNCH_FAILED_EVENT, tid, "task", WORKER_ACTOR, {"detail": detail})
        state = record_failure(service.home_root, self._policy.breaker_threshold, f"{tid} : {detail}")
        if state.open:
            detail += " — disjoncteur ouvert"
        return LaunchOutcome(tid, "failed", detail)

    def _argv(self, tid: str, model: str, session_id: str) -> list[str]:
        argv = [self._claude, "--bg", "--name", f"grimoire-{tid}", "--session-id", session_id]
        if model:
            argv += ["--model", model]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(self._prompt(tid))
        return argv

    @staticmethod
    def _prompt(tid: str) -> str:
        return (
            f"Tu es le worker de la tâche {tid}, dans son worktree task/{tid}. "
            f"Rattache cette session à ton claim (`grimoire task attach {tid}`), lis `grimoire task show {tid}`, "
            "réalise la tâche selon ses critères d'acceptation, vérifie-la avec ses checks, puis ouvre la PR. "
            "Ne sors pas de ce worktree. Si tu es bloqué, dis-le par `grimoire task ask` plutôt que de deviner."
        )
