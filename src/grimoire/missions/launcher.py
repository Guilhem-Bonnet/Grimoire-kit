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
import hashlib
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
from grimoire.missions.liveness import probe_status
from grimoire.missions.provider_failure import transient_api_error
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import create_task_worktree
from grimoire.missions.verifiability import Verifiability, classify
from grimoire.missions.worker_provider import WorkerRoute, is_local_provider, route_denial, route_worker, settings_path
from grimoire.providers.data_policy import hosted_fallback_refusal
from grimoire.tools.workspace_work import transcript_path

if TYPE_CHECKING:
    from grimoire.missions.admission import Admit
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "LAUNCHED_EVENT",
    "LAUNCH_FAILED_EVENT",
    "MAX_PROVIDER_RESUMES",
    "PROVIDER_ERROR_CAUSE",
    "WORKER_ACTOR",
    "WORKER_DEFERRED_EVENT",
    "ExecResult",
    "LaunchOutcome",
    "ProviderResume",
    "WorkerLauncher",
    "active_worker_ids",
    "attest_session",
    "default_exec",
    "halt_idle_workers",
    "launched_sessions",
    "live_background_sessions",
    "mandate_precheck",
    "provider_resumes",
    "renew_live_workers",
    "resolve_background_session",
    "resume_provider_failed_workers",
    "resume_worker",
    "session_env",
    "unhalted_sessions",
]

LAUNCHED_EVENT = "task.session_launched"
LAUNCH_FAILED_EVENT = "task.session_launch_failed"
#: La session du worker, arrêtée (``claude stop``) quand sa tâche passe en revue ou en attente : la conversation reste.
WORKER_HALTED_EVENT = "task.worker_session_halted"
#: La session du worker, reprise (``claude --bg --resume``) ou repartie à neuf sur le même worktree après un renvoi.
WORKER_RESUMED_EVENT = "task.worker_session_resumed"
#: Le repli sur le modèle de l'hôte a été refusé (classe de données sans fournisseur hébergé admis) : la tâche
#: retourne en ``ready`` avec son motif, sans question à l'humain et sans compter au disjoncteur.
WORKER_DEFERRED_EVENT = "task.worker_deferred"
#: La reprise automatique qui suit une erreur transitoire du fournisseur (#1158) : sa cause au ledger.
PROVIDER_ERROR_CAUSE = "provider_error"
#: Plafond des reprises automatiques après une erreur du fournisseur ; au-delà, la tâche se gare.
MAX_PROVIDER_RESUMES = 3
#: La consigne de reprise : repartir de l'état du worktree, sans refaire ce qui est fait.
PROVIDER_RESUME_MESSAGE = (
    "Ton dernier tour s'est arrêté sur une erreur transitoire du fournisseur. Reprends où tu en étais : "
    "relis l'état du worktree et de la tâche avant d'agir, ne recommence pas ce qui est déjà fait, et "
    "poursuis jusqu'à la PR prête à relire et la tâche passée en revue."
)
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
#: Mesuré le 2026-11-06 : 45 k jetons de contexte au premier tour sans ces options, 10 k avec. Sans ``Skill`` ni ``Agent``.
#: ``--tools`` est variadique : en deux jetons, il avale la consigne qui le suit (relecteur muet, #1160).
LEAN_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
LEAN_SESSION_ARGS = ("--strict-mcp-config", f"--tools={LEAN_TOOLS}")


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
    """``launched`` (session détachée posée), ``skipped`` (une garde a refusé, rien n'est écrit), ``deferred`` (le
    repli sur l'hôte est fermé par la classe de données, la tâche retourne en ``ready``) ou ``failed``."""

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

    Seul le statut ``busy`` compte : une session ``idle`` (ou ``blocked``) a fini son tour (question posée,
    travail rendu sans transition) et ne travaille plus ; la tenir pour vivante repousserait sans fin le
    plafond dur du claim. Le statut se lit par :func:`~grimoire.missions.liveness.probe_status` : Claude Code
    2.1.319 rend ``state: "working"`` pour une session de fond, pas ``status: "busy"``.
    """
    raw = _read_agents(exec_, cwd, claude_bin)
    if raw is None:
        return None
    return frozenset(
        (str(a["id"]), str(a.get("name", ""))) for a in raw
        if a.get("kind") == "background" and probe_status(a) == "busy" and a.get("id")
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
    for tid, (_payload, epoch) in held.items():
        short, _started, _halted = _session_marks(service, tid)  # la dernière session, reprise comprise
        if not short or (short, f"grimoire-{tid}") not in alive:
            continue
        try:
            service.ledger.renew_lease(tid, epoch, actor_id="tick", now=now)
        except GrimoireError:
            continue  # epoch périmé ou bail rendu entre-temps : rien à renouveler
        renewed.append(tid)
    return tuple(renewed)


def release_ended_workers(
    service: TaskService, exec_: Exec, claude_bin: str = "claude", *, now: datetime | None = None
) -> tuple[str, ...]:
    """P2 de la vivacité (P90) : rend ``ready`` la tâche tenue dont la session lancée est sondée terminée.

    Sans ce geste, une session finie (``done``/``stopped``/``failed``) qui tient encore sa tâche la gardait jusqu'à
    la fin du bail (20 min). Le lancement doit être celui du claim courant ; une sonde en échec ne rend rien.
    """
    from grimoire.missions.ended import release_ended

    held = {
        tid: payload for tid, payload in launched_sessions(service).items()
        if (task := service.ledger.get_task(tid)) is not None and task.status in (TaskState.CLAIMED, TaskState.RUNNING)
        and task.claim is not None and payload.get("lease_epoch") == task.claim.lease_epoch
    }
    if not held:
        return ()
    raw = _read_agents(exec_, service.home_root, claude_bin)
    if raw is None:
        return ()
    ended = {
        (str(a["id"]), str(a.get("name", ""))) for a in raw
        if a.get("kind") == "background" and a.get("state") in ("done", "stopped", "failed") and a.get("id")
    }
    released: list[str] = []
    for tid, payload in held.items():
        short, _started, _halted = _session_marks(service, tid)  # la dernière session, reprise comprise
        if not short or (short, f"grimoire-{tid}") not in ended:
            continue
        try:
            decision = release_ended(service.ledger, tid, int(payload["lease_epoch"]), actor_id="tick", now=now)
        except GrimoireError:
            continue
        if decision is not None:
            released.append(tid)
    return tuple(released)


@dataclass(frozen=True, slots=True)
class ProviderResume:
    """Ce que le tick a fait d'un worker dont la session a fini sur une erreur du fournisseur (#1158)."""

    task_id: str
    action: str  # ``resumed`` | ``parked`` | ``failed``
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"task_id": self.task_id, "action": self.action, "detail": self.detail}


