#!/usr/bin/env python3
"""
dashboard.py — Bioluminescence Dashboard BMAD.
================================================

Dashboard complet en Markdown + Mermaid montrant l'état de santé,
les métriques, et la dynamique du projet en un seul artefact.

  1. `health`    — Métriques de santé du projet
  2. `entropy`   — Entropie et complexité
  3. `pareto`    — Analyse 80/20 (quels 20% créent 80% de la valeur)
  4. `activity`  — Activité récente
  5. `full`      — Dashboard complet (Markdown)

Métriques :
  - Health score global (0-100)
  - Shannon entropy des fichiers
  - Concentration Pareto (gini coefficient approx)
  - Velocity (commits/semaine)
  - Coverage metrics

Principe : "Ce que vous ne mesurez pas, vous ne pouvez pas l'améliorer."

Usage :
  python3 dashboard.py --project-root . health
  python3 dashboard.py --project-root . entropy
  python3 dashboard.py --project-root . pareto
  python3 dashboard.py --project-root . activity
  python3 dashboard.py --project-root . full > dashboard.md

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class HealthMetric:
    name: str
    score: int        # 0-100
    detail: str = ""

@dataclass
class HealthReport:
    metrics: list[HealthMetric] = field(default_factory=list)
    global_score: int = 0

@dataclass
class EntropyReport:
    file_entropy: float = 0.0    # Shannon entropy of file types
    dir_entropy: float = 0.0     # Shannon entropy of directories
    total_files: int = 0
    type_distribution: dict[str, int] = field(default_factory=dict)

@dataclass
class ParetoReport:
    top_20_files: list[tuple[str, int]] = field(default_factory=list)
    top_20_value: float = 0.0      # % of total size
    gini: float = 0.0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _shannon_entropy(counts: dict[str, int]) -> float:
    """Calcule l'entropie de Shannon."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


def _gini_coefficient(values: list[int]) -> float:
    """Calcule le coefficient de Gini (inégalité)."""
    if not values:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumsum = 0
    gini_sum = 0
    for _i, v in enumerate(sorted_vals):
        cumsum += v
        gini_sum += cumsum
    gini = (2 * gini_sum) / (n * total) - (n + 1) / n
    return max(0, min(1, gini))


def _git_activity(project_root: Path) -> list[dict]:
    """Récupère l'activité git récente."""
    try:
        r = subprocess.run(
            ["git", "log", "--since=30 days ago", "--format=%H|%an|%ai|%s", "--no-merges"],
            capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if r.returncode != 0:
            return []
        commits = []
        for line in r.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2][:10],
                    "message": parts[3][:80],
                })
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


# ── Health ───────────────────────────────────────────────────────────────────

def analyze_health(project_root: Path) -> HealthReport:
    report = HealthReport()

    # 1. Documentation
    docs = list(project_root.rglob("docs/**/*.md"))
    readme = (project_root / "README.md").exists()
    doc_score = min(100, len(docs) * 12 + (30 if readme else 0))
    report.metrics.append(HealthMetric("Documentation", doc_score,
                                       f"{len(docs)} docs, README: {'✅' if readme else '❌'}"))

    # 2. Tests
    tests = list(project_root.rglob("test_*.py")) + list(project_root.rglob("*.test.*"))
    test_score = min(100, len(tests) * 5)
    report.metrics.append(HealthMetric("Tests", test_score, f"{len(tests)} fichiers de test"))

    # 3. Outillage
    tools_dir = project_root / "framework" / "tools"
    tools = list(tools_dir.glob("*.py")) if tools_dir.exists() else []
    tool_score = min(100, len(tools) * 4)
    report.metrics.append(HealthMetric("Outillage", tool_score, f"{len(tools)} outils"))

    # 4. Fraîcheur (dernière activité)
    activity = _git_activity(project_root)
    fresh_score = min(100, len(activity) * 5)
    report.metrics.append(HealthMetric("Fraîcheur", fresh_score,
                                       f"{len(activity)} commits ce mois"))

    # 5. Cohérence (pas de TODO excessifs)
    todo_count = 0
    for f in project_root.rglob("*.py"):
        try:
            todo_count += f.read_text(encoding="utf-8", errors="ignore").upper().count("TODO")
        except OSError:
            pass
    coherence_score = max(0, 100 - todo_count * 2)
    report.metrics.append(HealthMetric("Cohérence", coherence_score,
                                       f"{todo_count} TODOs"))

    if report.metrics:
        report.global_score = round(sum(m.score for m in report.metrics) / len(report.metrics))

    return report


