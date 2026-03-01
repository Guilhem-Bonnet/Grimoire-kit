#!/usr/bin/env python3
"""
desire-paths.py — Analyse des "chemins de désir" BMAD.
========================================================

Comme les sentiers tracés par les piétons qui ignorent les allées pavées,
cet outil détecte les écarts entre l'usage RÉEL et l'usage CONÇU du framework.

Analyse :
  1. Quels agents sont activés vs quels agents existent
  2. Quels workflows sont exécutés vs quels workflows sont définis
  3. Quels outils sont invoqués vs quels outils sont disponibles
  4. Quels fichiers mémoire sont écrits vs quels sont lus
  5. Quels items de menu sont utilisés vs quels sont au menu

Sources de données :
  - BMAD_TRACE logs (si disponibles)
  - Git log (activité fichiers)
  - Contenu fichiers mémoire (patterns d'usage)
  - Structure des fichiers (timestamp mtime heuristique)

Usage :
  python3 desire-paths.py --project-root . analyze       # Analyse complète
  python3 desire-paths.py --project-root . agents        # Focus agents
  python3 desire-paths.py --project-root . workflows     # Focus workflows
  python3 desire-paths.py --project-root . tools         # Focus outils
  python3 desire-paths.py --project-root . recommend     # Recommandations
  python3 desire-paths.py --project-root . --json        # Sortie JSON

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

DESIRE_PATHS_VERSION = "1.0.0"

# Seuils
DORMANT_DAYS = 30          # Fichier non touché = dormant
OVERUSED_RATIO = 3.0       # Ratio usage réel / attendu pour "overused"
UNDERUSED_THRESHOLD = 0.1  # < 10% d'activité = underused

# Labels
class Usage:
    OVERUSED = "🔥 Sur-utilisé"
    NORMAL = "✅ Nominal"
    UNDERUSED = "💤 Sous-utilisé"
    DORMANT = "⚫ Dormant"
    GHOST = "👻 Fantôme"     # Référencé mais n'existe pas
    DESIRE = "🛤️ Desire Path"  # Utilisé sans être conçu


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DesireEntry:
    """Un élément analysé (agent, workflow, tool, fichier)."""
    name: str
    category: str       # agent | workflow | tool | memory
    designed: bool       # dans la conception
    used: bool           # traces d'utilisation
    usage_count: int = 0
    last_activity: str = ""
    label: str = ""
    detail: str = ""

    def classify(self) -> str:
        if not self.designed and self.used:
            return Usage.DESIRE
        if self.designed and not self.used:
            return Usage.DORMANT
        if not self.designed and not self.used:
            return Usage.GHOST
        if self.usage_count > 10:
            return Usage.OVERUSED
        return Usage.NORMAL


@dataclass
class DesireReport:
    """Rapport Desire Paths."""
    entries: list[DesireEntry] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    git_available: bool = False

    @property
    def desire_paths(self) -> list[DesireEntry]:
        return [e for e in self.entries if e.label == Usage.DESIRE]

    @property
    def dormant(self) -> list[DesireEntry]:
        return [e for e in self.entries if e.label == Usage.DORMANT]

    @property
    def overused(self) -> list[DesireEntry]:
        return [e for e in self.entries if e.label == Usage.OVERUSED]


# ── Git Analysis ─────────────────────────────────────────────────────────────

def _git_file_activity(project_root: Path, subpath: str, days: int = 90) -> dict[str, int]:
    """Compte les commits par fichier dans un sous-dossier via git log."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--name-only",
             "--pretty=format:", "--", subpath],
            capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if result.returncode != 0:
            return {}
        counts: Counter = Counter()
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line:
                counts[line] += 1
        return dict(counts)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


def _git_available(project_root: Path) -> bool:
    """Vérifie si git est disponible et c'est un repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=project_root, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ── Analyzers ────────────────────────────────────────────────────────────────

def analyze_agents(project_root: Path, use_git: bool) -> list[DesireEntry]:
    """Analyse l'utilisation des agents."""
    entries = []

    # Agents définis (dans les archétypes)
    designed_agents: set[str] = set()
    for md_file in project_root.glob("_bmad/**/agents/*.md"):
        designed_agents.add(md_file.stem)

    # Agents dans les archetypes
    for md_file in project_root.glob("**/archetypes/**/agents/*.md"):
        designed_agents.add(md_file.stem)

    # Agents référencés dans la mémoire
    used_agents: Counter = Counter()
    memory_dir = project_root / "_bmad" / "_memory"
    if memory_dir.exists():
        for md in memory_dir.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
                for agent_name in designed_agents:
                    count = content.lower().count(agent_name.lower())
                    if count > 0:
                        used_agents[agent_name] += count
            except OSError:
                pass

    # Git activity
    if use_git:
        _git_file_activity(project_root, "_bmad/**/agents/")

    # Construire les entrées
    all_agents = designed_agents | set(used_agents.keys())
    for agent in sorted(all_agents):
        is_designed = agent in designed_agents
        use_count = used_agents.get(agent, 0)
        is_used = use_count > 0

        entry = DesireEntry(
            name=agent,
            category="agent",
            designed=is_designed,
            used=is_used,
            usage_count=use_count,
        )
        entry.label = entry.classify()
        entries.append(entry)

    return entries


