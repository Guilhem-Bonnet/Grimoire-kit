"""Le repli automatique de l'usage des sessions dans les tâches (GAO-i12-replier-001).

Avant ce module, seule la commande manuelle ``grimoire task fold-usage`` repliait
un journal de session Claude Code dans le TraceLedger : toutes les tâches closes
affichaient « coût inconnu ». :func:`fold_pending` est le pas, borné et idempotent,
que le tick et ``grimoire task usage --backfill`` partagent :

- il ne regarde que les tâches qui portent ``claim.session_id`` ;
- le journal est cherché dans le dossier du worktree de la tâche, sinon dans
  ``<projects>/*/<session_id>.jsonl`` ; un identifiant hors motif sûr est refusé
  sans toucher au disque ;
- la fenêtre repliée va de la réclamation à maintenant (à la clôture pour une
  tâche terminée : le journal d'une session qui continue ailleurs n'est pas
  attribué à une tâche qui n'y travaille plus) ;
- au plus :data:`MAX_LOGS` journaux et :data:`MAX_BYTES` octets par pas ; un
  journal plus gros que le plafond est reporté avec une ligne de diagnostic.

Un journal absent ou purgé est ``missing`` (« journal de session introuvable »),
jamais un zéro replié.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_BYTES",
    "MAX_LOGS",
    "MISSING_LABEL",
    "FoldOutcome",
    "UsageFoldReport",
    "fold_pending",
    "locate_transcript",
    "transcript_is_missing",
]

MAX_LOGS = 5
MAX_BYTES = 50 * 1024 * 1024
MISSING_LABEL = "journal de session introuvable"

#: Un identifiant de session sûr comme composant de chemin (le même motif que ``workspace_work``).
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_TERMINAL = ("closed", "cancelled", "failed")
#: Un journal introuvable n'est pas cherché plus d'une fois par minute (les cartes du cockpit se rafraîchissent en continu).
_MISSING_TTL = 60.0
_missing_cache: dict[tuple[str, str], tuple[float, bool]] = {}


@dataclass(frozen=True)
class FoldOutcome:
    """Ce qu'un pas a fait d'une tâche : ``folded`` | ``missing`` | ``refused`` | ``deferred`` | ``error``."""

    task_id: str
    status: str
    new_calls: int = 0
    already_folded: int = 0
    size_bytes: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "new_calls": self.new_calls,
            "already_folded": self.already_folded,
            "size_bytes": self.size_bytes,
            "detail": self.detail,
        }


@dataclass
class UsageFoldReport:
    outcomes: list[FoldOutcome] = field(default_factory=list)
    logs: int = 0
    bytes: int = 0
    dry_run: bool = False

    @property
    def new_calls(self) -> int:
        return sum(o.new_calls for o in self.outcomes)

    @property
    def already_folded(self) -> int:
        return sum(o.already_folded for o in self.outcomes)

    def of(self, status: str) -> list[FoldOutcome]:
        return [o for o in self.outcomes if o.status == status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "logs": self.logs,
            "bytes": self.bytes,
            "new_calls": self.new_calls,
            "already_folded": self.already_folded,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def locate_transcript(session_id: str, *, worktree: Path | None = None, home: Path | None = None) -> Path | None:
    """Le journal Claude Code de *session_id*, ou ``None`` (identifiant refusé, ou journal absent).

    Le dossier du *worktree* de la tâche est essayé d'abord (le slug de Claude
    Code est le chemin résolu, tout non-alphanumérique devient ``-``), puis tous
    les dossiers de projets : une session lancée d'ailleurs reste retrouvable.
    """
    if not _SAFE_SESSION_RE.match(session_id or ""):
        return None
    from grimoire.tools.workspace_work import claude_projects_dir

    projects = claude_projects_dir(home)
    if worktree is not None:
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(worktree.resolve()))
        candidate = projects / slug / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    if not projects.is_dir():
        return None
    for candidate in sorted(projects.glob(f"*/{session_id}.jsonl")):
        if candidate.is_file():
            return candidate
    return None


def transcript_is_missing(session_id: str, *, home: Path | None = None) -> bool:
    """Vrai si une session au motif sûr n'a aucun journal sous les dossiers de projets (mémorisé 60 s)."""
    if not _SAFE_SESSION_RE.match(session_id or ""):
        return False
    key = (str(home or ""), session_id)
    now = time.monotonic()
    cached = _missing_cache.get(key)
    if cached is not None and now - cached[0] < _MISSING_TTL:
        return cached[1]
    missing = locate_transcript(session_id, home=home) is None
    _missing_cache[key] = (now, missing)
    return missing


