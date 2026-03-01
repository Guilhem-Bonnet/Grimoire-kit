#!/usr/bin/env python3
"""
quorum.py — Quorum Sensing BMAD.
==================================

Détection de seuils collectifs via accumulation de signaux stigmergiques.
Quand suffisamment de signaux convergent, un mode collectif se déclenche :

  1. `scan`     — Scanner les signaux stigmergiques actifs
  2. `quorum`   — Vérifier si un quorum est atteint
  3. `modes`    — Lister les modes collectifs disponibles
  4. `trigger`  — Déclencher manuellement un mode
  5. `history`  — Historique des quorums atteints

Modes collectifs :
  - SECURITY_FIRST  : Accumulation de signaux sécurité → mode sécurité renforcé
  - QUALITY_FOCUS   : Accumulation de bugs/dette → focus qualité
  - SPEED_MODE      : Deadline proche + signaux urgence → mode vélocité
  - LEARNING_PHASE  : Accumulation d'erreurs répétées → mode apprentissage
  - REFACTOR_TIME   : Dette technique accumulée → mode refactoring

Inspiré du quorum sensing bactérien : communication chimique collective.

Usage :
  python3 quorum.py --project-root . scan
  python3 quorum.py --project-root . quorum
  python3 quorum.py --project-root . modes
  python3 quorum.py --project-root . trigger --mode SECURITY_FIRST
  python3 quorum.py --project-root . history

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
QUORUM_LOG = "quorum-log.json"

# Signal categories
SIGNAL_CATEGORIES = {
    "security": ["vuln", "cve", "injection", "xss", "csrf", "auth", "secret", "credential", "password"],
    "quality": ["bug", "fix", "patch", "regression", "broken", "fail", "error"],
    "urgency": ["urgent", "asap", "hotfix", "critical", "blocker", "deadline"],
    "learning": ["todo", "fixme", "hack", "workaround", "temporary", "kludge"],
    "debt": ["refactor", "cleanup", "deprecated", "legacy", "tech-debt", "debt"],
}


# ── Modes ────────────────────────────────────────────────────────────────────

@dataclass
class CollectiveMode:
    id: str
    name: str
    description: str
    signal_category: str             # Quelle catégorie de signaux
    threshold: int                    # Nombre de signaux pour déclencher
    actions: list[str] = field(default_factory=list)
    active: bool = False


COLLECTIVE_MODES: list[CollectiveMode] = [
    CollectiveMode(
        id="SECURITY_FIRST", name="Sécurité renforcée",
        description="Mode sécurité activé quand trop de signaux de vulnérabilité détectés",
        signal_category="security", threshold=5,
        actions=[
            "Activer immune-system.py scan sur tout le code",
            "Forcer review sécurité avant merge",
            "Bloquer les chemins d'exécution non-auditables",
        ],
    ),
    CollectiveMode(
        id="QUALITY_FOCUS", name="Focus qualité",
        description="Mode qualité quand trop de bugs/régressions s'accumulent",
        signal_category="quality", threshold=8,
        actions=[
            "Prioriser les fix avant les features",
            "Augmenter la couverture de tests",
            "Activer l'adversarial review systématique",
        ],
    ),
    CollectiveMode(
        id="SPEED_MODE", name="Mode vélocité",
        description="Mode rapide quand l'urgence s'accumule",
        signal_category="urgency", threshold=3,
        actions=[
            "Réduire le ceremony (skip non-essentiels)",
            "Prioriser le chemin le plus court",
            "Auto-merge sur tests verts",
        ],
    ),
    CollectiveMode(
        id="LEARNING_PHASE", name="Phase apprentissage",
        description="Mode apprentissage quand trop de TODO/HACK détectés",
        signal_category="learning", threshold=15,
        actions=[
            "Documenter les workarounds",
            "Créer des tickets de nettoyage",
            "Activer crescendo.py pour monter en compétence",
        ],
    ),
    CollectiveMode(
        id="REFACTOR_TIME", name="Refactoring",
        description="Mode refactoring quand la dette technique s'accumule",
        signal_category="debt", threshold=10,
        actions=[
            "Allouer 20% du temps au refactoring",
            "Identifier les fichiers les plus en dette",
            "Programmer des sessions de pair-review",
        ],
    ),
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Un signal stigmergique détecté."""
    category: str
    keyword: str
    file: str
    line: int
    context: str = ""