def provider_resumes(service: TaskService, task_id: str) -> int:
    """Les reprises automatiques déjà tentées depuis le dernier lancement, pour une erreur du fournisseur.

    Le plafond se compte par *série* : un nouveau lancement (``task.session_launched``) ou une reprise
    venue d'ailleurs (relecteur, merger) repart à neuf. Seules les reprises de cause
    :data:`PROVIDER_ERROR_CAUSE` s'accumulent.
    """
    count = 0
    for event in reversed(service.ledger.events_for(task_id)):
        if event.event_type == LAUNCHED_EVENT:
            break
        if event.event_type != WORKER_RESUMED_EVENT:
            continue
        if event.payload.get("cause") != PROVIDER_ERROR_CAUSE:
            break
        count += 1
    return count


def _park_provider_error(service: TaskService, task: MissionTask, error: str, tried: int) -> bool:
    """Gare la tâche ``waiting(human)`` avec la question qui nomme l'erreur du fournisseur ; vrai si écrite.

    Ce n'est pas un simple « bail perdu (heartbeat) : resume_branch » : l'humain lit la vraie cause et
    choisit entre relancer et abandonner.
    """
    claim = task.claim
    if claim is None:
        return False
    question = (
        f"La session de {task.id} s'est arrêtée sur une erreur du fournisseur ({error[:300]}) "
        f"et {tried} reprise(s) automatique(s) n'ont pas suffi. La relancer encore, ou l'abandonner ?"
    )
    try:
        service.ask(
            task.id, question, "tick", kind="retry_exhausted",
            options=("réessayer", "abandonner"), epoch=claim.lease_epoch,
        )
    except GrimoireError:
        return False  # la récolte ordinaire jugera à l'expiration du bail
    return True


