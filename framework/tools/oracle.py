#!/usr/bin/env python3
"""
oracle.py — Oracle Introspectif BMAD.
========================================

CTO virtuel qui analyse le projet de manière holistique et produit :
  - SWOT automatique (Forces, Faiblesses, Opportunités, Menaces)
  - Attracteurs naturels (vers quoi le projet converge organiquement)
  - Recommandations stratégiques
  - Score de maturité du projet

Inspiré de l'oracle de Delphes : "Connais-toi toi-même."

Features :
  1. `swot`     — Analyse SWOT automatique
  2. `attract`  — Attracteurs naturels du projet
  3. `maturity` — Score de maturité multi-dimension
  4. `advise`   — Recommandations stratégiques
  5. `report`   — Rapport complet

Usage :
  python3 oracle.py --project-root . swot
  python3 oracle.py --project-root . attract
  python3 oracle.py --project-root . maturity
  python3 oracle.py --project-root . advise
  python3 oracle.py --project-root . report
  python3 oracle.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.oracle")

# ── Constantes ────────────────────────────────────────────────────────────────

ORACLE_VERSION = "1.0.0"

# Maturity levels
MATURITY_LEVELS = [
    (90, "🏆 Exemplaire"),
    (75, "🟢 Mature"),
    (60, "🟡 En progression"),
    (40, "🟠 Émergent"),
    (20, "🔴 Initial"),
    (0, "⚫ Inexistant"),
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SWOTItem:
    """Élément d'analyse SWOT."""
    text: str
    evidence: str = ""
    score: float = 0.5   # Impact 0.0-1.0

@dataclass
class SWOT:
    strengths: list[SWOTItem] = field(default_factory=list)
    weaknesses: list[SWOTItem] = field(default_factory=list)
    opportunities: list[SWOTItem] = field(default_factory=list)
    threats: list[SWOTItem] = field(default_factory=list)

@dataclass
class Attractor:
    """Attracteur naturel — vers quoi le projet converge."""
    name: str
    description: str
    evidence: list[str] = field(default_factory=list)
    strength: float = 0.5   # 0.0-1.0

@dataclass
class MaturityDimension:
    name: str
    score: int = 0       # 0-100
    detail: str = ""

@dataclass
class OracleReport:
    swot: SWOT = field(default_factory=SWOT)
    attractors: list[Attractor] = field(default_factory=list)
    maturity_dimensions: list[MaturityDimension] = field(default_factory=list)
    maturity_score: int = 0
    maturity_level: str = ""
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── Git Helpers ──────────────────────────────────────────────────────────────

