"""Les sessions GitHub Copilot de « Flotte en direct » (pendant de :mod:`.workspace_fleet_live`, non vérifié).

La flotte en direct ne lisait que les journaux de Claude Code. Quand l'emplacement des journaux Copilot
est configuré (:func:`grimoire.traces.copilot_usage.copilot_logs_location`, aucun défaut), chaque journal
récent dont le dossier de travail (``hook.start.data.input.cwd``) est sous le projet devient une session
de la même charge utile, avec son hôte (``host: host-github-copilot``), son modèle réel et ses compteurs.

Ce que le journal ne donne pas reste ``None`` (jamais 0) : une session non arrêtée n'a pas d'entrée connue,
le coût en dollars n'existe pas dans le format, ni l'effort, ni les tours, ni la branche. Une session dont
le journal ne dit pas où elle travaille n'est rattachée à aucun projet (comptée dans ``ignored``).
Tout porte ``usage_verified: false`` : le format n'a été lu que dans des fixtures.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.missions.liveness import DEFAULT_ACTIVE_WINDOW, TERMINE, VIVANT, liveness
from grimoire.traces.copilot_usage import (
    UNVERIFIED_NOTE,
    CopilotSession,
    LogsLocation,
    list_journals,
    read_copilot_journal,
)

__all__ = ["assemble_copilot", "journals_by_root"]


def _under(cwd: str, root: Path) -> bool:
    if not cwd:
        return False
    with contextlib.suppress(OSError, ValueError):
        return Path(cwd).resolve().is_relative_to(root.resolve())
    return False


def journals_by_root(
    location: LogsLocation, roots: Sequence[Path], *, cutoff: float
) -> tuple[dict[int, list[tuple[CopilotSession, float]]], int]:
    """Les sessions Copilot récentes groupées par indice de projet, et le nombre de journaux écartés.

    Un journal plus ancien que *cutoff* (mtime, secondes d'epoch) n'est pas ouvert. Écarté : illisible,
    non reconnu, ou sans dossier de travail sous un projet. La racine la plus profonde l'emporte.
    """
    grouped: dict[int, list[tuple[CopilotSession, float]]] = {}
    ignored = 0
    if not location.usable or location.path is None:
        return grouped, ignored
    for journal in list_journals(location.path):
        try:
            modified = journal.lstat().st_mtime
        except OSError:
            continue
        if modified < cutoff:
            continue
        try:
            session = read_copilot_journal(journal, base=location.path)
        except (OSError, ValueError):
            ignored += 1
            continue
        if not session.recognized:
            ignored += 1
            continue
        owners = [(len(roots[i].parts), i) for i in range(len(roots)) if _under(session.cwd, roots[i])]
        if not owners:
            ignored += 1
            continue
        grouped.setdefault(max(owners)[1], []).append((session, modified))
    return grouped, ignored


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def assemble_copilot(
    session: CopilotSession,
    modified: float,
    *,
    project: str,
    now: datetime,
    ledger: Any,
    lookup: Callable[[str], tuple[str, str | None]],
    probe: Any = None,
) -> list[dict[str, Any]]:
    """La session (en tête) puis ses sous-agents, au format des sessions de la flotte ; rien d'inventé.

    *ledger* est le ``_Ledger`` du projet (rôle et tâche d'une session), *lookup* le registre de modèles
    (modèle → ``(fournisseur, palier)``).
    """
    role, task = ledger.role_and_task(session.session_id)
    last_at = session.last_at or _iso(modified)
    entry = (probe or {}).get(session.session_id) or {}
    status = entry.get("status") if hasattr(entry, "get") else None
    live = liveness(
        now=now,
        journal_at=_parse(last_at),
        ended=session.ended,
        probe_status=str(status) if status else None,
        active_window=DEFAULT_ACTIVE_WINDOW,
    )
    state = "ended" if live.state == TERMINE else ("active" if live.state == VIVANT else "silent")
    provider, tier = lookup(session.model) if session.model else ("inconnu", None)
    usage = session.usage()
    pr = ledger.pr_of(task)
    payload: dict[str, Any] = {
        "id": session.session_id,
        "project": project,
        "host": HostId.GITHUB_COPILOT.value,
        "role": role or "other",
        "parent_id": None,
        "task_id": getattr(task, "id", None),
        "pr": pr,
        "worktree": None,
        "branch": None,
        "entrypoint": None,
        "agent": None,
        "started_at": session.started_at or None,
        "last_at": last_at,
        "liveness": state,
        "model": {
            "current": session.model or None,
            "requested": None,
            "picked": None,
            "provider": provider,
            "tier": tier,
            "effort": None,
        },
        "model_changes": [],
        "turns": {"count": session.messages or None, "by_origin": {}},
        "usage": {
            "input": usage["input_tokens"],
            "output": usage["output_tokens"],
            "cache_read": usage["cache_read_tokens"],
            "cost_usd": None,
            "source": usage["source"],
            "premium_requests": usage["premium_requests"],
        },
        "usage_verified": False,
        "note": UNVERIFIED_NOTE,
        "lanes": [],
        "markers": [],
        "tail": [],
        "subagents": [sub.tool_call_id for sub in session.subagents],
        "links": {
            "task": f"/workspace/index.html#executer?task={task.id}" if task is not None else None,
            "pr": pr["url"] if pr is not None else None,
            "resume": None,
            "transcript": session.path,
        },
    }
    rows = [payload]
    for sub in session.subagents:
        sub_provider, sub_tier = lookup(sub.model) if sub.model else ("inconnu", None)
        rows.append(
            {
                **payload,
                "id": sub.tool_call_id,
                "role": "subagent",
                "parent_id": session.session_id,
                "task_id": None,
                "pr": None,
                "agent": {"type": sub.agent_name or None, "description": None},
                "liveness": "ended" if sub.completed else state,
                "model": {**payload["model"], "current": sub.model or None, "provider": sub_provider, "tier": sub_tier},
                "turns": {"count": sub.messages or None, "by_origin": {}},
                "usage": {
                    "input": None,
                    "output": sub.output_tokens if sub.messages else None,
                    "cache_read": None,
                    "cost_usd": None,
                    "source": "messages" if sub.messages else "none",
                    "premium_requests": None,
                },
                "subagents": [],
                "links": {**payload["links"], "task": None, "pr": None},
            }
        )
    return rows
