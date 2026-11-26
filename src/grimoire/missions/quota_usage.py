"""La consommation des sessions lancées par l'orchestrateur, écrite au ledger (``quota.consumed``).

Avant ce module, l'admission lisait ``quota.consumed`` pour tenir un plafond ``quota.tokens_per_day`` et
aucun code du kit ne l'écrivait : sous un plafond fini la consommation du jour était « inconnue » et tout était
refusé par prudence. :func:`record_consumption` lit le journal de chaque session que le tick a lancée (worker,
relecteur, arbitre, session orchestrateur) et écrit ce qu'elle a consommé.

Règles :

* **Une session n'est comptée qu'une fois.** La clé est l'identifiant de la session (le condensé SHA-256 pour
  les rôles dont le ledger ne garde que le condensé). L'événement porte le *delta* depuis le dernier événement de
  la session et le total cumulé : rejouer le tick sans que le journal ait grandi n'écrit rien, et une session qui
  continue ne se compte pas deux fois. Le jour d'un delta est celui de son écriture.
* **Borne basse.** Le kit voit le journal de l'hôte, pas la facture : ``status`` vaut ``lower_bound``. Un coût
  inconnu pour un appel (modèle non tarifé) n'est jamais compté zéro : ``cost_status`` vaut alors ``lower_bound``
  ou ``unknown``.
* **Jamais de chiffre inventé.** Journal introuvable, illisible, trop gros, ou d'un hôte non lu : un événement
  ``status: unknown`` avec sa ``reason``, sans ``tokens``. Il est écrit une fois, et remplacé par un événement
  connu dès que le journal devient lisible. Une session lancée depuis moins de :data:`GRACE` n'est pas déclarée
  inconnue : son journal peut ne pas encore exister.
* ``tokens`` est le total que ``grimoire task usage`` rapporte : entrée, sortie, cache lu et cache écrit.

Ce module n'a aucune voie d'écriture à la main : il n'écrit que ce que les journaux de session disent, et la
lecture (``queue rules``, cockpit) ne l'appelle pas.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.lease import parse_instant
from grimoire.missions.usage_rules import QUOTA_CONSUMED_EVENT, SESSION_EVENTS

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = ["GRACE", "MAX_BYTES", "MAX_SESSIONS", "SESSION_EVENTS", "ConsumedOutcome", "record_consumption"]

GRACE = timedelta(minutes=10)
#: Au plus ce nombre de journaux lus par pas, et au plus cette taille chacun.
MAX_SESSIONS = 20
MAX_BYTES = 50 * 1024 * 1024
_TERMINAL = ("closed", "cancelled", "failed")
_SAFE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_ACTOR = "tick"


@dataclass(frozen=True, slots=True)
class ConsumedOutcome:
    session: str
    role: str
    task_id: str
    status: str  # recorded | unknown | unchanged | skipped
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"session": self.session, "role": self.role, "task_id": self.task_id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class _Session:
    key: str
    role: str
    task_id: str
    model: str
    session_id: str
    cwd: str
    launched_at: str
    provider: str


def _sessions(events: list[Any]) -> dict[str, _Session]:
    found: dict[str, _Session] = {}
    for event in events:
        role = SESSION_EVENTS.get(event.event_type)
        if role is None:
            continue
        payload = event.payload
        session_id = str(payload.get("session_id") or "")
        key = session_id or str(payload.get("session_sha256") or "")
        if not key or key in found:
            continue
        task = "" if role == "orchestrator" else event.entity_id
        found[key] = _Session(
            key, role, task, str(payload.get("model") or ""), session_id, str(payload.get("cwd") or ""),
            event.created_at, str(payload.get("provider") or ""),
        )
    return found


@dataclass(slots=True)
class _Seen:
    total: int = 0
    cost: float = 0.0
    status: str = ""
    reason: str = ""


def _seen(events: list[Any]) -> dict[str, _Seen]:
    seen: dict[str, _Seen] = {}
    for event in events:
        if event.event_type != QUOTA_CONSUMED_EVENT:
            continue
        key = str(event.payload.get("session") or "")
        if not key:
            continue
        mine = seen.setdefault(key, _Seen())
        if event.payload.get("status") == "unknown":
            if not mine.status:
                mine.status, mine.reason = "unknown", str(event.payload.get("reason") or "")
            continue
        total = event.payload.get("session_total_tokens")
        if isinstance(total, int) and not isinstance(total, bool):
            mine.total = max(mine.total, total)
            cost = event.payload.get("session_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                mine.cost = max(mine.cost, float(cost))
            mine.status, mine.reason = "known", ""
    return seen


def _digest_of(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _locate(session: _Session, home: Path | None) -> Path | None:
    from grimoire.missions.usage_fold import locate_transcript
    from grimoire.tools.workspace_work import claude_projects_dir

    cwd = Path(session.cwd) if session.cwd else None
    if session.session_id:
        return locate_transcript(session.session_id, worktree=cwd, home=home)
    if cwd is None:
        return None
    folder = claude_projects_dir(home) / re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.glob("*.jsonl")):
        if _SAFE_RE.match(candidate.stem) and _digest_of(candidate.stem) == session.key:
            return candidate
    return None


def _measure(path: Path, root: Path) -> dict[str, Any]:
    from grimoire.providers.registry import read_model_pricing
    from grimoire.traces.transcript_usage import call_cost_usd, read_claude_transcript

    pricing_for = read_model_pricing(root).get
    read = read_claude_transcript(path)
    cost, unpriced = 0.0, 0
    models: dict[str, int] = {}
    for call in read.calls:
        value = call_cost_usd(call, pricing_for(call.model) if call.model else None)
        if value is None:
            unpriced += 1
        else:
            cost += value
        if call.model:
            models[call.model] = models.get(call.model, 0) + 1
    totals = {
        "input_tokens": sum(c.input_tokens for c in read.calls),
        "output_tokens": sum(c.output_tokens for c in read.calls),
        "cache_read_tokens": sum(c.cache_read_tokens for c in read.calls),
        "cache_creation_tokens": sum(c.cache_creation_tokens for c in read.calls),
    }
    if not read.calls or unpriced == 0:
        cost_status = "exact"
    elif unpriced < len(read.calls):
        cost_status = "lower_bound"
    else:
        cost_status = "unknown"
    return {
        **totals, "total": sum(totals.values()), "calls": len(read.calls), "cost": cost, "cost_status": cost_status,
        "models": sorted(models, key=lambda m: -models[m]),
    }


def record_consumption(
    service: TaskService, *, home: Path | None = None, now: datetime | None = None,
    max_sessions: int = MAX_SESSIONS, max_bytes: int = MAX_BYTES,
) -> list[ConsumedOutcome]:
    """Écrit ``quota.consumed`` pour chaque session lancée dont le journal a grandi (idempotent, borné)."""
    ledger = service.ledger
    events = ledger.list_events()
    sessions = _sessions(events)
    seen = _seen(events)
    moment = now or datetime.now(UTC)
    outcomes: list[ConsumedOutcome] = []
    reads = 0
    # Les sessions jamais comptées d'abord : celles déjà comptées, relues à chaque pas, ne les affament pas.
    for key, session in sorted(sessions.items(), key=lambda kv: kv[0] in seen):
        mine = seen.get(key, _Seen())
        task = ledger.get_task(session.task_id) if session.task_id else None
        launched = parse_instant(session.launched_at)
        old = launched is not None and moment - launched > timedelta(days=2)
        finished = task is not None and task.status.value in _TERMINAL
        if mine.status == "known" and old and (finished or task is None):
            continue  # une session ancienne, déjà comptée et rendue n'est plus relue
        if reads >= max_sessions:
            outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "skipped", "plafond du pas atteint, repris au suivant"))
            continue
        path = _locate(session, home)
        reason = ""
        measure: dict[str, Any] | None = None
        if path is None:
            reason = f"journal de session introuvable ou d'un hôte non lu{f' ({session.provider})' if session.provider else ''}"
        else:
            reads += 1
            try:
                size = path.stat().st_size
                if size > max_bytes:
                    reason = f"journal de {size // 1_048_576} Mo au-dessus du plafond de lecture ({max_bytes // 1_048_576} Mo)"
                else:
                    measure = _measure(path, service.home_root)
            except OSError as exc:
                reason = f"journal illisible ({type(exc).__name__})"
        if measure is None:
            young = launched is not None and moment - launched < GRACE
            if young or mine.status == "known" or (mine.status == "unknown" and mine.reason == reason):
                outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged", reason))
                continue
            ledger.append_event(
                QUOTA_CONSUMED_EVENT, session.task_id or session.role, "task", _ACTOR,
                {"session": key, "role": session.role, "task_id": session.task_id, "model": session.model,
                 "status": "unknown", "reason": reason, "tokens": None, "cost_usd": None, "cost_status": "unknown"},
            )
            outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unknown", reason))
            continue
        total = int(measure["total"])
        if total <= mine.total and mine.status != "unknown":
            outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "unchanged"))
            continue
        cost_known = measure["cost_status"] != "unknown"
        delta_cost = max(measure["cost"] - mine.cost, 0.0) if cost_known else None
        ledger.append_event(
            QUOTA_CONSUMED_EVENT, session.task_id or session.role, "task", _ACTOR,
            {"session": key, "role": session.role, "task_id": session.task_id,
             "model": (measure["models"] or [session.model])[0], "models": measure["models"],
             "status": "lower_bound", "tokens": total - mine.total, "session_total_tokens": total,
             "input_tokens": measure["input_tokens"], "output_tokens": measure["output_tokens"],
             "cache_read_tokens": measure["cache_read_tokens"], "cache_creation_tokens": measure["cache_creation_tokens"],
             "calls": measure["calls"], "cost_usd": delta_cost, "session_cost_usd": measure["cost"] if cost_known else None,
             "cost_status": measure["cost_status"]},
        )
        outcomes.append(ConsumedOutcome(key[:12], session.role, session.task_id, "recorded", f"+{total - mine.total} tokens"))
    return outcomes
