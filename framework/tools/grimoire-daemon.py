#!/usr/bin/env python3
"""
grimoire-daemon.py — Daemon de maintenance automatique en arrière-plan.
=======================================================================

Exécute périodiquement les tâches de maintenance du framework Grimoire :
- dream --quick : consolidation de la mémoire
- stigmergy evaporate : nettoyage des phéromones périmées
- rag-indexer : re-indexation incrémentale

Usage :
  python3 grimoire-daemon.py --project-root . start
  python3 grimoire-daemon.py --project-root . start --interval 600
  python3 grimoire-daemon.py --project-root . run-once
  python3 grimoire-daemon.py --project-root . status

Le daemon écrit un fichier PID et un état dans _bmad/_memory/daemon/.
Sous forme de boucle Python, pas un vrai démon Unix (pas de fork/double-fork).

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("grimoire.daemon")

DAEMON_VERSION = "1.1.0"
DAEMON_DIR = "_bmad/_memory/daemon"
PID_FILE = "grimoire-daemon.pid"
STATE_FILE = "daemon-state.json"
LOG_FILE = "daemon.log"
DEFAULT_INTERVAL = 600  # 10 minutes
MEMORY_BRIDGE_SOURCE = "_bmad/_memory"
MEMORY_BRIDGE_TARGET = ".github/memories/repo"

# ── Data Model ───────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    name: str
    status: str = "skipped"  # success | failed | skipped
    duration_s: float = 0.0
    message: str = ""


@dataclass
class CycleResult:
    cycle: int = 0
    timestamp: str = ""
    tasks: list[TaskResult] = field(default_factory=list)
    total_duration_s: float = 0.0


@dataclass
class DaemonState:
    pid: int = 0
    started_at: str = ""
    last_cycle: str = ""
    total_cycles: int = 0
    interval_s: int = DEFAULT_INTERVAL
    status: str = "stopped"  # running | stopped


# ── Daemon Directory ─────────────────────────────────────────────────────────


def _daemon_dir(root: Path) -> Path:
    d = root / DAEMON_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_state(root: Path) -> DaemonState:
    path = _daemon_dir(root) / STATE_FILE
    if not path.exists():
        return DaemonState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DaemonState(**data)
    except (json.JSONDecodeError, TypeError):
        return DaemonState()


def _write_state(root: Path, state: DaemonState) -> None:
    path = _daemon_dir(root) / STATE_FILE
    path.write_text(json.dumps(asdict(state), indent=2, ensure_ascii=False), encoding="utf-8")


def _write_pid(root: Path) -> None:
    path = _daemon_dir(root) / PID_FILE
    path.write_text(str(os.getpid()), encoding="utf-8")


def _read_pid(root: Path) -> int | None:
    path = _daemon_dir(root) / PID_FILE
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _clear_pid(root: Path) -> None:
    path = _daemon_dir(root) / PID_FILE
    if path.exists():
        path.unlink()


def _is_running(root: Path) -> bool:
    """Vérifie si le daemon est déjà en cours d'exécution."""
    pid = _read_pid(root)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        _clear_pid(root)
        return False


# ── Task Runners ─────────────────────────────────────────────────────────────


def _run_tool(root: Path, tool_name: str, args: list[str]) -> TaskResult:
    """Exécute un tool Python en subprocess."""
    tool_path = root / "framework" / "tools" / tool_name
    if not tool_path.exists():
        return TaskResult(name=tool_name, status="skipped", message=f"{tool_name} not found")

    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(tool_path), "--project-root", str(root), *args],
            capture_output=True, text=True, timeout=120,
        )
        duration = round(time.monotonic() - start, 3)
        if result.returncode == 0:
            return TaskResult(name=tool_name, status="success", duration_s=duration,
                              message=result.stdout[:200].strip())
        return TaskResult(name=tool_name, status="failed", duration_s=duration,
                          message=result.stderr[:200].strip())
    except subprocess.TimeoutExpired:
        duration = round(time.monotonic() - start, 3)
        return TaskResult(name=tool_name, status="failed", duration_s=duration,
                          message="Timeout (120s)")
    except Exception as exc:
        duration = round(time.monotonic() - start, 3)
        _log.debug("Task %s failed: %s", tool_name, exc)
        return TaskResult(name=tool_name, status="failed", duration_s=duration,
                          message=str(exc)[:200])


