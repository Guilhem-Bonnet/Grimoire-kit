#!/usr/bin/env python3
"""
auto-index.py — Auto-indexation RAG sur fichiers modifiés.
============================================================

Surveille les changements (git ou polling) et déclenche automatiquement
l'indexation RAG incrémentale. Comparable au "Codebase Indexing" natif
de Cursor, mais intégré au pipeline Grimoire.

Modes :
  hook      — Installe un git hook post-commit pour auto-index
  unhook    — Désinstalle le hook
  run       — Indexation ponctuelle des fichiers modifiés
  watch     — Surveillance continue par polling (pour dev)
  status    — État de l'index et fichiers stale

Usage :
  python3 auto-index.py --project-root . hook           # Installer le hook
  python3 auto-index.py --project-root . unhook         # Désinstaller
  python3 auto-index.py --project-root . run             # Indexer maintenant
  python3 auto-index.py --project-root . run --since 3   # Modifiés depuis 3 jours
  python3 auto-index.py --project-root . watch           # Mode surveillance
  python3 auto-index.py --project-root . status          # État de l'index
  python3 auto-index.py --project-root . --json

Stdlib only — aucune dépendance externe (sauf rag-indexer pour l'indexation).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.auto_index")

# ── Constantes ────────────────────────────────────────────────────────────────

AUTO_INDEX_VERSION = "1.0.0"

HASH_INDEX_FILE = "_grimoire-output/.auto-index-hashes.json"
HOOK_MARKER = "# Grimoire-AUTO-INDEX"
WATCH_INTERVAL = 5.0  # sec

# Patterns à inclure (ceux qu'on veut indexer)
INDEXABLE_EXTENSIONS = {
    ".py", ".md", ".yaml", ".yml", ".json", ".toml",
    ".xml", ".sh", ".ts", ".js", ".html", ".css",
}

# Dossiers exclus
EXCLUDE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".venv", "venv", ".grimoire-rnd",
})

# Hook script content
HOOK_SCRIPT = """#!/bin/sh
{marker}
# Auto-index les fichiers modifiés après chaque commit
# Installé par: python3 auto-index.py hook
# Désinstaller avec: python3 auto-index.py unhook

PROJECT_ROOT="$(git rev-parse --show-toplevel)"
TOOL="$PROJECT_ROOT/{tool_path}"

if [ -f "$TOOL" ]; then
    python3 "$TOOL" --project-root "$PROJECT_ROOT" run --quiet &
fi
"""


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class FileState:
    """État d'un fichier pour le tracking."""
    path: str
    hash: str
    indexed_at: str
    size: int = 0


@dataclass
class IndexState:
    """État global de l'index."""
    version: str = AUTO_INDEX_VERSION
    last_run: str = ""
    files: dict[str, FileState] = field(default_factory=dict)  # path → FileState
    total_indexed: int = 0


@dataclass
class AutoIndexReport:
    """Rapport d'auto-indexation."""
    files_checked: int = 0
    files_new: int = 0
    files_modified: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    files_indexed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Hash Index ────────────────────────────────────────────────────────────────

def _hash_file(path: Path) -> str:
    """SHA256 d'un fichier (first 32 chars)."""
    h = hashlib.sha256()
    try:
        content = path.read_bytes()
        h.update(content)
    except OSError:
        return ""
    return h.hexdigest()[:32]


def load_index(project_root: Path) -> IndexState:
    """Charge l'index de hashes depuis le disque."""
    index_path = project_root / HASH_INDEX_FILE
    if not index_path.exists():
        return IndexState()

    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return IndexState()

    state = IndexState(
        version=data.get("version", AUTO_INDEX_VERSION),
        last_run=data.get("last_run", ""),
        total_indexed=data.get("total_indexed", 0),
    )

    for path, info in data.get("files", {}).items():
        state.files[path] = FileState(
            path=path,
            hash=info.get("hash", ""),
            indexed_at=info.get("indexed_at", ""),
            size=info.get("size", 0),
        )

    return state


