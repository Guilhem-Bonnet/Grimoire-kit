"""Le pouls de la vue de travail — « quelque chose a-t-il changé ? » à coût quasi nul (UI-3).

Avant ce module, la coque ne se mettait jamais à jour seule : un changement du
ledger (une décision posée, une session qui bat) restait invisible jusqu'au
rechargement de la page. Un sondage naïf qui relirait le ledger à chaque tour
déplacerait le coût sans le supprimer. Le pouls sépare donc deux questions :

- **a-t-il bougé ?** une empreinte calculée par ``stat`` seulement (date et taille
  des fichiers du ledger, des baux et des journaux de session) — aucune lecture
  de contenu, aucun rejeu d'événements ;
- **qu'est-ce qui a bougé ?** le détail (les décisions en attente), relu
  uniquement quand l'empreinte diffère de celle que l'appelant dit connaître
  (``since``). Sinon la réponse tient en cinq clés et ``changed`` vaut ``false``.

L'empreinte porte aussi le nombre de journaux de session encore dans la fenêtre
de vie : la vivacité se calcule avec l'horloge (``missions.liveness``), donc une
session qui se tait passe de ``vivante`` à ``inactive`` sans qu'aucun fichier ne
bouge. Sans ce terme, l'interface afficherait « vivante » indéfiniment.

Le compteur de décisions est la lecture de ``grimoire status`` et de la
notification (:func:`grimoire.missions.decisions.pending_decisions`), pas une
seconde définition. ``scope=portfolio`` agrège le registre du cockpit ; un projet
dont le ledger est illisible est nommé (``unreadable``), jamais compté pour zéro.

L'intervalle de sondage est déclaré ici (``GRIMOIRE_PULSE_SECONDS``, borné), et
l'interface le lit : elle n'a pas sa propre constante.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.standard_state import LEDGER_RELPATH
from grimoire.missions.lease import LEASES_DIRNAME, parse_instant
from grimoire.tools.project_registry import load_registry, slug_for_path
from grimoire.tools.workspace_portfolio import DEFAULT_LIVE_MINUTES, RUNS_RELPATH

__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "INTERVAL_ENV",
    "MAX_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "SCHEMA_VERSION",
    "pulse_interval",
    "pulse_view",
]

SCHEMA_VERSION = "grimoire-workspace-pulse/v1"
INTERVAL_ENV = "GRIMOIRE_PULSE_SECONDS"
DEFAULT_INTERVAL_SECONDS = 5
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 300


def pulse_interval() -> int:
    """L'intervalle de sondage déclaré, en secondes : le défaut, ou la variable bornée à [1, 300]."""
    raw = os.environ.get(INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS  # une valeur illisible ne se devine pas : le défaut déclaré
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


_CONTENT_HASH_MAX_BYTES = 64 * 1024


def _stat(path: Path) -> tuple[str, int, int, str] | None:
    try:
        info = path.stat()
        # Un petit JSON réécrit en place (session, bail) peut garder taille et mtime : l'horloge
        # du système de fichiers est grossière sous Windows. Son contenu tranche ; le journal
        # d'événements, lui, ne fait que croître.
        content = ""
        if path.suffix == ".json" and info.st_size <= _CONTENT_HASH_MAX_BYTES:
            content = hashlib.blake2b(path.read_bytes(), digest_size=8).hexdigest()
    except OSError:
        return None  # absent ou illisible : l'absence fait elle-même partie de l'empreinte
    return (path.name, info.st_mtime_ns, info.st_size, content)


def _watched(root: Path) -> list[Path]:
    ledger = root / LEDGER_RELPATH
    files = [ledger / "events.jsonl", ledger / "incidents.jsonl"]
    leases = ledger / LEASES_DIRNAME
    if leases.is_dir():
        files.extend(sorted(leases.glob("*.json")))
    runs = root / RUNS_RELPATH
    if runs.is_dir():
        files.extend(sorted(runs.glob("session-*.json")))
    return files


def _fingerprint(roots: list[tuple[str, Path]], now: datetime) -> str:
    """Empreinte de ce que l'interface affiche : dates et tailles, plus les sessions encore dans la fenêtre."""
    window_start = (now - timedelta(minutes=DEFAULT_LIVE_MINUTES)).timestamp()
    digest = hashlib.sha256()
    for slug, root in roots:
        in_window = 0
        digest.update(f"{slug}\0{root}\0".encode())
        for path in _watched(root):
            stamp = _stat(path)
            digest.update(repr((str(path.relative_to(root)), stamp)).encode())
            if stamp is not None and path.name.startswith("session-") and stamp[1] / 1e9 >= window_start:
                in_window += 1
        digest.update(f"window={in_window}".encode())
    return digest.hexdigest()[:20]


def _roots(project_root: Path, portfolio: bool) -> list[tuple[str, Path]]:
    if not portfolio:
        return [(slug_for_path(project_root) or "", project_root)]
    return [(str(e.get("slug", "")), Path(str(e.get("path", "")))) for e in load_registry()]


def _decisions(roots: list[tuple[str, Path]], *, portfolio: bool) -> dict[str, Any]:
    from grimoire.missions.decisions import pending_decisions
    from grimoire.missions.ledger import MissionLedger

    projects: list[dict[str, Any]] = []
    unreadable: list[str] = []
    oldest: tuple[datetime, str] | None = None
    for slug, root in roots:
        events = root / LEDGER_RELPATH / "events.jsonl"
        if not root.is_dir():
            unreadable.append(slug)
            continue
        if not events.is_file():
            projects.append({"slug": slug, "pending": 0})
            continue
        try:
            pending = pending_decisions(MissionLedger(events.parent))
        except Exception:  # frontière : un ledger abîmé sur un projet ne fait pas tomber le compteur des autres
            unreadable.append(slug)
            continue
        projects.append({"slug": slug, "pending": len(pending)})
        for decision in pending:
            moment = parse_instant(decision.since)
            if moment is not None and (oldest is None or moment < oldest[0]):
                oldest = (moment, decision.since)
    out: dict[str, Any] = {
        "pending": sum(p["pending"] for p in projects),
        "oldest_at": oldest[1] if oldest else None,
        "projects": projects,
    }
    if portfolio or unreadable:
        out["unreadable"] = unreadable
    return out


def pulse_view(
    project_root: Path,
    *,
    since: str | None = None,
    portfolio: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/pulse``.

    *since* est l'empreinte que l'appelant connaît : identique, la réponse ne porte
    ni décisions ni détail (``changed: false``). *portfolio* agrège le registre.
    """
    moment = now or datetime.now(UTC)
    roots = _roots(project_root, portfolio)
    fingerprint = _fingerprint(roots, moment)
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "interval_seconds": pulse_interval(),
        "scope": "portfolio" if portfolio else "project",
        "fingerprint": fingerprint,
        "changed": since != fingerprint,
    }
    if payload["changed"]:
        payload["decisions"] = _decisions(roots, portfolio=portfolio)
    return payload
