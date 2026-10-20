"""File triée et admission par réservoirs déclarés (K10, épopée #705).

Avant ce lot, ``task_list_ready`` rendait les tâches dans l'ordre du ledger, rien ne
bornait la concurrence (le WIP de ``orchestration-policy.yaml`` n'était lu que par le
contrôle du standard, jamais au claim), aucune sonde de ressource n'existait et aucun
quota de tokens par jour n'était réglable. Dix PR réalignées donnaient une file CI de
plus de 30 minutes.

Ce module est pur : aucun appel de LLM, aucun démon, aucun compteur à côté du ledger.

- **File** (:func:`queue_order`) : priorité, vieillissement (+1 cran par 24 h passées
  à l'état ``ready``, plafonné à ``critical``), ancienneté.
- **Admission** (:func:`admit`) : ``Admit`` ou ``Defer(raison, retry_at | unknown)``.
  Toutes les gardes échouent fermées : une capacité illisible, une sonde muette ou un
  quota dont la consommation est inconnue (en autonome) refusent l'admission.
- **Réservoirs** : ``ci.runners`` (3 par défaut), ``host`` (charge et mémoire lues dans
  ``/proc``, déclaré seulement), ``llm.<fournisseur>`` (sessions concurrentes). Un pool non
  déclaré n'est pas sondé. L'occupation est dérivée du ledger (:func:`derive_view`) :
  aucun fichier ``held/``.
- **Quota de tokens par jour** : ``illimité`` par défaut, et valeur explicite de la
  configuration ; le kit ne pose alors jamais ``Defer(quota)``. Fini, il est atteint à la
  consommation connue (un minimum) ; la route choisie par le routeur n'est jamais changée.
  Le routeur ne lit pas ce module : le quota relève de l'admission seule.
- **Famine** : une tâche différée depuis plus de 24 h monte au niveau 2 d'attention.

Les tâches ``waiting``, ``needs_verification`` et les tâches closes ne comptent pas dans
le WIP : seules les tâches ``claimed`` et ``running`` travaillent. Un bail perdu occupe
son créneau jusqu'à la récolte (``task reap``) : une horloge incohérente ne libère rien.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.core.standard_state import LEDGER_RELPATH
from grimoire.missions.board import PRIORITIES, priority_of, priority_rank
from grimoire.missions.lease import lost_reason, parse_instant
from grimoire.missions.ledger_rules import unclosed_blockers
from grimoire.missions.liveness import Attention
from grimoire.missions.pause import PAUSE_LIFTED_CAUSE, PauseState
from grimoire.missions.schemas import MissionTask, TaskState

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger

__all__ = [
    "CAPACITY_FILE",
    "CAPACITY_RELEASED_EVENT",
    "CI_FINISHED_EVENT",
    "CI_PUSHED_EVENT",
    "QUOTA_CONSUMED_EVENT",
    "UNLIMITED",
    "Admit",
    "Capacity",
    "Defer",
    "Famine",
    "HostReading",
    "LedgerView",
    "QueuePlan",
    "RouteRef",
    "TokenUse",
    "admit",
    "build_plan",
    "derive_view",
    "load_capacity",
    "queue_order",
    "read_host",
    "ready_since",
    "release_capacity",
    "wip_claim_guard",
]

_log = logging.getLogger(__name__)

#: Capacité déclarée du projet (D7 : ``ci.runners`` vaut 3 sans fichier).
CAPACITY_FILE = STANDARD_DIR / "capacity.yaml"
#: Le WIP vient d'abord de ``capacity.yaml``, sinon de la politique d'orchestration du standard.
ORCHESTRATION_FILE = STANDARD_DIR / "orchestration-policy.yaml"

#: Le quota de tokens par jour par défaut : une valeur explicite, pas un 0 ni une absence.
UNLIMITED = "unlimited"
_UNLIMITED_WORDS = frozenset({UNLIMITED, "illimité", "illimite"})

DEFAULT_CI_RUNNERS = 3
DEFAULT_CI_EXEMPT: tuple[str, ...] = ("analysis", "architecture")
DEFAULT_AGING_HOURS = 24
DEFAULT_FAMINE_HOURS = 24

#: Un push de la branche de la tâche est observé : le créneau CI est pris jusqu'à la fin des checks.
CI_PUSHED_EVENT = "task.ci_pushed"
#: La fin des checks de la tâche est observée : le créneau CI est rendu.
CI_FINISHED_EVENT = "task.ci_finished"
#: Un humain libère un créneau dont l'observation est impossible (``pool`` dans le payload).
CAPACITY_RELEASED_EVENT = "capacity.released"
#: Des tokens consommés (``tokens`` entier dans le payload) : la seule source de la consommation du jour.
QUOTA_CONSUMED_EVENT = "quota.consumed"

_HELD = (TaskState.CLAIMED, TaskState.RUNNING)
#: Les états où une réservation de créneau CI tient encore : une tâche garée (vérification, attente)
#: n'a pas vu ses checks finir. La clôture, l'annulation, l'échec et le retour à ``ready`` la rendent.
_CI_HELD = (*_HELD, TaskState.NEEDS_VERIFICATION, TaskState.WAITING)
_POOL_SECTIONS = frozenset({"ci", "host", "llm"})
_KNOWN_POOLS = ("ci",)
_TOP_KEYS = frozenset({"version", "quota", "pools", "wip", "queue"})


class CapacityError(ValueError):
    """Une section de ``capacity.yaml`` est invalide : la garde qu'elle porte refuse."""


