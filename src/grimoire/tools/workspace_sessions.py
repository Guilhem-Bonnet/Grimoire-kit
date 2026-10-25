"""La vue « Sessions » de Travail (P33) : les sessions d'agent, pas les tâches.

Le board dit ce que chaque tâche attend ; cette lecture dit ce que fait chaque
**session** — laquelle travaille, laquelle attend ta réponse, laquelle tient des
claims sans plus donner signe de vie. Lecture seule, quatre sources, aucune écrite :

- les journaux de session des hooks (``_grimoire-output/.runs/session-<id>.json``,
  :func:`grimoire.tools.workspace_portfolio.sessions_index`) ;
- les claims du Mission Ledger (``claim.session_id`` rattaché par le hook) ;
- la fin du TraceLedger (``_grimoire-output/traces/traces.jsonl``) : les appels
  d'outil des quinze dernières minutes par session, l'agent, le modèle et le
  dernier outil — seulement la queue du fichier, jamais tout le journal ;
- la sonde d'hôte ``claude agents --json`` (:func:`grimoire.missions.liveness.probe_entries`),
  une fois par lecture, injectable.

La vivacité vient de :func:`grimoire.missions.liveness.liveness` — la seule
définition de « vivante » du kit. Elle se projette sur les quatre états de la
maquette :

- ``active`` : vivante ;
- ``waiting`` : la session tient une tâche en attente d'une décision humaine (K6),
  ou la sonde dit qu'elle attend ;
- ``silent`` : inactive ou inconnue — jamais « morte » ;
- ``ended`` : la session (ou son hôte) a dit qu'elle finissait.

Une lecture ratée n'est jamais un zéro : un projet illisible est nommé avec sa
raison, une activité que rien ne trace vaut ``None`` (« non lue »), pas sept barres
vides. Reprendre une session reste une commande à copier, jamais exécutée ici.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.missions.lease import parse_instant
from grimoire.missions.liveness import DEFAULT_ACTIVE_WINDOW, TERMINE, VIVANT, Liveness, liveness
from grimoire.tools.project_registry import load_registry
from grimoire.tools.workspace_portfolio import resume_command, sessions_index

__all__ = [
    "ACTIVE",
    "ACTIVITY_BUCKETS",
    "ACTIVITY_WINDOW",
    "ENDED",
    "RECENT_WINDOW",
    "SCHEMA_VERSION",
    "SILENT",
    "TRACES_RELPATH",
    "WAITING",
    "recent_tool_calls",
    "sessions_view",
    "view_state",
]

SCHEMA_VERSION = "grimoire-workspace-sessions/v1"

ACTIVE = "active"
WAITING = "waiting"
SILENT = "silent"
ENDED = "ended"
#: Ordre d'affichage : ce qui attend un humain d'abord, ce qui est fini en dernier.
_ORDER = {WAITING: 0, ACTIVE: 1, SILENT: 2, ENDED: 3}

#: L'activité récente que la vue dessine en mini barres : sept tranches sur quinze minutes.
ACTIVITY_WINDOW = timedelta(minutes=15)
ACTIVITY_BUCKETS = 7
#: Une session silencieuse sans claim, ou terminée, n'est listée que si son dernier
#: signe date de moins de 24 h ; au-delà elle n'occupe plus personne.
RECENT_WINDOW = timedelta(hours=24)

TRACES_RELPATH = Path("_grimoire-output") / "traces" / "traces.jsonl"
#: Queue du TraceLedger lue à chaque requête : assez pour quinze minutes d'une
#: session très active, sans jamais relire un journal de plusieurs dizaines de Mo.
TRACE_TAIL_BYTES = 2 * 1024 * 1024

#: L'hôte que la sonde ``claude agents`` décrit : une session qu'elle seule connaît est une session Claude Code.
_PROBE_HOST = "host-claude-code-cli"
#: États d'une tâche dont le claim compte encore : la session la tient.
_HELD = frozenset({"claimed", "running", "waiting", "blocked", "needs_verification"})

ProbeEntries = Callable[[], Mapping[str, Mapping[str, Any]] | None]


def view_state(live: Liveness, *, waiting: bool) -> str:
    """L'état affiché d'une session : terminée, sinon en attente, sinon vivante ou silencieuse."""
    if live.state == TERMINE:
        return ENDED
    if waiting:
        return WAITING
    return ACTIVE if live.state == VIVANT else SILENT


@dataclass(slots=True)
class _Activity:
    buckets: list[int] = field(default_factory=lambda: [0] * ACTIVITY_BUCKETS)
    last_at: datetime | None = None
    last_tool: str = ""
    agent: str = ""
    host: str = ""
    model: str = ""


def _tail_lines(path: Path, tail_bytes: int) -> list[bytes] | None:
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, 2)
            start = max(0, size - tail_bytes)
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return None
    lines = raw.split(b"\n")
    return lines[1:] if start > 0 else lines  # la première ligne d'une queue est coupée


def recent_tool_calls(
    project_root: Path, *, now: datetime, tail_bytes: int = TRACE_TAIL_BYTES
) -> dict[str, _Activity] | None:
    """Les appels d'outil récents par session (``run_id``), ou ``None`` si le TraceLedger manque ou ne se lit pas.

    Chaque session reçoit le compte de ses appels par tranche des quinze
    dernières minutes, son dernier appel (date et outil) et le dernier agent,
    hôte et modèle non vides que la trace nomme. Une ligne illisible est sautée.
    """
    path = project_root / TRACES_RELPATH
    if not path.is_file():
        return None
    lines = _tail_lines(path, tail_bytes)
    if lines is None:
        return None
    window_start = now - ACTIVITY_WINDOW
    slice_s = ACTIVITY_WINDOW.total_seconds() / ACTIVITY_BUCKETS
    out: dict[str, _Activity] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not record.get("run_id"):
            continue
        act = out.setdefault(str(record["run_id"]), _Activity())
        raw_agent = record.get("agent")
        agent: dict[str, Any] = raw_agent if isinstance(raw_agent, dict) else {}
        act.agent = str(agent.get("agent_id") or "") or act.agent
        act.host = str(agent.get("host_id") or "") or act.host
        act.model = str(agent.get("model") or "") or act.model
        raw_calls = record.get("tool_calls")
        calls: list[Any] = raw_calls if isinstance(raw_calls, list) else []
        for call in calls:
            if not isinstance(call, dict):
                continue
            at = parse_instant(call.get("timestamp"))
            if at is None:
                continue
            if act.last_at is None or at >= act.last_at:
                act.last_at, act.last_tool = at, str(call.get("tool") or "")
            if window_start <= at <= now:
                index = min(int((at - window_start).total_seconds() // slice_s), ACTIVITY_BUCKETS - 1)
                act.buckets[index] += 1
    return out


@dataclass(slots=True)
class _Root:
    slug: str
    name: str
    path: Path
    current: bool = False
    reason: str = ""


def _roots(project_root: Path, portfolio: bool) -> list[_Root]:
    served = project_root.resolve()
    if not portfolio:
        entry = next((e for e in load_registry() if e.get("path") == str(served)), {})
        slug = str(entry.get("slug", "") or "") or served.name
        return [_Root(slug, str(entry.get("name", "") or "") or slug, served, current=True)]
    roots: list[_Root] = []
    for entry in load_registry():
        raw = str(entry.get("path", "") or "")
        slug = str(entry.get("slug", "") or "")
        root = _Root(slug, str(entry.get("name", "") or "") or slug, Path(raw) if raw else Path())
        if not raw:
            root.reason = "chemin absent du registre"
        elif not root.path.is_dir():
            root.reason = f"dossier introuvable : {raw}"
        else:
            root.path = root.path.resolve()
            root.current = root.path == served
        roots.append(root)
    return roots


def _owner_root(cwd: str, roots: list[_Root]) -> _Root | None:
    """Le projet le plus profond qui contient *cwd* (un projet peut en contenir un autre)."""
    if not cwd:
        return None
    try:
        where = Path(cwd).resolve()
    except (OSError, ValueError):
        return None
    best: _Root | None = None
    for root in roots:
        inside = not root.reason and (where == root.path or where.is_relative_to(root.path))
        if inside and (best is None or len(root.path.parts) > len(best.path.parts)):
            best = root
    return best


def _question(task: Any) -> dict[str, str] | None:
    waiting = getattr(task, "waiting", None)
    if waiting is None or not waiting.is_human or waiting.question is None:
        return None
    return {"task": task.id, "id": waiting.question.id, "text": waiting.question.text, "since": waiting.since}


def _held_tasks(root: Path, journals: Mapping[str, Mapping[str, Any]]) -> dict[str, list[Any]]:
    """``session_id → tâches tenues`` : le ``session_id`` du claim, sinon le journal qui nomme la tâche."""
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    if not service.has_ledger:
        return {}
    by_task = {str(j["task_id"]): sid for sid, j in journals.items() if j.get("task_id")}
    held: dict[str, list[Any]] = {}
    for task in service.list_tasks():
        claim = getattr(task, "claim", None)
        if claim is None or str(getattr(task.status, "value", task.status)) not in _HELD:
            continue
        session_id = str(claim.session_id or "") or by_task.get(task.id, "")
        if session_id:
            held.setdefault(session_id, []).append(task)
    return held


def _task_row(task: Any) -> dict[str, Any]:
    from grimoire.missions.board import board_status_of

    return {
        "id": task.id,
        "title": task.title,
        "status": str(getattr(task.status, "value", task.status)),
        "board": board_status_of(task.status),
        "directives_pending": len(task.pending_directives),
        "question": _question(task),
    }


def _activity_json(act: _Activity | None, traced: bool) -> dict[str, Any] | None:
    if not traced:
        return None  # rien ne trace les appels d'outil ici : non lue, pas zéro
    buckets = list(act.buckets) if act else [0] * ACTIVITY_BUCKETS
    return {"window_minutes": int(ACTIVITY_WINDOW.total_seconds() // 60), "buckets": buckets, "calls": sum(buckets)}


def _session(
    session_id: str,
    root: _Root,
    *,
    now: datetime,
    journal: Mapping[str, Any] | None,
    tasks: list[Any],
    probe: Mapping[str, Any] | None,
    act: _Activity | None,
    traced: bool,
) -> dict[str, Any]:
    from grimoire.missions.session_link import canonical_host

    updated = parse_instant((journal or {}).get("updated_at"))
    ended_at = parse_instant((journal or {}).get("ended_at"))
    claim = tasks[0].claim if tasks else None
    started_at = parse_instant((journal or {}).get("started_at")) or parse_instant((probe or {}).get("started_at"))
    live = liveness(
        now=now,
        journal_at=max((m for m in (updated, act.last_at if act else None) if m is not None), default=None),
        ended=bool(ended_at),
        probe_status=(probe or {}).get("status"),
        active_window=DEFAULT_ACTIVE_WINDOW,
        claimed_at=started_at,
    )
    rows = [_task_row(t) for t in tasks]
    question = next((r["question"] for r in rows if r["question"]), None)
    waiting = question is not None or (probe or {}).get("status") == "waiting"
    raw_host = (
        str(getattr(claim, "session_host", "") or "")
        or str((journal or {}).get("host") or "")
        or (act.host if act else "")
        or (_PROBE_HOST if probe else "")
    )
    host = canonical_host(raw_host) or None
    # Le dernier signe : le plus récent du journal, de sa fin et du dernier appel d'outil tracé.
    signs = [(m, src) for m, src in ((updated, "journal"), (ended_at, "fin de session")) if m is not None]
    if act and act.last_at is not None:
        signs.append((act.last_at, f"outil {act.last_tool}" if act.last_tool else "appel d'outil"))
    last_at, last_source = max(signs, key=lambda s: s[0]) if signs else (None, "")
    persona = str(getattr(claim, "actor_id", "") or "") or (act.agent if act else "") or None
    return {
        "id": session_id,
        "project": {"slug": root.slug, "name": root.name, "current": root.current},
        "host": host,
        "state": view_state(live, waiting=waiting),
        "liveness": live.to_dict(),
        "started_at": started_at.isoformat() if started_at else None,
        "last_sign": {"at": last_at.isoformat() if last_at else None, "source": last_source or None},
        "tasks": rows,
        "claims": len(rows),
        "directives_pending": sum(r["directives_pending"] for r in rows),
        "question": question,
        "persona": persona,
        "model": (act.model if act else "") or None,
        "activity": _activity_json(act, traced),
        "probe": {"status": probe.get("status"), "name": probe.get("name") or None} if probe else None,
        "journal": (journal or {}).get("journal") or None,
        "resume_command": resume_command(session_id, host),
    }


def _keep(session: Mapping[str, Any], now: datetime) -> bool:
    """Ce qui occupe encore quelqu'un : un claim, une attente, une session en cours, ou un signe de moins de 24 h."""
    if session["claims"] or session["state"] in (WAITING, ACTIVE):
        return True
    probe = session.get("probe") or {}
    if probe.get("status") in ("busy", "idle", "waiting"):
        return True
    moment = parse_instant(session["last_sign"]["at"]) or parse_instant(session["started_at"])
    return moment is not None and now - moment <= RECENT_WINDOW


