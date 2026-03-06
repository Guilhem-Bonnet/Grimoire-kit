#!/usr/bin/env python3
"""
session-lifecycle.py — Hooks de début et fin de session BMAD.
=============================================================

Orchestre des actions automatiques au démarrage et à la clôture
d'une session agent :

  pre-session :
    1. maintenance.py health-check (rate-limité à 1×/24h)
    2. Vérification de l'intégrité de _memory/

  post-session :
    1. dream.py --quick (consolidation rapide)
    2. stigmergy.py evaporate (nettoyage des signaux morts)
    3. session-save.py (sauvegarde de session)

Usage :
  python3 session-lifecycle.py --project-root . pre
  python3 session-lifecycle.py --project-root . post
  python3 session-lifecycle.py --project-root . status

Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

SESSION_LIFECYCLE_VERSION = "1.1.0"

# ── Constants ────────────────────────────────────────────────────────────────

LIFECYCLE_DIR = "_bmad-output/.session-lifecycle"
STATE_FILE = "current-session.json"
SESSION_CHAIN_FILE = "_bmad/_memory/session-chain.jsonl"
SESSION_CHAIN_MAX_ENTRIES = 50  # Keep last N summaries for context injection

# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class HookResult:
    """Résultat d'un hook individuel."""
    name: str = ""
    status: str = "pending"  # pending, running, completed, skipped, failed
    message: str = ""
    duration_seconds: float = 0.0


@dataclass
class LifecycleResult:
    """Résultat global d'un cycle pre/post."""
    phase: str = ""  # pre, post
    hooks: list[HookResult] = field(default_factory=list)
    status: str = "pending"
    started_at: str = ""
    completed_at: str = ""
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "hooks": [asdict(h) for h in self.hooks],
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_seconds": self.total_duration_seconds,
        }


# ── Module Loader ────────────────────────────────────────────────────────────


def _load_module(name: str, path: Path):
    """Charge un module Python depuis un chemin fichier."""
    if not path.exists():
        return None
    safe_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe_name, path)
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Pre-Session Hooks ────────────────────────────────────────────────────────


def _hook_health_check(project_root: Path) -> HookResult:
    """Exécute maintenance.py health-check (rate-limité)."""
    result = HookResult(name="health-check")
    start = time.monotonic()

    maintenance_path = project_root / "framework" / "memory" / "maintenance.py"
    try:
        mod = _load_module("maintenance", maintenance_path)
        if mod and hasattr(mod, "health_check"):
            mod.health_check(force=False)
            result.status = "completed"
            result.message = "Health-check exécuté (rate-limité 1×/24h)"
        else:
            result.status = "skipped"
            result.message = "Module maintenance introuvable"
    except Exception as exc:
        result.status = "failed"
        result.message = f"Erreur: {exc}"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


def _hook_memory_integrity(project_root: Path) -> HookResult:
    """Vérifie l'intégrité basique de _memory/."""
    result = HookResult(name="memory-integrity")
    start = time.monotonic()

    memory_dir = project_root / "_memory"
    if not memory_dir.exists():
        result.status = "skipped"
        result.message = "Dossier _memory/ absent — probablement un nouveau projet"
        result.duration_seconds = round(time.monotonic() - start, 3)
        return result

    issues = []
    # Check critical files
    for critical in ["memories.json", "shared-context.md"]:
        f = memory_dir / critical
        if f.exists():
            try:
                content = f.read_text(encoding="utf-8")
                if critical.endswith(".json"):
                    json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                issues.append(f"{critical}: {exc}")

    if issues:
        result.status = "completed"
        result.message = f"Problèmes détectés: {'; '.join(issues)}"
    else:
        result.status = "completed"
        result.message = "Intégrité OK"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


# ── Post-Session Hooks ───────────────────────────────────────────────────────