@dataclass
class QuorumResult:
    signals_by_category: dict[str, int] = field(default_factory=dict)
    total_signals: int = 0
    modes_triggered: list[str] = field(default_factory=list)
    modes_approaching: list[tuple[str, int, int]] = field(default_factory=list)  # mode, current, threshold
    all_signals: list[Signal] = field(default_factory=list)


# ── Signal Scanner ───────────────────────────────────────────────────────────

def scan_signals(project_root: Path) -> list[Signal]:
    """Scanne le projet pour détecter les signaux stigmergiques."""
    signals = []
    extensions = {".py", ".md", ".yaml", ".yml", ".sh", ".ts", ".js", ".json"}

    for fpath in project_root.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.suffix not in extensions:
            continue
        if ".git" in str(fpath) or "__pycache__" in str(fpath) or "node_modules" in str(fpath):
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            line_lower = line.lower()
            for category, keywords in SIGNAL_CATEGORIES.items():
                for kw in keywords:
                    if kw in line_lower:
                        signals.append(Signal(
                            category=category,
                            keyword=kw,
                            file=str(fpath.relative_to(project_root)),
                            line=i,
                            context=line.strip()[:100],
                        ))
                        break  # Un seul keyword par ligne par catégorie

    return signals


def check_quorum(project_root: Path) -> QuorumResult:
    """Vérifie si des quorums sont atteints."""
    signals = scan_signals(project_root)
    counts = Counter(s.category for s in signals)

    result = QuorumResult(
        signals_by_category=dict(counts),
        total_signals=len(signals),
        all_signals=signals,
    )

    for mode in COLLECTIVE_MODES:
        current = counts.get(mode.signal_category, 0)
        if current >= mode.threshold:
            result.modes_triggered.append(mode.id)
        elif current >= mode.threshold * 0.6:
            result.modes_approaching.append((mode.id, current, mode.threshold))

    return result


# ── History ──────────────────────────────────────────────────────────────────

def save_quorum_event(project_root: Path, result: QuorumResult) -> Path:
    out = project_root / "_bmad" / "_memory" / QUORUM_LOG
    out.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if out.exists():
        try:
            history = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    entry = {
        "timestamp": datetime.now().isoformat(),
        "total_signals": result.total_signals,
        "by_category": result.signals_by_category,
        "modes_triggered": result.modes_triggered,
    }
    history.append(entry)
    # Garder les 50 dernières entrées
    history = history[-50:]
    out.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ── Formatters ───────────────────────────────────────────────────────────────

def format_scan(signals: list[Signal]) -> str:
    counts = Counter(s.category for s in signals)
    lines = [f"📡 Signaux stigmergiques : {len(signals)} détectés\n"]
    for cat, count in counts.most_common():
        bar = "█" * min(20, count)
        lines.append(f"   {cat:15s} {bar} {count}")
    lines.append("")
    # Top signals
    lines.append("   Exemples :")
    shown = set()
    for s in signals[:15]:
        key = f"{s.category}:{s.file}"
        if key not in shown:
            lines.append(f"      [{s.category}] {s.file}:{s.line} — {s.context[:60]}")
            shown.add(key)
    return "\n".join(lines)


