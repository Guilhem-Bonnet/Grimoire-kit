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

**Identité de session (lot 3, verrou 7 ; #966).** L'identité d'une session de fond se *lit* après le lancement, elle
ne se fixe pas : ``claude --bg`` ignore ``--session-id``, et l'environnement passé au client n'atteint que le démon que
le premier ``--bg`` démarre — toutes les sessions suivantes héritent du sien. Le lanceur lit donc l'identifiant court
que ``--bg`` imprime, résout le vrai ``sessionId`` dans ``claude agents --json``
(:func:`resolve_background_session`), et seulement alors, sous un mandat actif, l'inscrit au journal d'autorité
(:meth:`~grimoire.missions.mandate.MandateStore.register_session`) en rôle ``worker``. C'est ce qui permet de refuser un
reçu de revue écrit par la session qui a fait le travail. Sans mandat, rien ne s'inscrit. Avant de lancer, le mandat
est relu sans écrire (:func:`mandate_precheck`) : plafond du jour atteint ou journal illisible, rien n'est lancé.
Identité introuvable ou inscription refusée après le lancement : la session est arrêtée (``claude stop``) et la tâche
rendue ; la tentative est notée (``task.session_launch_failed``, qui compte au plafond du jour) et, sauf refus de
plafond (qui révoque déjà le mandat), comptée au disjoncteur. Un arrêt **non confirmé** ne rend pas la tâche : elle
reste tenue (``claimed``) avec la session nommée au ledger (``halted: false``), compte au WIP et au disjoncteur, et la
récolte la gare chez l'humain à l'expiration du bail. La session a pu commencer à travailler avant son inscription :
c'est sans effet sur la garde, l'inscription précède tout tick qui jugerait ses reçus. Dans la session, les commandes
``grimoire`` lisent l'identité que l'hôte exporte (``CLAUDE_CODE_SESSION_ID``, qui prime sous ``GRIMOIRE_UNATTENDED=1`` :
:func:`~grimoire.core.standard_state.current_session`).

**Limites (#966).**

- L'identité de session n'est pas un secret : un worker peut lire le ``sessionId`` du relecteur dans ``claude agents``
  et signer un reçu avec ``CLAUDE_CODE_SESSION_ID=<cet id>``. Ce n'est pas pire qu'avant (l'environnement est aux mains
  de l'agent), mais le verrou 7 ne tient que contre un worker honnête. Défense à venir : prouver l'appelant par le hook
  ``PreToolUse`` de l'hôte (``session_id`` lu sur son entrée standard), pas par l'environnement.
- Une reprise ``claude --bg --resume <sessionId>`` (relecteur et merger) n'est ni résolue ni réinscrite : on suppose
  que l'hôte garde l'identité reprise, ce qui n'a pas été vérifié sur Claude Code 2.1.310.
- Un tick qui meurt entre ``claude --bg`` et l'événement qui note la session laisse une session orpheline, que rien
  au ledger ne nomme.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
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
    "mandate_precheck",
    "renew_live_workers",
    "resolve_background_session",
    "session_env",
    "unhalted_sessions",
]

LAUNCHED_EVENT = "task.session_launched"
LAUNCH_FAILED_EVENT = "task.session_launch_failed"
WORKER_ACTOR = "worker"
_HOST = "claude-code-cli"
_EXEC_TIMEOUT_S = 60.0
#: ``backgrounded · 0d310bc4 (…)`` : la ligne que ``claude --bg`` imprime (vérifié avec Claude Code 2.1.310).
_BG_ID = re.compile(r"backgrounded\s*\S?\s*([0-9a-f]{8})\b")
#: Le ``sessionId`` d'une session de fond, tel que ``claude agents --json`` le rend : un uuid (le nom de son transcript).
_SESSION_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Lectures de ``claude agents`` avant de conclure qu'une session lancée est introuvable, et la pause entre deux.
_RESOLVE_ATTEMPTS = 10
_RESOLVE_PAUSE_S = 1.0
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
    raw = _read_agents(exec_, cwd, claude_bin)
    if raw is None:
        return None
    return frozenset(
        (str(a["id"]), str(a.get("name", ""))) for a in raw
        if a.get("kind") == "background" and a.get("status") == "busy" and a.get("id")
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


def _read_agents(exec_: Exec, cwd: Path, claude_bin: str) -> list[dict[str, Any]] | None:
    result = exec_([claude_bin, "agents", "--json"], cwd, None)
    if result.returncode != 0:
        return None
    try:
        raw = json.loads(result.stdout)
    except ValueError:
        return None
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else None


def _same_dir(seen: object, expected: Path) -> bool:
    try:
        return Path(str(seen)).resolve() == expected.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def resolve_background_session(
    exec_: Exec, cwd: Path, short: str, claude_bin: str = "claude", *,
    name: str = "", session_cwd: Path | None = None,
    attempts: int = _RESOLVE_ATTEMPTS, pause_s: float | None = None,
) -> str:
    """Le vrai ``sessionId`` de la session de fond d'identifiant court *short* (``claude agents --json``) ; ``""`` sinon.

    ``claude --bg`` ignore ``--session-id`` (#966, Claude Code 2.1.310) : l'identité d'une session de fond se lit, elle
    ne se choisit pas. Retenue : l'entrée ``kind: background`` dont l'``id`` est *short* et dont le ``sessionId`` est un
    uuid qui commence par *short* ; quand l'entrée porte ``name`` ou ``cwd``, ils doivent valoir *name* (le ``--name``
    du lancement) et *session_cwd* (le dossier où la session a été lancée), si l'appelant les donne.
    Lecture en échec, aucune entrée ou plusieurs candidates : ``""`` (échec fermé),
    après *attempts* lectures espacées de *pause_s* (la session peut n'être pas encore listée à la sortie de ``--bg``) ;
    *pause_s* omis : :data:`_RESOLVE_PAUSE_S`, lu à l'appel.
    """
    if not re.fullmatch(r"[0-9a-f]{8}", short):
        return ""
    for attempt in range(max(attempts, 1)):
        if attempt:
            time.sleep(_RESOLVE_PAUSE_S if pause_s is None else pause_s)
        agents = _read_agents(exec_, cwd, claude_bin)
        if agents is None:
            continue
        found = {
            str(a.get("sessionId") or "") for a in agents
            if a.get("kind") == "background" and str(a.get("id") or "") == short
            and not (name and a.get("name") is not None and str(a.get("name")) != name)
            and not (session_cwd is not None and a.get("cwd") is not None and not _same_dir(a.get("cwd"), session_cwd))
        }
        found = {s for s in found if s.startswith(short) and _SESSION_UUID.fullmatch(s)}
        if len(found) == 1:
            return found.pop()
        if len(found) > 1:
            return ""  # deux sessions pour un même id court : on ne choisit pas
    return ""


def unhalted_sessions(service: TaskService) -> dict[str, dict[str, Any]]:
    """Task id → payload du dernier ``task.session_launch_failed`` d'une session lancée dont l'arrêt n'est pas confirmé.

    Seules les tâches qui tiennent encore le claim de cette tentative (même ``lease_epoch``) sont rendues.
    """
    last: dict[str, dict[str, Any]] = {}
    for event in service.ledger.list_events():
        if event.event_type == LAUNCH_FAILED_EVENT:
            last[event.entity_id] = dict(event.payload)
    found: dict[str, dict[str, Any]] = {}
    for tid, payload in last.items():
        task = service.ledger.get_task(tid)
        if payload.get("halted") is False and task is not None and task.claim is not None \
                and payload.get("lease_epoch") == task.claim.lease_epoch:
            found[tid] = payload
    return found


def _git_out(cwd: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def session_env() -> dict[str, str]:
    """L'environnement passé à ``claude --bg`` : la seule marque « sans surveillance », jamais une identité.

    Cet environnement n'atteint que le démon que le premier ``--bg`` démarre, et toutes les sessions de fond suivantes
    héritent du sien (#966) : un identifiant posé ici serait celui de la première session pour toutes. Chaque session
    reçoit de l'hôte son vrai identifiant (``CLAUDE_CODE_SESSION_ID``). ``GRIMOIRE_UNATTENDED`` peut, lui, fuir dans le
    démon : c'est une marque de sûreté (elle refuse les gestes d'origine humaine), pas une identité.
    """
    return {"GRIMOIRE_UNATTENDED": "1"}


#: Code d'un refus d'inscription qui n'est pas un refus du mandat : le journal d'autorité est en erreur.
JOURNAL_ERROR = "journal_error"


def attest_session(service: TaskService, session_id: str, role: str, task_id: str) -> tuple[str, str]:
    """Inscrit *session_id* (résolu après le lancement) au journal d'autorité sous mandat actif ; ``(refus, code)``.

    ``("", "")`` : inscrite, ou aucun mandat actif (rien n'est attesté, rien n'est refusé — aucun geste ne sera pris
    sous mandat). Sinon la raison et son code : celui du :class:`~grimoire.missions.mandate.MandateRefusedError`
    (``cap_exceeded``, ``role_conflict``…) ou :data:`JOURNAL_ERROR` ; l'appelant arrête la session (échec fermé).
    """
    from grimoire.missions.mandate import MandateRefusedError, MandateStore

    try:
        store = MandateStore(service.home_root)
        if not store.state().active:
            return "", ""
        store.register_session(session_id, role, task_id)
    except MandateRefusedError as exc:
        return f"session {role} non inscrite au journal d'autorité : {exc}", str(exc.code)
    except Exception as exc:  # une garde qui échoue ne laisse pas la session travailler
        return f"session {role} non inscrite : journal d'autorité en erreur ({type(exc).__name__}: {exc})", JOURNAL_ERROR
    return "", ""


def mandate_precheck(service: TaskService, role: str) -> str:
    """Avant de lancer, relu sans écrire : ``""`` si l'inscription peut réussir, sinon pourquoi elle échouerait.

    Sans mandat actif, rien ne sera inscrit : ``""``. Un worker au plafond du jour du mandat, ou un journal illisible :
    la raison — rien n'est lancé, plutôt que lancer une session qu'il faudrait arrêter aussitôt.
    """
    from grimoire.missions.mandate import MandateStore

    try:
        state = MandateStore(service.home_root).state()
    except Exception as exc:  # journal illisible : l'inscription échouerait
        return f"journal d'autorité illisible ({type(exc).__name__}: {exc}) : rien n'est lancé"
    if not state.active or state.mandate is None:
        return ""
    if role == WORKER_ACTOR and state.sessions_today >= state.mandate.max_sessions_per_day:
        return f"plafond de {state.mandate.max_sessions_per_day} sessions par jour du mandat atteint : rien n'est lancé"
    return ""


def _halt(exec_: Exec, cwd: Path, short: str, claude_bin: str) -> tuple[bool, str]:
    """Arrête (``claude stop``) une session lancée qu'on ne peut pas attester : ``(arrêt confirmé, fin de phrase)``."""
    result = exec_([claude_bin, "stop", short], cwd, None)
    if result.returncode == 0:
        return True, f", session {short} arrêtée"
    why = (result.stderr or result.stdout).strip()[:200] or f"sortie {result.returncode}"
    return False, f", session {short} NON arrêtée ({why})"


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
        self._version: str | None = None

    # -- gardes -------------------------------------------------------------

    def _active(self) -> int:
        """Les sessions de worker vivantes : tâches portant un lancement et tenant encore leur claim.

        Une session qui attend (``blocked``, ``waiting``, ``needs_verification``) est vivante : elle compte. Une session
        lancée puis abandonnée sans arrêt confirmé (``halted: false``) tient encore son claim : elle compte aussi.
        """
        tasks = set(launched_sessions(self._service)) | set(unhalted_sessions(self._service))
        return sum(1 for tid in tasks if self._service.require(tid).status in _LIVE)

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

    def _host_version(self) -> str:
        """La version de l'hôte (``claude --version``), lue une fois par tick (P86) ; ``""`` si illisible.

        C'est elle que le claim porte : le frein d'un hôte ne se prouve que pour une version exacte.
        """
        if self._version is None:
            from grimoire.hosts.host_version import VERSION_PATTERN

            try:
                done = self._exec([self._claude, "--version"], self._service.home_root, None)
                found = VERSION_PATTERN.search(done.stdout or "") if done.returncode == 0 else None
            except (OSError, ValueError):
                found = None
            self._version = found.group(0) if found else ""
        return self._version

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
        if blocked := mandate_precheck(service, WORKER_ACTOR):
            return LaunchOutcome(tid, "skipped", blocked)
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
        refused = _freeze_repository(service, tid)
        if refused:
            service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=refused)
            return LaunchOutcome(tid, "skipped", refused)
        model = self._model_for(admit)
        result = self._exec(self._argv(tid, model), wt.path, session_env())
        match = _BG_ID.search(result.stdout) if result.returncode == 0 else None
        if match is None:
            detail = (result.stderr or result.stdout).strip()[:300] or f"sortie {result.returncode}, aucun identifiant de session"
            return self._failed(tid, f"claude --bg a échoué ({result.returncode}) : {detail}")
        session = match.group(1)
        # #966 : l'identité se lit après le lancement ; c'est elle, et elle seule, qui s'inscrit et se note.
        session_id = resolve_background_session(
            self._exec, wt.path, session, self._claude, name=f"grimoire-{tid}", session_cwd=wt.path
        )
        if not session_id:
            return self._abandon(tid, session, wt.path, f"session {session} lancée mais introuvable dans `claude agents` : "
                                 "identité non résolue", count=True)
        refused, code = attest_session(service, session_id, WORKER_ACTOR, tid)
        if refused:
            from grimoire.missions.mandate import CAP_EXCEEDED

            # Un refus de plafond révoque déjà le mandat ; un rôle en conflit ou un journal en erreur est une panne.
            return self._abandon(tid, session, wt.path, refused, count=code != CAP_EXCEEDED)
        version = self._host_version()
        if version:
            # P86 : la session est nommée au claim avec la version de l'hôte ; sans elle le frein n'est jamais prouvé.
            with contextlib.suppress(GrimoireError):
                service.attach_session(tid, session_id, session_host=_HOST, actor=WORKER_ACTOR, host_version=version)
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

    def _abandon(self, tid: str, short: str, cwd: Path, detail: str, *, count: bool) -> LaunchOutcome:
        """Une session lancée qu'on n'atteste pas : arrêtée, la tentative notée, la tâche rendue.

        Arrêt non confirmé : la tâche reste tenue (une session qui tourne peut encore pousser sur sa branche), la
        session est nommée au ledger (``halted: false``) et l'échec compte au disjoncteur quoi qu'il arrive.
        """
        service = self._service
        halted, said = _halt(self._exec, cwd, short, self._claude)
        detail += said
        claim = service.require(tid).claim
        epoch = claim.lease_epoch if claim is not None else 0
        if halted:
            try:
                service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=f"lancement abandonné : {detail}")
            except GrimoireError as exc:
                detail += f" (claim non rendu : {exc})"
        else:
            detail += " : tâche gardée tenue tant que l'arrêt n'est pas confirmé (la récolte la gare chez l'humain)"
        service.ledger.append_event(
            LAUNCH_FAILED_EVENT, tid, "task", WORKER_ACTOR,
            {"detail": detail, "session": short, "halted": halted, "name": f"grimoire-{tid}", "cwd": str(cwd),
             "lease_epoch": epoch},
        )
        if count or not halted:
            if record_failure(service.home_root, self._policy.breaker_threshold, f"{tid} : {detail}").open:
                detail += " — disjoncteur ouvert"
            return LaunchOutcome(tid, "failed", detail, short)
        return LaunchOutcome(tid, "skipped", detail, short)

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

    def _argv(self, tid: str, model: str) -> list[str]:
        # Pas de ``--session-id`` : ``claude --bg`` l'ignore (#966), l'identité se lit après le lancement.
        argv = [self._claude, "--bg", "--name", f"grimoire-{tid}"]
        if model:
            argv += ["--model", model]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(self._prompt(tid, self._host_version()))
        return argv

    @staticmethod
    def _prompt(tid: str, host_version: str = "") -> str:
        """La consigne du worker ; sa PR s'ouvre prête à relire : un brouillon n'est jamais fusionné (#993)."""
        flag = f" --host-version {host_version}" if host_version else ""
        return (
            f"Tu es le worker de la tâche {tid}, dans son worktree task/{tid}. "
            f"Rattache cette session à ton claim (`grimoire task attach {tid}{flag}`), lis `grimoire task show {tid}`, "
            "réalise la tâche selon ses critères d'acceptation, vérifie-la avec ses checks, puis ouvre la PR "
            "prête à relire, jamais en brouillon (`gh pr create` sans `--draft` ; déjà en brouillon : `gh pr ready`), "
            f"et passe la tâche en revue (`grimoire task move {tid} --to running`, puis `--to needs_verification`) : "
            "le relecteur ne prend que les tâches en revue. "
            "Un choix réversible qui reste dans le périmètre des critères, tranche-le toi-même et note-le "
            f"(`grimoire task note {tid}`). Aux étapes clés (plan arrêté, difficulté rencontrée, tests verts, "
            f"PR ouverte), note ta progression en une phrase (`grimoire task note {tid} \"...\"`) : c'est ce que "
            "le cockpit montre de toi ; rien ne te bloque si tu l'oublies. Ne sors pas de ce worktree. "
            "Si tu es bloqué (critère inatteignable, hors périmètre, travail déjà livré), dis-le par "
            "`grimoire task ask` plutôt que de deviner."
        )
