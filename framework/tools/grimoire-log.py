#!/usr/bin/env python3
"""
grimoire-log.py — Logging structuré partagé pour tous les outils Grimoire.
======================================================================

Configure un logger hiérarchique ``grimoire.*`` qui écrit dans
``_grimoire-output/.logs/grimoire.log`` (rotation automatique à 2 Mo,
5 backups).

Chaque outil utilise ``import logging`` + ``_log = logging.getLogger(__name__)``.
Quand ce module est importé (ou ``setup()`` appelé), le handler fichier est
installé une seule fois.

Usage CLI :
  python3 grimoire-log.py --project-root . tail               # dernières 30 lignes
  python3 grimoire-log.py --project-root . tail --lines 100   # dernières 100 lignes
  python3 grimoire-log.py --project-root . search --query SSR  # grep dans les logs
  python3 grimoire-log.py --project-root . stats               # stats par level
  python3 grimoire-log.py --project-root . rotate              # force la rotation
  python3 grimoire-log.py --project-root . clear               # purge les logs

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import re
import time
from collections import Counter
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

GRIMOIRE_LOG_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

LOG_DIR = "_grimoire-output/.logs"
LOG_FILE = "grimoire.log"
JSON_LOG_FILE = "grimoire.jsonl"
MAX_BYTES = 2 * 1024 * 1024  # 2 MB
BACKUP_COUNT = 5
DEFAULT_LEVEL = "DEBUG"
ROOT_LOGGER_NAME = "grimoire"

# ── Formatter ────────────────────────────────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """Ligne JSON structurée par enregistrement."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exc_type"] = type(record.exc_info[1]).__name__
            entry["exc_msg"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


# ── Setup ────────────────────────────────────────────────────────────────────

_CONFIGURED = False


def setup(project_root: Path | str | None = None, level: str = DEFAULT_LEVEL) -> logging.Logger:
    """Configure le logger racine ``grimoire`` avec handler fichier rotatif.

    Idempotent — les appels suivants retournent le même logger sans
    ajouter de handlers supplémentaires.

    Args:
        project_root: Racine du projet (pour localiser le dossier de logs).
                       Si None, détecte via $Grimoire_PROJECT_ROOT ou cwd.
        level: Niveau de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Le logger racine ``grimoire``.
    """
    global _CONFIGURED
    root_log = logging.getLogger(ROOT_LOGGER_NAME)

    if _CONFIGURED:
        return root_log

    # Resolve project root
    if project_root is None:
        project_root = Path(os.environ.get("Grimoire_PROJECT_ROOT", "."))
    project_root = Path(project_root)

    log_dir = project_root / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # Text handler (human-readable, rotating)
    text_path = log_dir / LOG_FILE
    text_handler = logging.handlers.RotatingFileHandler(
        text_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    text_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))

    # JSON handler (structured, rotating)
    json_path = log_dir / JSON_LOG_FILE
    json_handler = logging.handlers.RotatingFileHandler(
        json_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    json_handler.setFormatter(JsonFormatter())

    root_log.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root_log.addHandler(text_handler)
    root_log.addHandler(json_handler)

    _CONFIGURED = True
    root_log.debug("grimoire-log initialized (root=%s, level=%s)", project_root, level)
    return root_log


def get_logger(name: str, project_root: Path | str | None = None) -> logging.Logger:
    """Raccourci : setup() + retourne un logger enfant.

    Usage typique dans un outil::

        from grimoire_log import get_logger
        _log = get_logger("mon-outil")
    """
    setup(project_root)
    if not name.startswith(ROOT_LOGGER_NAME + "."):
        name = f"{ROOT_LOGGER_NAME}.{name}"
    return logging.getLogger(name)


# ── CLI helpers ──────────────────────────────────────────────────────────────


def _log_path(root: Path) -> Path:
    return root / LOG_DIR / LOG_FILE


def _jsonl_path(root: Path) -> Path:
    return root / LOG_DIR / JSON_LOG_FILE


def cmd_tail(root: Path, args: argparse.Namespace) -> int:
    """Affiche les dernières N lignes du log."""
    path = _log_path(root)
    if not path.exists():
        print("Aucun log trouvé.")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    count = min(args.lines, len(lines))
    for line in lines[-count:]:
        print(line)
    return 0


def cmd_search(root: Path, args: argparse.Namespace) -> int:
    """Recherche dans les logs."""
    path = _log_path(root)
    if not path.exists():
        print("Aucun log trouvé.")
        return 0
    pattern = re.compile(re.escape(args.query), re.IGNORECASE)
    matches = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if pattern.search(line):
            print(line)
            matches += 1
            if matches >= args.max:
                break
    if matches == 0:
        print(f"Aucun résultat pour '{args.query}'.")
    else:
        print(f"\n--- {matches} résultat(s) ---")
    return 0


def cmd_stats(root: Path, _args: argparse.Namespace) -> int:
    """Statistiques des logs par level."""
    path = _jsonl_path(root)
    if not path.exists():
        print("Aucun log structuré trouvé.")
        return 0
    levels: Counter[str] = Counter()
    loggers: Counter[str] = Counter()
    exc_types: Counter[str] = Counter()
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        levels[entry.get("level", "?")] += 1
        loggers[entry.get("logger", "?")] += 1
        if "exc_type" in entry:
            exc_types[entry["exc_type"]] += 1

    print(f"📊 Grimoire Log Stats — {total} entrées")
    print("=" * 50)
    print("\nPar level:")
    for lvl, cnt in levels.most_common():
        print(f"  {lvl:8s} : {cnt}")
    print("\nTop 10 loggers:")
    for name, cnt in loggers.most_common(10):
        print(f"  {name:40s} : {cnt}")
    if exc_types:
        print("\nTop 10 exception types:")
        for exc, cnt in exc_types.most_common(10):
            print(f"  {exc:30s} : {cnt}")
    return 0


def cmd_rotate(root: Path, _args: argparse.Namespace) -> int:
    """Force la rotation des logs."""
    log_dir = root / LOG_DIR
    rotated = 0
    for name in (LOG_FILE, JSON_LOG_FILE):
        path = log_dir / name
        if path.exists() and path.stat().st_size > 0:
            ts = time.strftime("%Y%m%d-%H%M%S")
            dest = path.with_suffix(f".{ts}{path.suffix}")
            path.rename(dest)
            path.touch()
            rotated += 1
            print(f"  Rotaté: {name} → {dest.name}")
    if rotated == 0:
        print("Rien à rotater.")
    return 0


def cmd_clear(root: Path, _args: argparse.Namespace) -> int:
    """Purge tous les logs."""
    log_dir = root / LOG_DIR
    if not log_dir.exists():
        print("Aucun log.")
        return 0
    cleared = 0
    for path in log_dir.iterdir():
        if path.is_file():
            path.unlink()
            cleared += 1
    print(f"🗑️  {cleared} fichier(s) supprimé(s).")
    return 0


# ── MCP interface ────────────────────────────────────────────────────────────


def mcp_grimoire_log(
    project_root: str,
    action: str = "stats",
    query: str = "",
    lines: int = 30,
) -> dict:
    """MCP tool ``bmad_grimoire_log`` — consulter les logs structurés.

    Args:
        project_root: Racine du projet.
        action: ``tail`` | ``search`` | ``stats``.
        query: Mot-clé pour action ``search``.
        lines: Nombre de lignes pour ``tail`` (défaut 30).

    Returns:
        dict avec ``status`` et ``data``.
    """
    root = Path(project_root)
    log_file = _log_path(root)
    jsonl_file = _jsonl_path(root)

    if action == "tail":
        if not log_file.exists():
            return {"status": "ok", "data": []}
        all_lines = log_file.read_text(encoding="utf-8").splitlines()
        return {"status": "ok", "data": all_lines[-lines:]}

    if action == "search":
        if not log_file.exists():
            return {"status": "ok", "data": [], "matches": 0}
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches = [ln for ln in log_file.read_text(encoding="utf-8").splitlines()
                   if pattern.search(ln)]
        return {"status": "ok", "data": matches[:100], "matches": len(matches)}

    if action == "stats":
        if not jsonl_file.exists():
            return {"status": "ok", "data": {"total": 0}}
        levels: Counter[str] = Counter()
        total = 0
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                total += 1
                levels[entry.get("level", "?")] += 1
            except json.JSONDecodeError:
                continue
        return {"status": "ok", "data": {"total": total, "by_level": dict(levels)}}

    return {"status": "error", "error": f"Action inconnue: {action}"}


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grimoire-log",
        description="Grimoire Log — logging structuré partagé Grimoire",
    )
    p.add_argument("--project-root", type=Path, default=Path())
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {GRIMOIRE_LOG_VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("tail", help="Dernières lignes du log")
    t.add_argument("--lines", type=int, default=30)

    s = sub.add_parser("search", help="Recherche dans les logs")
    s.add_argument("--query", required=True)
    s.add_argument("--max", type=int, default=100)

    sub.add_parser("stats", help="Statistiques par level")
    sub.add_parser("rotate", help="Force la rotation")
    sub.add_parser("clear", help="Purge les logs")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    commands = {
        "tail": cmd_tail,
        "search": cmd_search,
        "stats": cmd_stats,
        "rotate": cmd_rotate,
        "clear": cmd_clear,
    }
    handler = commands.get(args.command)
    if not handler:
        parser.print_help()
        return 1
    return handler(root, args)


if __name__ == "__main__":
    raise SystemExit(main())
