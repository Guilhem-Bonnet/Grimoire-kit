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
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.costs import Cost
from grimoire.missions.lease import DEFAULT_LEASE_TTL_SECONDS, LeaseFile, parse_instant
from grimoire.missions.liveness import (
    DEFAULT_ACTIVE_MINUTES,
    INACTIF,
    INCONNU,
    TERMINE,
    VIVANT,
    SessionProbe,
    attention,
    last_sign,
    liveness,
    presence,
    task_progress,
)
from grimoire.missions.liveness import claimed_at as claim_time
from grimoire.tools.project_registry import load_registry, slug_for_path

__all__ = [
    "DEFAULT_LIVE_MINUTES",
    "PRIORITY_BY_RISK",
    "SCHEMA_VERSION",
    "annotate_cards",
    "portfolio_tasks",
    "resolve_registry_root",
    "resume_command",
    "sessions_index",
    "unread_directives",
]

SCHEMA_VERSION = "grimoire-task-portfolio/v1"

#: Fenêtre du journal de session : écrit depuis moins de N minutes, c'est un
#: signe de vie. Passé ce délai ce n'est PAS « mort » : la vivacité devient
#: `inactif` ou `inconnu` (`missions.liveness`). Paramétrable (`?minutes=`,
#: `--live-minutes`). La valeur est celle de la vivacité
#: (:data:`grimoire.missions.liveness.DEFAULT_ACTIVE_MINUTES`), partagée avec la Flotte.
DEFAULT_LIVE_MINUTES = DEFAULT_ACTIVE_MINUTES

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
    from grimoire.missions.session_link import resume_command as _resume

    return _resume(host or "", session_id) or None


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


def sessions_index(project_root: Path) -> dict[str, dict[str, Any]]:
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
            "started_at": str(data.get("started_at") or ""),
            "ended_at": str(data.get("ended_at") or ""),
            "ended": bool(data.get("ended_at")),
            "task_id": str(data.get("task_id") or ""),
            "host": str(data.get("host") or data.get("host_id") or ""),
            "journal": path.relative_to(project_root).as_posix(),
        }
    return index


def session_duration(started_at: str | None, last_sign_at: str | None) -> int | None:
    """Secondes entre le premier et le dernier signe de vie de la session, ou ``None``.

    Jamais ``0`` par défaut : un journal sans ``started_at`` lisible, ou dont le
    dernier signe précède le début, a une durée **inconnue**, pas nulle.
    """
    start, end = _parse_iso(started_at), _parse_iso(last_sign_at)
    if start is None or end is None or end < start:
        return None
    return int((end - start).total_seconds())


#: Le coût propre de la session hôte n'est mesuré nulle part dans le kit (voir
#: ``flows.autonomy.COST_SCOPE``) : le dire, plutôt qu'afficher un zéro.
SESSION_COST: dict[str, Any] = {"usd": None, "status": "unknown", "unpriced_calls": 0, "calls": 0, "scope": "host_session"}


def dispatch_costs(ledger: Any) -> dict[str, dict[str, Any]]:
    """Coût des tentatives de dispatch (``task.dispatched``) par tâche, en trois états (W1-01).

    Une tentative sans ``cost_usd`` est non chiffrée : le total d'une tâche est
    ``exact`` si tout est chiffré, ``lower_bound`` (un minimum) si certaines
    seulement le sont, ``unknown`` si aucune ne l'est. Une tâche **sans aucun
    dispatch** n'a pas un coût nul : le kit n'a rien mesuré, elle n'apparaît
    pas ici (voir :func:`cost_of`). Portée ``dispatch`` : le coût de la session
    qui a travaillé hors dispatch n'y est pas.
    """
    totals: dict[str, Cost] = {}
    calls: dict[str, int] = {}
    for event in ledger.list_events():
        if event.event_type != "task.dispatched":
            continue
        totals[event.entity_id] = totals.get(event.entity_id, Cost.none()) + Cost.coerce(event.payload.get("cost_usd"))
        calls[event.entity_id] = calls.get(event.entity_id, 0) + 1
    return {
        tid: {
            "usd": None if cost.usd is None else round(cost.usd, 6),
            "status": cost.status,
            "unpriced_calls": cost.unpriced_calls,
            "calls": calls[tid],
            "scope": "dispatch",
        }
        for tid, cost in totals.items()
    }


def cost_of(costs: Mapping[str, dict[str, Any]], task_id: str) -> dict[str, Any]:
    """Le coût de *task_id*, ou « inconnu, aucun dispatch » — jamais ``0``."""
    return dict(costs.get(task_id) or {"usd": None, "status": "unknown", "unpriced_calls": 0, "calls": 0, "scope": "dispatch"})


