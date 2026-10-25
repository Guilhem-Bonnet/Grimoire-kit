"""Vivacité calculée, progression et ligne de vue de soi d'une tâche tenue (K5, issue #770).

La vivacité n'est jamais stockée : elle se **calcule** à la lecture, à quatre
valeurs, depuis ce qu'on a pu observer — jamais depuis une absence.

- ``vivant`` : un signe récent (journal de session, ou sonde d'hôte « busy ») ;
- ``inactif`` (+ raison) : la session existe mais ne produit rien de visible —
  outil long déclaré (``busy_until``) sous un bail valide, ou hôte au repos ;
- ``inconnu`` : aucun signe exploitable. **Jamais « morte »** : l'absence de
  journal ne prouve rien, ``SessionEnd`` n'est émis que sur sortie propre ;
- ``terminé`` : la session a dit elle-même qu'elle finissait (``ended``).

« Vivant » n'est pas « avance » (D-10) : la progression est une dimension à
part, ``progress_at`` (dernier commit de la branche, dernière note de
progression, dernière transition que le ledger a vraiment écrite : passage à
``running``, preuve rendue, verdict ``closed``/``failed``). Un battement de
bail n'est pas une progression. Un signal d'attention monte quand un vivant n'avance pas, ou quand
l'``inconnu`` dure plus de ``2 × ttl`` — un signal, rien n'est tué.

Module pur : les entrées arrivent en paramètres, la sonde d'hôte et la lecture
git sont des exécuteurs injectables (:func:`probe_claude_agents`).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.missions.lease import DEFAULT_LEASE_TTL_SECONDS, LeaseFile, parse_instant

__all__ = [
    "DEFAULT_ACTIVE_MINUTES",
    "DEFAULT_ACTIVE_WINDOW",
    "DEFAULT_PROGRESS_THRESHOLD",
    "INACTIF",
    "INCONNU",
    "PROGRESS_EVENT_TYPES",
    "PROGRESS_TRANSITIONS",
    "TERMINE",
    "VIVANT",
    "Attention",
    "Liveness",
    "attention",
    "claimed_at",
    "git_commit_at",
    "label",
    "liveness",
    "probe_claude_agents",
    "probe_entries",
    "progress_at",
    "recent_sign",
    "self_view_line",
    "task_progress",
]

VIVANT = "vivant"
INACTIF = "inactif"
INCONNU = "inconnu"
TERMINE = "terminé"

#: Forme affichée, accordée avec « session » (les valeurs stockées restent masculines).
LABELS = {VIVANT: "vivante", INACTIF: "inactive", INCONNU: "inconnue", TERMINE: "terminée"}


def label(state: str) -> str:
    """Libellé féminin d'une valeur de vivacité (« session vivante »)."""
    return LABELS.get(state, state)

#: Un signe écrit depuis moins de ce délai vaut « signe de vie ». **Seule** définition
#: de « vivante » du kit (P33) : la Flotte (``project_health.ACTIVE_WINDOW_MINUTES``,
#: projet actif), Travail (``workspace_portfolio.DEFAULT_LIVE_MINUTES``, cartes et vue
#: Sessions), le pouls et ``grimoire task list --live`` la lisent ici. Avant, la Flotte
#: disait 15 min et Travail 30 : la même session était vivante dans un espace, pas dans l'autre.
DEFAULT_ACTIVE_MINUTES = 30
DEFAULT_ACTIVE_WINDOW = timedelta(minutes=DEFAULT_ACTIVE_MINUTES)
#: Au-delà (D19, §2.5), un vivant sans progression monte en attention.
DEFAULT_PROGRESS_THRESHOLD = timedelta(minutes=45)
#: Événement du ledger qui compte comme une progression : la note du titulaire
#: (:meth:`MissionLedger.note_progress`, ``grimoire task note``).
PROGRESS_EVENT_TYPES = frozenset({"task.progress_noted"})
#: Transitions de ``task.transitioned`` qui comptent comme une progression : le
#: travail démarre, la preuve est rendue, le verdict tombe. Pas ``claimed``
#: (c'est le point de départ, ``claimed_at``), ni ``ready`` (récolte), ni le
#: battement ``task.lease_renewed``.
PROGRESS_TRANSITIONS = frozenset({"running", "needs_verification", "closed", "failed"})

#: Sonde d'hôte : ``{session_id: "busy" | "idle"}`` des sessions listées, ou
#: ``None`` quand la sonde est indisponible (binaire absent, échec, délai).
SessionProbe = Callable[[], Mapping[str, str] | None]