def _hook_dream_quick(project_root: Path) -> HookResult:
    """Exécute dream.py --quick pour consolidation rapide."""
    result = HookResult(name="dream-quick")
    start = time.monotonic()

    dream_path = project_root / "framework" / "tools" / "dream.py"
    try:
        mod = _load_module("dream", dream_path)
        if mod and hasattr(mod, "DreamEngine"):
            engine = mod.DreamEngine(project_root)
            dream_result = engine.dream(quick=True)
            result.status = "completed"
            insights = dream_result.get("insights_count", 0) if isinstance(dream_result, dict) else 0
            result.message = f"Dream quick terminé — {insights} insights"
        else:
            result.status = "skipped"
            result.message = "Module dream introuvable ou API incompatible"
    except Exception as exc:
        result.status = "failed"
        result.message = f"Erreur: {exc}"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


def _hook_stigmergy_evaporate(project_root: Path) -> HookResult:
    """Exécute stigmergy.py evaporate pour nettoyer les signaux morts."""
    result = HookResult(name="stigmergy-evaporate")
    start = time.monotonic()

    stigmergy_path = project_root / "framework" / "tools" / "stigmergy.py"
    try:
        mod = _load_module("stigmergy", stigmergy_path)
        if mod and hasattr(mod, "load_board") and hasattr(mod, "evaporate"):
            board = mod.load_board(project_root)
            board, evaporated = mod.evaporate(board)
            mod.save_board(board, project_root)
            result.status = "completed"
            result.message = f"Évaporation terminée — {evaporated} signaux nettoyés"
        else:
            result.status = "skipped"
            result.message = "Module stigmergy introuvable ou API incompatible"
    except Exception as exc:
        result.status = "failed"
        result.message = f"Erreur: {exc}"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


def _hook_session_save(project_root: Path) -> HookResult:
    """Sauvegarde l'état de session via session-save.py."""
    result = HookResult(name="session-save")
    start = time.monotonic()

    save_path = project_root / "framework" / "memory" / "session-save.py"
    try:
        mod = _load_module("session_save", save_path)
        if mod and hasattr(mod, "save_session"):
            save_result = mod.save_session(project_root=project_root)
            result.status = "completed"
            result.message = f"Session sauvegardée: {save_result.get('file', 'OK') if isinstance(save_result, dict) else 'OK'}"
        else:
            result.status = "skipped"
            result.message = "Module session-save introuvable ou API incompatible"
    except Exception as exc:
        result.status = "failed"
        result.message = f"Erreur: {exc}"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


