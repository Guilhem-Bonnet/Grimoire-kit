"""Portefeuille de tâches — le board agrégé des projets du registre (issue #638, lot C).

Chaque projet enregistré au cockpit a son propre Mission Ledger ; l'espace
Exécuter n'en lisait qu'un à la fois (`workspace_api.tasks_view`). Ce module
agrège côté serveur, sur le même patron que `workspace_memory.memory_overview`
(#172) : il réutilise les lecteurs existants (`TaskService`, `_task_json`),
jamais une relecture parallèle du ledger.

Refus délibérés, tenus ici plutôt que rappelés à chaque appelant :

- aucun projet hors du registre de la machine
  (`grimoire.tools.project_registry.load_registry`) — les chemins viennent
  toujours du registre, jamais d'un paramètre de requête ni d'un corps ;
- aucune écriture : les actions du portefeuille passent par
  `workspace_routes._task_action`, donc par le `TaskService` du projet
  propriétaire, résolu par :func:`resolve_registry_root` (ADR-007 : la
  webview n'est jamais une autorité causale) ;
- un projet dont le ledger est absent ou illisible est listé avec sa raison,
  jamais compté comme un board vide en silence (leçon #264) ;
- « reprendre la session » est une commande à copier, jamais exécutée ici.

Champs optionnels lus s'ils existent (lots A et B de la même issue) :
``claim.session_id`` (A — sinon le journal de session qui porte ``task_id``
sert de lien), ``priority`` (déjà au schéma, dérivée du ``risk_profile`` sinon,
même table que ``board._PRIORITY_BY_RISK``), consignes non lues (B — voir
:func:`unread_directives`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.tools.project_registry import load_registry, slug_for_path

__all__ = [
    "DEFAULT_LIVE_MINUTES",
    "PRIORITY_BY_RISK",
    "SCHEMA_VERSION",
    "portfolio_tasks",
    "resolve_registry_root",
    "resume_command",
    "unread_directives",
]

SCHEMA_VERSION = "grimoire-task-portfolio/v1"

#: Une session dont le journal n'a pas bougé depuis N minutes n'est plus
#: « vivante » — N par défaut, paramétrable (`?minutes=`, `--live-minutes`).
DEFAULT_LIVE_MINUTES = 30

#: Journaux de session des hooks (`grimoire.policies.session_state`) :
#: `session-<id>.json`, avec `updated_at` réécrit à chaque événement de hook.
RUNS_RELPATH = Path("_grimoire-output") / ".runs"

#: Même table que ``grimoire.missions.board._PRIORITY_BY_RISK`` (gardée par un
#: test d'égalité) — recopiée plutôt qu'importée : la projection board reste
#: propriétaire de la règle, ce module ne fait que l'afficher.
PRIORITY_BY_RISK: dict[str, str] = {
    "light": "low",
    "standard": "medium",
    "strict": "high",
    "security_critical": "high",
    "release": "high",
}

#: Clés sous lesquelles le lot B range les consignes de l'orchestrateur
#: humain — lues si présentes, jamais exigées. Forme réelle du lot B (lue dans
#: son worktree le 2026-09-27, à recaler à la fusion) : `_task_json` porte
#: déjà `directives_unacknowledged` (compte) et la carte `directives`
#: (liste de `TaskDirective`, `acknowledged_at` vide = non lue).
_DIRECTIVE_LISTS = ("directives", "comments", "consignes")
_DIRECTIVE_COUNTS = ("directives_unacknowledged", "unread_directives", "unread_comments", "unread")


def resolve_registry_root(slug: str) -> Path:
    """Racine du projet *slug* — depuis le registre, et depuis lui seul.

    Fail-closed : un slug inconnu, ou connu mais dont le dossier n'existe
    plus, est un ``FileNotFoundError`` (404 côté transport), jamais un chemin
    deviné. Un chemin passé à la place d'un slug ne correspond à aucune entrée
    et tombe dans le même refus.
    """
    for entry in load_registry():
        if entry.get("slug") == slug:
            raw = str(entry.get("path", "") or "")
            root = Path(raw) if raw else None
            if root is None or not root.is_dir():
                raise FileNotFoundError(f"projet {slug} : dossier introuvable ({raw or 'chemin absent'})")
            return root.resolve()
    raise FileNotFoundError(f"projet inconnu au registre : {slug}")


def resume_command(session_id: str, host: str | None) -> str | None:
    """La commande qui reprend la session — sur l'hôte Claude Code seulement.

    ``claude --resume <id>`` est le seul geste de reprise connu ; pour les
    autres hôtes, l'identifiant est affiché tel quel par l'interface. Jamais
    exécutée ici : le portefeuille la rend à copier.
    """
    if not session_id or not host or "claude" not in host.lower():
        return None
    return f"claude --resume {session_id}"


def unread_directives(card: dict[str, Any]) -> int:
    """Consignes non lues portées par la carte, si le champ existe (lot B).

    Tolérant à la forme réelle : un compteur déjà calculé
    (``unread_directives``…), ou une liste (``directives``/``comments``…) dont
    chaque entrée dit si elle a été lue (``read``/``acknowledged``/``seen``
    faux, ou un ``*_at`` de lecture présent mais vide). Une entrée qui ne dit
    rien n'est pas comptée : mieux vaut un zéro qu'un faux « à lire ».
    """
    for key in _DIRECTIVE_COUNTS:
        value = card.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(value, 0)
    for key in _DIRECTIVE_LISTS:
        items = card.get(key)
        if isinstance(items, list):
            return sum(1 for item in items if isinstance(item, dict) and _is_unread(item))
    return 0


def _is_unread(item: dict[str, Any]) -> bool:
    if item.get("unread") is True:
        return True
    for flag in ("read", "acknowledged", "seen"):
        if item.get(flag) is False:
            return True
    return any(stamp in item and not item[stamp] for stamp in ("read_at", "acknowledged_at", "seen_at"))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _sessions_index(project_root: Path) -> dict[str, dict[str, Any]]:
    """``session_id → journal`` des sessions de hooks du projet, best-effort."""
    runs = project_root / RUNS_RELPATH
    index: dict[str, dict[str, Any]] = {}
    if not runs.is_dir():
        return index
    for path in sorted(runs.glob("session-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        session_id = str(data.get("session_id") or path.stem.removeprefix("session-"))
        index[session_id] = {
            "session_id": session_id,
            "updated_at": str(data.get("updated_at") or data.get("started_at") or ""),
            "task_id": str(data.get("task_id") or ""),
            "host": str(data.get("host") or data.get("host_id") or ""),
            "journal": path.relative_to(project_root).as_posix(),
        }
    return index


def _session_for(
    card: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    *,
    now: datetime,
    live_minutes: int,
) -> dict[str, Any] | None:
    """La session qui travaille sur la carte : le ``session_id`` du claim (lot
    A) d'abord, sinon le journal le plus récent qui nomme la tâche."""
    raw_claim = card.get("claim")
    claim: dict[str, Any] = raw_claim if isinstance(raw_claim, dict) else {}
    session_id = str(claim.get("session_id") or "")
    journal = sessions.get(session_id) if session_id else None
    if journal is None:
        candidates = [j for j in sessions.values() if j["task_id"] and j["task_id"] == card.get("id")]
        if candidates:
            journal = max(candidates, key=lambda j: j["updated_at"])
            session_id = session_id or journal["session_id"]
    if not session_id:
        return None
    updated_at = journal["updated_at"] if journal else None
    moment = _parse_iso(updated_at)
    live = moment is not None and (now - moment) <= timedelta(minutes=live_minutes)
    # `session_host` : l'hôte de la session rattachée (lot A) ; `host_id` :
    # celui déclaré au claim ; le journal en dernier recours.
    host = str(claim.get("session_host") or claim.get("host_id") or (journal or {}).get("host") or "") or None
    return {
        "id": session_id,
        "host": host,
        "updated_at": updated_at or None,
        "live": live,
        "journal": journal["journal"] if journal else None,
        "resume_command": resume_command(session_id, host),
    }