def _run_memory_bridge(root: Path) -> TaskResult:
    """Synchronise _bmad/_memory/ → .github/memories/repo/ (one-way bridge).

    Copie les fichiers .md et .json (pas .jsonl — trop volumineux) depuis la
    mémoire BMAD vers le dossier que VS Code Copilot consulte nativement.
    Seuls les fichiers modifiés sont copiés (compare mtime).
    """
    start = time.monotonic()
    source = root / MEMORY_BRIDGE_SOURCE
    target = root / MEMORY_BRIDGE_TARGET

    if not source.exists():
        return TaskResult(name="memory-bridge", status="skipped",
                          duration_s=round(time.monotonic() - start, 3),
                          message="Source _bmad/_memory/ absent")
    try:
        target.mkdir(parents=True, exist_ok=True)
        synced = 0
        for src_file in source.iterdir():
            if not src_file.is_file():
                continue
            if src_file.suffix not in (".md", ".json"):
                continue
            dst_file = target / src_file.name
            # Copy only if source is newer
            if dst_file.exists() and dst_file.stat().st_mtime >= src_file.stat().st_mtime:
                continue
            dst_file.write_bytes(src_file.read_bytes())
            synced += 1

        duration = round(time.monotonic() - start, 3)
        return TaskResult(name="memory-bridge", status="success", duration_s=duration,
                          message=f"{synced} fichier(s) synchronisé(s)")
    except Exception as exc:
        duration = round(time.monotonic() - start, 3)
        return TaskResult(name="memory-bridge", status="failed", duration_s=duration,
                          message=str(exc)[:200])


def run_maintenance_cycle(root: Path, cycle_num: int = 1) -> CycleResult:
    """Exécute un cycle complet de maintenance."""
    start = time.monotonic()
    tasks: list[TaskResult] = []

    # 1. Dream --quick
    tasks.append(_run_tool(root, "dream.py", ["--quick"]))

    # 2. Stigmergy evaporate
    tasks.append(_run_tool(root, "stigmergy.py", ["evaporate"]))

    # 3. RAG re-index (incremental)
    tasks.append(_run_tool(root, "rag-indexer.py", ["index", "--all"]))

    # 4. Memory bridge sync
    tasks.append(_run_memory_bridge(root))

    total = round(time.monotonic() - start, 3)
    return CycleResult(
        cycle=cycle_num,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tasks=tasks,
        total_duration_s=total,
    )


# ── Daemon Loop ──────────────────────────────────────────────────────────────


def daemon_loop(root: Path, interval: int = DEFAULT_INTERVAL) -> None:
    """Boucle principale du daemon."""
    if _is_running(root):
        print(f"⚠️  Daemon déjà en cours (PID {_read_pid(root)})")
        return

    _write_pid(root)

    state = DaemonState(
        pid=os.getpid(),
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        interval_s=interval,
        status="running",
    )
    _write_state(root, state)

    # Setup log file
    log_path = _daemon_dir(root) / LOG_FILE
    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("grimoire.daemon").addHandler(file_handler)

    running = True

    def _shutdown(signum, frame):
        nonlocal running
        running = False
        _log.info("Shutdown signal reçu (sig=%s)", signum)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"🔄 Grimoire Daemon démarré (PID {os.getpid()}, interval {interval}s)")
    _log.info("Daemon started (PID=%d, interval=%ds)", os.getpid(), interval)

    cycle_num = 0
    try:
        while running:
            cycle_num += 1
            _log.info("Cycle %d starting", cycle_num)

            result = run_maintenance_cycle(root, cycle_num)

            for t in result.tasks:
                icon = "✅" if t.status == "success" else "❌" if t.status == "failed" else "⏭️"
                _log.info("  %s %s (%s, %.1fs)", icon, t.name, t.status, t.duration_s)

            state.total_cycles = cycle_num
            state.last_cycle = result.timestamp
            _write_state(root, state)

            # Wait for next cycle
            for _ in range(interval):
                if not running:
                    break
                time.sleep(1)
    finally:
        state.status = "stopped"
        _write_state(root, state)
        _clear_pid(root)
        _log.info("Daemon stopped after %d cycles", cycle_num)
        print(f"\n🛑 Daemon arrêté après {cycle_num} cycle(s)")