def resume_provider_failed_workers(
    service: TaskService, exec_: Exec, claude_bin: str = "claude", *, now: datetime | None = None,
    permission_mode: str = "", max_resumes: int = MAX_PROVIDER_RESUMES,
) -> tuple[ProviderResume, ...]:
    """Relance sur la même session le worker dont le dernier tour a fini sur une erreur du fournisseur (#1158).

    Un bail ne se renouvelle que pour une session ``busy`` : une erreur transitoire du fournisseur
    (5xx, 429, ``overloaded``) laisse la session au repos, donc muette pour le battement, et la récolte
    la garerait vingt minutes plus tard alors que la conversation est intacte. Le tick relit la fin du
    journal de session (:func:`~grimoire.missions.provider_failure.transient_api_error`) et reprend la
    session (``task.worker_session_resumed``) jusqu'à *max_resumes* fois ; au-delà, la tâche est garée
    ``waiting(human)`` avec une question qui nomme l'erreur du fournisseur.

    Une session encore ``busy``, un journal qui ne finit pas sur une erreur transitoire, ou une lecture de
    ``claude agents`` en échec ne déclenchent rien : l'échec reste fermé, la récolte juge comme avant.
    """
    launches = launched_sessions(service)
    if not launches:
        return ()
    alive = live_background_sessions(exec_, service.home_root, claude_bin)
    if alive is None:
        return ()  # sonde muette : on ne juge pas une session qu'on ne voit pas
    outcomes: list[ProviderResume] = []
    for tid, launch in launches.items():
        task = service.ledger.get_task(tid)
        if task is None or task.status not in (TaskState.CLAIMED, TaskState.RUNNING) or task.claim is None:
            continue
        if launch.get("lease_epoch") != task.claim.lease_epoch:
            continue
        short, _started, _halted = _session_marks(service, tid)
        if not short or (short, f"grimoire-{tid}") in alive:
            continue  # elle travaille encore : rien à reprendre
        error = transient_api_error(
            transcript_path(Path(str(launch.get("cwd") or "")), str(launch.get("session_id") or ""))
        )
        if not error:
            continue
        tried = provider_resumes(service, tid)
        if tried >= max_resumes:
            if _park_provider_error(service, task, error, tried):
                outcomes.append(ProviderResume(tid, "parked", error))
            continue
        result = resume_worker(
            service, exec_, claude_bin, tid, launch, PROVIDER_RESUME_MESSAGE,
            permission_mode=str(launch.get("permission_mode") or permission_mode), cause=PROVIDER_ERROR_CAUSE,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:300] or f"sortie {result.returncode}"
            outcomes.append(ProviderResume(tid, "failed", detail))
            continue
        with contextlib.suppress(GrimoireError):  # la session reprise vit : son bail repart maintenant
            service.ledger.renew_lease(tid, task.claim.lease_epoch, actor_id="tick", now=now)
        outcomes.append(ProviderResume(tid, "resumed", error))
    return tuple(outcomes)


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