def _updated_at(ledger: Any, task: Any) -> str:
    """Dernier événement du ledger sur la tâche — sinon sa création."""
    stamps = [e.created_at for e in ledger.list_events(task.id) if e.created_at]
    return max(stamps) if stamps else str(task.created_at)


def _project_row(entry: dict[str, str]) -> dict[str, Any]:
    slug = str(entry.get("slug", ""))
    return {
        "slug": slug,
        "name": str(entry.get("name", "")) or slug,
        "path": str(entry.get("path", "")),
        "state": "unreadable",
        "reason": None,
        "count": 0,
    }


def _project_cards(
    entry: dict[str, str], row: dict[str, Any], *, now: datetime, live_minutes: int
) -> list[dict[str, Any]]:
    """Les cartes d'un projet — et l'état de sa ligne, mis à jour en place."""
    from grimoire.missions.service import TaskService
    from grimoire.tools.workspace_api import _task_json

    raw = row["path"]
    if not raw:
        row["reason"] = "chemin absent du registre"
        return []
    project_root = Path(raw)
    if not project_root.is_dir():
        row["reason"] = f"dossier introuvable : {raw}"
        return []
    try:
        service = TaskService(project_root)
        if not service.has_ledger:
            row["state"] = "no_ledger"
            row["reason"] = "aucun Mission Ledger — `grimoire task add` en ouvre un"
            return []
        tasks = service.list_tasks()
        sessions = _sessions_index(project_root)
        cards: list[dict[str, Any]] = []
        for task in tasks:
            card = _task_json(task)
            card["project"] = {"slug": row["slug"], "name": row["name"]}
            card["updated_at"] = _updated_at(service.ledger, task)
            # `effective_priority` : ce que `_task_json` calculera après le lot B
            # (déclarée, sinon dérivée du `risk_profile` par `board.priority_of`) ;
            # la même dérivation ici tant qu'il n'est pas là.
            card["priority"] = str(
                card.get("effective_priority")
                or card.get("priority")
                or PRIORITY_BY_RISK.get(str(card.get("risk_profile")), "")
            )
            card["session"] = _session_for(card, sessions, now=now, live_minutes=live_minutes)
            card["unread_directives"] = unread_directives(card)
            cards.append(card)
    except Exception as exc:
        # Un ledger corrompu sur UN projet de la flotte ne doit pas faire
        # tomber le portefeuille entier : la ligne du projet porte la raison.
        row["reason"] = f"ledger illisible : {exc}"
        return []
    row["state"] = "ok"
    row["count"] = len(cards)
    return cards