# ── Capacité déclarée ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HostLimits:
    """Les seuils du pool ``host`` ; ``None`` = non borné."""

    max_load_per_cpu: float | None = None
    min_mem_available_mb: int | None = None


@dataclass(frozen=True, slots=True)
class Capacity:
    """Ce que le projet déclare. Une section illisible est dans ``errors`` (clé = section, ``*`` = tout le fichier)."""

    ci_runners: int | None = DEFAULT_CI_RUNNERS
    ci_exempt_types: tuple[str, ...] = DEFAULT_CI_EXEMPT
    host: HostLimits | None = None
    llm_sessions: Mapping[str, int] = field(default_factory=dict)
    #: ``UNLIMITED`` (défaut) ou un entier strictement positif.
    tokens_per_day: int | str = UNLIMITED
    wip_global: int | None = None
    wip_per_role: int | None = None
    aging_hours: int = DEFAULT_AGING_HOURS
    famine_hours: int = DEFAULT_FAMINE_HOURS
    errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def unlimited(self) -> bool:
        return self.tokens_per_day == UNLIMITED

    def error_for(self, section: str) -> str:
        """Le motif qui rend *section* inutilisable : sa propre erreur, sinon celle du fichier entier.

        Les trois pools partagent la table ``pools`` : si elle est illisible (clé inconnue, casse
        fautive), on ne sait plus lequel elle a fait taire, donc ``ci``, ``host`` et ``llm`` refusent.
        """
        own = self.errors.get(section) or self.errors.get("*") or ""
        return own or (self.errors.get("pools", "") if section in _POOL_SECTIONS else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ci_runners": self.ci_runners,
            "host": None if self.host is None else {
                "max_load_per_cpu": self.host.max_load_per_cpu, "min_mem_available_mb": self.host.min_mem_available_mb,
            },
            "llm_sessions": dict(self.llm_sessions),
            "tokens_per_day": self.tokens_per_day,
            "wip": {"limit_global": self.wip_global, "limit_per_role": self.wip_per_role},
            "errors": dict(self.errors),
        }


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CapacityError(f"{where} doit être un entier strictement positif, pas {value!r}")
    return value


