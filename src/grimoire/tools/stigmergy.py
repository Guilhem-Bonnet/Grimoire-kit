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
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import pow as mpow
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from grimoire.tools._common import GrimoireTool

# ── Constants ─────────────────────────────────────────────────────────────────


class BulkSignal(TypedDict):
    ptype: str
    location: NotRequired[str]
    text: NotRequired[str]
    emitter: NotRequired[str]
    tags: NotRequired[list[str]]
    intensity: NotRequired[float]

PHEROMONE_FILE = "pheromone-board.json"
EVENTS_FILE = "stigmergy-events.jsonl"
BOARD_SCHEMA_VERSION = "1.0.0"
EVENTS_SCHEMA_VERSION = 1
EVENTS_MAX_LINES = 2000  # KNO-01 : le journal ne grandit pas indéfiniment
VALID_TYPES = frozenset({"NEED", "ALERT", "OPPORTUNITY", "PROGRESS", "COMPLETE", "BLOCK"})

DEFAULT_HALF_LIFE_HOURS = 72.0
DETECTION_THRESHOLD = 0.05
REINFORCEMENT_BOOST = 0.2
MAX_INTENSITY = 1.0
DEFAULT_INTENSITY = 0.7

# Hypothèse de promotion beta→stable de la stigmergie (QUA-13 : la mesure sert
# une décision). Exposée telle quelle par /api/stigmergy (bloc behavior) pour
# que les ratios soient lus contre la thèse qu'ils testent.
STIGMERGY_TARGET_USEFUL_RATIO = 0.4
STIGMERGY_PROMOTION_MIN_EMITTED = 20
STIGMERGY_PROMOTION_HYPOTHESIS = (
    "Le board coordonne réellement si au moins 40 % des signaux émis "
    "produisent une coordination utile (résolution ou relais), mesuré "
    "sur au moins 20 émissions."
)

TYPE_ICONS = {
    "NEED":        "[i]",
    "ALERT":       "[!!]",
    "OPPORTUNITY": "[ok]",
    "PROGRESS":    "[!]",
    "COMPLETE":    "[OK]",
    "BLOCK":       "",
}

# Per-type half-life (hours) — overrides board.half_life_hours
TYPE_HALF_LIFE: dict[str, float] = {
    "COMPLETE":    24.0,   # Done signal: evaporates quickly
    "PROGRESS":    48.0,   # Work in progress: moderate lifespan
    "NEED":        72.0,   # Collaboration request: default
    "ALERT":       72.0,   # Alert: default
    "BLOCK":       96.0,   # Blocker: stays longer
    "OPPORTUNITY": 168.0,  # Opportunity: persists 7 days
}


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


def atomic_write_text(path: Path, text: str) -> None:
    """Écriture atomique : fichier temporaire + ``os.replace`` (RUN-14).

    Un crash en cours d'écriture ne laisse jamais un board/état à moitié écrit
    et corrompu — l'ancien contenu reste intact jusqu'au rename atomique.
    """
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def log_event(project_root: Path, action: str, *, source: str = "cli",
              **fields: Any) -> None:
    """Journal JSONL des actes stigmergiques (fail-open, borné — KNO-01).

    Même fichier que les hooks (``_grimoire-output/stigmergy-events.jsonl``) :
    la base des métriques de promotion beta→stable. Le journal est plafonné
    (``EVENTS_MAX_LINES``) et versionné — pas d'anti-pattern « tout-indexer ».
    """
    try:
        path = project_root / "_grimoire-output" / EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"v": EVENTS_SCHEMA_VERSION,
                 "ts": datetime.now(tz=UTC).isoformat(),
                 "action": action, "source": source, **fields}
        line = json.dumps(entry, ensure_ascii=False)
        existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        existing.append(line)
        if len(existing) > EVENTS_MAX_LINES:
            existing = existing[-EVENTS_MAX_LINES:]
            atomic_write_text(path, "\n".join(existing) + "\n")
        else:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def read_events(project_root: Path) -> list[dict[str, Any]]:
    """Relit le journal comportemental (liste vide si absent/corrompu)."""
    path = project_root / "_grimoire-output" / EVENTS_FILE
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
    except OSError:
        return []
    return entries


def load_board(project_root: Path) -> PheromoneBoard:
    """The project's board, or an empty one when there is none.

    A board that exists but cannot be parsed is moved aside, never read as
    empty: the next ``save_board`` would otherwise overwrite it, and a single
    truncated write (full disk, two hooks racing without ``flock``) would
    erase every signal ever emitted, with ``deposit_pheromone`` returning a
    ``Pheromone`` as if nothing had happened.
    """
    path = _board_path(project_root)
    if not path.exists():
        return PheromoneBoard()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PheromoneBoard.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        _quarantine_board(path, exc)
        return PheromoneBoard()


