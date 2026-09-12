"""Per-session counters for the temporal policy layer (issue #429, point 3).

A hook is stateless by itself — every ``grimoire-hook`` invocation is a new
process. Budgets, "first occurrence" approval and cooldowns all need to
remember something *across* calls within the same session, so that memory
has to live on disk. This module is the read/write/reset boundary for it:

- one JSON file per session, at ``_grimoire-output/.runs/session-<id>.json``
  — next to ``standard-state-cache.json`` (:mod:`grimoire.core.standard_state`,
  issue #422), the file this module's atomic-write shape is copied from;
- never a secret or a tool argument, only counters and ISO timestamps (see
  :class:`RuleState`) — the whole point of counting instead of recording;
- best-effort in every direction: a missing, truncated or wrong-version file
  is a *new* session, never an error a hook could fail on, and a failed
  write never raises either.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_RUNS_DIR = Path("_grimoire-output") / ".runs"
_SCHEMA_VERSION = 1
#: Session ids are host-generated (UUIDs, usually) but never trusted as a
#: path component verbatim — one that carried ``/`` or ``..`` would escape
#: ``_grimoire-output/.runs``. Anything outside this charset is hashed instead
#: of sanitised, so two different raw ids can never collide onto one file.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_session_id(session_id: str) -> str:
    session_id = session_id.strip() or "unknown"
    if _SAFE_ID_RE.match(session_id):
        return session_id
    return "h-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def session_state_path(project_root: Path, session_id: str) -> Path:
    return project_root / _RUNS_DIR / f"session-{_safe_session_id(session_id)}.json"


@dataclass
class RuleState:
    """One rule's running counters for the current session."""

    calls: int = 0
    writes: int = 0
    cost_usd: float = 0.0
    approved: bool = False
    """Set the first time a ``require_approval`` rule matched and asked."""
    hits: list[str] = field(default_factory=list)
    """ISO timestamps of past matches, kept for :class:`CooldownRule` windows."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "writes": self.writes,
            "cost_usd": self.cost_usd,
            "approved": self.approved,
            "hits": list(self.hits),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RuleState:
        return cls(
            calls=int(d.get("calls", 0)) if isinstance(d.get("calls"), int | float) else 0,
            writes=int(d.get("writes", 0)) if isinstance(d.get("writes"), int | float) else 0,
            cost_usd=float(d.get("cost_usd", 0.0)) if isinstance(d.get("cost_usd"), int | float) else 0.0,
            approved=bool(d.get("approved", False)),
            hits=[str(h) for h in d.get("hits", []) if isinstance(h, str)],
        )


@dataclass
class SessionState:
    """Everything the temporal layer remembers about one session."""

    session_id: str
    started_at: str
    updated_at: str = ""
    rules: dict[str, RuleState] = field(default_factory=dict)
    schema_version: int = _SCHEMA_VERSION

    def rule_state(self, rule_id: str) -> RuleState:
        return self.rules.setdefault(rule_id, RuleState())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "rules": {rule_id: state.to_dict() for rule_id, state in self.rules.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, session_id: str, started_at: str) -> SessionState:
        rules_raw = d.get("rules", {})
        rules = {
            str(rule_id): RuleState.from_dict(state)
            for rule_id, state in rules_raw.items()
            if isinstance(state, dict)
        } if isinstance(rules_raw, dict) else {}
        return cls(
            session_id=session_id,
            started_at=str(d.get("started_at") or started_at),
            updated_at=str(d.get("updated_at", "")),
            rules=rules,
        )

    @classmethod
    def new(cls, session_id: str, started_at: str) -> SessionState:
        return cls(session_id=session_id, started_at=started_at, updated_at=started_at)


def load_session_state(project_root: Path, session_id: str, *, now_iso: str) -> SessionState:
    """The session's state, or a fresh one — never an exception.

    A missing file, invalid JSON, the wrong ``schema_version`` or any other
    corruption all fall back to :meth:`SessionState.new`: a session the
    engine cannot read from is indistinguishable from one that has not
    written anything yet, by design (see the module docstring).
    """
    path = session_state_path(project_root, session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return SessionState.new(session_id, now_iso)
    try:
        data = json.loads(raw)
    except ValueError:
        return SessionState.new(session_id, now_iso)
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return SessionState.new(session_id, now_iso)
    return SessionState.from_dict(data, session_id=session_id, started_at=now_iso)


def save_session_state(project_root: Path, state: SessionState, *, now_iso: str) -> None:
    """Atomic write (temp file + ``os.replace``), best-effort — mirrors
    ``grimoire.core.standard_state._write_cache_entries`` (issue #422)."""
    state.updated_at = now_iso
    path = session_state_path(project_root, state.session_id)
    payload = json.dumps(state.to_dict(), sort_keys=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def reset_session_state(project_root: Path, session_id: str) -> None:
    """Drop a session's state — called on ``SessionStart`` so a new session
    never inherits a previous one's counters, approvals or cooldowns.

    Best-effort: a file that fails to delete is caught by
    :func:`load_session_state`'s own corruption handling on the very next
    read only if it is actually corrupt; an *un*deleted, still-valid file
    from a reused session id is the one case this cannot paper over, which is
    why hosts are expected to hand out a fresh id per session (they do).
    """
    path = session_state_path(project_root, session_id)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
