"""Fichier de bail d'une tâche — fraîcheur hors ledger et cache d'epoch (K2, issue #767).

Le Mission Ledger reste la vérité (détenteur, epoch, plafond dur
``expires_at``). Le fichier ``<ledger>/leases/<tâche>.json`` ne porte que ce
qui change trop souvent pour y être journalisé — ``renewed_at`` (battement de
cœur) et ``busy_until`` (outil long déclaré) — et une copie de l'epoch, que le
frein d'outil lit sans rejouer le journal : un ``stat`` et une lecture.

Écriture : :func:`grimoire.core.fsio.atomic_write` (temporaire, ``fsync``,
renommage, ``fsync`` du dossier). Un changement d'epoch écrit d'abord ce
fichier, puis l'événement du ledger, les deux sous le verrou du journal
(:meth:`MissionLedger.transition_task`) : interrompu entre les deux, le
fichier est *en avance* sur le ledger et l'ancien titulaire est refusé —
l'erreur échoue fermée, la récolte suivante termine le geste.

Un fichier absent, illisible ou d'un autre schéma n'est jamais une preuve de
bail : c'est un défaut de cache, qui renvoie au ledger.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from grimoire.core.fsio import atomic_write

#: Durée de vie d'un bail sans battement de cœur (§2.3 du plan, 20 min).
DEFAULT_LEASE_TTL_SECONDS = 20 * 60
#: Tolérance de la garde d'horloge : en deçà, un retard d'horloge est du bruit.
CLOCK_TOLERANCE_SECONDS = 5.0
#: Au-delà de ce nombre de tentatives (D15), une récolte ne relance plus.
MAX_ATTEMPTS = 3
#: Premier délai de réadmission après une récolte, doublé à chaque tentative.
BACKOFF_BASE = timedelta(minutes=10)

LEASES_DIRNAME = "leases"
_SCHEMA = "grimoire-task-lease/v1"
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def lease_path(ledger_root: Path, task_id: str) -> Path:
    """Le fichier de bail de *task_id* ; un identifiant hors charte devient son empreinte."""
    name = task_id if _SAFE_NAME.fullmatch(task_id) and task_id not in (".", "..") else sha256(
        task_id.encode("utf-8", "surrogatepass")
    ).hexdigest()
    return ledger_root / LEASES_DIRNAME / f"{name}.json"


def parse_instant(value: Any) -> datetime | None:
    """Un ISO 8601 du journal en datetime UTC, ``None`` s'il est absent ou illisible."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LeaseFile:
    """Le contenu du fichier de bail. ``holder=False`` : barrière posée par une récolte."""

    task_id: str
    epoch: int
    renewed_at: str
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    busy_until: str = ""
    holder: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "$schema": _SCHEMA,
            "task_id": self.task_id,
            "epoch": self.epoch,
            "renewed_at": self.renewed_at,
            "ttl_seconds": self.ttl_seconds,
            "busy_until": self.busy_until,
            "holder": self.holder,
        }

    def lost_at(self) -> datetime | None:
        """``max(renewed_at + ttl, busy_until)`` ; ``None`` si ``renewed_at`` est illisible."""
        renewed = parse_instant(self.renewed_at)
        if renewed is None or self.ttl_seconds <= 0:
            return None
        deadline = renewed + timedelta(seconds=self.ttl_seconds)
        busy = parse_instant(self.busy_until)
        return max(deadline, busy) if busy is not None else deadline

    def is_lost(self, now: datetime) -> bool:
        """Règle de perte du §2.3 ; une valeur illisible vaut « perdu » (comme ``TaskClaim.is_expired``)."""
        if not self.holder:
            return True
        lost_at = self.lost_at()
        return lost_at is None or now > lost_at


def read_lease(path: Path) -> LeaseFile | None:
    """Le bail écrit dans *path*, ou ``None`` (absent, illisible, autre schéma) : jamais une exception."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("$schema") != _SCHEMA:
        return None
    epoch, ttl = raw.get("epoch"), raw.get("ttl_seconds", DEFAULT_LEASE_TTL_SECONDS)
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        ttl = 0  # illisible : lost_at() rendra None, donc « perdu »
    return LeaseFile(
        task_id=str(raw.get("task_id", "")),
        epoch=epoch,
        renewed_at=str(raw.get("renewed_at", "") or ""),
        ttl_seconds=ttl,
        busy_until=str(raw.get("busy_until", "") or ""),
        holder=raw.get("holder", True) is not False,
    )


def write_lease(path: Path, lease: LeaseFile) -> None:
    """Écrit *lease* de façon durable (``fsio.atomic_write`` : fichier puis dossier synchronisés)."""
    atomic_write(path, json.dumps(lease.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def backoff_delay(attempts: int) -> timedelta:
    """``10 min × 2^(attempts−1)`` — 0 avant toute tentative."""
    if attempts <= 0:
        return timedelta(0)
    return BACKOFF_BASE * int(2 ** (attempts - 1))