def portfolio_tasks(
    project_root: Path,
    *,
    state: str | None = None,
    project: str | None = None,
    live: bool | None = None,
    live_minutes: int = DEFAULT_LIVE_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/portfolio/tasks`` et de
    ``grimoire task list --all-projects``.

    *project_root* est le projet déjà servi par l'hôte : il ne borne pas la
    lecture (le registre entier est parcouru), il ne sert qu'à marquer
    ``current`` pour que l'interface distingue « ce projet » des autres.
    ``state`` accepte un état du ledger (``claimed``…) ou une colonne du
    board (``in_progress``…) ; ``project`` un slug ; ``live`` ne garde que les
    cartes dont la session a écrit son journal depuis moins de *live_minutes*.
    """
    moment = now or datetime.now(UTC)
    minutes = max(int(live_minutes), 1)
    rows: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for entry in load_registry():
        row = _project_row(entry)
        rows.append(row)
        cards.extend(_project_cards(entry, row, now=moment, live_minutes=minutes))

    total = len(cards)
    live_total = sum(1 for c in cards if c["session"] and c["session"]["live"])
    if project:
        cards = [c for c in cards if c["project"]["slug"] == project]
    if state:
        cards = [c for c in cards if state in (c.get("status"), c.get("board"))]
    if live:
        cards = [c for c in cards if c["session"] and c["session"]["live"]]
    cards.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "current": slug_for_path(project_root),
        "live_minutes": minutes,
        "filters": {"state": state or "", "project": project or "", "live": bool(live)},
        "projects": rows,
        "tasks": cards,
        "count": len(cards),
        "summary": {
            "projects": len(rows),
            "readable": sum(1 for r in rows if r["state"] == "ok"),
            "tasks": total,
            "live": live_total,
        },
    }