def _quarantine_board(path: Path, exc: Exception) -> None:
    """Keep the unreadable board under a dated ``.corrupt-*`` name, and say so."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    aside = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        path.replace(aside)
        where = f"mis de côté sous {aside.name}"
    except OSError as move_exc:
        where = f"impossible de le mettre de côté ({move_exc})"
    print(
        f"[stigmergy] pheromone board corrompu ({type(exc).__name__}: {exc}) — {where} ; "
        "un board vide repart de zéro.",
        file=sys.stderr,
    )


def save_board(project_root: Path, board: PheromoneBoard) -> None:
    atomic_write_text(
        _board_path(project_root),
        json.dumps(board.to_dict(), indent=2, ensure_ascii=False),
    )


@contextmanager
def _board_lock(project_root: Path) -> Iterator[None]:
    """Verrou exclusif inter-process sur le board (POSIX/Windows, best-effort).

    Évite l'anti-pattern « lost-update » quand plusieurs hooks PostToolUse
    (subagents parallèles) font un read-modify-write concurrent. Dégrade en
    no-op si le verrouillage n'est pas disponible — jamais bloquant.
    """
    path = _board_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    fh = None
    try:
        fh = lock_path.open("w", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass  # Windows ou FS sans flock : best-effort
        yield
    finally:
        if fh is not None:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            fh.close()


def update_board(
    project_root: Path, mutator: Callable[[PheromoneBoard], object]
) -> None:
    """Read-modify-write atomique et verrouillé du board.

    ``mutator`` reçoit le board chargé sous verrou et le modifie en place ;
    la sauvegarde (atomique) et le déverrouillage sont garantis.
    """
    with _board_lock(project_root):
        board = load_board(project_root)
        mutator(board)
        save_board(project_root, board)


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
    # Per-type scaling: effective_hl = board_hl × (type_hl / default_hl)
    effective_hl = half_life * (
        TYPE_HALF_LIFE.get(pheromone.pheromone_type, DEFAULT_HALF_LIFE_HOURS)
        / DEFAULT_HALF_LIFE_HOURS
    )
    return pheromone.intensity * mpow(0.5, age_h / effective_hl)


def compute_urgency_score(pheromone: Pheromone, half_life: float,
                          now: datetime | None = None) -> float:
    """Urgency score: current intensity × (1 + reinforcements × 0.3), capped at 2.0.

    Prioritises heavily-reinforced signals over lightly-reinforced ones
    regardless of raw intensity.
    """
    current = compute_intensity(pheromone, half_life, now)
    return min(current * (1.0 + pheromone.reinforcements * 0.3), 2.0)


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


def deposit_pheromone(
    project_root: Path,
    ptype: str,
    location: str,
    text: str,
    emitter: str,
    tags: list[str] | None = None,
    intensity: float = DEFAULT_INTENSITY,
) -> Pheromone | None:
    """Atomic load → deduplicate/amplify → emit → save.

    If an active signal with the same ptype + location + text already exists,
    amplifies it instead of creating a duplicate. Returns None on error.
    """
    try:
        board = load_board(project_root)
        existing = next(
            (p for p in board.pheromones
             if not p.resolved
             and p.pheromone_type == ptype
             and p.location == location
             and p.text == text[:200]),
            None,
        )
        if existing:
            existing.intensity = min(existing.intensity + REINFORCEMENT_BOOST, MAX_INTENSITY)
            existing.reinforcements += 1
            if emitter not in existing.reinforced_by:
                existing.reinforced_by.append(emitter)
            save_board(project_root, board)
            return existing
        p = emit_pheromone(board, ptype, location, text, emitter,
                           tags=tags, intensity=intensity)
        save_board(project_root, board)
        return p
    except Exception:
        return None


def bulk_deposit(
    project_root: Path,
    signals: list[BulkSignal],
) -> int:
    """Deposit multiple pheromones atomically (1 load + N emits + 1 save).

    Each signal dict requires: ptype, location, text, emitter;
    optional: tags, intensity.
    Deduplicates internally (amplifies existing active signals).
    Returns count of signals processed, 0 on error.
    """
    if not signals:
        return 0
    try:
        board = load_board(project_root)
        count = 0
        for sig in signals:
            ptype = sig["ptype"]
            location = sig.get("location", "")
            text = sig.get("text", "")
            emitter = sig.get("emitter", "")
            tags = sig.get("tags")
            intensity = sig.get("intensity", DEFAULT_INTENSITY)
            existing = next(
                (p for p in board.pheromones
                 if not p.resolved
                 and p.pheromone_type == ptype
                 and p.location == location
                 and p.text == text[:200]),
                None,
            )
            if existing:
                existing.intensity = min(
                    existing.intensity + REINFORCEMENT_BOOST, MAX_INTENSITY)
                existing.reinforcements += 1
                if emitter not in existing.reinforced_by:
                    existing.reinforced_by.append(emitter)
            else:
                emit_pheromone(board, ptype, location, text, emitter,
                               tags=tags, intensity=intensity)
            count += 1
        save_board(project_root, board)
        return count
    except Exception:
        return 0


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
                     emitter: str | None = None,
                     include_resolved: bool = False,
                     now: datetime | None = None) -> list[tuple[Pheromone, float]]:
    """Detect active pheromones above detection threshold."""
    if now is None:
        now = datetime.now(tz=UTC)
    results: list[tuple[Pheromone, float]] = []
    for p in board.pheromones:
        if not include_resolved and p.resolved:
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
        if emitter and emitter.lower() != p.emitter.lower():
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

    # Cold zones: resolved locations with no active signals
    resolved_locs = {p.location for p in board.pheromones if p.resolved}
    for loc in resolved_locs - set(by_loc.keys()):
        patterns.append(TrailPattern(
            pattern_type="cold-zone", location=loc,
            description="Previously active zone, now silent",
        ))

    # Relay: COMPLETE followed by NEED/PROGRESS in the same zone by a different agent
    for cp in board.pheromones:
        if cp.pheromone_type != "COMPLETE":
            continue
        if compute_intensity(cp, board.half_life_hours, now) < DETECTION_THRESHOLD:
            continue
        for p in board.pheromones:
            if (p.pheromone_id != cp.pheromone_id
                    and p.location == cp.location
                    and p.emitter != cp.emitter
                    and p.pheromone_type in ("NEED", "PROGRESS")
                    and not p.resolved):
                curr = compute_intensity(p, board.half_life_hours, now)
                if curr >= DETECTION_THRESHOLD:
                    patterns.append(TrailPattern(
                        pattern_type="relay", location=cp.location,
                        description=f"Relay: {cp.emitter} → {p.emitter} "
                                    f"(complete → {p.pheromone_type.lower()})",
                        involved_agents=(cp.emitter, p.emitter),
                        pheromone_count=2, avg_intensity=curr,
                    ))

    # Deduplicate (same pattern_type + location)
    seen: set[str] = set()
    unique: list[TrailPattern] = []
    for pat in patterns:
        key = f"{pat.pattern_type}:{pat.location}"
        if key not in seen:
            seen.add(key)
            unique.append(pat)
    return unique


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


# ── vue agrégée pour le serveur de forge ──
# Extraite de forge_server (P3.4) : le hub avait atteint son plafond de
# lignes, et cette vue appartient de toute façon au module qui possède le
# domaine plutôt qu'au routeur HTTP.
def board_view(project_root: Path) -> dict[str, Any]:
    """Vue live du tableau phéromonique du projet (signaux actifs + trails).

    Lit ``_grimoire-output/pheromone-board.json`` via le module SDK ;
    l'intensité est décroissante (calculée à la lecture). Rien n'est écrit.
    """

    board = load_board(project_root)
    active = sense_pheromones(board)
    signals = [
        {
            "id": p.pheromone_id,
            "type": p.pheromone_type,
            "location": p.location,
            "text": p.text,
            "emitter": p.emitter,
            "intensity": round(inten, 4),
            "reinforcements": p.reinforcements,
        }
        for p, inten in active
    ]
    trails = [
        {
            "type": t.pattern_type,
            "location": t.location,
            "description": t.description,
            "agents": list(t.involved_agents),
        }
        for t in analyze_trails(board)
    ]
    by_type: dict[str, int] = {}
    for p, _ in active:
        by_type[p.pheromone_type] = by_type.get(p.pheromone_type, 0) + 1

    # Métriques comportementales (base de la promotion beta→stable) :
    # le journal dit si le board coordonne réellement ou tourne à vide.
    events = read_events(project_root)
    actions = [str(e.get("action", "")) for e in events]
    resolved = sum(1 for p in board.pheromones if p.resolved)
    reinforcements = sum(p.reinforcements for p in board.pheromones)
    relays = sum(1 for t in trails if t["type"] == "relay")
    useful = resolved + relays
    denominator = board.total_emitted or 1
    useful_ratio = round(useful / denominator, 3)
    # Seuil de promotion beta→stable (QUA-13) : la mesure sert une décision.
    target_ratio = STIGMERGY_TARGET_USEFUL_RATIO
    min_emitted = STIGMERGY_PROMOTION_MIN_EMITTED
    return {
        "active": signals,
        "trails": trails,
        "stats": {
            "active": len(signals),
            "emitted": board.total_emitted,
            "evaporated": board.total_evaporated,
            "halfLifeHours": board.half_life_hours,
            "byType": by_type,
        },
        "behavior": {
            "senseInjections": actions.count("sense-injected"),
            "autoEmits": sum(
                1 for e in events
                if e.get("source") == "hook" and e.get("action") in ("emit", "reinforce")
            ),
            "manualEmits": sum(
                1 for e in events
                if e.get("source") != "hook" and e.get("action") in ("emit", "reinforce")
            ),
            "resolved": resolved,
            "reinforcements": reinforcements,
            "relays": relays,
            "usefulRatio": useful_ratio,
            "targetUsefulRatio": target_ratio,
            "minEmitted": min_emitted,
            "hypothesis": STIGMERGY_PROMOTION_HYPOTHESIS,
            "promotionReady": bool(board.total_emitted >= min_emitted and useful_ratio >= target_ratio),
        },
    }
