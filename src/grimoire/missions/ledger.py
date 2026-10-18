"""Mission Ledger — append-only JSONL source of truth for missions and tasks.

Design principles (from CAHIER-DES-CHARGES §5.3):
  - Append-only event log: state is derived by replaying events, never mutated in place
  - Atomic writes: temp file + rename to avoid partial JSONL corruption
  - State machine: transitions validated before appending
  - Queries: ready / blocked / claimed / stale / needs_verification exposed as properties

Implements the upstream pattern catalogue's QUA-03 ("Mission ledger
append-only", `web/data/catalogue-export.json`) — see #246 lot 4, which
converged the pattern codes cited across `tools/`, the extensions and this
module onto that one catalogue instead of two disconnected namings.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import atomic_write, locked
from grimoire.missions.lease import (
    CLOCK_TOLERANCE_SECONDS,
    DEFAULT_LEASE_TTL_SECONDS,
    MAX_ATTEMPTS,
    LeaseFile,
    backoff_delay,
    lease_path,
    parse_instant,
    read_lease,
    write_lease,
)
from grimoire.missions.schemas import (
    DEFAULT_CLAIM_TTL_SECONDS,
    ORIGIN_AGENT,
    ORIGIN_HUMAN,
    DependencyKind,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    LedgerEvent,
    Mission,
    MissionState,
    MissionTask,
    RiskProfile,
    TaskCheck,
    TaskClaim,
    TaskDependency,
    TaskDirective,
    TaskState,
    TaskType,
)
from grimoire.missions.session_link import canonical_host

_log = logging.getLogger(__name__)

# Valid state transitions for missions
_MISSION_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.DRAFT: frozenset({MissionState.OPEN, MissionState.CANCELLED}),
    MissionState.OPEN: frozenset({MissionState.BLOCKED, MissionState.VERIFYING, MissionState.CLOSED, MissionState.CANCELLED}),
    MissionState.BLOCKED: frozenset({MissionState.OPEN, MissionState.CANCELLED}),
    MissionState.VERIFYING: frozenset({MissionState.OPEN, MissionState.CLOSED, MissionState.CANCELLED}),
    MissionState.CLOSED: frozenset(),
    MissionState.CANCELLED: frozenset(),
}

# Valid state transitions for tasks
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PROPOSED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.CLAIMED, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.CLAIMED: frozenset(
        {TaskState.RUNNING, TaskState.READY, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    # RUNNING → READY : bail perdu, réservé à la récolte (:meth:`MissionLedger.reap`).
    TaskState.RUNNING: frozenset(
        {TaskState.NEEDS_VERIFICATION, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED, TaskState.READY}
    ),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.NEEDS_VERIFICATION: frozenset({TaskState.CLOSED, TaskState.RUNNING, TaskState.FAILED}),
    TaskState.FAILED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.CLOSED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


#: Événements du pilotage humain (issue #638, lot B) rejoués par
#: :meth:`MissionLedger._replay_steering`.
_STEERING_EVENTS = frozenset({
    "task.prioritized",
    "task.directive_added",
    "task.directive_delivered",
    "task.directive_acknowledged",
})

#: L'événement qui rattache une session d'hôte au claim d'une tâche (issue #638).
SESSION_ATTACHED_EVENT = "task.session_attached"

#: Un risque accepté en l'état sur une tâche (W2-10) : ``accept_as_is`` d'un
#: checkpoint de flow (``FlowEngine``). Rejoué dans ``MissionTask.accepted_risks``.
RISK_ACCEPTED_EVENT = "task.risk_accepted"

#: W5-01 : un lien de dépendance entre deux tâches. ``task.dependency.added`` est
#: l'ancien nom (import Beads) : les deux se rejouent, un seul s'écrit.
TASK_LINKED_EVENT = "task.linked"
_LEGACY_LINK_EVENT = "task.dependency.added"

#: K2 (issue #767) : le plafond dur ``expires_at`` du claim repoussé par ``task renew``.
LEASE_RENEWED_EVENT = "task.lease_renewed"
#: K2 : une récolte qui ne relance pas (quota, tentatives épuisées, hôte sans frein).
REAP_DEFERRED_EVENT = "task.reap_deferred"
#: K2 : la clé, dans un ``task.transitioned`` de récolte, qui marque un bail perdu.
LEASE_LOST_KEY = "lease_lost"
#: K6 (à venir) : une réponse humaine remet le compteur de tentatives à zéro.
HUMAN_ANSWERED_EVENT = "task.human_answered"
#: Verdicts de ``task.dispatched`` comptés comme tentatives (``rate_limit`` est un quota, pas un échec).
_FAILED_DISPATCH = frozenset({"red", "timeout", "error"})
_HELD = (TaskState.CLAIMED, TaskState.RUNNING)
#: Origine d'une barrière du fichier de bail (K2-S2-02) : voir :class:`LeaseFile`.
ORIGIN_REAP = "reap"
ORIGIN_HAND_BACK = "hand_back"


@dataclass(frozen=True, slots=True)
class LeaseView:
    """L'état du bail d'une tâche tenue : ``valid``, ``lost`` ou ``unknown`` (horloge incohérente)."""

    task_id: str
    status: str
    reason: str
    epoch: int
    actor_id: str
    host: str
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class HolderCheck:
    """Le verdict du contrôle d'epoch d'un titulaire — ``source`` : ``lease`` (cache) ou ``ledger``."""

    ok: bool
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class ReapDecision:
    """Ce que la récolte fait d'un bail perdu : ``ready`` (relancée) ou ``defer`` (cause nommée)."""

    task_id: str
    action: str
    cause: str
    epoch: int
    attempts: int
    retry_at: str = ""
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "cause": self.cause,
            "epoch": self.epoch,
            "attempts": self.attempts,
            "retry_at": self.retry_at,
            "written": self.written,
        }


@dataclass(frozen=True, slots=True)
class ReapReport:
    """Le résultat d'une récolte : ``clock`` vaut ``unknown`` quand rien n'a pu être jugé."""

    clock: str
    decisions: tuple[ReapDecision, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"clock": self.clock, "decisions": [d.to_dict() for d in self.decisions]}


def _slug(title: str, limit: int) -> str:
    """Fragment d'identifiant sûr dans un chemin.

    Le slug naïf recopiait le titre : « Ajouter /health » donnait
    ``GAO-ajouter-/hea-001``, un identifiant que ``normalize_task_id`` refuse et
    qui, employé tel quel, ouvrait un chemin de plus d'un segment. Les
    identifiants du ledger servent de nom de dossier aux artefacts du standard :
    ils ne peuvent contenir que ce que ce contrat autorise.
    """
    kept = [c if c.isascii() and (c.isalnum() or c in "_.-") else "-" for c in title.lower()]
    slug = re.sub(r"-{2,}", "-", "".join(kept))[:limit].strip("-._")
    return slug or "task"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


#: Les seuls journaux que le ledger écrit sous sa racine.
_LEDGER_FILES = frozenset({"events.jsonl", "incidents.jsonl"})