# ── Display ──────────────────────────────────────────────────────────────────


def display_status(root: Path) -> None:
    """Affiche le statut du daemon."""
    state = _read_state(root)
    running = _is_running(root)

    icon = "🟢" if running else "🔴"
    print(f"\n{icon} Grimoire Daemon")
    print("=" * 40)
    print(f"  Status   : {'running' if running else 'stopped'}")
    if state.pid:
        print(f"  PID      : {state.pid}")
    if state.started_at:
        print(f"  Démarré  : {state.started_at}")
    if state.last_cycle:
        print(f"  Dernier  : {state.last_cycle}")
    print(f"  Cycles   : {state.total_cycles}")
    print(f"  Interval : {state.interval_s}s")
    print()


def display_cycle(result: CycleResult) -> None:
    """Affiche le résultat d'un cycle."""
    print(f"\n🔄 Cycle #{result.cycle} — {result.timestamp}")
    print("-" * 50)
    for t in result.tasks:
        icon = "✅" if t.status == "success" else "❌" if t.status == "failed" else "⏭️"
        print(f"  {icon} {t.name:20s} {t.status:8s} ({t.duration_s:.1f}s)")
        if t.message:
            print(f"     {t.message[:80]}")
    print(f"\n  Total : {result.total_duration_s:.1f}s")


# ── MCP Interface ───────────────────────────────────────────────────────────


def mcp_grimoire_daemon(
    project_root: str,
    action: str = "status",
) -> dict:
    """MCP tool ``bmad_grimoire_daemon`` — statut et contrôle du daemon.

    Args:
        project_root: Racine du projet.
        action: status | run-once.

    Returns:
        dict avec l'état du daemon ou le résultat d'un cycle.
    """
    root = Path(project_root)

    if action == "status":
        state = _read_state(root)
        running = _is_running(root)
        d = asdict(state)
        d["daemon_status"] = d.pop("status")  # avoid collision with MCP status
        return {"status": "ok", "running": running, **d}

    if action == "run-once":
        result = run_maintenance_cycle(root)
        return {"status": "ok", **asdict(result)}

    return {"status": "error", "error": f"Unknown action: {action}"}


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grimoire-daemon",
        description="Grimoire Daemon — Maintenance automatique en arrière-plan",
    )
    p.add_argument("--project-root", type=Path, default=Path("."))
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {DAEMON_VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Démarrer le daemon")
    start.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                       help=f"Interval entre les cycles en secondes (défaut: {DEFAULT_INTERVAL})")

    sub.add_parser("run-once", help="Exécuter un seul cycle de maintenance")
    sub.add_parser("status", help="Afficher le statut du daemon")

    sub.add_parser("stop", help="Arrêter le daemon")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    if args.command == "start":
        daemon_loop(root, args.interval)
        return 0

    if args.command == "run-once":
        result = run_maintenance_cycle(root)
        display_cycle(result)
        return 0

    if args.command == "status":
        display_status(root)
        return 0

    if args.command == "stop":
        pid = _read_pid(root)
        if pid and _is_running(root):
            os.kill(pid, signal.SIGTERM)
            print(f"🛑 Signal SIGTERM envoyé au PID {pid}")
            return 0
        print("⚠️  Daemon non actif.")
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