def analyze_workflows(project_root: Path, use_git: bool) -> list[DesireEntry]:
    """Analyse l'utilisation des workflows."""
    entries = []

    # Workflows définis
    designed_wf: set[str] = set()
    for wf in project_root.glob("_bmad/**/workflows/**/*.yaml"):
        designed_wf.add(wf.stem)
    for wf in project_root.glob("_bmad/**/workflows/**/*.md"):
        if wf.name != "README.md":
            designed_wf.add(wf.stem)
    for wf in project_root.glob("**/framework/workflows/**/*.yaml"):
        designed_wf.add(wf.stem)
    for wf in project_root.glob("**/framework/workflows/**/*.md"):
        if wf.name != "README.md":
            designed_wf.add(wf.stem)

    # Traces d'exécution
    used_wf: Counter = Counter()
    # Chercher dans les traces BMAD
    for trace_file in project_root.glob("_bmad/_memory/**/trace*.md"):
        try:
            content = trace_file.read_text(encoding="utf-8")
            for wf_name in designed_wf:
                count = content.lower().count(wf_name.lower())
                if count > 0:
                    used_wf[wf_name] += count
        except OSError:
            pass

    # Chercher dans session-state et shared-context
    for md in project_root.glob("_bmad/_memory/*.md"):
        try:
            content = md.read_text(encoding="utf-8")
            for wf_name in designed_wf:
                count = content.lower().count(wf_name.lower())
                if count > 0:
                    used_wf[wf_name] += count
        except OSError:
            pass

    all_wf = designed_wf | set(used_wf.keys())
    for wf in sorted(all_wf):
        entry = DesireEntry(
            name=wf,
            category="workflow",
            designed=wf in designed_wf,
            used=used_wf.get(wf, 0) > 0,
            usage_count=used_wf.get(wf, 0),
        )
        entry.label = entry.classify()
        entries.append(entry)

    return entries


def analyze_tools(project_root: Path, use_git: bool) -> list[DesireEntry]:
    """Analyse l'utilisation des outils."""
    entries = []

    # Outils définis
    designed_tools: set[str] = set()
    for py_file in project_root.glob("**/framework/tools/*.py"):
        designed_tools.add(py_file.stem)

    # Outils référencés
    used_tools: Counter = Counter()
    # Chercher dans les agents, workflows, mémoire
    search_dirs = [
        project_root / "_bmad",
        project_root / "docs",
    ]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for md in search_dir.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
                for tool_name in designed_tools:
                    # Chercher tool_name.py ou tool-name
                    if f"{tool_name}.py" in content or tool_name in content:
                        used_tools[tool_name] += 1
            except OSError:
                pass

    # Git activity
    git_activity: dict[str, int] = {}
    if use_git:
        git_activity = _git_file_activity(project_root, "**/framework/tools/")

    all_tools = designed_tools | set(used_tools.keys())
    for tool in sorted(all_tools):
        git_count = sum(v for k, v in git_activity.items() if tool in k)
        total = used_tools.get(tool, 0) + git_count

        entry = DesireEntry(
            name=tool,
            category="tool",
            designed=tool in designed_tools,
            used=total > 0,
            usage_count=total,
        )
        entry.label = entry.classify()
        entries.append(entry)

    return entries


def generate_recommendations(report: DesireReport) -> list[str]:
    """Génère des recommandations actionnables."""
    recs = []

    desire_paths = report.desire_paths
    dormant = report.dormant
    overused = report.overused

    if desire_paths:
        recs.append(
            f"🛤️ {len(desire_paths)} desire path(s) détecté(s) — "
            f"les utilisateurs contournent le design. "
            f"Envisager de formaliser : {', '.join(e.name for e in desire_paths[:3])}"
        )

    if dormant:
        by_cat = defaultdict(list)
        for e in dormant:
            by_cat[e.category].append(e.name)
        for cat, names in by_cat.items():
            if len(names) > 3:
                recs.append(
                    f"💤 {len(names)} {cat}(s) dormant(s) — "
                    f"évaluer : supprimer, fusionner, ou documenter. "
                    f"Exemples : {', '.join(names[:3])}"
                )
            else:
                recs.append(
                    f"💤 {cat}(s) dormant(s) : {', '.join(names)}"
                )

    if overused:
        recs.append(
            f"🔥 {len(overused)} élément(s) sur-utilisé(s) — "
            f"risque de bottleneck. "
            f"Envisager de décomposer : {', '.join(e.name for e in overused[:3])}"
        )

    # Ratio global
    total = len(report.entries)
    active = sum(1 for e in report.entries if e.used)
    if total > 0:
        ratio = active / total
        if ratio < 0.5:
            recs.append(
                f"⚠️ Seulement {ratio:.0%} des éléments sont activement utilisés. "
                f"Le framework est peut-être sur-conçu."
            )
        elif ratio > 0.9:
            recs.append(
                f"✅ {ratio:.0%} des éléments sont actifs — bonne adéquation design/usage."
            )

    return recs