def _mapping(value: Any, where: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapacityError(f"{where} doit être une table, pas {value!r}")
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise CapacityError(f"{where} : clé inconnue {', '.join(unknown)} (admises : {', '.join(sorted(allowed))})")
    return value


def _parse_quota(raw: Any) -> int | str:
    value = _mapping(raw, "quota", frozenset({"tokens_per_day"})).get("tokens_per_day", UNLIMITED)
    if isinstance(value, str) and value.strip().lower() in _UNLIMITED_WORDS:
        return UNLIMITED
    return _positive_int(value, "quota.tokens_per_day (ou « unlimited »)")


def _parse_wip(raw: Any, where: str) -> tuple[int | None, int | None]:
    wip = _mapping(raw, where, frozenset({"limit_global", "limit_per_role", "open_delegation"}))
    total, role = wip.get("limit_global"), wip.get("limit_per_role")
    return (
        None if total is None else _positive_int(total, f"{where}.limit_global"),
        None if role is None else _positive_int(role, f"{where}.limit_per_role"),
    )


def _parse_ci(raw: Any) -> dict[str, Any]:
    ci = _mapping(raw, "pools.ci", frozenset({"runners", "exempt_types"}))
    out: dict[str, Any] = {}
    if "runners" in ci:
        out["ci_runners"] = _positive_int(ci["runners"], "pools.ci.runners")
    exempt = ci.get("exempt_types", DEFAULT_CI_EXEMPT)
    if not isinstance(exempt, (list, tuple)) or not all(isinstance(t, str) for t in exempt):
        raise CapacityError("pools.ci.exempt_types doit être une liste de types de tâche")
    out["ci_exempt_types"] = tuple(str(t).strip().lower() for t in exempt)
    return out


def _parse_host(raw: Any) -> dict[str, Any]:
    host = _mapping(raw, "pools.host", frozenset({"max_load_per_cpu", "min_mem_available_mb"}))
    load = host.get("max_load_per_cpu")
    if load is not None and (isinstance(load, bool) or not isinstance(load, (int, float)) or load <= 0):
        raise CapacityError(f"pools.host.max_load_per_cpu doit être un nombre positif, pas {load!r}")
    mem = host.get("min_mem_available_mb")
    mem = None if mem is None else _positive_int(mem, "pools.host.min_mem_available_mb")
    if load is None and mem is None:
        raise CapacityError("pools.host déclare un pool sans aucune limite")
    return {"host": HostLimits(None if load is None else float(load), mem)}


def _parse_llm(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CapacityError("pools.llm doit associer un fournisseur à ses limites")
    sessions = {}
    for pid, limits in raw.items():
        fields = _mapping(limits, f"pools.llm.{pid}", frozenset({"sessions"}))
        sessions[str(pid)] = _positive_int(fields.get("sessions"), f"pools.llm.{pid}.sessions")
    return {"llm_sessions": sessions}


def _parse_queue(raw: Any) -> dict[str, int]:
    queue = _mapping(raw, "queue", frozenset({"aging_hours", "famine_hours"}))
    return {k: _positive_int(v, f"queue.{k}") for k, v in queue.items()}


def _load_yaml(path: Path) -> Any:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def load_capacity(root: Path) -> Capacity:
    """La capacité du projet : défauts, ``capacity.yaml``, WIP de la politique d'orchestration.

    Fail-closed, section par section : une section invalide garde sa valeur inerte et
    note son motif dans ``errors`` ; la garde qui la lit refuse (:meth:`Capacity.error_for`).
    Un fichier qui n'est pas du YAML lisible invalide tout (clé ``*``).
    """
    errors: dict[str, str] = {}
    fields: dict[str, Any] = {}
    path = root / CAPACITY_FILE
    data: Any = {}
    if path.is_file():
        try:
            data = _load_yaml(path) or {}
        except (YAMLError, OSError, UnicodeDecodeError) as exc:
            errors["*"] = f"{CAPACITY_FILE} illisible : {type(exc).__name__}: {exc}"
            data = {}
        if not isinstance(data, dict):
            errors["*"] = f"{CAPACITY_FILE} doit être une table"
            data = {}
        elif data.get("version") not in (None, 1):
            errors["*"] = f"{CAPACITY_FILE} : version {data.get('version')!r} inconnue (attendu 1)"
        elif (unknown := sorted(str(k) for k in data if k not in _TOP_KEYS)):
            errors["*"] = f"{CAPACITY_FILE} : clé inconnue {', '.join(unknown)} (admises : {', '.join(sorted(_TOP_KEYS))})"
    sections: dict[str, tuple[Any, Callable[[Any], dict[str, Any]]]] = {}
    if isinstance(data, dict):
        pools = data.get("pools", {})
        if not isinstance(pools, dict) or set(pools) - {"ci", "host", "llm"}:
            errors["pools"] = f"pools : table de ci, host, llm attendue, pas {pools!r}"
            pools = {}
        sections.update({name: (pools[name], parse) for name, parse in (("ci", _parse_ci), ("host", _parse_host), ("llm", _parse_llm)) if name in pools})
        for name, parse in (("quota", lambda r: {"tokens_per_day": _parse_quota(r)}), ("queue", _parse_queue)):
            if name in data:
                sections[name] = (data[name], parse)
        if "wip" in data:
            sections["wip"] = (data["wip"], lambda r: dict(zip(("wip_global", "wip_per_role"), _parse_wip(r, "wip"), strict=True)))
    for name, (raw, parse) in sections.items():
        if "*" in errors:
            break
        try:
            fields.update(parse(raw))
        except CapacityError as exc:
            errors[name] = str(exc)
    if "wip" not in sections and "*" not in errors:
        try:
            fields.update(_orchestration_wip(root))
        except (CapacityError, YAMLError, OSError, UnicodeDecodeError) as exc:
            errors["wip"] = f"{ORCHESTRATION_FILE} : {exc}"
    return Capacity(errors=errors, **fields)


def _orchestration_wip(root: Path) -> dict[str, int | None]:
    path = root / ORCHESTRATION_FILE
    if not path.is_file():
        return {}
    data = _load_yaml(path)
    if not isinstance(data, dict) or "wip" not in data or not isinstance(data["wip"], dict):
        return {}  # ni borne déclarée ni table : le contrôle du standard (AG-ORC-004) le dit déjà
    total, role = _parse_wip(data["wip"], "wip")
    return {"wip_global": total, "wip_per_role": role}


# ── Sonde de l'hôte ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HostReading:
    """Charge par CPU (moyenne sur 1 min) et mémoire disponible."""

    load_per_cpu: float
    mem_available_mb: int


def read_host(
    *,
    meminfo: Path | None = None,
    getloadavg: Callable[[], tuple[float, float, float]] | None = None,
    cpu_count: Callable[[], int | None] = os.cpu_count,
) -> HostReading | None:
    """Lit ``os.getloadavg`` et ``MemAvailable`` de ``/proc/meminfo`` ; ``None`` si l'un manque.

    Hors Linux (pas de ``/proc/meminfo``, pas de ``getloadavg``) la sonde est muette :
    ``None``, jamais une valeur inventée. Un pool ``host`` déclaré vaut alors ``unknown``.
    """
    try:
        load = (getloadavg or os.getloadavg)()[0]
        cpus = cpu_count() or 0
        text = (meminfo or Path("/proc/meminfo")).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("MemAvailable:"):
                kib = int(line.split()[1])
                break
        else:
            return None
    except (OSError, AttributeError, ValueError, IndexError) as exc:
        _log.debug("host.probe_unreadable %s: %s", type(exc).__name__, exc)
        return None
    return HostReading(load / cpus, kib // 1024) if cpus > 0 else None


# ── Vue du ledger ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Held:
    task_id: str
    role: str = ""
    provider: str = ""


@dataclass(frozen=True, slots=True)
class TokenUse:
    """La consommation du jour : ``lower_bound`` (un minimum, le kit ne voit pas la session) ou ``unknown``."""

    tokens: int | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "status": self.status}


@dataclass(frozen=True, slots=True)
class LedgerView:
    """Ce que l'admission sait du moment : occupation dérivée du ledger, sonde de l'hôte, consommation du jour."""

    now: datetime
    held: tuple[Held, ...] = ()
    ci_occupants: frozenset[str] = frozenset()
    tokens: TokenUse = TokenUse(0, "lower_bound")
    host: HostReading | None = None

    def reserve(self, task: MissionTask, route: RouteRef | None, *, consumes_ci: bool) -> LedgerView:
        """Le même instant, une fois *task* admise : elle tient un créneau (planification à blanc)."""
        held = (*self.held, Held(task.id, task.owner, route.provider if route and route.outcome == "route" else ""))
        return replace(self, held=held, ci_occupants=self.ci_occupants | ({task.id} if consumes_ci else set()))


def _today(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


def _consumed_today(events: list[Any], now: datetime) -> TokenUse:
    today = _today(now)
    summed, dispatched = 0, 0
    measured = False
    for event in events:
        stamp = parse_instant(event.created_at)
        if stamp is None or _today(stamp) != today:
            continue
        if event.event_type == QUOTA_CONSUMED_EVENT:
            tokens = event.payload.get("tokens")
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                summed, measured = summed + tokens, True
        elif event.event_type == "task.dispatched":
            dispatched += 1
    if measured:
        return TokenUse(summed, "lower_bound")
    # Des appels ont eu lieu aujourd'hui sans qu'aucun chiffre de tokens n'existe : on ne sait pas.
    return TokenUse(None, "unknown") if dispatched else TokenUse(0, "lower_bound")


def derive_view(ledger: MissionLedger, capacity: Capacity, *, now: datetime, host: HostReading | None = None) -> LedgerView:
    """L'occupation, relue du ledger à chaque appel (aucun compteur, aucun fichier ``held/``).

    Un créneau CI est tenu par une tâche (hors types exemptés) réservé depuis son dernier claim,
    ou dont un push est observé ; il est rendu par ``task.ci_finished`` ou ``capacity.released``
    (``pool: ci``). Une tâche garée (``needs_verification``, ``waiting``) garde sa réservation :
    ses checks n'ont pas fini. Clôture, annulation, échec ou retour à ``ready`` la rendent.
    """
    tasks = {t.id: t for t in ledger.list_tasks()}
    events = ledger.list_events()
    reserved: set[str] = set()
    pushed: set[str] = set()
    provider: dict[str, str] = {}
    for event in events:
        tid = event.entity_id
        if event.event_type == "task.transitioned" and event.payload.get("to_state") == TaskState.CLAIMED.value:
            reserved.add(tid)
        elif event.event_type == CI_PUSHED_EVENT:
            pushed.add(tid)
        elif event.event_type == CI_FINISHED_EVENT or (
            event.event_type == CAPACITY_RELEASED_EVENT and event.payload.get("pool") == "ci"
        ):
            reserved.discard(tid)
            pushed.discard(tid)
        elif event.event_type == "task.routed":
            decision = event.payload.get("decision")
            if isinstance(decision, dict) and decision.get("outcome") == "route":
                provider[tid] = str(decision.get("provider") or "")
            else:
                provider.pop(tid, None)
    lost = {v.task_id for v in ledger.expired_claims(now)}  # KAN-FRICTIONS : un bail perdu ne tient plus de place
    held = tuple(Held(t.id, t.owner, provider.get(t.id, "")) for t in tasks.values() if t.status in _HELD and t.id not in lost)
    exempt = set(capacity.ci_exempt_types)
    ci = {tid for tid in reserved if tid in tasks and tasks[tid].status in _CI_HELD and tasks[tid].type.value not in exempt} | {tid for tid in pushed if tid in tasks}
    return LedgerView(now, held, frozenset(ci), _consumed_today(events, now), host)


# ── File ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QueueEntry:
    task: MissionTask
    base: str
    effective: str
    since: datetime
    boosts: int
    waited_hours: float


def ready_since(ledger: MissionLedger) -> dict[str, datetime]:
    """Depuis quand chaque tâche est ``ready`` : sa dernière entrée dans cet état, sinon sa création.

    Le réveil d'une tâche ``waiting(paused)`` à la levée d'une pause (K11) n'est pas une nouvelle entrée dans
    la file : la tâche garde l'ancienneté d'avant son claim, sinon une pause d'une nuit la ferait repartir
    derrière des tâches qu'elle précédait.
    """
    from grimoire.missions.ledger import WOKEN_KEY

    since: dict[str, datetime] = {}
    for event in ledger.list_events():
        if event.event_type == "task.transitioned" and event.payload.get("to_state") == TaskState.READY.value:
            stamp = parse_instant(event.created_at)
            if stamp is None:
                continue
            woken = event.payload.get(WOKEN_KEY)
            if event.entity_id in since and isinstance(woken, dict) and woken.get("cause") == PAUSE_LIFTED_CAUSE:
                continue
            since[event.entity_id] = stamp
    return since


def queue_order(
    tasks: list[MissionTask], since: Mapping[str, datetime], *, now: datetime, aging_hours: int = DEFAULT_AGING_HOURS
) -> list[QueueEntry]:
    """Priorité effective, puis ancienneté : la file de §4.1.

    Le vieillissement ajoute un cran de priorité par tranche de *aging_hours* passée à
    l'état ``ready``, sans dépasser ``critical``. À priorité égale, la plus ancienne passe
    devant ; l'identifiant départage pour un ordre total.
    """
    top = len(PRIORITIES) - 1
    entries: list[QueueEntry] = []
    for task in tasks:
        base = priority_of(task)
        moment = since.get(task.id) or parse_instant(task.created_at) or now
        waited = max((now - moment).total_seconds(), 0.0) / 3600
        boosts = int(waited // aging_hours)
        effective = PRIORITIES[min(max(priority_rank(base), 0) + boosts, top)]
        entries.append(QueueEntry(task, base, effective, moment, boosts, waited))
    entries.sort(key=lambda e: (-priority_rank(e.effective), e.since, e.task.id))
    return entries


# ── Admission ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RouteRef:
    """La route que le routeur a choisie (K9) : l'admission la lit, ne la change jamais."""

    outcome: str = "route"
    provider: str = ""
    model: str = ""
    defer_until: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"outcome": self.outcome, "provider": self.provider, "model": self.model}


@dataclass(frozen=True, slots=True)
class Admit:
    task_id: str
    route: RouteRef | None = None

    @property
    def label(self) -> str:
        return "Admit"

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "decision": "admit", "route": self.route.to_dict() if self.route else None}


@dataclass(frozen=True, slots=True)
class Defer:
    """Pas maintenant : la raison nomme le réservoir ; ``retry_at`` est une date ISO ou ``unknown``."""

    task_id: str
    reason: str
    detail: str
    retry_at: str = "unknown"
    route: RouteRef | None = None

    @property
    def label(self) -> str:
        return f"Defer({self.reason})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "decision": "defer", "reason": self.reason, "detail": self.detail,
            "retry_at": self.retry_at, "route": self.route.to_dict() if self.route else None,
        }


def _next_midnight(now: datetime) -> str:
    moment = now.astimezone(UTC)
    return (moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


def _consumes_ci(task: MissionTask, capacity: Capacity) -> bool:
    return capacity.ci_runners is not None and task.type.value not in capacity.ci_exempt_types


def admit(
    task: MissionTask,
    route: RouteRef | None,
    capacity: Capacity,
    view: LedgerView,
    *,
    paused: bool = False,
    autonomous: bool = False,
    pause_detail: str = "",
) -> Admit | Defer:
    """Fonction pure : ``Admit`` ou ``Defer(raison)``. Chaque garde échoue fermée.

    Ordre : pause, route différée par le routeur, WIP, ``ci``, ``host``, ``llm``, quota.
    La route du routeur est rendue telle quelle dans la décision, jamais remplacée.
    *pause_detail* dit quel drapeau (``drain``/``hold``, projet/machine, illisible) retient la tâche.
    """
    tid = task.id

    def defer(reason: str, detail: str, retry: str = "unknown") -> Defer:
        return Defer(tid, reason, detail, retry, route)

    if paused:
        return defer("pause", pause_detail or "pause demandée : aucune admission")
    if route is not None and route.outcome == "defer":
        return defer("provider", "aucun modèle disponible au palier retenu", route.defer_until or "unknown")
    if route is not None and route.outcome == "refuse":
        return defer("unknown", "politique de routage illisible : aucune route autonome")
    if (wip := _wip_refusal(task, capacity, view.held)) is not None:
        return defer(*wip)
    if _consumes_ci(task, capacity):
        if (err := capacity.error_for("ci")):
            return defer("unknown", f"pool ci illisible : {err}")
        busy = len(view.ci_occupants - {tid})
        if capacity.ci_runners is not None and busy >= capacity.ci_runners:
            return defer("ci", f"{busy}/{capacity.ci_runners} runners CI occupés")
    if (err := capacity.error_for("host")):
        return defer("unknown", f"pool host illisible : {err}")
    if capacity.host is not None:
        if view.host is None:
            return defer("unknown", "pool host déclaré mais sonde illisible (/proc/meminfo ou getloadavg)")
        assert capacity.host is not None
        limits = capacity.host
        if limits.max_load_per_cpu is not None and view.host.load_per_cpu > limits.max_load_per_cpu:
            return defer("host", f"charge {view.host.load_per_cpu:.2f}/CPU au-dessus de {limits.max_load_per_cpu:g}")
        if limits.min_mem_available_mb is not None and view.host.mem_available_mb < limits.min_mem_available_mb:
            return defer("host", f"mémoire disponible {view.host.mem_available_mb} Mo sous {limits.min_mem_available_mb} Mo")
    if (err := capacity.error_for("llm")):
        return defer("unknown", f"pool llm illisible : {err}")
    provider = route.provider if route is not None and route.outcome == "route" else ""
    if provider and (limit := capacity.llm_sessions.get(provider)) is not None:
        running = sum(1 for h in view.held if h.provider == provider)
        if running >= limit:
            return defer("llm", f"{running}/{limit} sessions {provider} en cours")
    quota = _quota_refusal(capacity, view, autonomous, defer)
    return quota if quota is not None else Admit(tid, route)


def _wip_refusal(task: MissionTask, capacity: Capacity, held: tuple[Held, ...]) -> tuple[str, str] | None:
    """``(raison, détail)`` du refus de WIP, ``None`` s'il y a de la place ; limite illisible : raison ``unknown``."""
    if (err := capacity.error_for("wip")):
        return "unknown", f"limite de WIP illisible : {err}"
    if capacity.wip_global is not None and len(held) >= capacity.wip_global:
        return "wip", f"WIP global atteint ({len(held)}/{capacity.wip_global})"
    if capacity.wip_per_role is not None and task.owner:
        mine = sum(1 for h in held if h.role == task.owner)
        if mine >= capacity.wip_per_role:
            return "wip", f"WIP du rôle {task.owner} atteint ({mine}/{capacity.wip_per_role})"
    return None


def _quota_refusal(
    capacity: Capacity, view: LedgerView, autonomous: bool, defer: Callable[..., Defer]
) -> Defer | None:
    """Illimité : jamais de garde. Fini : consommation inconnue refusée en autonome, atteinte différée à minuit UTC."""
    if (err := capacity.error_for("quota")):
        return defer("unknown", f"quota illisible : {err}")
    if capacity.unlimited:
        return None
    limit = int(capacity.tokens_per_day)
    used = view.tokens
    if used.tokens is None:
        return defer("unknown", "consommation du jour inconnue sous un quota fini") if autonomous else None
    if used.tokens >= limit:
        return defer("quota", f"quota du jour atteint (>= {used.tokens}/{limit} tokens)", _next_midnight(view.now))
    return None


# ── Plan ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Famine:
    task_id: str
    waited_hours: float
    reason: str
    attention: Attention


@dataclass(frozen=True, slots=True)
class QueuePlan:
    """Ce que le tick admettrait maintenant, à blanc : rien n'est écrit."""

    now: datetime
    admitted: tuple[Admit, ...]
    deferred: tuple[Defer, ...]
    excluded: tuple[tuple[str, str], ...]
    famine: tuple[Famine, ...]
    view: LedgerView
    capacity: Capacity
    order: tuple[tuple[str, str, str], ...] = ()
    pause: PauseState = field(default_factory=PauseState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(),
            "admitted": [a.to_dict() for a in self.admitted],
            "deferred": [d.to_dict() for d in self.deferred],
            "excluded": [{"task_id": t, "reason": r} for t, r in self.excluded],
            "famine": [
                {"task_id": f.task_id, "waited_hours": round(f.waited_hours, 1), "reason": f.reason, "attention": f.attention.to_dict()}
                for f in self.famine
            ],
            "order": [{"task_id": t, "base": b, "effective": e} for t, b, e in self.order],
            "occupancy": {
                "held": len(self.view.held),
                "ci": {"busy": len(self.view.ci_occupants), "runners": self.capacity.ci_runners},
                "host": None if self.view.host is None else {
                    "load_per_cpu": round(self.view.host.load_per_cpu, 3), "mem_available_mb": self.view.host.mem_available_mb,
                },
                "tokens_today": self.view.tokens.to_dict(),
                "tokens_per_day": self.capacity.tokens_per_day,
            },
            "capacity_errors": dict(self.capacity.errors),
            "pause": self.pause.to_dict(),
        }


def build_plan(
    ledger: MissionLedger,
    capacity: Capacity,
    *,
    now: datetime,
    host: HostReading | None = None,
    route_of: Callable[[MissionTask], RouteRef | None] | None = None,
    paused: bool = False,
    autonomous: bool = False,
    mission_id: str | None = None,
    pause: PauseState | None = None,
) -> QueuePlan:
    """La file triée, passée une fois à l'admission ; chaque tâche admise réserve son créneau pour la suivante.

    *pause* (K11) est l'union des drapeaux du projet et de la machine : une tâche qu'un drapeau couvre est
    différée (``Defer(pause)``), les autres suivent leur chemin. *paused* force la pause pour toutes.
    """
    pause = pause or PauseState()
    view = derive_view(ledger, capacity, now=now, host=host)
    tasks = {t.id: t for t in ledger.list_tasks()}
    since = ready_since(ledger)
    admitted: list[Admit] = []
    deferred: list[Defer] = []
    excluded: list[tuple[str, str]] = []
    candidates: list[MissionTask] = []
    for task in (t for t in ledger.list_tasks(mission_id) if t.status is TaskState.READY):
        if (waiting := unclosed_blockers(tasks, task)):
            excluded.append((task.id, f"dépend de {'; '.join(waiting)}"))
        elif (backoff := ledger.backoff_until(task.id)) is not None and backoff > now:
            excluded.append((task.id, f"backoff jusqu'à {backoff.isoformat()}"))
        else:
            candidates.append(task)
    ordered = queue_order(candidates, since, now=now, aging_hours=capacity.aging_hours)
    by_id = {e.task.id: e for e in ordered}
    for entry in ordered:
        route = route_of(entry.task) if route_of is not None else None
        held_back = paused or pause.blocks_admission(entry.task.id)
        decision = admit(
            entry.task, route, capacity, view, paused=held_back, autonomous=autonomous,
            pause_detail=pause.reason_for(entry.task.id),
        )
        if isinstance(decision, Admit):
            admitted.append(decision)
            view = view.reserve(entry.task, route, consumes_ci=_consumes_ci(entry.task, capacity))
        else:
            deferred.append(decision)
    famine = tuple(_famine(d, by_id[d.task_id], capacity.famine_hours) for d in deferred if by_id[d.task_id].waited_hours >= capacity.famine_hours)
    order = tuple((e.task.id, e.base, e.effective) for e in ordered)
    return QueuePlan(now, tuple(admitted), tuple(deferred), tuple(excluded), famine, view, capacity, order, pause)


def _famine(defer: Defer, entry: QueueEntry, famine_hours: int) -> Famine:
    hours = entry.waited_hours
    reason = f"différée depuis plus de {int(hours)} h (seuil {famine_hours} h) : {defer.label} — {defer.detail}"
    return Famine(defer.task_id, hours, reason, Attention(2, reason))


# ── Gestes ─────────────────────────────────────────────────────────────────────


def release_capacity(ledger: MissionLedger, task_id: str, pool: str, *, actor: str, reason: str) -> None:
    """Libère le créneau de *pool* tenu par *task_id* : un humain, quand la fin des checks n'est pas observable."""
    if pool not in _KNOWN_POOLS:
        raise ValueError(f"pool inconnu : {pool!r} — parmi {', '.join(_KNOWN_POOLS)}")
    ledger.append_event(CAPACITY_RELEASED_EVENT, task_id, "task", actor, {"task_id": task_id, "pool": pool, "reason": reason})


def wip_claim_guard(root: Path, *, ledger_root: Path | None = None) -> Callable[[Mapping[str, MissionTask], MissionTask], str]:
    """La garde que le ledger appelle sous son verrou, au claim : WIP global et par rôle.

    Elle relit la capacité à chaque claim (deux fichiers au plus). Une limite de WIP
    illisible refuse le claim (fail-closed) ; sans limite déclarée, rien n'est refusé.
    Seuls les claims **vivants** occupent une place (KAN-FRICTIONS) : un claim dont le bail est
    perdu (:func:`~grimoire.missions.lease.lost_reason`, fichiers de bail sous *ledger_root*) ne
    bloque pas l'admission — la récolte ou une reprise le rendra. Une horloge qui recule ne rend
    aucun bail perdu : elle ne libère jamais de place à tort.
    """
    leases = ledger_root if ledger_root is not None else root / LEDGER_RELPATH

    def guard(tasks: Mapping[str, MissionTask], task: MissionTask) -> str:
        capacity = load_capacity(root)
        now = datetime.now(UTC)
        held = tuple(Held(t.id, t.owner) for t in tasks.values() if t.status in _HELD and not lost_reason(leases, t, now))
        refusal = _wip_refusal(task, capacity, held)
        return "" if refusal is None else refusal[1]

    return guard