def _claim_window(service: Any, task: Any) -> tuple[str, str]:
    """``(since, until)`` : la dernière réclamation jusqu'à maintenant (à la dernière trace du ledger si la tâche est terminée)."""
    events = service.ledger.events_for(task.id)
    since = ""
    for event in reversed(events):
        if event.event_type == "task.transitioned" and event.payload.get("to_state") == "claimed":
            since = event.created_at
            break
    until = ""
    if task.status.value in _TERMINAL and events:
        until = events[-1].created_at
    return since, until or datetime.now(UTC).isoformat()


def fold_pending(
    service: Any,
    *,
    home: Path | None = None,
    max_logs: int = MAX_LOGS,
    max_bytes: int = MAX_BYTES,
    only_terminal: bool = False,
    dry_run: bool = False,
) -> UsageFoldReport:
    """Replie, pour chaque tâche à session rattachée dont l'usage n'est pas replié, le journal de sa session.

    Idempotent : rejoué, un pas n'ajoute que les appels nouveaux (``already_folded``
    les compte). Une tâche terminée déjà repliée n'est plus lue ; une tâche
    vivante l'est à chaque pas (son journal grandit), après celles jamais
    repliées. *max_logs* ≤ 0 ou *max_bytes* ≤ 0 : pas de plafond de ce côté.
    *only_terminal* : les tâches closes seulement (``--backfill``). *dry_run* :
    compte les journaux et les octets qui seraient lus, n'écrit rien.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.missions.task_worktree import find_task_worktree
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import SOURCE_CLAUDE_TRANSCRIPT, SOURCE_TAG_PREFIX, fold_claude_transcript

    report = UsageFoldReport(dry_run=dry_run)
    traces_path = service.home_root / TRACES_DIR
    ledger = TraceLedger(traces_path)
    has_traces = (traces_path / "traces.jsonl").is_file()
    folded_tasks = {
        t.task_id
        for t in (ledger.list_traces() if has_traces else [])
        if f"{SOURCE_TAG_PREFIX}{SOURCE_CLAUDE_TRANSCRIPT}" in t.tags
    }

    candidates = []
    for task in service.list_tasks():
        if task.claim is None or not task.claim.session_id:
            continue
        terminal = task.status.value in _TERMINAL
        if only_terminal and not terminal:
            continue
        if task.id in folded_tasks and terminal:
            continue
        candidates.append(task)
    candidates.sort(key=lambda t: (t.id in folded_tasks, t.id))

    for task in candidates:
        session_id = task.claim.session_id
        if not _SAFE_SESSION_RE.match(session_id):
            report.outcomes.append(FoldOutcome(task.id, "refused", detail=f"identifiant de session refusé : {session_id!r}"))
            continue
        worktree = find_task_worktree(service, task.id)
        path = locate_transcript(session_id, worktree=worktree.path if worktree else None, home=home)
        if path is None:
            report.outcomes.append(FoldOutcome(task.id, "missing", detail=MISSING_LABEL))
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            report.outcomes.append(FoldOutcome(task.id, "error", detail=f"{MISSING_LABEL} ({exc})"))
            continue
        if max_bytes > 0 and size > max_bytes:
            report.outcomes.append(
                FoldOutcome(
                    task.id, "deferred", size_bytes=size,
                    detail=f"journal de {size / 1_048_576:.0f} Mo reporté (plafond {max_bytes / 1_048_576:.0f} Mo par journal)",
                )
            )
            continue
        if (max_logs > 0 and report.logs >= max_logs) or (max_bytes > 0 and report.bytes + size > max_bytes):
            report.outcomes.append(FoldOutcome(task.id, "deferred", size_bytes=size, detail="plafond du pas atteint, repris au suivant"))
            continue
        report.logs += 1
        report.bytes += size
        if dry_run:
            report.outcomes.append(FoldOutcome(task.id, "folded", size_bytes=size, detail="lu à blanc"))
            continue
        since, until = _claim_window(service, task)
        try:
            result = fold_claude_transcript(
                ledger, path, task_id=task.id, mission_id=task.mission_id, since=since, until=until
            )
        except (OSError, ValueError) as exc:
            report.outcomes.append(FoldOutcome(task.id, "error", size_bytes=size, detail=f"{type(exc).__name__}: {exc}"))
            continue
        report.outcomes.append(
            FoldOutcome(task.id, "folded", result.new_calls, result.already_folded, size)
        )
    return report