def save_index(project_root: Path, state: IndexState) -> None:
    """Sauvegarde l'index sur disque."""
    index_path = project_root / HASH_INDEX_FILE
    index_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": state.version,
        "last_run": state.last_run,
        "total_indexed": state.total_indexed,
        "files": {},
    }
    for path, fs in state.files.items():
        data["files"][path] = {
            "hash": fs.hash,
            "indexed_at": fs.indexed_at,
            "size": fs.size,
        }

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── File Discovery ───────────────────────────────────────────────────────────

def discover_files(project_root: Path) -> list[Path]:
    """Découvre les fichiers indexables dans le projet."""
    files: list[Path] = []

    for fpath in project_root.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.suffix not in INDEXABLE_EXTENSIONS:
            continue
        if any(part in EXCLUDE_DIRS for part in fpath.parts):
            continue
        files.append(fpath)

    return sorted(files)


def discover_git_modified(project_root: Path, since_days: int | None = None) -> list[Path]:
    """Découvre les fichiers modifiés via git."""
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"]

    if since_days:
        cmd = ["git", "log", f"--since={since_days} days ago",
               "--name-only", "--diff-filter=ACMR", "--pretty=format:"]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    files: list[Path] = []
    seen: set[str] = set()
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        fpath = project_root / line
        if fpath.exists() and fpath.suffix in INDEXABLE_EXTENSIONS:
            files.append(fpath)

    return sorted(files)


# ── Auto-Index Engine ────────────────────────────────────────────────────────

def detect_changes(project_root: Path, state: IndexState,
                   files: list[Path] | None = None) -> tuple[list[Path], list[Path], list[str]]:
    """Détecte les fichiers nouveaux, modifiés et supprimés.

    Returns: (new_or_modified, unchanged, deleted_paths)
    """
    if files is None:
        files = discover_files(project_root)

    new_or_modified: list[Path] = []
    unchanged: list[Path] = []
    current_paths: set[str] = set()

    for fpath in files:
        rel = str(fpath.relative_to(project_root))
        current_paths.add(rel)
        file_hash = _hash_file(fpath)

        if rel not in state.files or state.files[rel].hash != file_hash:
            new_or_modified.append(fpath)
        else:
            unchanged.append(fpath)

    # Fichiers supprimés
    deleted = [p for p in state.files if p not in current_paths]

    return new_or_modified, unchanged, deleted


def run_indexation(project_root: Path, files: list[Path],
                   state: IndexState, quiet: bool = False) -> AutoIndexReport:
    """Exécute l'indexation des fichiers modifiés."""
    report = AutoIndexReport()
    start = time.monotonic()
    now = datetime.now().isoformat()

    report.files_checked = len(files)

    for fpath in files:
        rel = str(fpath.relative_to(project_root))
        file_hash = _hash_file(fpath)

        if rel in state.files and state.files[rel].hash == file_hash:
            report.files_unchanged += 1
            continue

        is_new = rel not in state.files
        if is_new:
            report.files_new += 1
        else:
            report.files_modified += 1

        # Mettre à jour l'index
        state.files[rel] = FileState(
            path=rel,
            hash=file_hash,
            indexed_at=now,
            size=fpath.stat().st_size,
        )
        report.files_indexed += 1

    # Tenter un indexation RAG incrémentale si disponible
    if report.files_indexed > 0:
        _trigger_rag_index(project_root, quiet)

    state.last_run = now
    state.total_indexed += report.files_indexed
    save_index(project_root, state)

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


