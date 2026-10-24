"""Créneaux CI rendus : le tick constate les checks de la PR et émet ``ci_pushed`` / ``ci_finished`` (#876, verrou 2).

Avant ce module, rien n'émettait ``task.ci_finished`` : une tâche garée (``needs_verification``, ``waiting``)
gardait son créneau ``ci.runners`` pour toujours, et l'admission différait tout (« 50/3 runners CI occupés »).

Deux gestes, tous deux écrits **par événements du ledger**, jamais en écrivant le snapshot :

- :func:`reconcile_ci` (à chaque tick) : pour chaque occupant d'un créneau, la PR de sa branche ``task/<id>``
  (une seule lecture ``gh pr list`` par tick, pour tous les occupants) dit si des checks tournent encore (``task.ci_pushed``, une fois par sha de tête) ou si tous sont
  terminés sur le sha de tête courant (``task.ci_pushed`` si pas encore vu, puis ``task.ci_finished`` : le créneau est rendu). Sans PR, ou
  avec une sonde muette, rien n'est écrit : ce que l'on ne constate pas ne libère rien.
- :func:`migrate_orphan_slots` (une seule fois par ledger) : un occupant qui n'est pas porté par une session
  vivante et n'a ni PR ni check en cours n'a rien à attendre : ``capacity.released`` (``pool: ci``) le libère.
  Le marqueur ``capacity.ci_migrated`` rend le geste unique ; une sonde muette (``unknown``) sur un occupant ne libère rien et diffère le marqueur au tick suivant.

Le janitor de l'épopée n'a pas de code à part pour l'état terminal d'une réservation : :func:`~grimoire.missions.admission.derive_view`
ne retient un créneau réservé que pour un état ``_CI_HELD`` ; clôture, annulation, échec ou retour à ``ready`` le rendent.
Seul un push déjà vu (``task.ci_pushed``) survit à l'état : :func:`reconcile_ci` rend sans ``gh`` le créneau d'une
tâche clôturée ou annulée (PR fusionnée, branche supprimée).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.missions.admission import (
    CAPACITY_RELEASED_EVENT,
    CI_FINISHED_EVENT,
    CI_PUSHED_EVENT,
    ORCHESTRATION_FILE,
    derive_view,
    load_capacity,
)
from grimoire.missions.schemas import TaskState

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "CI_MIGRATED_EVENT",
    "CheckReading",
    "CiOutcome",
    "ci_repository",
    "default_ci_probe",
    "migrate_orphan_slots",
    "once_per_tick",
    "reconcile_ci",
]

#: Le marqueur de la migration unique des créneaux orphelins (entité ``ci-slots``).
CI_MIGRATED_EVENT = "capacity.ci_migrated"
_MIGRATION_ENTITY = "ci-slots"
_BRANCH_PREFIX = "task/"
_PROBE_TIMEOUT_S = 60
#: Nombre de PR relues par tick (toutes les PR, toutes branches) ; une liste pleine peut cacher des branches (voir ``_readings``).
_PR_LIMIT = 1000
_PR_FIELDS = "headRefName,headRefOid,number,state,statusCheckRollup"
#: Les états après lesquels aucun check n'est plus attendu ; un push vu sur une telle tâche ne tient plus de créneau.
_TERMINAL = frozenset({TaskState.CLOSED, TaskState.CANCELLED})
#: Un ``StatusContext`` (API historique) est en cours tant qu'il est ``PENDING`` ou ``EXPECTED``.
_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
NO_PR, PENDING, DONE, UNKNOWN = "no_pr", "pending", "done", "unknown"
#: Clé de la politique d'orchestration : ``ci: {repository: owner/repo}``.
POLICY_CI_KEY = "ci"
_SLUG = re.compile(r"^(?:[A-Za-z0-9.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REMOTE = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?(?P<host>[^:/]+)[:/](?P<path>[^:]+?)(?:\.git)?/?$")


@dataclass(frozen=True, slots=True)
class CheckReading:
    """Ce que la remote dit des checks d'une branche : ``no_pr`` (aucune PR), ``pending`` (checks en cours ou pas encore posés), ``done`` ou ``unknown``.

    ``head_sha`` est le commit de tête de la PR au moment de la lecture : sans lui, ``done`` ne libère rien.
    """

    status: str
    detail: str = ""
    head_sha: str = ""


#: Une sonde reçoit les branches de TOUS les occupants, le dépôt (``owner/repo``) et le dossier du projet ;
#: elle rend une lecture par branche, en une seule lecture ``gh``.
CiProbe = Callable[[tuple[str, ...], str, Path], Mapping[str, CheckReading]]


@dataclass(frozen=True, slots=True)
class CiOutcome:
    """Un geste de créneau CI du tick : ``pushed``, ``finished`` ou ``released`` (migration), avec sa preuve."""

    task_id: str
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "detail": self.detail}


class _RepositoryUnresolvedError(Exception):
    """Le dépôt des PR n'est pas déterminable : ``status`` dit ce que valent alors les lectures."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def ci_repository(home_root: Path, project_root: Path) -> str:
    """Le dépôt qui porte les PR : la politique d'orchestration (``ci.repository``), sinon le remote ``origin``.

    Jamais ``project_root`` pris comme dépôt par défaut de ``gh`` : les PR peuvent vivre ailleurs que le ledger.
    Une clé invalide, ou un ``git`` en échec, rend les lectures ``unknown`` ; un projet sans remote ``origin``
    n'a pas de PR possible (``no_pr``).
    """
    policy = home_root / ORCHESTRATION_FILE
    if policy.is_file():
        try:
            data = YAML(typ="safe").load(policy.read_text(encoding="utf-8"))
        except (YAMLError, OSError, UnicodeDecodeError) as exc:
            raise _RepositoryUnresolvedError(
                UNKNOWN, f"politique d'orchestration illisible ({type(exc).__name__})"
            ) from exc
        section = data.get(POLICY_CI_KEY) if isinstance(data, dict) else None
        if section is not None:
            repo = section.get("repository") if isinstance(section, dict) else None
            if not isinstance(repo, str) or not _SLUG.match(repo.strip()):
                raise _RepositoryUnresolvedError(UNKNOWN, f"{POLICY_CI_KEY}.repository invalide : attendu owner/repo")
            return repo.strip()
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["git", "-C", str(project_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _RepositoryUnresolvedError(UNKNOWN, "git indisponible") from exc
    if done.returncode != 0 or not done.stdout.strip():
        if "no such remote" in done.stderr.lower():
            raise _RepositoryUnresolvedError(NO_PR, "aucun remote origin : aucune PR possible")
        raise _RepositoryUnresolvedError(UNKNOWN, f"remote origin illisible (git {done.returncode})")
    match = _REMOTE.match(done.stdout.strip())
    path = match.group("path").strip("/") if match else ""
    if not match or path.count("/") != 1:
        raise _RepositoryUnresolvedError(UNKNOWN, "remote origin non reconnu comme owner/repo")
    return path if match.group("host") == "github.com" else f"{match.group('host')}/{path}"


def default_ci_probe(branches: tuple[str, ...], repo: str, cwd: Path) -> dict[str, CheckReading]:
    """UNE lecture ``gh pr list -R <repo> --state all`` pour toutes les branches ; un ``gh`` en échec rend tout ``unknown``."""
    argv = ["gh", "pr", "list", "-R", repo, "--state", "all", "--json", _PR_FIELDS, "--limit", str(_PR_LIMIT)]
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _all(branches, CheckReading(UNKNOWN, "gh indisponible"))
    if done.returncode != 0:
        return _all(branches, CheckReading(UNKNOWN, f"gh pr list a échoué ({done.returncode})"))
    return _readings(done.stdout, branches)


def _all(branches: tuple[str, ...], reading: CheckReading) -> dict[str, CheckReading]:
    return dict.fromkeys(branches, reading)


def _readings(raw: str, branches: tuple[str, ...]) -> dict[str, CheckReading]:
    """Lit le JSON de ``gh pr list`` ; une sortie vide ou illisible est ``unknown`` (non authentifié, hors réseau, limite de débit)."""
    if not raw.strip():
        return _all(branches, CheckReading(UNKNOWN, "gh n'a rien répondu"))
    try:
        prs = json.loads(raw)
    except json.JSONDecodeError:
        return _all(branches, CheckReading(UNKNOWN, "réponse de gh illisible"))
    if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
        return _all(branches, CheckReading(UNKNOWN, "réponse de gh inattendue"))
    latest: dict[str, dict[str, Any]] = {}
    for pr in prs:  # plusieurs PR sur une branche : la plus récente (numéro le plus haut) fait foi
        name = pr.get("headRefName")
        number = pr.get("number")
        if isinstance(name, str) and isinstance(number, int) and number >= latest.get(name, {}).get("number", -1):
            latest[name] = pr
    full = len(prs) >= _PR_LIMIT
    out: dict[str, CheckReading] = {}
    for branch in branches:
        pr = latest.get(branch)
        if pr is None:
            # Liste pleine : la PR de la branche peut être au-delà de la limite, ce n'est pas « aucune PR ».
            out[branch] = (
                CheckReading(UNKNOWN, f"liste de PR tronquée à {_PR_LIMIT}")
                if full
                else CheckReading(NO_PR, "aucune PR pour cette branche")
            )
        else:
            out[branch] = _reading(pr)
    return out


def _reading(pr: dict[str, Any]) -> CheckReading:
    sha = pr.get("headRefOid")
    rollup = pr.get("statusCheckRollup")
    if not isinstance(sha, str) or not sha:
        return CheckReading(UNKNOWN, "sha de tête absent de la réponse de gh")
    if rollup is None:
        rollup = []
    if not isinstance(rollup, list) or not all(isinstance(c, dict) for c in rollup):
        return CheckReading(UNKNOWN, "checks de la PR illisibles", sha)
    if not rollup:
        # La PR existe : ses checks ne sont pas encore posés (gh les rend vides juste après un push), ils
        # vont venir. « Aucune PR » n'est que l'absence de PR ; sinon la migration rendrait le créneau d'une PR vivante.
        return CheckReading(PENDING, "PR sans check encore rapporté", sha)
    running = 0
    for check in rollup:
        if "status" in check:  # CheckRun : terminé seulement à COMPLETED
            running += str(check["status"]).upper() != "COMPLETED"
        elif "state" in check:  # StatusContext
            running += str(check["state"]).upper() in _PENDING_STATES
        else:
            return CheckReading(UNKNOWN, "check de forme inconnue", sha)
    if running:
        return CheckReading(PENDING, f"{running}/{len(rollup)} check(s) en cours", sha)
    return CheckReading(DONE, f"{len(rollup)} check(s) terminé(s)", sha)


def once_per_tick(probe: CiProbe | None = None) -> CiProbe:
    """Une sonde qui ne lit chaque branche qu'une fois : ``reconcile_ci`` et ``migrate_orphan_slots`` du même tick la partagent."""
    cache: dict[str, CheckReading] = {}

    def read(branches: tuple[str, ...], repo: str, cwd: Path) -> Mapping[str, CheckReading]:
        missing = tuple(b for b in branches if b not in cache)
        if missing:
            got = (probe or default_ci_probe)(missing, repo, cwd)
            cache.update({b: got.get(b, CheckReading(UNKNOWN, "sonde muette sur cette branche")) for b in missing})
        return {b: cache[b] for b in branches}

    return read


def _read_all(service: TaskService, tids: Sequence[str], probe: CiProbe | None) -> dict[str, CheckReading]:
    """Une lecture pour tous les occupants : ``{tâche: lecture}`` ; sans dépôt déterminable, aucune lecture n'est « terminée »."""
    if not tids:
        return {}
    branches = tuple(f"{_BRANCH_PREFIX}{t}" for t in tids)
    try:
        repo = ci_repository(service.home_root, service.project_root)
    except _RepositoryUnresolvedError as exc:
        return {t: CheckReading(exc.status, exc.detail) for t in tids}
    got = (probe or default_ci_probe)(branches, repo, service.project_root)
    return {t: got.get(f"{_BRANCH_PREFIX}{t}", CheckReading(UNKNOWN, "sonde muette sur cette branche")) for t in tids}


def _occupants(service: TaskService, now: datetime) -> tuple[list[str], frozenset[str], dict[str, str]]:
    """``(occupants triés, tâches portées par un bail vivant, push vu sans fin : tâche → sha du dernier push)``."""
    view = derive_view(service.ledger, load_capacity(service.home_root), now=now)
    pushed: dict[str, str] = {}
    for event in service.ledger.list_events():
        if event.event_type == CI_PUSHED_EVENT:
            pushed[event.entity_id] = str(event.payload.get("head_sha") or "")
        elif event.event_type == CI_FINISHED_EVENT or (
            event.event_type == CAPACITY_RELEASED_EVENT and event.payload.get("pool") == "ci"
        ):
            pushed.pop(event.entity_id, None)
    return sorted(view.ci_occupants), frozenset(h.task_id for h in view.held), pushed


def reconcile_ci(
    service: TaskService, *, probe: CiProbe | None = None, now: datetime | None = None, actor: str = "tick"
) -> list[CiOutcome]:
    """Constate les checks de la PR de chaque occupant (UNE lecture ``gh``) et écrit ``ci_pushed`` puis ``ci_finished`` ; idempotent.

    Checks en cours : ``ci_pushed`` une fois par sha de tête (un tick de plus n'écrit rien). Un sha de tête
    différent du dernier push vu est un nouveau push : nouveau ``ci_pushed``, le créneau reste occupé, et
    ``ci_finished`` attend une lecture qui porte ce même sha (gh peut encore rendre les checks de l'ancien
    commit). Checks finis sur le sha vu : ``ci_finished``. Sans PR, sans sha ou sonde muette : aucun événement.
    """
    occupants, _live, pushed = _occupants(service, now or datetime.now(UTC))
    live_slots = [t for t in occupants if not (t in pushed and service.require(t).status in _TERMINAL)]
    readings = _read_all(service, live_slots, probe)
    outcomes: list[CiOutcome] = []
    for tid in occupants:
        if tid in pushed and service.require(tid).status in _TERMINAL:
            # PR fusionnée, branche supprimée : gh ne sait plus rien, la tâche est finie. Sans gh, on rend.
            service.ledger.append_event(
                CAPACITY_RELEASED_EVENT, tid, "task", actor, {"pool": "ci", "reason": "tâche terminale"}
            )
            outcomes.append(CiOutcome(tid, "released", "tâche terminale"))
            continue
        reading = readings[tid]
        if reading.status not in (PENDING, DONE) or not reading.head_sha:
            continue
        payload = {"branch": f"{_BRANCH_PREFIX}{tid}", "checks": reading.detail, "head_sha": reading.head_sha}
        known = tid in pushed
        if not known or pushed[tid] != reading.head_sha:
            service.ledger.append_event(CI_PUSHED_EVENT, tid, "task", actor, payload)
            outcomes.append(CiOutcome(tid, "pushed", reading.detail))
            if known:  # nouveau sha : ces checks peuvent être ceux de l'ancien commit, on attend la lecture suivante
                continue
        if reading.status == DONE:
            service.ledger.append_event(CI_FINISHED_EVENT, tid, "task", actor, payload)
            outcomes.append(CiOutcome(tid, "finished", reading.detail))
    return outcomes


def migrate_orphan_slots(
    service: TaskService, *, probe: CiProbe | None = None, now: datetime | None = None, actor: str = "tick"
) -> list[CiOutcome]:
    """Libère une fois les créneaux des occupants sans session vivante, sans PR et sans check en cours.

    Un occupant porté par un bail vivant garde son créneau (il va pousser). Un occupant dont la sonde est muette
    (``unknown``) le garde aussi, et la migration n'est pas marquée faite : le tick suivant la reprend. Une fois
    marquée (``capacity.ci_migrated``), elle ne fait plus rien : ensuite, seuls les checks rendent un créneau.
    """
    if any(e.event_type == CI_MIGRATED_EVENT for e in service.ledger.list_events(_MIGRATION_ENTITY)):
        return []
    occupants, live, _pushed = _occupants(service, now or datetime.now(UTC))
    readings = _read_all(service, [t for t in occupants if t not in live], probe)
    outcomes: list[CiOutcome] = []
    complete = True
    for tid in occupants:
        if tid in live:
            continue
        reading = readings[tid]
        if reading.status == NO_PR:
            service.ledger.append_event(
                CAPACITY_RELEASED_EVENT, tid, "task", actor, {"pool": "ci", "reason": "migration : créneau orphelin"}
            )
            outcomes.append(CiOutcome(tid, "released", reading.detail))
        elif reading.status == UNKNOWN:
            complete = False
    if complete:
        service.ledger.append_event(
            CI_MIGRATED_EVENT, _MIGRATION_ENTITY, "capacity", actor, {"released": [o.task_id for o in outcomes]}
        )
    return outcomes