def _confined_ledger_file(path: Path) -> Path:
    """Résout *path* et refuse tout ce qui n'est pas un journal du ledger sous sa racine.

    La racine vient du CLI (``--ledger-root``) : on n'écrit que ``events.jsonl``
    et ``incidents.jsonl``, et le chemin résolu (liens symboliques compris) doit
    rester directement sous le dossier résolu de la racine.
    """
    if path.name not in _LEDGER_FILES:
        raise GrimoireMissionError(f"Fichier de ledger non autorisé : {path.name!r}")
    base = path.parent.resolve()
    resolved = (base / path.name).resolve()
    if resolved.parent != base or not resolved.is_relative_to(base):
        raise GrimoireMissionError(f"Fichier de ledger hors de sa racine : {path.name!r}")
    return resolved


@contextmanager
def _ledger_file_lock(target_path: Path) -> Iterator[None]:
    """Verrou exclusif inter-processus sur *target_path* — :func:`grimoire.core.fsio.locked`.

    ``_atomic_append`` lit le fichier entier, ajoute une ligne, puis renomme
    un temporaire par-dessus : sans ce verrou, deux écrivains concurrents
    (deux requêtes HTTP du cockpit servies par des threads séparés,
    typiquement) peuvent tous deux lire le même contenu avant que l'un des
    deux n'écrive — le second écrase alors l'événement du premier, silencieux
    (« lost update »). :meth:`MissionLedger.transition_task` relit et valide
    sous ce même verrou : un compare-and-set (issue #710).

    Un verrou impossible à prendre (délai dépassé, système de fichiers sans
    ``flock``) lève :class:`GrimoireMissionError` au lieu d'écrire sans
    exclusion : deux titulaires valent pire qu'une écriture à refaire. Un
    ledger partagé par un système de fichiers réseau n'est pas supporté.
    """
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(locked(target_path))
        except (TimeoutError, OSError) as exc:
            raise GrimoireMissionError(
                f"Écriture refusée : aucun verrou inter-processus n'a pu être pris sur {target_path.name} ({exc}) "
                "— sans lui, deux écritures simultanées pourraient réussir toutes les deux. "
                "Réessaie ; si cela persiste, le système de fichiers ne supporte pas le verrouillage."
            ) from exc
        yield


