"""Stigmergy — pheromone-based agent coordination board.

Agents emit typed pheromones (NEED, ALERT, OPPORTUNITY, PROGRESS,
COMPLETE, BLOCK) that evaporate over time.  Other agents sense and
amplify signals to coordinate without direct communication.

Usage::

    from grimoire.tools.stigmergy import Stigmergy

    st = Stigmergy(Path("."))
    board = st.run(action="emit", ptype="ALERT", location="src/auth",
                    text="Security review needed", emitter="dev")
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import pow as mpow
from pathlib import Path
from typing import Any

from grimoire.tools._common import GrimoireTool

# ── Constants ─────────────────────────────────────────────────────────────────

PHEROMONE_FILE = "pheromone-board.json"
VALID_TYPES = frozenset({"NEED", "ALERT", "OPPORTUNITY", "PROGRESS", "COMPLETE", "BLOCK"})

DEFAULT_HALF_LIFE_HOURS = 72.0
DETECTION_THRESHOLD = 0.05
REINFORCEMENT_BOOST = 0.2
MAX_INTENSITY = 1.0
DEFAULT_INTENSITY = 0.7


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class Pheromone:
    """A single pheromone signal."""

    pheromone_id: str
    pheromone_type: str
    location: str
    text: str
    emitter: str
    timestamp: str
    intensity: float = DEFAULT_INTENSITY
    tags: list[str] = field(default_factory=list)
    reinforcements: int = 0
    reinforced_by: list[str] = field(default_factory=list)
    resolved: bool = False
    resolved_by: str = ""
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pheromone_id": self.pheromone_id,
            "pheromone_type": self.pheromone_type,
            "location": self.location,
            "text": self.text,
            "emitter": self.emitter,
            "timestamp": self.timestamp,
            "intensity": round(self.intensity, 4),
            "tags": self.tags,
            "reinforcements": self.reinforcements,
            "reinforced_by": self.reinforced_by,
            "resolved": self.resolved,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pheromone:
        return cls(
            pheromone_id=d.get("pheromone_id", ""),
            pheromone_type=d.get("pheromone_type", "NEED"),
            location=d.get("location", ""),
            text=d.get("text", ""),
            emitter=d.get("emitter", ""),
            timestamp=d.get("timestamp", ""),
            intensity=d.get("intensity", DEFAULT_INTENSITY),
            tags=d.get("tags", []),
            reinforcements=d.get("reinforcements", 0),
            reinforced_by=d.get("reinforced_by", []),
            resolved=d.get("resolved", False),
            resolved_by=d.get("resolved_by", ""),
            resolved_at=d.get("resolved_at", ""),
        )


@dataclass
class PheromoneBoard:
    """The project pheromone board."""

    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS
    pheromones: list[Pheromone] = field(default_factory=list)
    total_emitted: int = 0
    total_evaporated: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "half_life_hours": self.half_life_hours,
            "pheromones": [p.to_dict() for p in self.pheromones],
            "total_emitted": self.total_emitted,
            "total_evaporated": self.total_evaporated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PheromoneBoard:
        return cls(
            half_life_hours=d.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS),
            pheromones=[Pheromone.from_dict(p) for p in d.get("pheromones", [])],
            total_emitted=d.get("total_emitted", 0),
            total_evaporated=d.get("total_evaporated", 0),
        )


@dataclass(frozen=True, slots=True)
class TrailPattern:
    """An emergent coordination pattern."""

    pattern_type: str  # hot-zone, convergence, bottleneck
    location: str
    description: str
    involved_agents: tuple[str, ...] = ()
    pheromone_count: int = 0
    avg_intensity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "location": self.location,
            "description": self.description,
            "involved_agents": list(self.involved_agents),
            "pheromone_count": self.pheromone_count,
            "avg_intensity": round(self.avg_intensity, 2),
        }


# ── Persistence ───────────────────────────────────────────────────────────────

def _board_path(project_root: Path) -> Path:
    return project_root / "_grimoire-output" / PHEROMONE_FILE


def load_board(project_root: Path) -> PheromoneBoard:
    path = _board_path(project_root)
    if not path.exists():
        return PheromoneBoard()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PheromoneBoard.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return PheromoneBoard()


def save_board(project_root: Path, board: PheromoneBoard) -> None:
    path = _board_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(board.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Core Logic ────────────────────────────────────────────────────────────────

def _generate_id(ptype: str, location: str, text: str, timestamp: str) -> str:
    raw = f"{ptype}:{location}:{text}:{timestamp}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"PH-{h}"


def compute_intensity(pheromone: Pheromone, half_life: float,
                      now: datetime | None = None) -> float:
    """Compute current intensity after time decay."""
    if now is None:
        now = datetime.now(tz=UTC)
    try:
        emit = datetime.fromisoformat(pheromone.timestamp)
        if emit.tzinfo is None:
            emit = emit.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return pheromone.intensity
    age_h = (now - emit).total_seconds() / 3600.0
    if age_h <= 0:
        return pheromone.intensity
    return pheromone.intensity * mpow(0.5, age_h / half_life)


def emit_pheromone(board: PheromoneBoard, ptype: str, location: str,
                   text: str, emitter: str, tags: list[str] | None = None,
                   intensity: float = DEFAULT_INTENSITY) -> Pheromone:
    """Emit a pheromone onto the board."""
    now = datetime.now(tz=UTC).isoformat()
    p = Pheromone(
        pheromone_id=_generate_id(ptype, location, text, now),
        pheromone_type=ptype, location=location, text=text,
        emitter=emitter, timestamp=now,
        intensity=min(max(intensity, 0.0), MAX_INTENSITY),
        tags=tags or [],
    )
    board.pheromones.append(p)
    board.total_emitted += 1
    return p


def amplify_pheromone(board: PheromoneBoard, pheromone_id: str,
                      agent: str) -> Pheromone | None:
    """Reinforce an existing pheromone."""
    for p in board.pheromones:
        if p.pheromone_id == pheromone_id:
            p.intensity = min(p.intensity + REINFORCEMENT_BOOST, MAX_INTENSITY)
            p.reinforcements += 1
            if agent not in p.reinforced_by:
                p.reinforced_by.append(agent)
            return p
    return None


def resolve_pheromone(board: PheromoneBoard, pheromone_id: str,
                      agent: str) -> Pheromone | None:
    """Mark a pheromone as resolved."""
    for p in board.pheromones:
        if p.pheromone_id == pheromone_id:
            p.resolved = True
            p.resolved_by = agent
            p.resolved_at = datetime.now(tz=UTC).isoformat()
            return p
    return None


def sense_pheromones(board: PheromoneBoard, ptype: str | None = None,
                     location: str | None = None, tag: str | None = None,
                     now: datetime | None = None) -> list[tuple[Pheromone, float]]:
    """Detect active pheromones above detection threshold."""
    if now is None:
        now = datetime.now(tz=UTC)
    results: list[tuple[Pheromone, float]] = []
    for p in board.pheromones:
        if p.resolved:
            continue
        current = compute_intensity(p, board.half_life_hours, now)
        if current < DETECTION_THRESHOLD:
            continue
        if ptype and p.pheromone_type != ptype:
            continue
        if location and location.lower() not in p.location.lower():
            continue
        if tag and tag.lower() not in [t.lower() for t in p.tags]:
            continue
        results.append((p, current))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def evaporate(board: PheromoneBoard,
              now: datetime | None = None) -> tuple[PheromoneBoard, int]:
    """Remove pheromones below detection threshold."""
    if now is None:
        now = datetime.now(tz=UTC)
    surviving: list[Pheromone] = []
    removed = 0
    for p in board.pheromones:
        current = compute_intensity(p, board.half_life_hours, now)
        if current >= DETECTION_THRESHOLD and not p.resolved:
            surviving.append(p)
        else:
            removed += 1
    board.pheromones = surviving
    board.total_evaporated += removed
    return board, removed


def analyze_trails(board: PheromoneBoard,
                   now: datetime | None = None) -> list[TrailPattern]:
    """Detect emergent coordination patterns."""
    if now is None:
        now = datetime.now(tz=UTC)
    patterns: list[TrailPattern] = []
    by_loc: dict[str, list[tuple[Pheromone, float]]] = defaultdict(list)

    for p in board.pheromones:
        if p.resolved:
            continue
        current = compute_intensity(p, board.half_life_hours, now)
        if current >= DETECTION_THRESHOLD:
            by_loc[p.location].append((p, current))

    for loc, items in by_loc.items():
        agents = tuple(sorted({p.emitter for p, _ in items}))
        avg = sum(i for _, i in items) / len(items)

        if len(items) >= 3:
            patterns.append(TrailPattern(
                pattern_type="hot-zone", location=loc,
                description=f"{len(items)} active signals — intense activity zone",
                involved_agents=agents, pheromone_count=len(items),
                avg_intensity=avg,
            ))
        if len(agents) >= 2:
            patterns.append(TrailPattern(
                pattern_type="convergence", location=loc,
                description=f"{len(agents)} agents converging on this zone",
                involved_agents=agents, pheromone_count=len(items),
                avg_intensity=avg,
            ))
        blocks = [p for p, _ in items if p.pheromone_type == "BLOCK"]
        if len(blocks) >= 2:
            patterns.append(TrailPattern(
                pattern_type="bottleneck", location=loc,
                description=f"{len(blocks)} blocks — coordination bottleneck",
                involved_agents=agents, pheromone_count=len(blocks),
                avg_intensity=avg,
            ))

    return patterns


# ── Tool ──────────────────────────────────────────────────────────────────────

class Stigmergy(GrimoireTool):
    """Pheromone-based agent coordination tool."""

    def run(self, **kwargs: Any) -> PheromoneBoard:
        action = kwargs.get("action", "sense")
        board = load_board(self._project_root)

        if action == "emit":
            emit_pheromone(
                board,
                ptype=kwargs.get("ptype", "NEED"),
                location=kwargs.get("location", ""),
                text=kwargs.get("text", ""),
                emitter=kwargs.get("emitter", ""),
                tags=kwargs.get("tags"),
                intensity=kwargs.get("intensity", DEFAULT_INTENSITY),
            )
            save_board(self._project_root, board)
        elif action == "amplify":
            amplify_pheromone(board, kwargs.get("pheromone_id", ""),
                              kwargs.get("agent", ""))
            save_board(self._project_root, board)
        elif action == "resolve":
            resolve_pheromone(board, kwargs.get("pheromone_id", ""),
                              kwargs.get("agent", ""))
            save_board(self._project_root, board)
        elif action == "evaporate":
            evaporate(board)
            save_board(self._project_root, board)

        return board