@dataclass(frozen=True, slots=True)
class Liveness:
    state: str
    reason: str = ""
    #: Pour ``inconnu`` : depuis quand on n'a plus de signe (dernier indice connu).
    since: str = ""

    def to_dict(self) -> dict[str, str]:
        out = {"state": self.state, "reason": self.reason}
        if self.since:
            out["since"] = self.since
        return out


@dataclass(frozen=True, slots=True)
class Attention:
    level: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "reason": self.reason}


def _ago(moment: datetime, now: datetime) -> str:
    minutes = max(int((now - moment).total_seconds() // 60), 0)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"


def recent_sign(moment: datetime | None, now: datetime, window: timedelta = DEFAULT_ACTIVE_WINDOW) -> bool:
    """Vrai si *moment* tombe dans la fenêtre de vie : ni absent, ni dans le futur, ni trop vieux."""
    return moment is not None and 0 <= (now - moment).total_seconds() <= window.total_seconds()


def liveness(
    *,
    now: datetime,
    lease: LeaseFile | None = None,
    journal_at: datetime | None = None,
    ended: bool = False,
    probe_status: str | None = None,
    active_window: timedelta = DEFAULT_ACTIVE_WINDOW,
    claimed_at: datetime | None = None,
) -> Liveness:
    """La vivacité à l'instant *now* — fonction pure.

    Ordre : ``ended`` (terminé) ; sonde ``ended`` (terminé) ; sonde ``busy``
    ou ``waiting`` (vivant) ; journal récent (vivant) ; sonde ``idle``
    (inactif) ; outil long déclaré sous un bail valide (inactif) ; sinon
    ``inconnu``, daté du dernier indice. Une session sans journal, sans bail et
    sans sonde est ``inconnu``, pas morte. Sonde muette (``None``) : le journal
    de session tranche seul.
    """
    if ended:
        return Liveness(TERMINE, "la session a déclaré sa fin")
    if probe_status == "ended":
        return Liveness(TERMINE, "sonde de l'hôte : session arrêtée")
    if probe_status == "busy":
        return Liveness(VIVANT, "sonde de l'hôte : occupée")
    if probe_status == "waiting":
        return Liveness(VIVANT, "sonde de l'hôte : attend une réponse")
    if journal_at is not None and recent_sign(journal_at, now, active_window):
        return Liveness(VIVANT, f"journal de session écrit il y a {_ago(journal_at, now)}")
    if probe_status == "idle":
        return Liveness(INACTIF, "sonde de l'hôte : au repos")
    if lease is not None and lease.holder and not lease.is_lost(now):
        busy = parse_instant(lease.busy_until)
        if busy is not None and busy > now:
            return Liveness(INACTIF, f"outil long déclaré jusqu'à {busy.astimezone(UTC):%H:%M} UTC, bail valide")
    hints = [t for t in (journal_at, parse_instant(lease.renewed_at) if lease else None, claimed_at) if t is not None]
    since = max(hints).isoformat() if hints else ""
    return Liveness(INCONNU, "aucun signe exploitable (ni journal récent, ni sonde, ni outil long déclaré)", since)


def progress_at(*moments: datetime | None) -> datetime | None:
    """La plus récente des dates de progression (commit, note, verdict) ; ``None`` si aucune."""
    known = [m for m in moments if m is not None]
    return max(known) if known else None


def attention(
    live: Liveness,
    progress: datetime | None,
    *,
    now: datetime,
    since_work: datetime | None = None,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    threshold: timedelta = DEFAULT_PROGRESS_THRESHOLD,
) -> Attention | None:
    """Niveau 2 quand un vivant n'avance pas, ou quand l'``inconnu`` dure plus de ``2 × ttl``.

    *since_work* : où compter quand rien n'a encore progressé (le claim). Un
    signal, pas une panne : ``None`` si tout va bien.
    """
    if live.state == VIVANT:
        reference = progress or since_work
        if reference is not None and now - reference > threshold:
            return Attention(2, f"vivant, sans progression depuis {_ago(reference, now)}")
    elif live.state == INCONNU:
        since = parse_instant(live.since)
        if since is not None and now - since > timedelta(seconds=2 * ttl_seconds):
            return Attention(2, f"vivacité inconnue depuis {_ago(since, now)}")
    return None


#: ``state`` des sessions d'arrière-plan de ``claude agents --json`` → statut de sonde.
#: Relevé sur la sortie réelle (2026-10-25) : ``done`` et ``stopped`` ; ``running`` et
#: ``waiting`` sont les états en cours que l'hôte documente. Un état absent d'ici
#: n'est pas lu : il ne prouve rien.
_BACKGROUND_STATES = {
    "running": "busy",
    "waiting": "waiting",
    "done": "ended",
    "stopped": "ended",
    "failed": "ended",
}


def _probe_status(entry: Mapping[str, Any]) -> str | None:
    if entry.get("waitingFor"):
        return "waiting"
    status = entry.get("status")
    if status in ("busy", "idle"):
        return str(status)
    return _BACKGROUND_STATES.get(str(entry.get("state") or ""))


def probe_entries(
    *, run: Callable[..., Any] = subprocess.run, timeout: float = 3.0
) -> dict[str, dict[str, Any]] | None:
    """``claude agents --json`` : ``{sessionId: {status, cwd, name, kind, started_at}}``, ``None`` si la sonde échoue.

    ``status`` ∈ ``busy`` | ``idle`` (sessions interactives, K0 : ni ``state`` ni
    ``waitingFor``) | ``waiting`` | ``ended`` (sessions d'arrière-plan, champ
    ``state``). Une entrée sans statut reconnu est écartée. Toute erreur rend
    ``None`` : une sonde muette n'est pas une preuve, ni une flotte vide.
    """
    try:
        done = run(
            ["claude", "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        if done.returncode != 0:
            return None
        data = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not isinstance(data, list):
        return None
    out: dict[str, dict[str, Any]] = {}
    for e in data:
        if not isinstance(e, dict) or not e.get("sessionId"):
            continue
        status = _probe_status(e)
        if status is None:
            continue
        started = e.get("startedAt")
        out[str(e["sessionId"])] = {
            "status": status,
            "cwd": str(e.get("cwd") or ""),
            "name": str(e.get("name") or ""),
            "kind": str(e.get("kind") or ""),
            "started_at": (
                datetime.fromtimestamp(started / 1000, UTC).isoformat()
                if isinstance(started, int | float) and not isinstance(started, bool) and started > 0
                else ""
            ),
        }
    return out


def probe_claude_agents(*, run: Callable[..., Any] = subprocess.run, timeout: float = 3.0) -> dict[str, str] | None:
    """``claude agents --json`` : ``{sessionId: status}``, ou ``None`` si la sonde échoue (voir :func:`probe_entries`)."""
    entries = probe_entries(run=run, timeout=timeout)
    return None if entries is None else {sid: e["status"] for sid, e in entries.items()}


_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def git_commit_at(project_root: Path, task_id: str, *, run: Callable[..., Any] = subprocess.run) -> datetime | None:
    """Date du dernier commit de la branche ``task/<id>`` ; ``None`` si elle n'existe pas ou si git échoue."""
    if not _SAFE_TASK_ID.fullmatch(task_id) or task_id.startswith("-"):
        return None
    try:
        done = run(
            ["git", "-C", str(project_root), "log", "-1", "--format=%cI", f"refs/heads/task/{task_id}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_instant(done.stdout.strip()) if done.returncode == 0 else None


def task_progress(
    ledger: Any,
    task_id: str,
    project_root: Path,
    *,
    commit_at: Callable[[Path, str], datetime | None] = git_commit_at,
) -> datetime | None:
    """``progress_at`` d'une tâche : dernier commit, dernière note, dernière transition de progression.

    Les événements refusés au rejeu (epoch périmé) ne comptent pas.
    """
    rejected = ledger.rejected_events()
    stamps = [
        parse_instant(e.created_at)
        for e in ledger.list_events(task_id)
        if e.id not in rejected
        and (
            e.event_type in PROGRESS_EVENT_TYPES
            or (e.event_type == "task.transitioned" and e.payload.get("to_state") in PROGRESS_TRANSITIONS)
        )
    ]
    return progress_at(commit_at(project_root, task_id), *stamps)


def claimed_at(ledger: Any, task_id: str) -> datetime | None:
    """Quand la tâche a été réclamée pour la dernière fois (dernier passage à ``claimed``)."""
    stamps = [
        parse_instant(e.created_at)
        for e in ledger.list_events(task_id)
        if e.event_type == "task.transitioned" and e.payload.get("to_state") == "claimed"
    ]
    return progress_at(*stamps)


def self_view_line(
    *,
    task_id: str,
    epoch: int,
    lease: LeaseFile | None,
    live: Liveness,
    progress: datetime | None,
    now: datetime,
) -> str:
    """La ligne que la session lit à chaque tour : où en est son propre bail."""
    if lease is None:
        bail = f"bail epoch {epoch} (fichier de bail absent, le ledger fait foi)"
    else:
        renewed = parse_instant(lease.renewed_at)
        bail = f"bail epoch {epoch}, " + (
            f"renouvelé il y a {_ago(renewed, now)}" if renewed is not None else "renouvellement illisible"
        )
        if lease.is_lost(now):
            bail += " — PERDU"
    avance = f"dernière progression il y a {_ago(progress, now)}" if progress else "aucune progression observée"
    return f"[Grimoire] Vue de soi — {task_id} : {bail} ; session {label(live.state)} ; {avance}."