def _git_stats(project_root: Path) -> dict:
    """Récupère des stats git basiques."""
    stats = {"available": False, "commits": 0, "contributors": 0, "age_days": 0}
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if r.returncode == 0:
            stats["available"] = True
            stats["commits"] = int(r.stdout.strip())

        r = subprocess.run(
            ["git", "log", "--format=%an", "--since=365 days ago"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if r.returncode == 0:
            authors = set(r.stdout.strip().split("\n"))
            stats["contributors"] = len(authors - {""})

        r = subprocess.run(
            ["git", "log", "--format=%ai", "--reverse", "-1"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            first_date = datetime.fromisoformat(r.stdout.strip()[:19])
            stats["age_days"] = (datetime.now() - first_date).days

    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as _exc:
        _log.debug("subprocess.TimeoutExpired, FileNotFoundError, ValueError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    return stats


# ── SWOT Analysis ────────────────────────────────────────────────────────────

def analyze_swot(project_root: Path) -> SWOT:
    """Analyse SWOT automatique."""
    swot = SWOT()
    git_stats = _git_stats(project_root)

    # ── STRENGTHS ──
    # Tests
    test_files = list(project_root.rglob("test_*.py")) + list(project_root.rglob("*_test.py"))
    test_files += list(project_root.rglob("*.test.ts")) + list(project_root.rglob("*.test.js"))
    if len(test_files) > 10:
        swot.strengths.append(SWOTItem(
            text=f"Suite de tests robuste ({len(test_files)} fichiers)",
            evidence="Tests = filet de sécurité",
            score=0.8,
        ))

    # Documentation
    doc_files = list(project_root.rglob("docs/**/*.md"))
    readme = project_root / "README.md"
    if doc_files or readme.exists():
        swot.strengths.append(SWOTItem(
            text=f"Documentation présente ({len(doc_files)} fichiers + README)",
            evidence="Documentation = onboarding facilité",
            score=0.6,
        ))

    # Outils Python
    tools = list((project_root / "framework" / "tools").glob("*.py")) if (project_root / "framework" / "tools").exists() else []
    if len(tools) > 5:
        swot.strengths.append(SWOTItem(
            text=f"Écosystème d'outils riche ({len(tools)} outils CLI)",
            evidence="Outillage = productivité",
            score=0.7,
        ))

    # Git activity
    if git_stats.get("commits", 0) > 50:
        swot.strengths.append(SWOTItem(
            text=f"Projet actif ({git_stats['commits']} commits)",
            score=0.6,
        ))

    # Agents
    agent_files = list(project_root.rglob("**/agents/*.md"))
    if len(agent_files) > 5:
        swot.strengths.append(SWOTItem(
            text=f"Diversité d'agents ({len(agent_files)} agents)",
            score=0.7,
        ))

    # ── WEAKNESSES ──
    # Bus factor
    if git_stats.get("contributors", 0) <= 1:
        swot.weaknesses.append(SWOTItem(
            text="Bus factor = 1 — un seul contributeur",
            evidence="Risque de perte de connaissance",
            score=0.9,
        ))

    # Pas de CI/CD
    ci_files = list(project_root.glob(".github/workflows/*.yml")) + list(project_root.glob(".gitlab-ci.yml"))
    if not ci_files:
        swot.weaknesses.append(SWOTItem(
            text="Pas de CI/CD détecté",
            evidence="Pas de pipeline de vérification automatique",
            score=0.6,
        ))

    # Trop de TODO
    todo_count = 0
    for fpath in project_root.rglob("*.py"):
        try:
            content = fpath.read_text(encoding="utf-8")
            todo_count += content.upper().count("TODO")
        except (OSError, UnicodeDecodeError) as _exc:
            _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    if todo_count > 20:
        swot.weaknesses.append(SWOTItem(
            text=f"{todo_count} TODO dans le code",
            evidence="Dette technique accumulée",
            score=0.5,
        ))

    # Pas de changelog
    if not (project_root / "CHANGELOG.md").exists():
        swot.weaknesses.append(SWOTItem(
            text="Pas de CHANGELOG",
            score=0.3,
        ))

    # ── OPPORTUNITIES ──
    # Mémoire collective
    memory_dir = project_root / "_bmad" / "_memory"
    if memory_dir.exists():
        mem_files = list(memory_dir.rglob("*.md"))
        swot.opportunities.append(SWOTItem(
            text=f"Mémoire collective à exploiter ({len(mem_files)} fichiers)",
            evidence="Base pour le machine learning et l'auto-amélioration",
            score=0.7,
        ))

    # Archétypes
    archetypes = list(project_root.glob("archetypes/*/"))
    if archetypes:
        swot.opportunities.append(SWOTItem(
            text=f"Système d'archétypes extensible ({len(archetypes)} archétypes)",
            evidence="Potentiel de marketplace d'archétypes",
            score=0.6,
        ))

    swot.opportunities.append(SWOTItem(
        text="Écosystème d'outils composables",
        evidence="Les outils peuvent être chaînés en pipelines",
        score=0.5,
    ))

    # ── THREATS ──
    # Complexité
    total_files = sum(1 for _ in project_root.rglob("*") if _.is_file())
    if total_files > 500:
        swot.threats.append(SWOTItem(
            text=f"Complexité croissante ({total_files} fichiers)",
            evidence="Risque de perte de cohérence",
            score=0.6,
        ))

    # Dépendance à un LLM
    swot.threats.append(SWOTItem(
        text="Dépendance aux capacités des LLM",
        evidence="Les agents dépendent de la qualité du modèle sous-jacent",
        score=0.5,
    ))

    return swot


# ── Attractors ──────────────────────────────────────────────────────────────

def analyze_attractors(project_root: Path) -> list[Attractor]:
    """Détecte les attracteurs naturels du projet."""
    attractors = []
    git_stats = _git_stats(project_root)

    # Analyser les zones les plus actives (via git)
    if git_stats.get("available"):
        try:
            r = subprocess.run(
                ["git", "log", "--since=90 days ago", "--name-only", "--pretty=format:"],
                capture_output=True, text=True, cwd=project_root, timeout=15,
            )
            if r.returncode == 0:
                dir_counts: Counter = Counter()
                for line in r.stdout.split("\n"):
                    line = line.strip()
                    if "/" in line:
                        top_dir = line.split("/")[0]
                        dir_counts[top_dir] += 1

                if dir_counts:
                    top_dir, top_count = dir_counts.most_common(1)[0]
                    total = sum(dir_counts.values())
                    ratio = top_count / total
                    attractors.append(Attractor(
                        name=f"Gravité : {top_dir}/",
                        description=f"{ratio:.0%} de l'activité récente se concentre dans {top_dir}/",
                        evidence=[f"{d}: {c} modifications" for d, c in dir_counts.most_common(5)],
                        strength=min(1.0, ratio * 1.5),
                    ))
        except (subprocess.TimeoutExpired, FileNotFoundError) as _exc:
            _log.debug("subprocess.TimeoutExpired, FileNotFoundError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Taille relative des composants
    component_sizes: dict[str, int] = {}
    for d in project_root.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            size = sum(1 for _ in d.rglob("*") if _.is_file())
            if size > 5:
                component_sizes[d.name] = size

    if component_sizes:
        total = sum(component_sizes.values())
        for name, size in sorted(component_sizes.items(), key=lambda x: x[1], reverse=True)[:3]:
            ratio = size / total
            if ratio > 0.3:
                attractors.append(Attractor(
                    name=f"Centre de masse : {name}/",
                    description=f"Contient {ratio:.0%} des fichiers du projet ({size} fichiers)",
                    strength=ratio,
                ))

    return attractors


# ── Maturity ────────────────────────────────────────────────────────────────

def analyze_maturity(project_root: Path) -> list[MaturityDimension]:
    """Évalue la maturité multi-dimension."""
    dimensions = []
    git_stats = _git_stats(project_root)

    # 1. Documentation
    docs = list(project_root.rglob("docs/**/*.md"))
    readme = (project_root / "README.md").exists()
    contributing = (project_root / "CONTRIBUTING.md").exists()
    doc_score = min(100, len(docs) * 10 + (30 if readme else 0) + (20 if contributing else 0))
    dimensions.append(MaturityDimension("Documentation", doc_score,
                                        f"{len(docs)} docs, README: {'✅' if readme else '❌'}"))

    # 2. Tests
    test_files = list(project_root.rglob("test_*.py")) + list(project_root.rglob("*.test.*"))
    test_score = min(100, len(test_files) * 5)
    dimensions.append(MaturityDimension("Tests", test_score,
                                        f"{len(test_files)} fichiers de test"))

    # 3. Outillage
    tools = list((project_root / "framework" / "tools").glob("*.py")) if (project_root / "framework" / "tools").exists() else []
    tool_score = min(100, len(tools) * 7)
    dimensions.append(MaturityDimension("Outillage", tool_score,
                                        f"{len(tools)} outils CLI"))

    # 4. Mémoire / Apprentissage
    memory = project_root / "_bmad" / "_memory"
    mem_files = list(memory.rglob("*.md")) if memory.exists() else []
    mem_score = min(100, len(mem_files) * 12)
    dimensions.append(MaturityDimension("Mémoire", mem_score,
                                        f"{len(mem_files)} fichiers mémoire"))

    # 5. Processus
    workflows = list(project_root.rglob("**/workflows/**/*.yaml")) + list(project_root.rglob("**/workflows/**/*.md"))
    proc_score = min(100, len(workflows) * 8)
    dimensions.append(MaturityDimension("Processus", proc_score,
                                        f"{len(workflows)} workflows définis"))

    # 6. Collaboration
    collab_score = min(100, git_stats.get("contributors", 0) * 25)
    dimensions.append(MaturityDimension("Collaboration", collab_score,
                                        f"{git_stats.get('contributors', 0)} contributeur(s)"))

    return dimensions


# ── Recommendations ─────────────────────────────────────────────────────────

def generate_recommendations(report: OracleReport) -> list[str]:
    recs = []

    # Basé sur les faiblesses
    for w in report.swot.weaknesses:
        if w.score >= 0.7:
            recs.append(f"🔴 Corriger : {w.text}")
        elif w.score >= 0.4:
            recs.append(f"🟡 Améliorer : {w.text}")

    # Basé sur les opportunités
    for o in report.swot.opportunities[:3]:
        recs.append(f"🟢 Exploiter : {o.text}")

    # Basé sur la maturité
    for dim in report.maturity_dimensions:
        if dim.score < 40:
            recs.append(f"📈 Développer {dim.name} (actuellement {dim.score}/100)")

    # Basé sur les attracteurs
    for att in report.attractors:
        if att.strength > 0.5:
            recs.append(f"🎯 Levier : {att.name} — {att.description[:80]}")

    return recs[:10]


# ── Report Builder ──────────────────────────────────────────────────────────

def build_report(project_root: Path) -> OracleReport:
    report = OracleReport()
    report.swot = analyze_swot(project_root)
    report.attractors = analyze_attractors(project_root)
    report.maturity_dimensions = analyze_maturity(project_root)

    if report.maturity_dimensions:
        report.maturity_score = round(
            sum(d.score for d in report.maturity_dimensions) / len(report.maturity_dimensions)
        )
    for threshold, label in MATURITY_LEVELS:
        if report.maturity_score >= threshold:
            report.maturity_level = label
            break

    report.recommendations = generate_recommendations(report)
    return report


# ── Formatters ───────────────────────────────────────────────────────────────

def format_swot(swot: SWOT) -> str:
    lines = ["🔮 SWOT — Analyse stratégique\n"]
    sections = [
        ("💪 FORCES", swot.strengths),
        ("⚠️ FAIBLESSES", swot.weaknesses),
        ("🌟 OPPORTUNITÉS", swot.opportunities),
        ("🔥 MENACES", swot.threats),
    ]
    for title, items in sections:
        lines.append(f"   {title}")
        if items:
            for item in items:
                score_bar = "█" * int(item.score * 5)
                lines.append(f"      {score_bar} {item.text}")
                if item.evidence:
                    lines.append(f"           ↳ {item.evidence}")
        else:
            lines.append("      (aucun)")
        lines.append("")
    return "\n".join(lines)


def format_maturity(dimensions: list[MaturityDimension], score: int, level: str) -> str:
    lines = [
        f"📊 Maturité : {score}/100  {level}\n",
    ]
    for dim in dimensions:
        bar = "█" * (dim.score // 10) + "░" * (10 - dim.score // 10)
        lines.append(f"   {dim.name:15s} {bar} {dim.score:3d}/100  {dim.detail}")
    return "\n".join(lines)


def format_full_report(report: OracleReport) -> str:
    lines = [
        "🔮 Oracle Introspectif — Rapport Complet BMAD",
        f"   Maturité globale : {report.maturity_score}/100  {report.maturity_level}",
        "",
        format_swot(report.swot),
    ]

    if report.attractors:
        lines.append("🧲 ATTRACTEURS NATURELS :")
        for att in report.attractors:
            bar = "█" * int(att.strength * 10)
            lines.append(f"   {bar} {att.name}")
            lines.append(f"      {att.description}")
        lines.append("")

    lines.append(format_maturity(report.maturity_dimensions, report.maturity_score, report.maturity_level))
    lines.append("")

    if report.recommendations:
        lines.append("📋 RECOMMANDATIONS STRATÉGIQUES :")
        for r in report.recommendations:
            lines.append(f"   {r}")

    return "\n".join(lines)


def report_to_dict(report: OracleReport) -> dict:
    return {
        "maturity_score": report.maturity_score,
        "maturity_level": report.maturity_level,
        "swot": {
            "strengths": [{"text": s.text, "score": s.score} for s in report.swot.strengths],
            "weaknesses": [{"text": w.text, "score": w.score} for w in report.swot.weaknesses],
            "opportunities": [{"text": o.text, "score": o.score} for o in report.swot.opportunities],
            "threats": [{"text": t.text, "score": t.score} for t in report.swot.threats],
        },
        "attractors": [{"name": a.name, "description": a.description, "strength": a.strength} for a in report.attractors],
        "maturity_dimensions": [{"name": d.name, "score": d.score, "detail": d.detail} for d in report.maturity_dimensions],
        "recommendations": report.recommendations,
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_swot(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    swot = analyze_swot(project_root)
    if args.json:
        print(json.dumps({
            "strengths": [{"text": s.text, "score": s.score} for s in swot.strengths],
            "weaknesses": [{"text": w.text, "score": w.score} for w in swot.weaknesses],
            "opportunities": [{"text": o.text, "score": o.score} for o in swot.opportunities],
            "threats": [{"text": t.text, "score": t.score} for t in swot.threats],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_swot(swot))
    return 0


def cmd_attract(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    attractors = analyze_attractors(project_root)
    if args.json:
        print(json.dumps([{"name": a.name, "description": a.description, "strength": a.strength}
                          for a in attractors], indent=2, ensure_ascii=False))
    else:
        for att in attractors:
            print(f"🧲 {att.name} (force: {att.strength:.0%})")
            print(f"   {att.description}")
            for ev in att.evidence[:3]:
                print(f"   → {ev}")
            print()
    return 0


def cmd_maturity(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    dims = analyze_maturity(project_root)
    score = round(sum(d.score for d in dims) / len(dims)) if dims else 0
    level = ""
    for threshold, lbl in MATURITY_LEVELS:
        if score >= threshold:
            level = lbl
            break
    if args.json:
        print(json.dumps({"score": score, "level": level,
                          "dimensions": [{"name": d.name, "score": d.score} for d in dims]},
                         indent=2, ensure_ascii=False))
    else:
        print(format_maturity(dims, score, level))
    return 0


def cmd_advise(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_report(project_root)
    if args.json:
        print(json.dumps({"recommendations": report.recommendations}, indent=2, ensure_ascii=False))
    else:
        print("📋 Recommandations de l'Oracle :\n")
        for r in report.recommendations:
            print(f"   {r}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_report(project_root)
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_full_report(report))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Oracle — Analyse stratégique introspective",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    subs.add_parser("swot", help="Analyse SWOT").set_defaults(func=cmd_swot)
    subs.add_parser("attract", help="Attracteurs naturels").set_defaults(func=cmd_attract)
    subs.add_parser("maturity", help="Score de maturité").set_defaults(func=cmd_maturity)
    subs.add_parser("advise", help="Recommandations").set_defaults(func=cmd_advise)
    subs.add_parser("report", help="Rapport complet").set_defaults(func=cmd_report)

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