def _sort_key(session: Mapping[str, Any]) -> tuple[int, int, float]:
    moment = parse_instant(session["last_sign"]["at"])
    return (_ORDER[session["state"]], 0 if session["claims"] else 1, -(moment.timestamp() if moment else 0.0))


def sessions_view(
    project_root: Path,
    *,
    portfolio: bool = False,
    now: datetime | None = None,
    probe: ProbeEntries | None = None,
    tail_bytes: int = TRACE_TAIL_BYTES,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/sessions[?scope=portfolio]``.

    Le projet servi seul, ou tout le registre du cockpit (``portfolio``). Les
    chemins viennent du registre, jamais de la requête. *probe* rend les
    entrées de ``claude agents --json`` (``None`` : sonde muette, le journal
    de session tranche seul) ; elle n'est appelée qu'une fois.
    """
    moment = now or datetime.now(UTC)
    roots = _roots(project_root, portfolio)
    entries: Mapping[str, Mapping[str, Any]] | None = probe() if probe is not None else None
    probed_root = {sid: _owner_root(str(e.get("cwd") or ""), roots) for sid, e in (entries or {}).items()}

    found: dict[str, dict[str, Any]] = {}
    projects: list[dict[str, Any]] = []
    for root in roots:
        row = {"slug": root.slug, "name": root.name, "current": root.current, "state": "ok", "reason": None, "count": 0}
        projects.append(row)
        if root.reason:
            row["state"], row["reason"] = "unreadable", root.reason
            continue
        try:
            journals = sessions_index(root.path)
            held = _held_tasks(root.path, journals)
            traces = recent_tool_calls(root.path, now=moment, tail_bytes=tail_bytes)
        except Exception as exc:  # frontière : un ledger abîmé sur un projet ne fait pas tomber la vue des autres
            row["state"], row["reason"] = "unreadable", f"lecture impossible : {exc}"
            continue
        ids = set(journals) | set(held) | {sid for sid, owner in probed_root.items() if owner is root}
        for sid in ids:
            act = (traces or {}).get(sid)
            session = _session(
                sid,
                root,
                now=moment,
                journal=journals.get(sid),
                tasks=held.get(sid, []),
                probe=(entries or {}).get(sid),
                act=act,
                traced=traces is not None,
            )
            if not _keep(session, moment):
                continue
            # Une session peut écrire dans deux projets : on garde celui de son signe le plus récent.
            before = found.get(sid)
            if before is None or (session["last_sign"]["at"] or "") > (before["last_sign"]["at"] or ""):
                found[sid] = session
    sessions = sorted(found.values(), key=_sort_key)
    for row in projects:
        row["count"] = sum(1 for s in sessions if s["project"]["slug"] == row["slug"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "scope": "portfolio" if portfolio else "project",
        "live_minutes": int(DEFAULT_ACTIVE_WINDOW.total_seconds() // 60),
        "activity_minutes": int(ACTIVITY_WINDOW.total_seconds() // 60),
        "probe": "absent" if probe is None else ("muette" if entries is None else "ok"),
        "projects": projects,
        "sessions": sessions,
        "count": len(sessions),
        "summary": {
            "active": sum(1 for s in sessions if s["state"] == ACTIVE),
            "waiting": sum(1 for s in sessions if s["state"] == WAITING),
            "silent": sum(1 for s in sessions if s["state"] == SILENT),
            "silent_with_claims": sum(1 for s in sessions if s["state"] == SILENT and s["claims"]),
            "ended_24h": sum(
                1
                for s in sessions
                if s["state"] == ENDED
                and (m := parse_instant(s["last_sign"]["at"]) or parse_instant(s["started_at"])) is not None
                and moment - m <= RECENT_WINDOW
            ),
        },
    }
