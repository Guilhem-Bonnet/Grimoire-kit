"""P2P exchange guard — MAST FM-1.3/FM-1.5 counted in code (B11).

``framework/agent-mesh-network.md:244`` declares a governance rule —
``max_p2p_without_sog: 5`` — that no code ever counted: agents were free to
exchange peer-to-peer messages indefinitely without the Smart Orchestrator
Gateway (SOG) ever being looped back in. This module is the counter.

``framework/tools/message-bus.py`` is frozen (see ``framework/FREEZE.md``):
this module never imports it and adds no line to it. It only consumes the
same shape a caller already has in hand from a bus message — the ``sender``
and ``recipient`` fields of ``AgentMessage`` in that file — so a hook
wrapping ``MessageBus.send()``, or a CLI/script replaying a message log,
can call :func:`observe_p2p_message` per message without this module ever
touching the bus itself. ``AgentMessage`` carries no explicit "thread"
field; the closest analogue, ``correlation_id``, ties a request to its
reply but not a whole conversation, so the guard tracks exchanges by the
unordered *pair* of agents talking — that is what
"deux agents" (framework/agent-mesh-network.md) means in practice.

State is persisted to a small JSON file under the given root, mirroring
``RuntimeKernel``'s JSONL persistence pattern in ``kernel.py`` — a hook
typically runs as a fresh subprocess per tool call, so an in-memory-only
counter would never see more than one message.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# framework/agent-mesh-network.md:244 — "messages avant notification SOG obligatoire"
DEFAULT_MAX_P2P_WITHOUT_SOG = 5

# Identities that count as "passage par le SOG" when they appear as either
# side of a message. No such constant exists elsewhere in the codebase
# (grep confirms no `sog` identifier in src/); this is this guard's own
# working definition, overridable via `sog_agent_ids=` or config.
DEFAULT_SOG_AGENT_IDS = frozenset({"sog", "grimoire-master", "concierge", "orchestrator"})

_STATE_FILENAME = "p2p_guard_state.json"
_EVENTS_FILENAME = "p2p_guard_events.jsonl"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def load_p2p_guard_config(project_root: Path) -> dict[str, Any]:
    """Read an optional ``max_p2p_without_sog`` override from project config.

    Mirrors the ``messaging`` section that ``load_bus_config`` reads in the
    frozen ``framework/tools/message-bus.py`` (same files, same section),
    without importing that module. Looks for
    ``messaging.governance.max_p2p_without_sog`` in ``project-context.yaml``
    or ``grimoire.yaml`` at the project root. Returns ``{}`` if PyYAML, the
    files, or the section are absent — callers fall back to
    :data:`DEFAULT_MAX_P2P_WITHOUT_SOG`.
    """
    try:
        import yaml
    except ImportError:
        return {}
    for candidate in ("project-context.yaml", "grimoire.yaml"):
        path = project_root / candidate
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}
        governance = (data.get("messaging", {}) or {}).get("governance", {}) or {}
        if isinstance(governance, dict):
            return governance
        return {}
    return {}


class P2PGuard:
    """Counts consecutive P2P exchanges per agent pair, persisted to disk.

    Call :meth:`observe` for every message a caller sees pass on the bus
    (sender, recipient). A message where either side is a SOG identity is
    "passage par le SOG": it does not count as a P2P exchange, and it
    resets the counter of every pair the non-SOG side is part of — the
    agent just looped the SOG back in, so its P2P clock restarts with
    whichever peer it was talking to. Any other message increments that
    pair's counter; reaching ``max_p2p_without_sog`` returns ``"escalate"``,
    appends a ``p2p.sog_escalation`` event to the journal, and resets the
    pair's counter so the same pair does not re-escalate on every
    subsequent message while still unresolved.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_p2p_without_sog: int = DEFAULT_MAX_P2P_WITHOUT_SOG,
        sog_agent_ids: Iterable[str] = DEFAULT_SOG_AGENT_IDS,
    ) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._state_path = root / _STATE_FILENAME
        self._events_path = root / _EVENTS_FILENAME
        self.max_p2p_without_sog = max_p2p_without_sog
        self._sog_agent_ids = {a.lower() for a in sog_agent_ids}

    def _is_sog(self, agent_id: str) -> bool:
        return agent_id.lower() in self._sog_agent_ids

    def _load_counts(self) -> dict[str, int]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_counts(self, counts: dict[str, int]) -> None:
        self._state_path.write_text(json.dumps(counts, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def observe(self, sender: str, recipient: str) -> str:
        """Observe one P2P message; return ``"ok"`` or ``"escalate"``."""
        sender = (sender or "").strip()
        recipient = (recipient or "").strip()
        if not sender or not recipient or sender == "*" or recipient == "*":
            return "ok"  # broadcast, or malformed: not a P2P exchange between two agents

        counts = self._load_counts()

        if self._is_sog(sender) or self._is_sog(recipient):
            agent = recipient if self._is_sog(sender) else sender
            changed = False
            for key in list(counts):
                if agent in key.split("|") and counts[key] != 0:
                    counts[key] = 0
                    changed = True
            if changed:
                self._save_counts(counts)
            return "ok"

        key = _pair_key(sender, recipient)
        count = counts.get(key, 0) + 1
        if count >= self.max_p2p_without_sog:
            counts[key] = 0
            self._save_counts(counts)
            self._emit_escalation(sender, recipient, count)
            return "escalate"
        counts[key] = count
        self._save_counts(counts)
        return "ok"

    def count_for(self, agent_a: str, agent_b: str) -> int:
        return self._load_counts().get(_pair_key(agent_a, agent_b), 0)

    def _emit_escalation(self, sender: str, recipient: str, count: int) -> None:
        event = {
            "id": f"evt-p2p-{uuid.uuid4().hex[:12]}",
            "event_type": "p2p.sog_escalation",
            "pair": sorted((sender, recipient)),
            "count": count,
            "threshold": self.max_p2p_without_sog,
            "decision": "escalate",
            "created_at": _now_iso(),
        }
        with open(self._events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def list_events(self) -> list[dict[str, Any]]:
        if not self._events_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def observe_p2p_message(
    sender: str,
    recipient: str,
    *,
    state_root: Path,
    max_p2p_without_sog: int = DEFAULT_MAX_P2P_WITHOUT_SOG,
    sog_agent_ids: Iterable[str] = DEFAULT_SOG_AGENT_IDS,
) -> str:
    """Simple entry point for a hook or CLI command.

    Observes one bus exchange (``sender``, ``recipient`` — the fields a
    caller already reads off ``AgentMessage`` from the frozen
    ``framework/tools/message-bus.py``) against the P2P guard state under
    ``state_root``, and returns the decision: ``"ok"`` or ``"escalate"``.
    """
    guard = P2PGuard(state_root, max_p2p_without_sog=max_p2p_without_sog, sog_agent_ids=sog_agent_ids)
    return guard.observe(sender, recipient)