def _hook_session_chain(project_root: Path, lifecycle_result: LifecycleResult) -> HookResult:
    """Écrit un résumé structuré de la session dans session-chain.jsonl.

    Ce fichier JSONL sert de mémoire cross-session : les N derniers
    résumés sont injectés en contexte au début de la session suivante.
    """
    result = HookResult(name="session-chain")
    start = time.monotonic()

    try:
        chain_path = project_root / SESSION_CHAIN_FILE
        chain_path.parent.mkdir(parents=True, exist_ok=True)

        # Build session summary from hook results
        hook_summaries = []
        for h in lifecycle_result.hooks:
            if h.status == "completed" and h.message:
                hook_summaries.append(h.message)

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "phase": lifecycle_result.phase,
            "status": lifecycle_result.status,
            "duration_s": lifecycle_result.total_duration_seconds,
            "hooks_completed": [h.name for h in lifecycle_result.hooks if h.status == "completed"],
            "hooks_failed": [h.name for h in lifecycle_result.hooks if h.status == "failed"],
            "summaries": hook_summaries,
        }

        # Append entry
        with open(chain_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Prune old entries if over cap
        _prune_session_chain(chain_path)

        result.status = "completed"
        result.message = f"Session chain updated ({chain_path.name})"
    except Exception as exc:
        result.status = "failed"
        result.message = f"Erreur: {exc}"

    result.duration_seconds = round(time.monotonic() - start, 3)
    return result


def _prune_session_chain(chain_path: Path) -> None:
    """Garde uniquement les N dernières entrées du fichier JSONL."""
    try:
        lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > SESSION_CHAIN_MAX_ENTRIES:
            kept = lines[-SESSION_CHAIN_MAX_ENTRIES:]
            chain_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_session_chain(project_root: Path, limit: int = 5) -> list[dict]:
    """Charge les N derniers résumés de session pour injection contextuelle."""
    chain_path = project_root / SESSION_CHAIN_FILE
    if not chain_path.exists():
        return []
    try:
        lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
        entries = []
        for line in lines[-limit:]:
            if line.strip():
                entries.append(json.loads(line))
        return entries
    except (json.JSONDecodeError, OSError):
        return []


# ── Lifecycle Orchestration ──────────────────────────────────────────────────


def run_pre_session(project_root: Path) -> LifecycleResult:
    """Exécute les hooks pré-session."""
    result = LifecycleResult(
        phase="pre",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    start = time.monotonic()

    hooks = [
        _hook_health_check,
        _hook_memory_integrity,
    ]

    for hook_fn in hooks:
        hook_result = hook_fn(project_root)
        result.hooks.append(hook_result)

    failed = [h for h in result.hooks if h.status == "failed"]
    result.status = "completed" if not failed else "partial"
    result.total_duration_seconds = round(time.monotonic() - start, 3)
    result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    _save_state(project_root, result)
    return result


def run_post_session(project_root: Path) -> LifecycleResult:
    """Exécute les hooks post-session."""
    result = LifecycleResult(
        phase="post",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    start = time.monotonic()

    hooks = [
        _hook_dream_quick,
        _hook_stigmergy_evaporate,
        _hook_session_save,
    ]

    for hook_fn in hooks:
        hook_result = hook_fn(project_root)
        result.hooks.append(hook_result)

    # Session chain — append structured summary for cross-session memory
    chain_result = _hook_session_chain(project_root, result)
    result.hooks.append(chain_result)

    failed = [h for h in result.hooks if h.status == "failed"]
    result.status = "completed" if not failed else "partial"
    result.total_duration_seconds = round(time.monotonic() - start, 3)
    result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    _save_state(project_root, result)
    return result


def get_status(project_root: Path) -> dict:
    """Retourne l'état du dernier cycle de session."""
    state_file = project_root / LIFECYCLE_DIR / STATE_FILE
    if not state_file.exists():
        return {"status": "no-session", "message": "Aucune session enregistrée"}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _save_state(project_root: Path, result: LifecycleResult) -> None:
    """Sauvegarde l'état courant."""
    state_dir = project_root / LIFECYCLE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILE
    state_file.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Display ──────────────────────────────────────────────────────────────────


def _print_result(result: LifecycleResult) -> None:
    """Affiche le résultat d'un cycle."""
    phase_icons = {"pre": "🌅", "post": "🌙"}
    icon = phase_icons.get(result.phase, "⚙️")
    print(f"\n  {icon} Session Lifecycle — {result.phase.upper()}")
    print(f"  {'─' * 50}")

    for hook in result.hooks:
        status_icon = {
            "completed": "✅",
            "skipped": "⏭️",
            "failed": "❌",
            "pending": "⏳",
        }.get(hook.status, "❓")
        print(f"  {status_icon} {hook.name:25s} [{hook.duration_seconds:.3f}s] {hook.message}")

    print(f"\n  Status: {result.status} — {result.total_duration_seconds:.3f}s total")
    print()


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-lifecycle",
        description="Hooks automatiques de début/fin de session BMAD",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {SESSION_LIFECYCLE_VERSION}")

    subs = parser.add_subparsers(dest="command", help="Phase du lifecycle")
    subs.add_parser("pre", help="Hooks pré-session (health-check, integrity)")
    subs.add_parser("post", help="Hooks post-session (dream, evaporate, save)")
    subs.add_parser("status", help="État du dernier cycle")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    if args.command == "pre":
        result = run_pre_session(root)
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_result(result)

    elif args.command == "post":
        result = run_post_session(root)
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_result(result)

    elif args.command == "status":
        state = get_status(root)
        if args.as_json:
            print(json.dumps(state, indent=2, ensure_ascii=False))
        else:
            if state.get("status") == "no-session":
                print("  ℹ️ Aucune session enregistrée")
            else:
                print(json.dumps(state, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