def _session_for(
    card: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    *,
    now: datetime,
    live_minutes: int,
    lease: LeaseFile | None = None,
    probe: Mapping[str, str] | None = None,
    progress: datetime | None = None,
    claimed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """La session qui travaille sur la carte : le ``session_id`` du claim (lot
    A) d'abord, sinon le journal le plus récent (par horodatage, pas par ordre
    lexicographique) qui nomme la tâche. Le repli ne joue que si le claim ne
    désigne aucune session — un claim dont le journal est absent ne se voit
    jamais prêter le journal d'une autre session (elle mélangerait un
    ``session_id`` avec un ``updated_at``/``journal`` qui ne lui appartient
    pas, faussant la vivacité et l'affichage).

    La vivacité est calculée (:func:`grimoire.missions.liveness.liveness`),
    à quatre valeurs, jamais un booléen ; ``progress_at`` et l'attention
    n'existent que pour une tâche tenue."""
    raw_claim = card.get("claim")
    claim: dict[str, Any] = raw_claim if isinstance(raw_claim, dict) else {}
    session_id = str(claim.get("session_id") or "")
    journal = sessions.get(session_id) if session_id else None
    if not session_id:
        candidates = [j for j in sessions.values() if j["task_id"] and j["task_id"] == card.get("id")]
        if candidates:
            journal = max(
                candidates,
                key=lambda j: _parse_iso(j["updated_at"]) or datetime.min.replace(tzinfo=UTC),
            )
            session_id = journal["session_id"]
    if not session_id:
        return None
    updated_at = journal["updated_at"] if journal else None
    moment = _parse_iso(updated_at)
    ended = bool(journal and journal.get("ended"))
    state = liveness(
        now=now,
        lease=lease,
        journal_at=moment,
        ended=ended,
        probe_status=(probe or {}).get(session_id),
        active_window=timedelta(minutes=live_minutes),
        claimed_at=claimed_at,
    )
    held = card.get("status") in ("claimed", "running")
    warn = (
        attention(
            state,
            progress,
            now=now,
            since_work=claimed_at or moment,
            ttl_seconds=lease.ttl_seconds if lease and lease.ttl_seconds > 0 else DEFAULT_LEASE_TTL_SECONDS,
        )
        if held
        else None
    )
    # `session_host` : l'hôte de la session rattachée (lot A) ; `host_id` :
    # celui déclaré au claim ; le journal en dernier recours.
    host = str(claim.get("session_host") or claim.get("host_id") or (journal or {}).get("host") or "") or None
    last_sign = (journal or {}).get("ended_at") or updated_at
    return {
        "id": session_id,
        "host": host,
        "updated_at": updated_at or None,
        "started_at": (journal or {}).get("started_at") or None,
        "duration_s": session_duration((journal or {}).get("started_at"), last_sign),
        "cost": dict(SESSION_COST),
        "liveness": state.to_dict(),
        "progress_at": progress.isoformat() if progress else None,
        "attention": warn.to_dict() if warn else None,
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


def block_reasons(ledger: Any) -> dict[str, str]:
    """La raison écrite au dernier passage de chaque tâche en ``blocked`` (ou ``failed``).

    Le motif d'un ``grimoire task block --reason`` vit dans l'événement
    ``task.transitioned``, jamais sur la tâche : sans cette lecture, une carte
    bloquée ne pouvait dire ni le mot ni la raison.
    """
    reasons: dict[str, str] = {}
    for event in ledger.list_events():
        if event.event_type != "task.transitioned":
            continue
        if event.payload.get("to_state") not in ("blocked", "failed"):
            continue
        reason = str(event.payload.get("reason") or "").strip()
        if reason:
            reasons[event.entity_id] = reason
    return reasons


def transition_times(ledger: Any) -> dict[str, str]:
    """L'horodatage du dernier ``task.transitioned`` de chaque tâche (P74).

    C'est l'entrée dans la colonne courante du board : la carte affiche « dans
    la colonne depuis … » depuis ce moment, distinct de l'âge total (depuis
    ``created_at``). Une tâche jamais transitionnée n'a pas d'entrée ici — la
    carte retombe alors sur sa création, ce qui est exact (elle n'a jamais
    quitté sa première colonne).
    """
    times: dict[str, str] = {}
    for event in ledger.list_events():
        if event.event_type == "task.transitioned" and event.created_at:
            times[event.entity_id] = str(event.created_at)
    return times


def lease_losses(ledger: Any) -> dict[str, dict[str, Any]]:
    """Par tâche, le ``lease_lost`` de sa **dernière** transition, si c'est lui qui l'a rendue ``ready`` (P90).

    Une transition ultérieure sans ``lease_lost`` (claim, réveil, retour) l'efface : « à reprendre » ne
    survit pas à la reprise.
    """
    found: dict[str, dict[str, Any]] = {}
    for event in ledger.list_events():
        if event.event_type != "task.transitioned":
            continue
        lost = event.payload.get("lease_lost")
        if isinstance(lost, dict) and event.payload.get("to_state") == "ready":
            found[event.entity_id] = dict(lost)
        else:
            found.pop(event.entity_id, None)
    return found


def _blocked_cause(task: Any, card: dict[str, Any]) -> str:
    waiting = getattr(task, "waiting", None)
    if waiting is not None:
        if waiting.question is not None:
            return f"attend une réponse : {waiting.question.text}"
        until = f" jusqu'à {waiting.until}" if waiting.until else ""
        return f"attend ({waiting.reason}){until}"
    return str(card.get("blocked_reason") or "")


def annotate_cards(
    project_root: Path,
    service: Any,
    tasks: list[Any],
    cards: list[dict[str, Any]],
    *,
    now: datetime,
    live_minutes: int = DEFAULT_LIVE_MINUTES,
    probe: SessionProbe | None = None,
) -> None:
    """Pose sur chaque carte ``session`` (vivacité, dernier signe, durée) et ``cost``.

    Partagé par le portefeuille et par la portée projet d'Exécuter
    (:func:`grimoire.tools.workspace_api.tasks_view`) : une seule arithmétique,
    donc la même carte dit la même chose dans les deux. La sonde d'hôte n'est
    appelée qu'une fois par projet, et seulement si une tâche est tenue.
    """
    from grimoire.missions.board import blockers_of
    from grimoire.missions.schemas import TaskState

    sessions = sessions_index(project_root)
    costs = dispatch_costs(service.ledger)
    reasons = block_reasons(service.ledger)
    since = transition_times(service.ledger)
    # P69v2 : la dernière entrée du fil d'activité, pour la ligne sur la carte
    # d'une tâche tenue dont la session est vivante — une passe du ledger.
    from grimoire.tools.workspace_session_feed import last_feed_labels

    held_ids = {t.id for t in tasks if t.status in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING)}
    feed_last = last_feed_labels(service, held_ids)
    losses = lease_losses(service.ledger)
    traced: dict[str, Any] | None = None
    traced_done = False
    closed: frozenset[str] | None = None
    probed: Mapping[str, str] | None = None
    probe_done = False
    for task, card in zip(tasks, cards, strict=True):
        held = task.status in (TaskState.CLAIMED, TaskState.RUNNING)
        if held and probe is not None and not probe_done:
            probed, probe_done = probe(), True  # une fois par projet, seulement si une tâche est tenue
        card["session"] = _session_for(
            card,
            sessions,
            now=now,
            live_minutes=live_minutes,
            lease=service.ledger.lease_file(task.id) if held else None,
            probe=probed,
            progress=task_progress(service.ledger, task.id, project_root) if held else None,
            claimed_at=claim_time(service.ledger, task.id) if held else None,
        )
        card["cost"] = cost_of(costs, task.id)
        if held and not traced_done:
            from grimoire.tools.workspace_sessions import recent_tool_calls

            traced, traced_done = recent_tool_calls(project_root, now=now), True
        # P69v2 : la dernière entrée du fil, en une ligne, seulement quand la
        # session de la carte est vivante — sinon la ligne raconterait le passé.
        session = card["session"]
        if session is not None and (session.get("liveness") or {}).get("state") == "vivant":
            card["feed_last"] = feed_last.get(task.id)
        # P74 : l'âge situé — depuis quand la carte est dans sa colonne.
        card["in_state_since"] = since.get(task.id) or str(task.created_at)
        if card.get("board") == "blocked" and task.status is not TaskState.WAITING:
            # Le motif écrit au blocage d'abord ; à défaut, celui que la
            # projection du board pose (dépendance ouverte, échec, état inconnu).
            if task.id not in reasons and closed is None:
                closed = frozenset(t.id for t in service.ledger.list_tasks() if t.status is TaskState.CLOSED)
            fallback = blockers_of(task, closed or frozenset())
            card["blocked_reason"] = reasons.get(task.id) or (fallback[0]["reason"] if fallback else "")
        card["presence"] = _presence_for(task, card, sessions, now=now, lease=service.ledger.lease_file(task.id) if held else None,
                                         probe=probed, traced=traced, loss=losses.get(task.id))
    # P75 : qui travaille, avec quoi, à quel coût — usage replié (task_usage),
    # coût en CAD au taux des options du projet, et pour une tâche tenue ce que
    # le journal de session rapporte (modèle réel, effort, skills).
    from grimoire.tools.workspace_work import annotate_work

    annotate_work(project_root, tasks, cards)


def _presence_for(
    task: Any,
    card: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    *,
    now: datetime,
    lease: LeaseFile | None,
    probe: Mapping[str, str] | None,
    traced: Mapping[str, Any] | None,
    loss: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Le champ ``presence`` de la carte (P90) : une seule vérité pour le glyphe, le mot et le temps.

    Le dernier signe réunit la sonde (lue à cet instant quand l'hôte la dit occupée), le dernier outil
    tracé, le battement du bail et le journal de hooks : le plus récent gagne. ``None`` pour une tâche
    close ou annulée.
    """
    status = str(getattr(task.status, "value", task.status))
    if status in ("closed", "cancelled", "unknown"):
        return None
    session = card.get("session") or {}
    session_id = str(session.get("id") or "")
    probe_status = (probe or {}).get(session_id) if session_id else None
    journal = sessions.get(session_id) if session_id else None
    activity = (traced or {}).get(session_id) if session_id else None
    beat = parse_instant(lease.renewed_at) if lease is not None else None
    stamp, source = last_sign(
        sonde=now if probe_status in ("busy", "waiting") else None,
        outil=getattr(activity, "last_at", None),
        battement=beat,
        journal=_parse_iso(journal["updated_at"]) if journal else None,
    )
    return presence(
        now=now,
        status=status,
        blocked_cause=_blocked_cause(task, card),
        lease=lease,
        probe_status=probe_status,
        ended=bool(journal and journal.get("ended")),
        sign_at=stamp,
        sign_source=source,
        lease_lost=loss,
    ).to_dict()


def _project_cards(
    entry: dict[str, str],
    row: dict[str, Any],
    *,
    now: datetime,
    live_minutes: int,
    probe: SessionProbe | None = None,
) -> list[dict[str, Any]]:
    """Les cartes d'un projet — et l'état de sa ligne, mis à jour en place."""
    from grimoire.missions.service import TaskService
    from grimoire.tools.workspace_api import _task_json, annotate_gates

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
            cards.append(card)
        annotate_cards(project_root, service, tasks, cards, now=now, live_minutes=live_minutes, probe=probe)
        annotate_gates(service.project_root, tasks, cards)
        for card in cards:
            card["unread_directives"] = unread_directives(card)
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
    probe: SessionProbe | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/portfolio/tasks`` et de
    ``grimoire task list --all-projects``.

    *project_root* est le projet déjà servi par l'hôte : il ne borne pas la
    lecture (le registre entier est parcouru), il ne sert qu'à marquer
    ``current`` pour que l'interface distingue « ce projet » des autres.
    ``state`` accepte un état du ledger (``claimed``…) ou une colonne du
    board (``in_progress``…) ; ``project`` un slug ; ``live`` ne garde que les
    cartes de vivacité ``vivant`` (journal écrit depuis moins de *live_minutes*,
    ou sonde d'hôte occupée via *probe*).
    """
    moment = now or datetime.now(UTC)
    minutes = max(int(live_minutes), 1)
    rows: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for entry in load_registry():
        row = _project_row(entry)
        rows.append(row)
        cards.extend(_project_cards(entry, row, now=moment, live_minutes=minutes, probe=probe))

    total = len(cards)
    by_liveness = dict.fromkeys((VIVANT, INACTIF, INCONNU, TERMINE), 0)
    for c in cards:
        if c["session"]:
            by_liveness[c["session"]["liveness"]["state"]] += 1
    attention_total = sum(1 for c in cards if c["session"] and c["session"]["attention"])
    if project:
        cards = [c for c in cards if c["project"]["slug"] == project]
    if state:
        cards = [c for c in cards if state in (c.get("status"), c.get("board"))]
    if live:
        cards = [c for c in cards if c["session"] and c["session"]["liveness"]["state"] == VIVANT]
    cards.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)
    # K6 : ce qui attend la décision d'un humain passe devant tout (tri stable : le reste garde son ordre).
    cards.sort(key=lambda c: 0 if c.get("status") == "waiting" and (c.get("waiting") or {}).get("reason") == "human" else 1)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "current": slug_for_path(project_root),
        "live_minutes": minutes,
        "filters": {"state": state or "", "project": project or "", "liveness": VIVANT if live else ""},
        "projects": rows,
        "tasks": cards,
        "count": len(cards),
        "summary": {
            "projects": len(rows),
            "readable": sum(1 for r in rows if r["state"] == "ok"),
            "tasks": total,
            "liveness": by_liveness,
            "attention": attention_total,
        },
    }