def format_quorum(result: QuorumResult) -> str:
    lines = [f"🔬 Quorum Sensing — {result.total_signals} signaux\n"]

    mode_map = {m.id: m for m in COLLECTIVE_MODES}

    if result.modes_triggered:
        lines.append("   🚨 QUORUMS ATTEINTS :")
        for mid in result.modes_triggered:
            m = mode_map[mid]
            count = result.signals_by_category.get(m.signal_category, 0)
            lines.append(f"      ■ {m.name} ({count}/{m.threshold} signaux)")
            for action in m.actions:
                lines.append(f"         → {action}")
        lines.append("")

    if result.modes_approaching:
        lines.append("   ⚠️ Quorums en approche :")
        for mid, current, threshold in result.modes_approaching:
            m = mode_map[mid]
            bar = "█" * int(current / threshold * 10) + "░" * (10 - int(current / threshold * 10))
            lines.append(f"      {bar} {m.name} ({current}/{threshold})")
        lines.append("")

    if not result.modes_triggered and not result.modes_approaching:
        lines.append("   ✅ Aucun quorum atteint — système en équilibre")

    return "\n".join(lines)


def format_modes() -> str:
    lines = ["🎛️ Modes collectifs disponibles\n"]
    for m in COLLECTIVE_MODES:
        lines.append(f"   [{m.id}]")
        lines.append(f"      {m.name} — {m.description}")
        lines.append(f"      Seuil : {m.threshold} signaux '{m.signal_category}'")
        lines.append("      Actions :")
        for action in m.actions:
            lines.append(f"         → {action}")
        lines.append("")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    signals = scan_signals(project_root)
    if args.json:
        counts = Counter(s.category for s in signals)
        print(json.dumps({"total": len(signals), "by_category": dict(counts)},
                         indent=2, ensure_ascii=False))
    else:
        print(format_scan(signals))
    return 0


def cmd_quorum(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    result = check_quorum(project_root)
    save_quorum_event(project_root, result)
    if args.json:
        print(json.dumps({
            "total_signals": result.total_signals,
            "by_category": result.signals_by_category,
            "modes_triggered": result.modes_triggered,
            "approaching": [{"mode": m, "current": c, "threshold": t}
                            for m, c, t in result.modes_approaching],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_quorum(result))
    return 0


def cmd_modes(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([{"id": m.id, "name": m.name, "threshold": m.threshold,
                           "category": m.signal_category}
                          for m in COLLECTIVE_MODES], indent=2, ensure_ascii=False))
    else:
        print(format_modes())
    return 0


def cmd_trigger(args: argparse.Namespace) -> int:
    mode_map = {m.id: m for m in COLLECTIVE_MODES}
    mode = mode_map.get(args.mode)
    if not mode:
        print(f"❌ Mode inconnu : {args.mode}")
        print(f"   Disponibles : {', '.join(mode_map.keys())}")
        return 1
    if args.json:
        print(json.dumps({"mode": mode.id, "name": mode.name, "actions": mode.actions},
                         indent=2, ensure_ascii=False))
    else:
        print(f"🚀 Mode activé manuellement : {mode.name}\n")
        for action in mode.actions:
            print(f"   → {action}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    logfile = project_root / "_bmad" / "_memory" / QUORUM_LOG
    if not logfile.exists():
        print("📜 Aucun historique — lancez 'quorum' d'abord")
        return 0
    data = json.loads(logfile.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"📜 Historique quorum ({len(data)} entrées)\n")
        for entry in data[-10:]:
            triggered = entry.get("modes_triggered", [])
            status = f"🚨 {', '.join(triggered)}" if triggered else "✅ équilibre"
            print(f"   {entry['timestamp'][:19]} — {entry['total_signals']} signaux — {status}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Quorum Sensing — Modes collectifs stigmergiques",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("scan", help="Scanner les signaux").set_defaults(func=cmd_scan)
    subs.add_parser("quorum", help="Vérifier les quorums").set_defaults(func=cmd_quorum)
    subs.add_parser("modes", help="Lister les modes collectifs").set_defaults(func=cmd_modes)

    p_trigger = subs.add_parser("trigger", help="Déclencher un mode")
    p_trigger.add_argument("--mode", required=True, help="ID du mode")
    p_trigger.set_defaults(func=cmd_trigger)

    subs.add_parser("history", help="Historique des quorums").set_defaults(func=cmd_history)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