def _trigger_rag_index(project_root: Path, quiet: bool = False) -> bool:
    """Tente de déclencher le rag-indexer en mode incrémental."""
    rag_indexer = project_root / "framework" / "tools" / "rag-indexer.py"
    if not rag_indexer.exists():
        # Chercher dans grimoire-kit s'il existe
        alt = project_root.parent / "grimoire-kit" / "framework" / "tools" / "rag-indexer.py"
        if alt.exists():
            rag_indexer = alt
        else:
            return False

    try:
        cmd = [sys.executable, str(rag_indexer),
               "--project-root", str(project_root),
               "index", "--incremental"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if not quiet and result.stdout:
            print(result.stdout)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ── Git Hook ──────────────────────────────────────────────────────────────────

def _find_git_hooks_dir(project_root: Path) -> Path | None:
    """Trouve le dossier .git/hooks."""
    git_dir = project_root / ".git"
    if git_dir.is_dir():
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        return hooks_dir
    return None


def _find_tool_relative_path(project_root: Path) -> str:
    """Trouve le chemin relatif de auto-index.py depuis la racine du projet."""
    this_file = Path(__file__).resolve()
    try:
        return str(this_file.relative_to(project_root.resolve()))
    except ValueError:
        return str(this_file)


def install_hook(project_root: Path) -> tuple[bool, str]:
    """Installe le hook git post-commit."""
    hooks_dir = _find_git_hooks_dir(project_root)
    if not hooks_dir:
        return False, "Pas de dépôt git trouvé"

    hook_file = hooks_dir / "post-commit"
    tool_path = _find_tool_relative_path(project_root)

    # Vérifier si déjà installé
    if hook_file.exists():
        content = hook_file.read_text(encoding="utf-8")
        if HOOK_MARKER in content:
            return True, "Hook déjà installé"

        # Ajouter à la fin du hook existant
        content += "\n" + HOOK_SCRIPT.format(marker=HOOK_MARKER, tool_path=tool_path)
        hook_file.write_text(content, encoding="utf-8")
    else:
        hook_file.write_text(
            HOOK_SCRIPT.format(marker=HOOK_MARKER, tool_path=tool_path),
            encoding="utf-8",
        )

    hook_file.chmod(0o755)
    return True, f"Hook installé dans {hook_file}"


def uninstall_hook(project_root: Path) -> tuple[bool, str]:
    """Désinstalle le hook git post-commit."""
    hooks_dir = _find_git_hooks_dir(project_root)
    if not hooks_dir:
        return False, "Pas de dépôt git trouvé"

    hook_file = hooks_dir / "post-commit"
    if not hook_file.exists():
        return True, "Aucun hook à désinstaller"

    content = hook_file.read_text(encoding="utf-8")
    if HOOK_MARKER not in content:
        return True, "Hook Grimoire non trouvé dans post-commit"

    # Retirer le bloc Grimoire
    lines = content.splitlines()
    new_lines: list[str] = []
    skip = False
    for line in lines:
        if HOOK_MARKER in line:
            skip = True
            continue
        if skip and line.strip() == "fi":
            skip = False
            continue
        if not skip:
            new_lines.append(line)

    new_content = "\n".join(new_lines).strip()
    if new_content and new_content != "#!/bin/sh":
        hook_file.write_text(new_content + "\n", encoding="utf-8")
    else:
        hook_file.unlink()

    return True, "Hook Grimoire désinstallé"


# ── Watch Mode ────────────────────────────────────────────────────────────────

def watch_directory(project_root: Path, interval: float = WATCH_INTERVAL) -> None:
    """Surveille les changements et déclenche l'auto-indexation."""
    print("\n  👁️  Auto-Index Watch Mode")
    print(f"  Polling toutes les {interval}s — Ctrl+C pour arrêter\n")

    state = load_index(project_root)

    try:
        while True:
            time.sleep(interval)
            files = discover_files(project_root)
            new_or_mod, _, deleted = detect_changes(project_root, state, files)

            if new_or_mod or deleted:
                count = len(new_or_mod) + len(deleted)
                print(f"  🔄 {count} changement(s) détecté(s) — indexation...")
                report = run_indexation(project_root, files, state, quiet=True)
                print(f"     ✅ {report.files_indexed} fichier(s) indexé(s) en {report.duration_ms}ms")

                # Nettoyer les supprimés
                for d in deleted:
                    state.files.pop(d, None)
                save_index(project_root, state)

    except KeyboardInterrupt:
        print("\n  👋 Watch arrêté.\n")


# ── Status ────────────────────────────────────────────────────────────────────

def show_status(project_root: Path, as_json: bool = False) -> str:
    """Affiche l'état de l'index."""
    state = load_index(project_root)
    files = discover_files(project_root)
    new_or_mod, unchanged, deleted = detect_changes(project_root, state, files)

    status = {
        "version": state.version,
        "last_run": state.last_run or "jamais",
        "total_indexed": state.total_indexed,
        "files_tracked": len(state.files),
        "files_stale": len(new_or_mod),
        "files_deleted": len(deleted),
        "files_up_to_date": len(unchanged),
    }

    if as_json:
        return json.dumps(status, indent=2, ensure_ascii=False)

    lines: list[str] = []
    lines.append("\n  📊 Auto-Index Status")
    lines.append(f"  {'─' * 45}")
    lines.append(f"  Dernière exécution  : {status['last_run']}")
    lines.append(f"  Total indexé        : {status['total_indexed']}")
    lines.append(f"  Fichiers suivis     : {status['files_tracked']}")
    lines.append(f"  À jour              : {status['files_up_to_date']}")
    lines.append(f"  Stale (à ré-indexer) : {status['files_stale']}")
    lines.append(f"  Supprimés           : {status['files_deleted']}")

    if new_or_mod:
        lines.append("\n  📝 Fichiers stale :")
        for fpath in new_or_mod[:15]:
            lines.append(f"     • {fpath.relative_to(project_root)}")
        if len(new_or_mod) > 15:
            lines.append(f"     ... et {len(new_or_mod) - 15} de plus")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Auto-Index — Indexation RAG automatique Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--version", action="version",
                        version=f"auto-index {AUTO_INDEX_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # hook
    sub.add_parser("hook", help="Installer le git hook post-commit")

    # unhook
    sub.add_parser("unhook", help="Désinstaller le hook")

    # run
    run_p = sub.add_parser("run", help="Indexer maintenant")
    run_p.add_argument("--since", type=int, help="Fichiers modifiés depuis N jours")
    run_p.add_argument("--quiet", action="store_true", help="Mode silencieux")

    # watch
    watch_p = sub.add_parser("watch", help="Surveillance continue")
    watch_p.add_argument("--interval", type=float, default=WATCH_INTERVAL,
                         help=f"Intervalle de polling (défaut: {WATCH_INTERVAL}s)")

    # status
    sub.add_parser("status", help="État de l'index")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    as_json = getattr(args, "json", False)

    if args.command == "hook":
        ok, msg = install_hook(project_root)
        print(f"\n  {'✅' if ok else '❌'} {msg}\n")

    elif args.command == "unhook":
        ok, msg = uninstall_hook(project_root)
        print(f"\n  {'✅' if ok else '❌'} {msg}\n")

    elif args.command == "run":
        state = load_index(project_root)
        since = getattr(args, "since", None)
        quiet = getattr(args, "quiet", False)

        files = discover_git_modified(project_root, since) if since else discover_files(project_root)

        report = run_indexation(project_root, files, state, quiet)

        if not quiet:
            if as_json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(f"\n  ✅ Auto-index terminé : {report.files_indexed} fichier(s) indexé(s) "
                      f"({report.files_new} nouveaux, {report.files_modified} modifiés) "
                      f"en {report.duration_ms}ms\n")

    elif args.command == "watch":
        interval = getattr(args, "interval", WATCH_INTERVAL)
        watch_directory(project_root, interval)

    elif args.command == "status":
        print(show_status(project_root, as_json))


if __name__ == "__main__":
    main()
