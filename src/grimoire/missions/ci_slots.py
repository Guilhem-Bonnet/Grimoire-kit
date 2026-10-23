"""Créneaux CI rendus : le tick constate les checks de la PR et émet ``ci_pushed`` / ``ci_finished`` (#876, verrou 2).

Avant ce module, rien n'émettait ``task.ci_finished`` : une tâche garée (``needs_verification``, ``waiting``)
gardait son créneau ``ci.runners`` pour toujours, et l'admission différait tout (« 50/3 runners CI occupés »).

Deux gestes, tous deux écrits **par événements du ledger**, jamais en écrivant le snapshot :

- :func:`reconcile_ci` (à chaque tick) : pour chaque occupant d'un créneau, la PR de sa branche ``task/<id>``
  (``gh pr checks``) dit si des checks tournent encore (``task.ci_pushed``, une seule fois) ou si tous sont
  terminés (``task.ci_pushed`` si pas encore vu, puis ``task.ci_finished`` : le créneau est rendu). Sans PR, ou
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
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.admission import (
    CAPACITY_RELEASED_EVENT,
    CI_FINISHED_EVENT,
    CI_PUSHED_EVENT,
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
    "default_ci_probe",
    "migrate_orphan_slots",
    "reconcile_ci",
]

#: Le marqueur de la migration unique des créneaux orphelins (entité ``ci-slots``).
CI_MIGRATED_EVENT = "capacity.ci_migrated"
_MIGRATION_ENTITY = "ci-slots"
_BRANCH_PREFIX = "task/"
_PROBE_TIMEOUT_S = 30
#: ``gh pr checks`` rend 8 quand des checks sont en attente, 1 quand l'un échoue : le JSON reste valable.
#: Le code 1 sans JSON n'est « aucune PR » que si stderr le dit (voir :func:`default_ci_probe`).
_GH_JSON_EXIT = (0, 1, 8)
#: Les états après lesquels aucun check n'est plus attendu ; un push vu sur une telle tâche ne tient plus de créneau.
_TERMINAL = frozenset({TaskState.CLOSED, TaskState.CANCELLED})
_PENDING_BUCKETS = frozenset({"pending"})
NO_PR, PENDING, DONE, UNKNOWN = "no_pr", "pending", "done", "unknown"


@dataclass(frozen=True, slots=True)
class CheckReading:
    """Ce que la remote dit des checks d'une branche : ``no_pr`` (ou aucun check), ``pending``, ``done`` ou ``unknown``."""

    status: str
    detail: str = ""


#: Une sonde reçoit la branche de la tâche et le dossier du dépôt.
CiProbe = Callable[[str, Path], CheckReading]


@dataclass(frozen=True, slots=True)
class CiOutcome:
    """Un geste de créneau CI du tick : ``pushed``, ``finished`` ou ``released`` (migration), avec sa preuve."""

    task_id: str
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "detail": self.detail}


def default_ci_probe(branch: str, repo: Path) -> CheckReading:
    """``gh pr checks <branche> --json name,bucket`` : pas de PR = ``no_pr`` ; un ``gh`` muet ou illisible = ``unknown``."""
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["gh", "pr", "checks", branch, "--json", "name,bucket"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return CheckReading(UNKNOWN, "gh indisponible")
    if done.returncode == 1 and "no pull requests found" in done.stderr.lower():
        return CheckReading(NO_PR, "aucune PR pour cette branche")
    if done.returncode not in _GH_JSON_EXIT:
        return CheckReading(UNKNOWN, f"gh pr checks a échoué ({done.returncode})")
    reading = _reading(done.stdout)
    if reading.status == NO_PR and done.returncode != 0:
        # Un ``[]`` n'est « aucune PR » que pour un gh qui a réussi : en échec, c'est une panne muette.
        return CheckReading(UNKNOWN, f"gh pr checks sort en {done.returncode} sans check")
    return reading


def _reading(raw: str) -> CheckReading:
    """Lit le JSON de ``gh`` ; une sortie vide ou illisible est ``unknown`` (non authentifié, hors réseau, limite de débit)."""
    if not raw.strip():
        return CheckReading(UNKNOWN, "gh n'a rien répondu")
    try:
        checks = json.loads(raw)
    except json.JSONDecodeError:
        return CheckReading(UNKNOWN, "réponse de gh illisible")
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return CheckReading(UNKNOWN, "réponse de gh inattendue")
    if not checks:
        return CheckReading(NO_PR, "aucun check rapporté")
    running = sum(1 for c in checks if str(c.get("bucket", "")) in _PENDING_BUCKETS)
    if running:
        return CheckReading(PENDING, f"{running}/{len(checks)} check(s) en cours")
    return CheckReading(DONE, f"{len(checks)} check(s) terminé(s)")


def _occupants(service: TaskService, now: datetime) -> tuple[list[str], frozenset[str], set[str]]:
    """``(occupants triés, tâches portées par un bail vivant, tâches dont un push est vu sans fin)``."""
    view = derive_view(service.ledger, load_capacity(service.home_root), now=now)
    pushed: set[str] = set()
    for event in service.ledger.list_events():
        if event.event_type == CI_PUSHED_EVENT:
            pushed.add(event.entity_id)
        elif event.event_type == CI_FINISHED_EVENT or (
            event.event_type == CAPACITY_RELEASED_EVENT and event.payload.get("pool") == "ci"
        ):
            pushed.discard(event.entity_id)
    return sorted(view.ci_occupants), frozenset(h.task_id for h in view.held), pushed


def reconcile_ci(
    service: TaskService, *, probe: CiProbe | None = None, now: datetime | None = None, actor: str = "tick"
) -> list[CiOutcome]:
    """Constate les checks de la PR de chaque occupant et écrit ``ci_pushed`` puis ``ci_finished`` ; idempotent.

    Checks en cours : ``ci_pushed`` une fois (un tick de plus n'écrit rien). Checks finis : ``ci_pushed`` si le
    push n'avait pas été vu, puis ``ci_finished``. Sans PR ou sonde muette : aucun événement.
    """
    check = probe or default_ci_probe
    occupants, _live, pushed = _occupants(service, now or datetime.now(UTC))
    outcomes: list[CiOutcome] = []
    for tid in occupants:
        if tid in pushed and service.require(tid).status in _TERMINAL:
            # PR fusionnée, branche supprimée : gh ne sait plus rien, la tâche est finie. Sans gh, on rend.
            service.ledger.append_event(
                CAPACITY_RELEASED_EVENT, tid, "task", actor, {"pool": "ci", "reason": "tâche terminale"}
            )
            outcomes.append(CiOutcome(tid, "released", "tâche terminale"))
            continue
        branch = f"{_BRANCH_PREFIX}{tid}"
        reading = check(branch, service.project_root)
        if reading.status not in (PENDING, DONE):
            continue
        payload = {"branch": branch, "checks": reading.detail}
        if tid not in pushed:
            service.ledger.append_event(CI_PUSHED_EVENT, tid, "task", actor, payload)
            outcomes.append(CiOutcome(tid, "pushed", reading.detail))
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
    check = probe or default_ci_probe
    occupants, live, _pushed = _occupants(service, now or datetime.now(UTC))
    outcomes: list[CiOutcome] = []
    complete = True
    for tid in occupants:
        if tid in live:
            continue
        reading = check(f"{_BRANCH_PREFIX}{tid}", service.project_root)
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
