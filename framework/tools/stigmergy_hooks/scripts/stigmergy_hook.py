#!/usr/bin/env python3
"""Logique des hooks stigmergiques (auto-émission / captation).

Script autonome (stdlib seule), installé dans un projet par l'extension
``stigmergy``. Il partage le tableau ``_grimoire-output/pheromone-board.json``
avec ``grimoire stigmergy`` et le script framework ; un test de parité garantit
la compatibilité de format.

Invariants :

- **Fail-open absolu** : toute erreur ⇒ sortie ``{}`` et code 0. Un hook ne
  doit jamais bloquer ni casser une session.
- **Anti-bruit** : l'activité d'édition ne crée pas un signal par édition —
  elle *renforce* le signal existant de la zone (comportement de piste).
- **Aucune exécution** : lecture/écriture d'un fichier JSON local, rien d'autre.

Sous-commandes :

- ``sense``            : SessionStart → injecte les signaux actifs en contexte.
- ``emit-post-edit``   : PostToolUse → PROGRESS (ou renfort) sur la zone éditée.
- ``emit-stop``        : Stop → COMPLETE sur la zone la plus active + purge.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from math import pow as mpow
from pathlib import Path
from typing import Any

BOARD_REL = Path("_grimoire-output") / "pheromone-board.json"
EVENTS_REL = Path("_grimoire-output") / "stigmergy-events.jsonl"
FEATURES_REL = Path("_grimoire") / "features.json"
DEFAULT_HALF_LIFE_HOURS = 72.0
DETECTION_THRESHOLD = 0.05
PROGRESS_INTENSITY = 0.45
COMPLETE_INTENSITY = 0.7
# Extensions de fichiers considérées comme « source » (émission ciblée).
SOURCE_SUFFIXES = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".cs", ".php", ".sh", ".sql", ".css", ".html",
    ".md", ".yaml", ".yml", ".toml", ".json",
})


# ── Board (format partagé, minimal) ────────────────────────────────────────

def _board_path(root: Path) -> Path:
    return root / BOARD_REL


def load_board(root: Path) -> dict[str, Any]:
    path = _board_path(root)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("pheromones", [])
                data.setdefault("half_life_hours", DEFAULT_HALF_LIFE_HOURS)
                data.setdefault("total_emitted", 0)
                data.setdefault("total_evaporated", 0)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": "1.0.0", "half_life_hours": DEFAULT_HALF_LIFE_HOURS,
            "pheromones": [], "total_emitted": 0, "total_evaporated": 0}


def save_board(root: Path, board: dict[str, Any]) -> None:
    path = _board_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def compute_intensity(ph: dict[str, Any], half_life: float, now: datetime) -> float:
    try:
        emit = datetime.fromisoformat(str(ph.get("timestamp", "")))
        if emit.tzinfo is None:
            emit = emit.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return float(ph.get("intensity", 0.0))
    age_h = (now - emit).total_seconds() / 3600.0
    if age_h <= 0:
        return float(ph.get("intensity", 0.0))
    return float(ph.get("intensity", 0.0)) * mpow(0.5, age_h / max(half_life, 0.1))


def _generate_id(ptype: str, location: str, text: str, timestamp: str) -> str:
    h = hashlib.sha256(f"{ptype}:{location}:{text}:{timestamp}".encode()).hexdigest()[:8]
    return f"PH-{h}"


def emit(board: dict[str, Any], ptype: str, location: str, text: str,
         emitter: str, intensity: float) -> dict[str, Any]:
    ts = _now().isoformat()
    ph = {
        "pheromone_id": _generate_id(ptype, location, text, ts),
        "pheromone_type": ptype, "location": location, "text": text,
        "emitter": emitter, "timestamp": ts, "intensity": min(max(intensity, 0.0), 1.0),
        "tags": [], "reinforcements": 0, "reinforced_by": [],
        "resolved": False, "resolved_by": "", "resolved_at": "",
    }
    board["pheromones"].append(ph)
    board["total_emitted"] = int(board.get("total_emitted", 0)) + 1
    return ph


def emit_or_reinforce(board: dict[str, Any], ptype: str, location: str,
                      text: str, emitter: str, intensity: float,
                      now: datetime) -> str:
    """Anti-bruit : renforce un signal actif de même type/zone plutôt que
    d'en empiler un nouveau. Renvoie l'action ('emit' | 'reinforce')."""
    half_life = float(board.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS))
    for ph in board["pheromones"]:
        if (ph.get("pheromone_type") == ptype
                and ph.get("location") == location
                and not ph.get("resolved")
                and compute_intensity(ph, half_life, now) >= DETECTION_THRESHOLD):
            ph["reinforcements"] = int(ph.get("reinforcements", 0)) + 1
            rb = ph.setdefault("reinforced_by", [])
            if emitter and emitter not in rb:
                rb.append(emitter)
            ph["intensity"] = min(float(ph.get("intensity", 0.0)) + 0.2, 1.0)
            ph["timestamp"] = now.isoformat()  # rafraîchit la piste
            return "reinforce"
    emit(board, ptype, location, text, emitter, intensity)
    return "emit"


def evaporate(board: dict[str, Any], now: datetime) -> int:
    half_life = float(board.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS))
    survivors, removed = [], 0
    for ph in board["pheromones"]:
        if not ph.get("resolved") and compute_intensity(ph, half_life, now) >= DETECTION_THRESHOLD:
            survivors.append(ph)
        else:
            removed += 1
    board["pheromones"] = survivors
    board["total_evaporated"] = int(board.get("total_evaporated", 0)) + removed
    return removed


# ── Canal beta : flag projet + journal comportemental ──────────────────────

def hooks_enabled(root: Path) -> bool:
    """Respecte le toggle `stigmergy-hooks` de _grimoire/features.json.

    Absent ou illisible ⇒ activé (les hooks ne sont copiés que sur opt-in ;
    le flag sert à couper la boucle sans désinstaller)."""
    path = root / FEATURES_REL
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("stigmergy-hooks")
        if isinstance(entry, dict) and entry.get("enabled") is False:
            return False
    except (json.JSONDecodeError, OSError):
        pass
    return True


def log_event(root: Path, action: str, **fields: Any) -> None:
    """Journal JSONL append-only des actes stigmergiques (fail-open).

    Base des métriques de promotion beta→stable : chaque émission, renfort,
    complétion et injection de contexte laisse une trace datée."""
    try:
        path = root / EVENTS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": _now().isoformat(), "action": action, "source": "hook", **fields}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Extraction défensive de l'event (Claude Code / Copilot) ─────────────────

def read_event() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def event_tool_name(event: dict[str, Any]) -> str:
    for key in ("tool_name", "toolName", "name", "tool"):
        val = event.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def event_file_path(event: dict[str, Any]) -> str:
    ti = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    if isinstance(ti, dict):
        for key in ("file_path", "filePath", "path", "notebook_path"):
            val = ti.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def event_emitter(event: dict[str, Any]) -> str:
    for key in ("agent", "agent_name", "agentName", "subagent", "session_id"):
        val = event.get(key)
        if isinstance(val, str) and val:
            return val.split("/")[-1][:40]
    return "session"


def zone_of(file_path: str) -> str:
    """Zone = dossier du fichier (granularité de coordination)."""
    p = Path(file_path)
    parent = p.parent.as_posix()
    return parent if parent not in ("", ".") else p.name


# ── Décisions (testables) ──────────────────────────────────────────────────

WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "create_file", "insert_edit_into_file"})


def decide_post_edit(board: dict[str, Any], event: dict[str, Any],
                     now: datetime) -> str | None:
    """Émet/renforce un PROGRESS sur la zone éditée. Renvoie l'action ou None
    (aucune émission : outil non-écriture, fichier hors source…)."""
    tool = event_tool_name(event)
    if tool not in WRITE_TOOLS:
        return None
    fpath = event_file_path(event)
    if not fpath or Path(fpath).suffix.lower() not in SOURCE_SUFFIXES:
        return None
    zone = zone_of(fpath)
    if not zone:
        return None
    emitter = event_emitter(event)
    return emit_or_reinforce(board, "PROGRESS", zone,
                             "travail en cours dans cette zone", emitter,
                             PROGRESS_INTENSITY, now)


def decide_stop(board: dict[str, Any], event: dict[str, Any],
                now: datetime) -> str | None:
    """Sur Stop : marque COMPLETE la zone la plus active (le plus de PROGRESS
    non résolus) et purge les signaux morts. Renvoie la zone ou None."""
    half_life = float(board.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS))
    scores: dict[str, float] = {}
    for ph in board["pheromones"]:
        if ph.get("pheromone_type") == "PROGRESS" and not ph.get("resolved"):
            inten = compute_intensity(ph, half_life, now)
            if inten >= DETECTION_THRESHOLD:
                scores[ph["location"]] = scores.get(ph["location"], 0.0) + inten
    zone = max(scores, key=lambda k: scores[k]) if scores else None
    if zone:
        emit(board, "COMPLETE", zone, "session terminée sur cette zone",
             event_emitter(event), COMPLETE_INTENSITY)
    evaporate(board, now)
    return zone


def format_sense(board: dict[str, Any], now: datetime, limit: int = 6) -> str:
    """Résumé compact des signaux actifs, pour additionalContext."""
    half_life = float(board.get("half_life_hours", DEFAULT_HALF_LIFE_HOURS))
    active = []
    for ph in board["pheromones"]:
        if ph.get("resolved"):
            continue
        inten = compute_intensity(ph, half_life, now)
        if inten >= DETECTION_THRESHOLD:
            active.append((ph, inten))
    if not active:
        return ""
    active.sort(key=lambda x: x[1], reverse=True)
    lines = ["Signaux de coordination actifs (stigmergie) :"]
    for ph, inten in active[:limit]:
        loc = ph.get("location") or "—"
        txt = ph.get("text") or ""
        line = f"- {ph.get('pheromone_type')} @ {loc} ({int(inten * 100)}%)"
        if txt:
            line += f" : {txt}"
        lines.append(line)
    extra = len(active) - limit
    if extra > 0:
        lines.append(f"- (+{extra} autre·s)")
    return "\n".join(lines)


# ── Dispatcher (fail-open) ─────────────────────────────────────────────────

def _emit_context(text: str, event_name: str) -> None:
    if not text:
        print("{}")
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name, "additionalContext": text,
    }}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else ""
    try:
        root = Path.cwd()
        if not hooks_enabled(root):
            if action == "sense":
                _emit_context("", "SessionStart")
            else:
                sys.stdin.read()
                print("{}")
            return 0
        if action == "sense":
            board = load_board(root)
            text = format_sense(board, _now())
            if text:
                log_event(root, "sense-injected",
                          signals=len(text.splitlines()) - 1)
            _emit_context(text, "SessionStart")
            return 0
        event = read_event()
        now = _now()
        if action == "emit-post-edit":
            board = load_board(root)
            outcome = decide_post_edit(board, event, now)
            if outcome:
                save_board(root, board)
                log_event(root, outcome, ptype="PROGRESS",
                          location=zone_of(event_file_path(event)),
                          emitter=event_emitter(event))
        elif action == "emit-stop":
            board = load_board(root)
            zone = decide_stop(board, event, now)
            save_board(root, board)
            if zone:
                log_event(root, "complete", ptype="COMPLETE", location=zone,
                          emitter=event_emitter(event))
        print("{}")
        return 0
    except Exception:  # fail-open strict : un hook ne casse jamais une session
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