class MissionLedger:
    """Append-only JSONL ledger for missions, tasks, events, and incidents.

    All state is projected from the event log on read; writes append atomically.

    Usage::

        ledger = MissionLedger(Path("_grimoire-runtime-output/ledger"))
        mission = ledger.create_mission("Pack Registry", origin="user")
        task = ledger.create_task(mission.id, "Implement ledger", type=TaskType.IMPLEMENTATION)
        ledger.transition_task(task.id, TaskState.READY, actor_id="agent-dev")
    """

    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._root = root
        #: L'horloge des baux (K2) — injectable pour les tests, UTC par défaut.
        self._clock = clock or (lambda: datetime.now(UTC))
        self._root.mkdir(parents=True, exist_ok=True)
        self._events_path = root / "events.jsonl"
        self._incidents_path = root / "incidents.jsonl"
        # In-memory projection caches (rebuilt lazily)
        self._missions: dict[str, Mission] = {}
        self._tasks: dict[str, MissionTask] = {}
        self._events: list[LedgerEvent] = []
        self._incidents: dict[str, Incident] = {}
        #: K2 : epoch le plus haut vu par tâche (claims et barrières de récolte).
        self._epoch_high: dict[str, int] = {}
        #: K2 : événements refusés au rejeu (id → motif) — gardés au journal, jamais appliqués.
        self._rejected: dict[str, str] = {}
        #: K2 : clés ``idem = (task_id, lease_epoch, step)`` des effets déjà appliqués.
        self._idem_seen: set[str] = set()
        self._loaded = False

    # ── Private helpers ────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._replay_events()
        self._replay_incidents()
        self._loaded = True

    def _replay_events(self) -> None:
        if not self._events_path.exists():
            return
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            evt_type = raw.get("event_type", "")
            payload = raw.get("payload", {})
            if evt_type == "mission.created":
                m = Mission.from_dict(payload)
                self._missions[m.id] = m
            elif evt_type == "mission.transitioned":
                mid = payload["mission_id"]
                if mid in self._missions:
                    old = self._missions[mid]
                    self._missions[mid] = Mission.from_dict({**old.to_dict(), "status": payload["to_state"]})
            elif evt_type == "task.created":
                t = MissionTask.from_dict(payload)
                self._tasks[t.id] = t
            elif evt_type == "task.transitioned":
                tid = payload["task_id"]
                if tid in self._tasks:
                    refusal = self._effect_refusal(tid, payload)
                    if refusal:
                        self._rejected[str(raw.get("id", ""))] = refusal
                    else:
                        self._apply_transition(tid, payload)
            elif evt_type == LEASE_RENEWED_EVENT:
                tid = str(raw.get("entity_id") or payload.get("task_id") or "")
                refusal = self._effect_refusal(tid, payload)
                current = self._tasks.get(tid)
                if refusal or current is None or current.claim is None:
                    self._rejected[str(raw.get("id", ""))] = refusal or "aucun claim à renouveler"
                else:
                    claim = replace(current.claim, expires_at=str(payload.get("expires_at", "") or ""))
                    self._tasks[tid] = MissionTask.from_dict({**current.to_dict(), "claim": claim.to_dict()})
            elif evt_type in _STEERING_EVENTS:
                self._replay_steering(evt_type, payload)
            elif evt_type == SESSION_ATTACHED_EVENT:
                # Issue #638 lot A : le claim apprend sa session après coup. Un
                # événement de plus dans le journal, jamais une ligne réécrite ;
                # sans claim au rejeu (tâche jamais réclamée, ou événement
                # orphelin), il n'y a rien à compléter et rien n'est inventé.
                # La tâche ciblée est ``entity_id`` — la clé que tout événement
                # du journal porte — et non une copie dans le payload qui
                # pourrait manquer ou diverger (revue Copilot, PR #639).
                tid = str(raw.get("entity_id") or payload.get("task_id") or "")
                current = self._tasks.get(tid)
                if current is not None and current.claim is not None:
                    claim = current.claim.with_session(
                        str(payload.get("session_id", "") or ""),
                        canonical_host(str(payload.get("session_host", "") or "")),
                        # Issue #695 : seul un ``task attach`` marque ``explicit``.
                        explicit_at=str(raw.get("created_at", "") or "") if payload.get("explicit") else "",
                    )
                    self._tasks[tid] = MissionTask.from_dict({**current.to_dict(), "claim": claim.to_dict()})
            elif evt_type in (TASK_LINKED_EVENT, _LEGACY_LINK_EVENT):
                # W5-01 : ni ``task.linked`` ni ``task.dependency.added`` n'étaient
                # rejoués — ``task link`` écrivait un événement que rien ne lisait,
                # et ``MissionTask.dependencies`` restait vide.
                self._replay_link(raw, payload)
            elif evt_type == RISK_ACCEPTED_EVENT:
                # W2-10 : la tâche ciblée est ``entity_id``, comme pour la session.
                # Un événement orphelin n'invente rien ; l'horodatage de
                # l'événement est gardé (``ts``) pour l'affichage.
                tid = str(raw.get("entity_id") or payload.get("task_id") or "")
                current = self._tasks.get(tid)
                if current is not None:
                    risk = {**payload, "ts": str(raw.get("created_at", "") or "")}
                    self._tasks[tid] = MissionTask.from_dict(
                        {**current.to_dict(), "accepted_risks": [*(dict(r) for r in current.accepted_risks), risk]}
                    )
            self._events.append(LedgerEvent.from_dict(raw))

    def _replay_link(self, raw: dict[str, Any], payload: dict[str, Any]) -> None:
        """Ajoute la dépendance d'un événement de lien à la tâche source, sans doublon."""
        tid = str(raw.get("entity_id") or payload.get("task_id") or "")
        current = self._tasks.get(tid)
        if current is None:
            return
        try:
            if "dependency" in payload:
                dep = TaskDependency.from_dict(payload["dependency"])
            else:
                dep = TaskDependency(DependencyKind(str(payload.get("kind", "blocks"))), str(payload["depends_on"]))
        except (KeyError, ValueError, TypeError):
            return
        if dep not in current.dependencies:
            self._tasks[tid] = MissionTask.from_dict(
                {**current.to_dict(), "dependencies": [d.to_dict() for d in (*current.dependencies, dep)]}
            )

    def _apply_transition(self, tid: str, payload: dict[str, Any]) -> None:
        old_task = self._tasks[tid]
        update: dict[str, Any] = {"status": payload["to_state"]}
        if "claim" in payload:
            update["claim"] = payload["claim"]
        self._tasks[tid] = MissionTask.from_dict({**old_task.to_dict(), **update})
        claim_epoch = payload.get("claim", {}).get("lease_epoch") if isinstance(payload.get("claim"), dict) else None
        for value in (claim_epoch, payload.get("fence_epoch")):
            if isinstance(value, int) and not isinstance(value, bool):
                self._epoch_high[tid] = max(self._epoch_high.get(tid, 0), value)
        if payload.get("idem"):
            self._idem_seen.add(str(payload["idem"]))

    def _effect_refusal(self, tid: str, payload: dict[str, Any]) -> str:
        """Motif de refus, au rejeu, d'un effet porteur d'un epoch (K2) ; ``""`` s'il s'applique.

        Un effet signé ``lease_epoch`` n'est appliqué que si cet epoch est celui
        du claim courant *et* le plus haut connu pour la tâche (aucune barrière
        de récolte ni claim plus récent) ; une clé ``idem`` déjà appliquée est un
        doublon. Un événement sans ``lease_epoch`` (historique, humain, claim)
        n'est pas concerné.
        """
        idem = str(payload.get("idem", "") or "")
        if idem and idem in self._idem_seen:
            return f"doublon de l'effet {idem}"
        if "lease_epoch" not in payload:
            return ""
        task = self._tasks.get(tid)
        return self._epoch_refusal(task, payload.get("lease_epoch"))

    def _epoch_refusal(self, task: MissionTask | None, epoch: Any) -> str:
        if task is None or task.claim is None:
            return "aucun claim ne porte cet epoch"
        current = task.claim.lease_epoch
        high = self._epoch_high.get(task.id, current)
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != current or epoch != high:
            return f"epoch {epoch!r} périmé : le bail de {task.id} est à l'epoch {max(current, high)}"
        if task.status not in _HELD:
            # Le claim survit au rendu (release, annulation, blocage) : sans ce garde, son epoch
            # resterait « le plus haut » et un effet périmé s'appliquerait à l'écriture comme au rejeu.
            return f"epoch {epoch!r} périmé : {task.id} est {task.status.value}, le bail est rendu"
        return ""

    def _replay_steering(self, evt_type: str, payload: dict[str, Any]) -> None:
        """Pilotage humain (issue #638) : priorité et journal de consignes.

        Chaque événement ne touche qu'un champ dérivé de la tâche ; le texte
        d'une consigne n'est jamais réécrit — livraison et accusé de réception
        s'ajoutent comme horodatages sur l'entrée existante.
        """
        tid = payload.get("task_id", "")
        task = self._tasks.get(tid)
        if task is None:
            return
        data = task.to_dict()
        if evt_type == "task.prioritized":
            data["priority"] = payload.get("to_priority", "")
        elif evt_type == "task.directive_added":
            data["directives"] = [*data.get("directives", []), dict(payload.get("directive", {}))]
        elif evt_type == "task.directive_delivered":
            ids, at = set(payload.get("directive_ids", [])), str(payload.get("delivered_at", ""))
            data["directives"] = [
                {**d, "delivered_at": d.get("delivered_at") or at} if d.get("id") in ids else d
                for d in data.get("directives", [])
            ]
        elif evt_type == "task.directive_acknowledged":
            did, at = payload.get("directive_id"), str(payload.get("acknowledged_at", ""))
            data["directives"] = [
                {**d, "acknowledged_at": d.get("acknowledged_at") or at} if d.get("id") == did else d
                for d in data.get("directives", [])
            ]
        self._tasks[tid] = MissionTask.from_dict(data)

    def _replay_incidents(self) -> None:
        if not self._incidents_path.exists():
            return
        for line in self._incidents_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            inc = Incident.from_dict(raw)
            self._incidents[inc.id] = inc

    def _invalidate(self) -> None:
        self._missions = {}
        self._tasks = {}
        self._events = []
        self._incidents = {}
        self._epoch_high = {}
        self._rejected = {}
        self._idem_seen = set()
        self._loaded = False

    def append_event(self, event_type: str, entity_id: str, entity_kind: str, actor_id: str, payload: dict[str, Any]) -> LedgerEvent:
        """Record a custom event on any ledger entity (public API)."""
        return self._append_event(event_type, entity_id, entity_kind, actor_id, payload)

    def _append_event(
        self,
        event_type: str,
        entity_id: str,
        entity_kind: str,
        actor_id: str,
        payload: dict[str, Any],
        *,
        lock_held: bool = False,
    ) -> LedgerEvent:
        evt = LedgerEvent(
            id=_new_id("evt"),
            event_type=event_type,
            entity_id=entity_id,
            entity_kind=entity_kind,
            actor_id=actor_id,
            created_at=_now_iso(),
            payload=payload,
        )
        if lock_held:
            self._write_appended(self._events_path, evt.to_dict())
        else:
            self._atomic_append(self._events_path, evt.to_dict())
        self._invalidate()
        return evt

    def _atomic_append(self, path: Path, record: dict[str, Any]) -> None:
        # Verrouillé : lire « existing » puis renommer un temporaire par-dessus
        # n'est atomique que pour un seul écrivain à la fois. Deux requêtes
        # concurrentes (cockpit multi-thread) sans ce verrou perdaient
        # silencieusement l'une des deux écritures (« lost update »).
        with _ledger_file_lock(path):
            self._write_appended(path, record)

    @staticmethod
    def _write_appended(path: Path, record: dict[str, Any]) -> None:
        """Ajoute *record* à *path* par temporaire + rename — l'appelant tient le verrou.

        ``flock`` n'est pas réentrant d'un descripteur à l'autre : qui tient déjà
        :func:`_ledger_file_lock` (``transition_task``) écrit par ici, jamais
        par :meth:`_atomic_append`, sous peine de s'attendre lui-même.
        """
        path = _confined_ledger_file(path)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        # K2 (§2.3) : ajout puis ``fsync`` — ``atomic_write`` synchronise le
        # fichier et le dossier avant de rendre la main.
        atomic_write(path, existing + line)

    def _next_seq(self, prefix: str, id_map: dict[str, Any]) -> str:
        existing = [k for k in id_map if k.startswith(prefix)]
        return f"{prefix}-{len(existing) + 1:03d}"

    # ── Public write API ───────────────────────────────────────────────────

    def create_mission(
        self,
        title: str,
        *,
        origin: str,
        description: str = "",
        risk_profile: RiskProfile = RiskProfile.STANDARD,
        created_by: str = "",
        scope_repos: tuple[str, ...] = (),
        scope_surfaces: tuple[str, ...] = (),
        scope_packs: tuple[str, ...] = (),
        mission_id: str | None = None,
    ) -> Mission:
        self._load()
        slug = _slug(title, 20)
        if mission_id is None:
            seq = len(self._missions) + 1
            mission_id = f"MIS-{slug}-{seq:03d}"
        mission = Mission(
            id=mission_id,
            title=title,
            status=MissionState.DRAFT,
            origin=origin,
            created_at=_now_iso(),
            description=description,
            risk_profile=risk_profile,
            created_by=created_by,
            scope_repos=scope_repos,
            scope_surfaces=scope_surfaces,
            scope_packs=scope_packs,
        )
        self._append_event("mission.created", mission.id, "mission", created_by or "system", mission.to_dict())
        return mission

    def transition_mission(self, mission_id: str, to_state: MissionState, *, actor_id: str = "system", reason: str = "") -> Mission:
        self._load()
        mission = self._missions.get(mission_id)
        if mission is None:
            raise GrimoireMissionError(f"Mission not found: {mission_id}")
        allowed = _MISSION_TRANSITIONS.get(mission.status, frozenset())
        if to_state not in allowed:
            raise GrimoireMissionError(
                f"Invalid mission transition {mission.status.value} → {to_state.value} for {mission_id}"
            )
        self._append_event(
            "mission.transitioned",
            mission_id,
            "mission",
            actor_id,
            {"mission_id": mission_id, "from_state": mission.status.value, "to_state": to_state.value, "reason": reason},
        )
        self._load()
        return self._missions[mission_id]

    def create_task(
        self,
        mission_id: str,
        title: str,
        *,
        type: TaskType = TaskType.IMPLEMENTATION,
        risk_profile: RiskProfile = RiskProfile.STANDARD,
        acceptance: tuple[str, ...] = (),
        description: str = "",
        surface: str = "",
        owner: str = "",
        expected_evidence: tuple[str, ...] = (),
        guardrails: tuple[str, ...] = (),
        dependencies: tuple[TaskDependency, ...] = (),
        task_id: str | None = None,
        priority: str = "",
        agent_roles: tuple[str, ...] = (),
        remediation_ref: str = "",
        extra: dict[str, Any] | None = None,
        checks: tuple[TaskCheck, ...] = (),
    ) -> MissionTask:
        self._load()
        if mission_id not in self._missions:
            raise GrimoireMissionError(f"Mission not found: {mission_id}")
        if not acceptance:
            raise GrimoireMissionError("At least one acceptance criterion is required")
        slug = _slug(title, 12)
        if task_id is None:
            area_tasks = [k for k in self._tasks if k.startswith(f"GAO-{slug}")]
            seq = len(area_tasks) + 1
            task_id = f"GAO-{slug}-{seq:03d}"
        task = MissionTask(
            id=task_id,
            mission_id=mission_id,
            title=title,
            status=TaskState.PROPOSED,
            type=type,
            risk_profile=risk_profile,
            acceptance=acceptance,
            created_at=_now_iso(),
            description=description,
            surface=surface,
            owner=owner,
            expected_evidence=expected_evidence,
            guardrails=guardrails,
            dependencies=dependencies,
            priority=priority,
            agent_roles=agent_roles,
            remediation_ref=remediation_ref,
            extra=extra or {},
            checks=checks,
        )
        self._append_event("task.created", task.id, "task", owner or "system", task.to_dict())
        return task

    def transition_task(
        self,
        task_id: str,
        to_state: TaskState,
        *,
        actor_id: str = "system",
        reason: str = "",
        claim: TaskClaim | None = None,
        extra_payload: dict[str, Any] | None = None,
        expected_from: TaskState | None = None,
        lease_epoch: int | None = None,
        step: str = "",
    ) -> MissionTask:
        """Valide puis écrit la transition, en un seul geste sous le verrou du journal.

        Issue #710 : l'état était lu (cache de l'instance, parfois chargé bien
        avant) hors verrou, puis l'événement ajouté sous verrou — deux claims
        simultanés réussissaient tous les deux. Désormais le journal est relu
        *sous* :func:`_ledger_file_lock` et la transition validée sur cet état
        frais : un compare-and-set. *expected_from* (l'état sur lequel
        l'appelant a jugé, ex. :meth:`TaskService.transition` et son gate)
        resserre encore : si l'état a bougé depuis, la transition est refusée.

        Sans verrou de l'OS réellement tenu, rien n'est écrit (fail-closed,
        :func:`_ledger_file_lock`) : deux titulaires valent pire qu'un claim à refaire.

        K2 (issue #767) : un claim reçoit son ``lease_epoch`` ici — le plus haut
        connu pour la tâche, plus un — et le fichier de bail est écrit *avant*
        l'événement (§2.3). *lease_epoch* signe l'effet d'un titulaire : un
        epoch qui n'est plus celui du bail (récolte, nouveau claim, barrière en
        avance sur le journal) est refusé. *step* nomme l'effet : la clé
        ``idem = (task_id, lease_epoch, step)`` rend sa répétition sans effet.
        ``RUNNING → READY`` est réservé à la récolte (:meth:`reap`).
        """
        with _ledger_file_lock(self._events_path):
            self._invalidate()
            self._load()
            return self._transition_locked(
                task_id, to_state, actor_id, reason, claim, extra_payload, expected_from, lease_epoch, step
            )

    def _transition_locked(
        self,
        task_id: str,
        to_state: TaskState,
        actor_id: str,
        reason: str,
        claim: TaskClaim | None,
        extra_payload: dict[str, Any] | None,
        expected_from: TaskState | None,
        lease_epoch: int | None = None,
        step: str = "",
        *,
        reaper: bool = False,
    ) -> MissionTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        idem = f"{task_id}#{lease_epoch}#{step}" if step and lease_epoch is not None else ""
        if idem and idem in self._idem_seen:
            return task  # effet déjà appliqué : la répétition ne réécrit rien, même après que le bail est rendu
        if lease_epoch is not None:
            refusal = self._holder_refusal(task, lease_epoch)
            if refusal:
                raise GrimoireMissionError(f"Effet refusé pour {task_id} : {refusal}")
        if task.status is TaskState.RUNNING and to_state is TaskState.READY and not reaper:
            raise GrimoireMissionError(
                f"Transition refusée pour {task_id} : running → ready est réservé à la récolte d'un bail perdu "
                "(grimoire task reap) ; le titulaire rend la tâche par blocked, failed ou cancelled"
            )
        held = task.claim
        if to_state is TaskState.CLAIMED and task.status in (TaskState.CLAIMED, TaskState.RUNNING) and held is not None:
            session = f", session {held.session_id}" if held.session_id else ""
            raise GrimoireMissionError(
                f"Claim refusé pour {task_id} : déjà {task.status.value} par {held.actor_id}{session} "
                f"jusqu'à {held.expires_at or 'sans expiration'}"
            )
        if to_state is TaskState.CLAIMED:
            waiting = self._unclosed_blockers(task)
            if waiting:
                raise GrimoireMissionError(
                    f"Claim refusé pour {task_id} : dépend de {'; '.join(waiting)} — "
                    "une tâche ne se réclame que lorsque ses dépendances `blocks` sont closes"
                )
        if expected_from is not None and task.status is not expected_from:
            raise GrimoireMissionError(
                f"Transition refusée pour {task_id} : l'état a changé entre-temps "
                f"({expected_from.value} attendu, {task.status.value} trouvé) — relire la tâche puis réessayer"
            )
        allowed = _TASK_TRANSITIONS.get(task.status, frozenset())
        if to_state not in allowed:
            if task.is_unknown_state:
                raise GrimoireMissionError(
                    f"Task {task_id} est dans un état inconnu de cette version ({task.raw_status!r}) : "
                    f"figée, aucune transition vers {to_state.value}. Mettre grimoire à jour."
                )
            raise GrimoireMissionError(
                f"Invalid task transition {task.status.value} → {to_state.value} for {task_id}"
            )
        payload: dict[str, Any] = {
            "task_id": task_id,
            "from_state": task.raw_status if task.is_unknown_state else task.status.value,
            "to_state": to_state.value,
            "reason": reason,
            "actor_id": actor_id,
        }
        hands_back = False
        if lease_epoch is not None:
            payload["lease_epoch"] = lease_epoch
        if idem:
            payload["idem"] = idem
        if (
            (task.status in _HELD or task.status is TaskState.NEEDS_VERIFICATION)
            and to_state not in _HELD
            and to_state is not TaskState.NEEDS_VERIFICATION
            and not reaper
        ):
            # Le titulaire rend le bail (release, blocage, échec, annulation) : même barrière que la
            # récolte — le fichier d'abord (§2.3), sans titulaire, à l'epoch suivant ; le journal la
            # porte en ``fence_epoch`` pour que le claim suivant parte au-dessus. Rendre sa preuve
            # (running → needs_verification) garde le titulaire, mais le bail ne survit pas à la
            # sortie de needs_verification (clôture, échec) : la barrière tombe à ce moment-là.
            payload["fence_epoch"] = self._epoch_high.get(task_id, 0) + 1
            hands_back = True
        if to_state is TaskState.CLAIMED and claim is not None:
            epoch = self._epoch_high.get(task_id, 0) + 1
            claim = replace(claim, lease_epoch=epoch)
            # §2.3 : (1) le fichier de bail, (2) l'événement — interrompu entre
            # les deux, le fichier est en avance et rien n'en profite.
            write_lease(lease_path(self._root, task_id), LeaseFile(task_id, epoch, self._now().isoformat()))
        if claim is not None:
            payload["claim"] = claim.to_dict()
        if extra_payload:
            # Les clés que le rejeu lit ne se laissent pas écraser : un
            # `extra_payload` ne complète l'événement, il ne le redéfinit pas.
            reserved = set(payload)
            clashes = reserved & set(extra_payload)
            if clashes:
                raise GrimoireMissionError(
                    f"extra_payload ne peut pas redéfinir {', '.join(sorted(clashes))} sur task.transitioned"
                )
            payload.update(extra_payload)
        if hands_back:
            fence = int(payload["fence_epoch"])
            write_lease(
                lease_path(self._root, task_id),
                LeaseFile(task_id, fence, self._now().isoformat(), holder=False, origin=ORIGIN_HAND_BACK),
            )
        self._append_event("task.transitioned", task_id, "task", actor_id, payload, lock_held=True)
        self._load()
        return self._tasks[task_id]

    def _unclosed_blockers(self, task: MissionTask) -> list[str]:
        """Les dépendances ``blocks`` de *task* qui ne sont pas closes, chacune avec son état."""
        out: list[str] = []
        for dep in task.dependencies:
            if dep.kind is not DependencyKind.BLOCKS:
                continue
            target = self._tasks.get(dep.target)
            if target is None:
                out.append(f"{dep.target} (inconnue du ledger)")
            elif target.status is not TaskState.CLOSED:
                out.append(f"{dep.target} ({target.status.value})")
        return out

    def link_tasks(
        self, task_id: str, depends_on: str, kind: DependencyKind = DependencyKind.BLOCKS, *, actor_id: str = "system"
    ) -> MissionTask:
        """Déclare « *task_id* dépend de *depends_on* » — refusé si cela ferme un cycle ``blocks``.

        Le contrôle se fait sous le verrou du journal, sur l'état relu : deux
        liens concurrents A→B et B→A ne passent pas tous les deux. Un lien déjà
        présent est un succès sans nouvel événement.
        """
        with _ledger_file_lock(self._events_path):
            self._invalidate()
            self._load()
            task = self._tasks.get(task_id)
            if task is None:
                raise GrimoireMissionError(f"Task not found: {task_id}")
            if depends_on not in self._tasks:
                raise GrimoireMissionError(f"Task not found: {depends_on}")
            if depends_on == task_id:
                raise GrimoireMissionError(f"Lien refusé : {task_id} ne peut pas dépendre d'elle-même")
            dep = TaskDependency(kind, depends_on)
            if dep in task.dependencies:
                return task
            if kind is DependencyKind.BLOCKS:
                path = self._blocks_path(depends_on, task_id)
                if path:
                    raise GrimoireMissionError(
                        f"Lien refusé : {task_id} → {depends_on} fermerait le cycle "
                        f"{' → '.join([task_id, *path])}"
                    )
            self._append_event(
                TASK_LINKED_EVENT, task_id, "task", actor_id,
                {"task_id": task_id, "depends_on": depends_on, "kind": kind.value}, lock_held=True,
            )
            self._load()
            return self._tasks[task_id]

    def _blocks_path(self, start: str, goal: str) -> list[str]:
        """Un chemin de dépendances ``blocks`` de *start* vers *goal* (``[start, …, goal]``), ``[]`` s'il n'y en a pas."""
        stack = [(start, [start])]
        seen: set[str] = set()
        while stack:
            node, path = stack.pop()
            if node == goal:
                return path
            if node in seen:
                continue
            seen.add(node)
            current = self._tasks.get(node)
            if current is None:
                continue
            for dep in current.dependencies:
                if dep.kind is DependencyKind.BLOCKS:
                    stack.append((dep.target, [*path, dep.target]))
        return []

    def claim_task(self, task_id: str, actor_id: str, host_id: str, exclusive_files: tuple[str, ...] = ()) -> MissionTask:
        """Convenience: READY → CLAIMED."""
        claim = TaskClaim.new(actor_id=actor_id, host_id=host_id, exclusive_files=exclusive_files)
        return self.transition_task(task_id, TaskState.CLAIMED, actor_id=actor_id, claim=claim)

    # ── Bail, epoch, récolte (K2, issue #767) ──────────────────────────────

    def _now(self) -> datetime:
        return self._clock()

    def _holder_refusal(self, task: MissionTask, lease_epoch: int) -> str:
        """Motif de refus de l'effet d'un titulaire d'epoch *lease_epoch* ; ``""`` s'il le tient encore.

        Le ledger d'abord (epoch du claim, barrière de récolte journalisée),
        puis le fichier de bail : un fichier *en avance* (barrière écrite,
        événement pas encore) refuse aussi l'ancien titulaire — fail-closed.
        """
        refusal = self._epoch_refusal(task, lease_epoch)
        if refusal:
            return refusal
        cached = read_lease(lease_path(self._root, task.id))
        if cached is not None and (cached.epoch != lease_epoch or not cached.holder):
            return f"epoch {lease_epoch} périmé : le fichier de bail porte l'epoch {cached.epoch} (récolte en cours)"
        return ""

    def check_holder(self, task_id: str, lease_epoch: int) -> HolderCheck:
        """Le contrôle d'epoch du frein d'outil : le fichier de bail seul, le ledger s'il manque.

        Ne dépend pas de l'horloge. Un fichier absent ou illisible est un défaut
        de cache (``lease.cache_miss``, journalisé) : la décision est alors prise
        sur le ledger relu une fois.
        """
        cached = read_lease(lease_path(self._root, task_id))
        if cached is not None:
            if cached.holder and cached.epoch == lease_epoch:
                return HolderCheck(True, "", "lease")
            return HolderCheck(
                False, f"epoch {lease_epoch} périmé : le bail de {task_id} est à l'epoch {cached.epoch}", "lease"
            )
        _log.info("lease.cache_miss task=%s epoch=%s", task_id, lease_epoch)
        self._invalidate()
        refusal = self._epoch_refusal(self.get_task(task_id), lease_epoch)
        return HolderCheck(not refusal, refusal, "ledger")

    def clock_state(self, now: datetime | None = None) -> str:
        """``ok``, ou ``unknown`` si *now* précède le dernier événement du journal de plus de la tolérance.

        Une horloge qui recule ne rend jamais un bail « valide » ni « perdu » :
        la récolte ne fait rien et les baux comptent comme occupés.
        """
        self._load()
        moment = now or self._now()
        stamps = [t for t in (parse_instant(e.created_at) for e in self._events) if t is not None]
        if stamps and moment < max(stamps) - timedelta(seconds=CLOCK_TOLERANCE_SECONDS):
            return "unknown"
        return "ok"

    def lease_view(self, task: MissionTask, now: datetime | None = None) -> LeaseView | None:
        """Le bail d'une tâche tenue (``claimed``/``running``) ; ``None`` sinon.

        Perdu si ``now > max(renewed_at + ttl, busy_until)`` (fichier de bail),
        si le plafond dur ``expires_at`` est dépassé, ou si le fichier est en
        avance sur le journal (récolte interrompue). Sans fichier, le ledger
        seul décide. La vivacité de la session (K5) n'est pas encore lue.
        """
        moment = now or self._now()
        return self._lease_view(task, moment, self.clock_state(moment))

    def _lease_view(self, task: MissionTask, moment: datetime, clock: str) -> LeaseView | None:
        claim = task.claim
        if task.status not in _HELD or claim is None:
            return None

        def view(status: str, reason: str) -> LeaseView:
            host = claim.session_host or claim.host_id
            return LeaseView(task.id, status, reason, claim.lease_epoch, claim.actor_id, host, claim.session_id)

        if clock == "unknown":
            return view("unknown", "horloge incohérente : plus tôt que le dernier événement du journal")
        cached = read_lease(lease_path(self._root, task.id))
        if cached is not None and cached.epoch > claim.lease_epoch:
            return view("lost", "fence_pending")
        if claim.is_expired(moment):
            return view("lost", "expires_at")
        if cached is not None and cached.epoch == claim.lease_epoch and cached.is_lost(moment):
            return view("lost", "heartbeat")
        return view("valid", "")

    def expired_claims(self, now: datetime | None = None) -> list[LeaseView]:
        """Les baux perdus — vide quand l'horloge est incohérente (rien n'est jugé perdu)."""
        moment = now or self._now()
        clock = self.clock_state(moment)
        views = [self._lease_view(t, moment, clock) for t in self.list_tasks()]
        return [v for v in views if v is not None and v.status == "lost"]

    def renew_lease(
        self,
        task_id: str,
        lease_epoch: int,
        *,
        actor_id: str = "system",
        busy_until: datetime | None = None,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        now: datetime | None = None,
    ) -> MissionTask:
        """Battement de cœur du titulaire : le fichier de bail, et le plafond dur s'il approche.

        Refusé à un epoch qui n'est plus celui du bail. Le fichier est réécrit à
        chaque appel ; ``expires_at`` n'est repoussé (événement
        ``task.lease_renewed``) que s'il reste moins de la moitié du TTL du
        claim et que l'horloge est cohérente : au plus un événement par demi-TTL.
        """
        with _ledger_file_lock(self._events_path):
            self._invalidate()
            self._load()
            task = self._tasks.get(task_id)
            if task is None:
                raise GrimoireMissionError(f"Task not found: {task_id}")
            if task.status not in _HELD or task.claim is None:
                raise GrimoireMissionError(f"Renouvellement refusé pour {task_id} : tâche {task.status.value}, aucun bail")
            refusal = self._holder_refusal(task, lease_epoch)
            if refusal:
                raise GrimoireMissionError(f"Renouvellement refusé pour {task_id} : {refusal}")
            moment = now or self._now()
            path = lease_path(self._root, task_id)
            previous = read_lease(path)
            busy = busy_until.isoformat() if busy_until else (previous.busy_until if previous else "")
            write_lease(path, LeaseFile(task_id, lease_epoch, moment.isoformat(), ttl_seconds, busy))
            expires = parse_instant(task.claim.expires_at)
            if (
                expires is not None
                and expires - moment < timedelta(seconds=DEFAULT_CLAIM_TTL_SECONDS / 2)
                and self.clock_state(moment) == "ok"
            ):
                pushed = (moment + timedelta(seconds=DEFAULT_CLAIM_TTL_SECONDS)).isoformat()
                payload = {"task_id": task_id, "lease_epoch": lease_epoch, "expires_at": pushed, "actor_id": actor_id}
                self._append_event(LEASE_RENEWED_EVENT, task_id, "task", actor_id, payload, lock_held=True)
                self._load()
            return self._tasks[task_id]

    def _since_last_answer(self, task_id: str) -> list[LedgerEvent]:
        events = [e for e in self.list_events(task_id) if e.id not in self._rejected]
        answers = [i for i, e in enumerate(events) if e.event_type == HUMAN_ANSWERED_EVENT]
        return events[answers[-1] + 1 :] if answers else events

    def attempts(self, task_id: str) -> int:
        """Tentatives, dérivées du journal seul : baux perdus et dispatchs en échec depuis la dernière réponse humaine."""
        return sum(
            1
            for evt in self._since_last_answer(task_id)
            if (evt.event_type == "task.transitioned" and LEASE_LOST_KEY in evt.payload)
            or (evt.event_type == "task.dispatched" and evt.payload.get("verdict") in _FAILED_DISPATCH)
        )

    def backoff_until(self, task_id: str) -> datetime | None:
        """Avant cet instant, une tâche récoltée n'est pas réadmise : ``last_lost_at + 10 min × 2^(attempts−1)``."""
        lost = [
            e for e in self._since_last_answer(task_id)
            if e.event_type == "task.transitioned" and LEASE_LOST_KEY in e.payload
        ]
        last = parse_instant(lost[-1].created_at) if lost else None
        return last + backoff_delay(self.attempts(task_id)) if last is not None else None

    def _last_route_quota(self, task: MissionTask) -> bool:
        dispatched = [e for e in self.list_events(task.id) if e.event_type == "task.dispatched"]
        return bool(dispatched) and dispatched[-1].payload.get("verdict") == "rate_limit"

    def reap(
        self,
        *,
        fenced_hosts: frozenset[str] = frozenset(),
        quota_blocked: Callable[[MissionTask], bool] | None = None,
        actor_id: str = "tick",
        apply: bool = True,
        now: datetime | None = None,
    ) -> ReapReport:
        """Récolte des baux perdus — le seul chemin de ``RUNNING → READY``.

        Sous le verrou du journal, pour chaque bail perdu :

        - fournisseur de la dernière route en quota (*quota_blocked*, défaut :
          dernier ``task.dispatched`` en ``rate_limit``) : différée, cause
          ``quota``, sans compter de tentative ;
        - plus de :data:`MAX_ATTEMPTS` tentatives : différée, ``retry_exhausted`` ;
        - hôte hors de *fenced_hosts* (sans frein vérifié) : différée,
          ``resume_unfenced`` — on ne relance pas de doublon ;
        - sinon : barrière (fichier de bail à l'epoch suivant, sans titulaire)
          puis ``task.transitioned`` vers ``ready`` marqué ``lease_lost``.

        Une différée s'écrit une fois par (epoch, cause) en ``task.reap_deferred`` ;
        la tâche reste tenue jusqu'à l'état ``waiting`` (K6). Horloge incohérente :
        rien n'est jugé. *apply* faux : les décisions sans aucune écriture.
        """
        is_quota = quota_blocked or self._last_route_quota
        with _ledger_file_lock(self._events_path):
            self._invalidate()
            self._load()
            moment = now or self._now()
            if self.clock_state(moment) == "unknown":
                return ReapReport("unknown")
            decisions: list[ReapDecision] = []
            for view in self.expired_claims(moment):
                task = self._tasks[view.task_id]
                decisions.append(self._reap_one(task, view, moment, fenced_hosts, is_quota, actor_id, apply))
            return ReapReport("ok", tuple(decisions))

    def _reap_one(
        self,
        task: MissionTask,
        view: LeaseView,
        moment: datetime,
        fenced_hosts: frozenset[str],
        is_quota: Callable[[MissionTask], bool],
        actor_id: str,
        apply: bool,
    ) -> ReapDecision:
        before = self.attempts(task.id)
        cause = ""
        path = lease_path(self._root, task.id)
        cached = read_lease(path)
        pending_reap = view.reason == "fence_pending" and cached is not None and cached.origin == ORIGIN_REAP
        if not pending_reap:  # une récolte interrompue a déjà décidé ; un rendu interrompu se juge comme un bail perdu
            if is_quota(task):
                cause = "quota"
            elif before + 1 > MAX_ATTEMPTS:
                cause = "retry_exhausted"
            elif view.host not in fenced_hosts:
                cause = "resume_unfenced"
        if cause:
            written = apply and not self._already_deferred(task.id, view.epoch, cause)
            if written:
                payload = {"task_id": task.id, "lease_epoch_lost": view.epoch, "cause": cause,
                           "attempts": before, "lease_reason": view.reason, "actor_id": actor_id}
                self._append_event(REAP_DEFERRED_EVENT, task.id, "task", actor_id, payload, lock_held=True)
                self._load()
            return ReapDecision(task.id, "defer", cause, view.epoch, before, written=written)
        attempt = before + 1
        retry_at = (moment + backoff_delay(attempt)).isoformat()
        if not apply:
            return ReapDecision(task.id, "ready", view.reason, view.epoch, attempt, retry_at)
        fence = cached.epoch if view.reason == "fence_pending" and cached is not None else (
            self._epoch_high.get(task.id, view.epoch) + 1
        )
        write_lease(path, LeaseFile(task.id, fence, moment.isoformat(), holder=False, origin=ORIGIN_REAP))
        lost = {"epoch": view.epoch, "reason": view.reason, "attempt": attempt, "retry_at": retry_at}
        self._transition_locked(
            task.id, TaskState.READY, actor_id, f"bail perdu ({view.reason})", None,
            {LEASE_LOST_KEY: lost, "fence_epoch": fence}, None, reaper=True,
        )
        return ReapDecision(task.id, "ready", view.reason, view.epoch, attempt, retry_at, written=True)

    def _already_deferred(self, task_id: str, epoch: int, cause: str) -> bool:
        return any(
            e.payload.get("lease_epoch_lost") == epoch and e.payload.get("cause") == cause
            for e in self.list_events(task_id)
            if e.event_type == REAP_DEFERRED_EVENT
        )

    def rejected_events(self) -> dict[str, str]:
        """Les événements refusés au rejeu (id → motif) : epoch périmé ou effet en double."""
        self._load()
        return dict(self._rejected)

    # ── Pilotage humain (issue #638, lot B) ────────────────────────────────

    def prioritize_task(self, task_id: str, priority: str, *, actor_id: str = "system", reason: str = "") -> MissionTask:
        """Change la priorité déclarée — l'ancienne reste lisible dans l'événement."""
        self._load()
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        self._append_event(
            "task.prioritized", task_id, "task", actor_id,
            {"task_id": task_id, "from_priority": task.priority, "to_priority": priority, "reason": reason, "actor_id": actor_id},
        )
        self._load()
        return self._tasks[task_id]

    def add_directive(
        self, task_id: str, text: str, *, actor_id: str, kind: str = "comment", origin: str = ORIGIN_AGENT
    ) -> TaskDirective:
        """Ajoute une consigne au journal de la tâche ; rend l'entrée créée.

        Une consigne (``kind="directive"``) n'est posée que par l'origine
        ``human`` (issue #706) : la refuser ici, plutôt qu'au rendu, parce qu'un
        événement écrit est déjà lu.
        """
        self._load()
        if task_id not in self._tasks:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        if kind == "directive" and origin != ORIGIN_HUMAN:
            raise GrimoireMissionError(
                f"une consigne (kind=directive) ne vient que du canal humain ; origine {origin!r} refusée — "
                "poser un commentaire (kind=comment), rendu comme note non fiable"
            )
        directive = TaskDirective(
            id=_new_id("dir"), author=actor_id, created_at=_now_iso(), text=text, kind=kind, origin=origin
        )
        self._append_event(
            "task.directive_added", task_id, "task", actor_id,
            {"task_id": task_id, "directive": directive.to_dict()},
        )
        return directive

    def mark_directives_delivered(self, task_id: str, directive_ids: tuple[str, ...], *, actor_id: str = "system") -> MissionTask:
        """Horodate la livraison de *directive_ids* à une session — rien si la liste est vide."""
        self._load()
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        if not directive_ids:
            return task
        self._append_event(
            "task.directive_delivered", task_id, "task", actor_id,
            {"task_id": task_id, "directive_ids": list(directive_ids), "delivered_at": _now_iso()},
        )
        self._load()
        return self._tasks[task_id]

    def acknowledge_directive(self, task_id: str, directive_id: str, *, actor_id: str) -> MissionTask:
        """Horodate l'accusé de réception d'une consigne connue de la tâche."""
        self._load()
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        if not any(d.id == directive_id for d in task.directives):
            raise GrimoireMissionError(f"Consigne inconnue sur {task_id} : {directive_id}")
        self._append_event(
            "task.directive_acknowledged", task_id, "task", actor_id,
            {"task_id": task_id, "directive_id": directive_id, "acknowledged_at": _now_iso(), "actor_id": actor_id},
        )
        self._load()
        return self._tasks[task_id]

    def attach_session(
        self, task_id: str, session_id: str, *, session_host: str = "", actor_id: str = "hook", explicit: bool = False
    ) -> MissionTask:
        """Rattache *session_id* au claim de *task_id* — un événement, pas une réécriture.

        Refuse (``GrimoireMissionError``) une tâche inconnue, une tâche sans
        claim, ou un claim déjà rattaché à une *autre* session : une session
        ne vole jamais le claim d'une autre. Idempotent quand la session est
        déjà celle du claim : rien n'est ajouté au journal — sauf pour un
        rattachement *explicite* (``task attach``, issue #695), qui s'écrit
        toujours : c'est la déclaration d'intention datée dont la résolution de
        la tâche active se sert pour départager les claims d'une même session.
        """
        self._load()
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        if task.claim is None:
            raise GrimoireMissionError(f"Task {task_id} has no claim to attach a session to")
        session_id = session_id.strip()
        if not session_id:
            raise GrimoireMissionError("A session id is required")
        if task.claim.session_id == session_id and not explicit:
            return task
        if task.claim.session_id and task.claim.session_id != session_id:
            raise GrimoireMissionError(
                f"Task {task_id} is already attached to session {task.claim.session_id}"
            )
        self._append_event(
            SESSION_ATTACHED_EVENT,
            task_id,
            "task",
            actor_id,
            {
                "task_id": task_id,
                "session_id": session_id,
                "session_host": canonical_host(session_host),
                **({"explicit": True} if explicit else {}),
            },
        )
        self._load()
        return self._tasks[task_id]

    def open_incident(
        self,
        mission_id: str,
        task_id: str,
        kind: str,
        summary: str,
        *,
        severity: IncidentSeverity = IncidentSeverity.MEDIUM,
        causes: tuple[str, ...] = (),
        recommended_actions: tuple[str, ...] = (),
        workflow_instance_id: str = "",
        created_by: str = "system",
    ) -> Incident:
        self._load()
        seq = sum(1 for i in self._incidents.values() if i.task_id == task_id) + 1
        inc_id = f"inc-{task_id}-{seq:03d}"
        incident = Incident(
            id=inc_id,
            mission_id=mission_id,
            task_id=task_id,
            kind=kind,
            severity=severity,
            status=IncidentStatus.OPEN,
            summary=summary,
            created_at=_now_iso(),
            workflow_instance_id=workflow_instance_id,
            causes=causes,
            recommended_actions=recommended_actions,
        )
        self._atomic_append(self._incidents_path, incident.to_dict())
        self._invalidate()
        return incident

    def resolve_incident(self, incident_id: str, *, actor_id: str = "system") -> Incident:
        self._load()
        inc = self._incidents.get(incident_id)
        if inc is None:
            raise GrimoireMissionError(f"Incident not found: {incident_id}")
        updated = Incident.from_dict({**inc.to_dict(), "status": IncidentStatus.RESOLVED.value})
        # Re-write the incidents file: replace matching line
        if self._incidents_path.exists():
            lines = self._incidents_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if raw.get("id") == incident_id:
                        new_lines.append(json.dumps(updated.to_dict(), ensure_ascii=False))
                    else:
                        new_lines.append(line)
                except json.JSONDecodeError:
                    new_lines.append(line)
            self._incidents_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self._invalidate()
        return updated

    # ── Queries ─────────────────────────────────────────────────────────────

    def get_mission(self, mission_id: str) -> Mission | None:
        self._load()
        return self._missions.get(mission_id)

    def get_task(self, task_id: str) -> MissionTask | None:
        self._load()
        return self._tasks.get(task_id)

    def list_missions(self) -> list[Mission]:
        self._load()
        return list(self._missions.values())

    def list_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        self._load()
        tasks = list(self._tasks.values())
        if mission_id is not None:
            tasks = [t for t in tasks if t.mission_id == mission_id]
        return tasks

    def list_events(self, entity_id: str | None = None) -> list[LedgerEvent]:
        """Return ledger events, optionally scoped to one entity."""
        self._load()
        events = list(self._events)
        if entity_id is not None:
            events = [e for e in events if e.entity_id == entity_id]
        return events

    def list_incidents(self, task_id: str | None = None) -> list[Incident]:
        """Return all incidents, optionally scoped to one task."""
        self._load()
        incidents = list(self._incidents.values())
        if task_id is not None:
            incidents = [i for i in incidents if i.task_id == task_id]
        return incidents

    def ready_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.READY]

    def blocked_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.BLOCKED]

    def claimed_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.CLAIMED]

    def needs_verification_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.NEEDS_VERIFICATION]

    def open_incidents(self, task_id: str | None = None) -> list[Incident]:
        self._load()
        incs = [i for i in self._incidents.values() if i.status == IncidentStatus.OPEN]
        if task_id is not None:
            incs = [i for i in incs if i.task_id == task_id]
        return incs

    def events_for(self, entity_id: str) -> list[LedgerEvent]:
        return self.list_events(entity_id)

    # ── Import / Export ──────────────────────────────────────────────────────

    def export_jsonl(self, path: Path) -> int:
        self._load()
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._events]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(lines)

    def import_jsonl(self, path: Path) -> int:
        """Append events from an external JSONL file into this ledger."""
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._atomic_append(self._events_path, raw)
            count += 1
        self._invalidate()
        return count