# ── Entropy ──────────────────────────────────────────────────────────────────

def analyze_entropy(project_root: Path) -> EntropyReport:
    report = EntropyReport()
    files = [f for f in project_root.rglob("*") if f.is_file()
             and ".git" not in str(f) and "__pycache__" not in str(f)]
    report.total_files = len(files)

    # File type distribution
    ext_counts = Counter(f.suffix or "(no ext)" for f in files)
    report.type_distribution = dict(ext_counts.most_common(15))
    report.file_entropy = _shannon_entropy(dict(ext_counts))

    # Directory distribution
    dir_counts = Counter()
    for f in files:
        try:
            rel = f.relative_to(project_root)
            top = rel.parts[0] if rel.parts else "(root)"
            dir_counts[top] += 1
        except ValueError:
            pass
    report.dir_entropy = _shannon_entropy(dict(dir_counts))

    return report


# ── Pareto ───────────────────────────────────────────────────────────────────

def analyze_pareto(project_root: Path) -> ParetoReport:
    report = ParetoReport()
    files = []
    for f in project_root.rglob("*"):
        if f.is_file() and ".git" not in str(f) and "__pycache__" not in str(f):
            try:
                files.append((str(f.relative_to(project_root)), f.stat().st_size))
            except OSError:
                pass

    if not files:
        return report

    files.sort(key=lambda x: x[1], reverse=True)
    total_size = sum(s for _, s in files)
    top_n = max(1, len(files) // 5)

    report.top_20_files = files[:top_n]
    top_size = sum(s for _, s in files[:top_n])
    report.top_20_value = top_size / total_size if total_size > 0 else 0
    report.gini = _gini_coefficient([s for _, s in files])

    return report


# ── Formatters ───────────────────────────────────────────────────────────────

def format_health(report: HealthReport) -> str:
    # Determine emoji
    if report.global_score >= 80:
        emoji = "🟢"
    elif report.global_score >= 60:
        emoji = "🟡"
    elif report.global_score >= 40:
        emoji = "🟠"
    else:
        emoji = "🔴"

    lines = [f"{emoji} Santé du projet : {report.global_score}/100\n"]
    for m in report.metrics:
        bar = "█" * (m.score // 10) + "░" * (10 - m.score // 10)
        lines.append(f"   {m.name:15s} {bar} {m.score:3d}/100  {m.detail}")
    return "\n".join(lines)


def format_entropy(report: EntropyReport) -> str:
    lines = [
        f"📊 Entropie du projet ({report.total_files} fichiers)\n",
        f"   Entropie types fichiers : {report.file_entropy:.2f} bits",
        f"   Entropie répertoires    : {report.dir_entropy:.2f} bits",
        "",
        "   Distribution des types :",
    ]
    for ext, count in sorted(report.type_distribution.items(), key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * min(20, count)
        lines.append(f"      {ext:10s} {bar} {count}")
    return "\n".join(lines)


def format_full_dashboard(project_root: Path) -> str:
    """Génère un dashboard Markdown complet."""
    health = analyze_health(project_root)
    entropy = analyze_entropy(project_root)
    pareto = analyze_pareto(project_root)
    activity = _git_activity(project_root)

    lines = [
        "# 🌟 Dashboard BMAD — Bioluminescence",
        "",
        f"> Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Santé globale",
        "",
        f"**Score : {health.global_score}/100**",
        "",
        "| Métrique | Score | Détail |",
        "|----------|-------|--------|",
    ]
    for m in health.metrics:
        bar = "█" * (m.score // 10) + "░" * (10 - m.score // 10)
        lines.append(f"| {m.name} | {bar} {m.score}/100 | {m.detail} |")

    lines.extend([
        "",
        "## Entropie & Complexité",
        "",
        f"- Fichiers totaux : {entropy.total_files}",
        f"- Entropie types : {entropy.file_entropy:.2f} bits",
        f"- Entropie dirs : {entropy.dir_entropy:.2f} bits",
        "",
        "```mermaid",
        "pie title Distribution des types de fichiers",
    ])
    for ext, count in sorted(entropy.type_distribution.items(), key=lambda x: x[1], reverse=True)[:8]:
        lines.append(f'    "{ext}" : {count}')
    lines.extend(["```", ""])

    lines.extend([
        "## Analyse Pareto (80/20)",
        "",
        f"- Top 20% des fichiers = {pareto.top_20_value:.0%} de la taille totale",
        f"- Coefficient de Gini = {pareto.gini:.2f}",
        "",
    ])

    if activity:
        lines.extend([
            "## Activité récente (30j)",
            "",
            f"- Commits : {len(activity)}",
            "",
            "| Date | Auteur | Message |",
            "|------|--------|---------|",
        ])
        for c in activity[:10]:
            lines.append(f"| {c['date']} | {c['author']} | {c['message']} |")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_health(args: argparse.Namespace) -> int:
    report = analyze_health(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({"global_score": report.global_score,
                          "metrics": [{"name": m.name, "score": m.score, "detail": m.detail}
                                      for m in report.metrics]}, indent=2, ensure_ascii=False))
    else:
        print(format_health(report))
    return 0


def cmd_entropy(args: argparse.Namespace) -> int:
    report = analyze_entropy(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({"file_entropy": report.file_entropy, "dir_entropy": report.dir_entropy,
                          "total_files": report.total_files, "types": report.type_distribution},
                         indent=2, ensure_ascii=False))
    else:
        print(format_entropy(report))
    return 0


def cmd_pareto(args: argparse.Namespace) -> int:
    report = analyze_pareto(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({"top_20_value_pct": report.top_20_value, "gini": report.gini},
                         indent=2, ensure_ascii=False))
    else:
        print("📊 Analyse Pareto\n")
        print(f"   Top 20% = {report.top_20_value:.0%} de la taille")
        print(f"   Gini = {report.gini:.2f}")
    return 0


def cmd_activity(args: argparse.Namespace) -> int:
    activity = _git_activity(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(activity, indent=2, ensure_ascii=False))
    else:
        print(f"📈 Activité récente — {len(activity)} commits (30j)\n")
        for c in activity[:15]:
            print(f"   {c['date']} {c['author']:15s} {c['message']}")
    return 0


def cmd_full(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    if args.json:
        health = analyze_health(project_root)
        entropy = analyze_entropy(project_root)
        pareto = analyze_pareto(project_root)
        print(json.dumps({
            "health": health.global_score,
            "entropy": entropy.file_entropy,
            "pareto_gini": pareto.gini,
        }, indent=2))
    else:
        print(format_full_dashboard(project_root))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Bioluminescence Dashboard",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("health", help="Métriques de santé").set_defaults(func=cmd_health)
    subs.add_parser("entropy", help="Entropie").set_defaults(func=cmd_entropy)
    subs.add_parser("pareto", help="Analyse 80/20").set_defaults(func=cmd_pareto)
    subs.add_parser("activity", help="Activité récente").set_defaults(func=cmd_activity)
    subs.add_parser("full", help="Dashboard complet").set_defaults(func=cmd_full)

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