def _worktree_hooks(wt_path: Path) -> str:
    """#957 : le worktree du worker porte le hook PreToolUse ; ``""`` ou la raison d'un refus.

    Un dépôt qui ne suit pas ses fichiers d'hôte (``.claude/`` non suivi) donne un worktree sans hooks : on y rejoue la
    synchronisation de l'hôte Claude (fichiers non suivis, retirés avec le worktree), puis on vérifie. Échec fermé.
    """
    from grimoire.bridges.schemas import HostId
    from grimoire.hosts.sync import sync_host_surfaces

    def armed() -> bool:
        try:
            data = json.loads((wt_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
            return bool(data.get("hooks", {}).get("PreToolUse"))
        except (OSError, ValueError, AttributeError):
            return False

    if armed():
        return ""
    outcome = sync_host_surfaces(
        wt_path,
        only=frozenset({HostId.CLAUDE_CODE_CLI}),
        # `CLAUDE.md` n'est pas émis ici : non suivi à la racine du worktree,
        # il compterait comme du travail non commité (sceau de relecture,
        # collecte du janitor). Le `.gitignore` posé dans `.claude/` ne couvre
        # pas la racine ; le worker reçoit son contexte par le hook
        # SessionStart armé juste après.
        skip_files=frozenset({"CLAUDE.md"}),
    )
    if armed():
        # Fichiers générés, propres à ce worktree : ignorés par lui seul, sinon le relecteur et le janitor y verraient
        # des « modifications non commitées » et refuseraient de relire ou de ranger.
        (wt_path / ".claude" / ".gitignore").write_text("*\n", encoding="utf-8")
        return ""
    return f"worktree sans hook PreToolUse Grimoire après host sync ({outcome.error or outcome.warning or 'hook absent'})"


def _freeze_repository(service: TaskService, task_id: str) -> str:
    """Lot 4 : fige le dépôt GitHub de la tâche au journal d'autorité, avant le worker ; ``""`` ou la raison d'un refus."""
    from grimoire.missions.review_seal import freeze_repository

    try:
        freeze_repository(service.home_root, task_id)
    except Exception as exc:  # journal d'autorité rompu : le worker n'est pas lancé (échec fermé)
        return f"dépôt de la tâche non figé au journal d'autorité ({type(exc).__name__}: {exc})"
    return ""


_LIVE = (TaskState.CLAIMED, TaskState.RUNNING, TaskState.BLOCKED, TaskState.NEEDS_VERIFICATION, TaskState.WAITING)
#: Les états où la session du worker travaille : seuls ceux-là occupent une place du WIP.
_WORKING = (TaskState.CLAIMED, TaskState.RUNNING)
#: Les états où le worker a rendu la main (revue, attente) : sa session est arrêtée, reprise si la tâche lui revient.
_PARKED = (TaskState.NEEDS_VERIFICATION, TaskState.WAITING)


def active_worker_ids(service: TaskService) -> frozenset[str]:
    """Les tâches dont une session de worker occupe une place du WIP.

    Une session compte quand elle travaille (``claimed``, ``running`` : une session reprise après un renvoi y revient
    d'elle-même). Revue, fusion et attente ne comptent pas : leur session est arrêtée. Une session lancée puis
    abandonnée sans arrêt confirmé (``halted: false``) tient encore son claim : elle compte tant qu'elle est vivante.
    """
    def status(tid: str) -> TaskState | None:
        task = service.ledger.get_task(tid)
        return task.status if task is not None else None

    working = {tid for tid in launched_sessions(service) if status(tid) in _WORKING}
    stray = {tid for tid in unhalted_sessions(service) if status(tid) in _LIVE}
    return frozenset(working | stray)


def _session_marks(service: TaskService, task_id: str) -> tuple[str, int, int]:
    """``(session courante, rang de sa dernière mise en route, rang de son dernier arrêt)`` dans le ledger de la tâche."""
    short, started, halted = "", -1, -1
    for rank, event in enumerate(service.ledger.events_for(task_id)):
        if event.event_type in (LAUNCHED_EVENT, WORKER_RESUMED_EVENT):
            short, started = str(event.payload.get("session") or short), rank
        elif event.event_type == WORKER_HALTED_EVENT:
            halted = rank
    return short, started, halted


def halt_idle_workers(service: TaskService, exec_: Exec, claude_bin: str = "claude") -> tuple[str, ...]:
    """Arrête (``claude stop``) la session de chaque worker dont la tâche est en revue ou en attente ; rend les tâches arrêtées.

    La conversation reste : un renvoi du relecteur ou du merger la reprend (:func:`resume_worker`). L'arrêt est noté au
    ledger (``task.worker_session_halted``) ; un arrêt refusé ne note rien et sera retenté au tick suivant, sauf si la
    session n'est plus listée par ``claude agents`` (déjà finie : rien à arrêter, notée comme telle).
    """
    halted: list[str] = []
    for tid, payload in launched_sessions(service).items():
        task = service.ledger.get_task(tid)
        if task is None or task.status not in _PARKED:
            continue
        short, started, last_halt = _session_marks(service, tid)
        if not short or last_halt > started:
            continue
        cwd = Path(str(payload.get("cwd") or service.home_root))
        confirmed, _ = _halt(exec_, cwd, short, claude_bin)
        gone = False
        if not confirmed:
            agents = _read_agents(exec_, cwd, claude_bin)
            gone = agents is not None and not any(str(a.get("id") or "") == short for a in agents)
            if not gone:
                continue
        service.ledger.append_event(
            WORKER_HALTED_EVENT, tid, "task", WORKER_ACTOR,
            {"session": short, "status": task.status.value, "already_gone": gone},
        )
        halted.append(tid)
    return tuple(halted)


def _seal_worker_for_dual_review(
    service: TaskService, task_id: str, session_id: str, model: str, provider: str, policy: OrchestratorPolicy | None = None,
) -> None:
    """En ``protected_gate: dual_review`` seulement : le modèle et l'empreinte de session d'un worker qui vient d'être
    lancé vont au journal d'autorité, où la double relecture les compare (:mod:`.dual_review`) — le ledger, que le
    worker écrit, n'y suffit pas. En ``human``, rien n'est écrit. Une politique illisible ou un journal qui refuse
    l'écriture ne retiennent pas le lancement : sans cette entrée, aucune double relecture ne tiendra pour la tâche et
    sa PR ira à l'humain (échec fermé).

    Sans *policy* (:func:`resume_worker`, une session neuve après une reprise ratée), l'entrée est notée ``relaunch`` :
    son modèle vient du lancement noté au ledger, elle ne vaut donc jamais lancement d'origine."""
    from grimoire.missions.dual_review import PROTECTED_GATE_DUAL_REVIEW
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy
    from grimoire.missions.review_seal import seal_worker_launch

    with contextlib.suppress(Exception):
        relaunch = policy is None
        policy = policy if policy is not None else load_orchestrator_policy(service.home_root).policy
        if policy is None or policy.merger.protected_gate != PROTECTED_GATE_DUAL_REVIEW:
            return
        seal_worker_launch(service.home_root, task_id, hashlib.sha256(session_id.encode("utf-8")).hexdigest(), model,
                           provider, relaunch=relaunch)


def resume_worker(
    service: TaskService, exec_: Exec, claude_bin: str, task_id: str, launch: Mapping[str, Any], message: str, *,
    permission_mode: str = "", cause: str = "",
) -> ExecResult:
    """Rend la tâche à son worker : reprend la session arrêtée par ``claude --bg --resume``, à défaut en lance une neuve.

    Une reprise qui échoue (conversation introuvable, hôte en erreur) ne laisse pas la tâche sans worker : une session
    neuve repart sur le même worktree, avec la consigne du worker et *message* pour contexte. Le résultat est celui de
    la reprise si les deux échouent. La session obtenue est notée (``task.worker_session_resumed``) : elle compte
    de nouveau dans le WIP et c'est elle que le prochain arrêt vise. *cause* nomme l'origine de la reprise
    (``provider_error`` pour le tick, #1158) ; elle est notée au ledger et plafonne les reprises automatiques.
    """
    cwd = Path(str(launch.get("cwd") or "."))
    argv = [claude_bin, "--bg", "--resume", str(launch.get("session_id") or "")]
    # Une conversation reprise reste chez son fournisseur : ses réglages ne sont pas gardés par la session.
    routed = ["--settings", str(settings_path(str(launch["provider"])))] if launch.get("provider") else []
    if launch.get("lean"):
        routed += LEAN_SESSION_ARGS
    argv += routed
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    result = exec_([*argv, message], cwd, session_env())
    fresh = False
    if result.returncode != 0:
        fresh_argv = [claude_bin, "--bg", "--name", f"grimoire-{task_id}", *routed]
        if launch.get("model"):
            fresh_argv += ["--model", str(launch["model"])]
        if permission_mode:
            fresh_argv += ["--permission-mode", permission_mode]
        retry = exec_([*fresh_argv, f"{WorkerLauncher._prompt(task_id)}\n\nReprise après un renvoi : {message}"], cwd, session_env())
        if retry.returncode != 0:
            return result
        result, fresh = retry, True
    match = _BG_ID.search(result.stdout)
    short = match.group(1) if match else str(launch.get("session") or "")
    payload: dict[str, Any] = {"session": short, "fresh": fresh, "name": f"grimoire-{task_id}"}
    if cause:
        payload["cause"] = cause
    if fresh and match:
        session_id = resolve_background_session(exec_, cwd, short, claude_bin, name=f"grimoire-{task_id}", session_cwd=cwd)
        payload["session_id"] = session_id
        refused, _code = attest_session(service, session_id, WORKER_ACTOR, task_id) if session_id else ("session non résolue", "")
        if refused:
            _halt(exec_, cwd, short, claude_bin)
            return ExecResult(1, "", refused)
        _seal_worker_for_dual_review(service, task_id, session_id, str(launch.get("model") or ""), str(launch.get("provider") or ""))
        current = service.require(task_id).claim
        service.ledger.append_event(
            LAUNCHED_EVENT, task_id, "task", WORKER_ACTOR,
            {**launch, "session": short, "session_id": session_id, "relaunch": True,
             "lease_epoch": current.lease_epoch if current is not None else launch.get("lease_epoch", 0)},
        )
    service.ledger.append_event(WORKER_RESUMED_EVENT, task_id, "task", WORKER_ACTOR, payload)
    return result


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
        """Les sessions de worker qui occupent une place : celles qui travaillent (:func:`active_worker_ids`)."""
        return len(active_worker_ids(self._service))

    def _busy_providers(self) -> frozenset[str]:
        """Les fournisseurs qu'un worker au travail tient déjà (``claimed``/``running``), lus au ledger.

        Sert la règle « un seul worker à la fois par fournisseur local » (#1154) : un serveur local n'a pas
        la capacité d'un hébergé, et deux sessions sur le même modèle local se ralentiraient sans le dire.
        """
        busy: set[str] = set()
        for tid, payload in launched_sessions(self._service).items():
            provider = str(payload.get("provider") or "")
            task = self._service.ledger.get_task(tid)
            if provider and task is not None and task.status in (TaskState.CLAIMED, TaskState.RUNNING):
                busy.add(provider)
        return frozenset(busy)

    def _launched_today(self) -> int:
        today = _day((self._now or datetime.now(UTC)).isoformat())
        return sum(
            1 for e in self._service.ledger.list_events()
            if e.event_type in (LAUNCHED_EVENT, LAUNCH_FAILED_EVENT) and _day(e.created_at) == today
        )

    def _tier_for(self, task: MissionTask) -> str:
        """Le palier de cette tâche : ``cheap`` pour une V0 sous ``model_by_class``, sinon celui du rôle."""
        role = self._policy.worker
        return "cheap" if role.model_by_class and classify(task) is Verifiability.V0 else role.tier

    def _model_for(self, admit: Admit, tier: str | None = None) -> str:
        role = self._policy.worker
        if role.model:
            return role.model
        if role.model_by_class and tier == "cheap":
            return _TIER_ALIAS["cheap"]  # un verdict mécanique juge seul : la route du routeur ne remonte pas ce palier
        route = admit.route
        if route is not None and route.outcome == "route" and route.model and route.provider.lower() in {"anthropic", "claude", "claude-code"}:
            return route.model
        return _TIER_ALIAS.get(tier if tier is not None else role.tier, "")

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
        refused = _worktree_hooks(wt.path) or _freeze_repository(service, tid)
        if refused:
            service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=refused)
            return LaunchOutcome(tid, "skipped", refused)
        task = service.require(tid)
        tier = self._tier_for(task)
        provider_id = self._policy.worker.provider
        busy = self._busy_providers()
        route = route_worker(service.home_root, provider_id, tier, task, busy_providers=busy)
        denial = ""
        if route is None:
            # Un fournisseur local déjà occupé fait attendre la tâche : elle ne retombe pas en silence sur
            # l'hôte, qui est un modèle hébergé (#1154). Le repli fermé de #1133 passe avant, pour garder son
            # motif : une classe qu'aucun hébergé n'admet ne part pas sur l'hôte.
            if refusal := hosted_fallback_refusal(service.home_root, task.data_class):
                return self._deferred(tid, refusal)
            denial = route_denial(service.home_root, provider_id, tier, task, busy_providers=busy)
            if denial and provider_id in busy and is_local_provider(service.home_root, provider_id):
                return self._deferred(tid, denial)
        model = route.model if route is not None else self._model_for(admit, tier)
        result = self._exec(self._argv(tid, model, route), wt.path, session_env())
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
        _seal_worker_for_dual_review(service, tid, session_id, model, route.provider if route is not None else "", self._policy)
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
                "provider": route.provider if route is not None else "",
                "provider_refused": denial,
                "lean": self._policy.worker.lean,
                "permission_mode": self._policy.worker.permission_mode,
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

    def _deferred(self, tid: str, reason: str) -> LaunchOutcome:
        """Refuse le repli sur l'hôte : rend le claim, note le motif, et la tâche attend un fournisseur admis.

        Ce n'est pas une panne : rien n'est compté au disjoncteur et aucune question n'est posée. Le motif est
        écrit au ledger (``task.worker_deferred``) pour que ``task show`` le rende, et la sortie du tick le
        reprend en note d'attention.
        """
        service = self._service
        detail = f"repli sur le modèle de l'hôte refusé : {reason}"
        try:
            service.ledger.transition_task(tid, TaskState.READY, actor_id=WORKER_ACTOR, reason=detail)
        except GrimoireError as exc:
            detail += f" (claim non rendu : {exc})"
        service.ledger.append_event(WORKER_DEFERRED_EVENT, tid, "task", WORKER_ACTOR, {"reason": reason})
        return LaunchOutcome(tid, "deferred", detail)

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

    def _argv(self, tid: str, model: str, route: WorkerRoute | None = None) -> list[str]:
        # Pas de ``--session-id`` : ``claude --bg`` l'ignore (#966), l'identité se lit après le lancement.
        argv = [self._claude, "--bg", "--name", f"grimoire-{tid}"]
        if route is not None:
            argv += ["--settings", str(route.settings)]
        if self._policy.worker.lean:
            argv += LEAN_SESSION_ARGS
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