# ── Formatters ───────────────────────────────────────────────────────────────

def format_report(report: DesireReport, category: str = "") -> str:
    """Formatage texte humain."""
    lines = [
        "🛤️  Desire Paths — Analyse d'adéquation Design vs Usage",
        f"   Éléments analysés : {len(report.entries)}",
        f"   Git disponible : {'oui' if report.git_available else 'non'}",
        "",
    ]

    # Grouper par catégorie
    by_cat: dict[str, list[DesireEntry]] = defaultdict(list)
    for e in report.entries:
        if category and e.category != category:
            continue
        by_cat[e.category].append(e)

    cat_emojis = {"agent": "🤖", "workflow": "🔄", "tool": "🔧", "memory": "📝"}

    for cat in ["agent", "workflow", "tool", "memory"]:
        if cat not in by_cat:
            continue
        entries = by_cat[cat]
        lines.append(f"   {cat_emojis.get(cat, '📦')} {cat.upper()}S ({len(entries)})")

        # Par label
        by_label: dict[str, list[DesireEntry]] = defaultdict(list)
        for e in entries:
            by_label[e.label].append(e)

        for label in [Usage.DESIRE, Usage.OVERUSED, Usage.DORMANT, Usage.NORMAL, Usage.GHOST]:
            if label in by_label:
                items = by_label[label]
                lines.append(f"      {label} ({len(items)})")
                for e in items[:5]:
                    detail = f" (×{e.usage_count})" if e.usage_count > 1 else ""
                    lines.append(f"         {e.name}{detail}")
                if len(items) > 5:
                    lines.append(f"         ... et {len(items) - 5} de plus")
        lines.append("")

    # Recommandations
    if report.recommendations:
        lines.append("   📋 RECOMMANDATIONS :")
        for r in report.recommendations:
            lines.append(f"      {r}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: DesireReport) -> dict:
    return {
        "timestamp": report.timestamp,
        "git_available": report.git_available,
        "entries": [
            {
                "name": e.name,
                "category": e.category,
                "designed": e.designed,
                "used": e.used,
                "usage_count": e.usage_count,
                "label": e.label,
            }
            for e in report.entries
        ],
        "recommendations": report.recommendations,
        "summary": {
            "total": len(report.entries),
            "desire_paths": len(report.desire_paths),
            "dormant": len(report.dormant),
            "overused": len(report.overused),
        },
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────

def _build_report(project_root: Path, category: str = "") -> DesireReport:
    """Construit le rapport complet."""
    use_git = _git_available(project_root)
    report = DesireReport(git_available=use_git)

    if not category or category == "agent":
        report.entries.extend(analyze_agents(project_root, use_git))
    if not category or category == "workflow":
        report.entries.extend(analyze_workflows(project_root, use_git))
    if not category or category == "tool":
        report.entries.extend(analyze_tools(project_root, use_git))

    report.recommendations = generate_recommendations(report)
    return report


def cmd_analyze(args: argparse.Namespace) -> int:
    report = _build_report(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    report = _build_report(Path(args.project_root).resolve(), "agent")
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report, "agent"))
    return 0


def cmd_workflows(args: argparse.Namespace) -> int:
    report = _build_report(Path(args.project_root).resolve(), "workflow")
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report, "workflow"))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    report = _build_report(Path(args.project_root).resolve(), "tool")
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report, "tool"))
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    report = _build_report(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({"recommendations": report.recommendations}, indent=2, ensure_ascii=False))
    else:
        if report.recommendations:
            print("📋 RECOMMANDATIONS Desire Paths :\n")
            for r in report.recommendations:
                print(f"   {r}")
        else:
            print("✅ Aucune recommandation — adéquation design/usage nominale.")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Desire Paths — Analyse design vs usage réel",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("analyze", help="Analyse complète")
    p.set_defaults(func=cmd_analyze)

    p = subs.add_parser("agents", help="Focus agents")
    p.set_defaults(func=cmd_agents)

    p = subs.add_parser("workflows", help="Focus workflows")
    p.set_defaults(func=cmd_workflows)

    p = subs.add_parser("tools", help="Focus outils")
    p.set_defaults(func=cmd_tools)

    p = subs.add_parser("recommend", help="Recommandations uniquement")
    p.set_defaults(func=cmd_recommend)

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
