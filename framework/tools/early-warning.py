#!/usr/bin/env python3
"""
early-warning.py — Système d'alerte précoce BMAD.
====================================================

Détecte les problèmes AVANT qu'ils ne deviennent des crises.
Inspiré de l'effet papillon : petits signaux → grandes conséquences.

5 métriques surveillées :
  1. Vélocité des erreurs (taux d'échec croissant)
  2. Entropie du projet (désordre / complexité)
  3. Concentration de risque (fichiers critiques trop modifiés)
  4. Stagnation (aucune progression depuis X jours)
  5. Divergence artefacts (drift entre documents)

3 niveaux d'alerte :
  🟢 NOMINAL — tout va bien
  🟡 WATCH   — tendance à surveiller
  🔴 ALERT   — intervention requise

Features :
  - Phase transitions : détecte les changements de régime
  - Entropy score : mesure la complexité/désordre du projet
  - Trend analysis : tendances sur fenêtre glissante
  - Intégration stigmergy : émet des phéromones ALERT

Usage :
  python3 early-warning.py --project-root . scan          # Scan complet
  python3 early-warning.py --project-root . entropy        # Score d'entropie
  python3 early-warning.py --project-root . trends         # Tendances
  python3 early-warning.py --project-root . scan --emit    # + émission stigmergy
  python3 early-warning.py --project-root . --json

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
import logging

_log = logging.getLogger("grimoire.early_warning")

# ── Constantes ────────────────────────────────────────────────────────────────

EARLY_WARNING_VERSION = "1.0.0"

# Niveaux d'alerte
class Level:
    NOMINAL = "🟢 NOMINAL"
    WATCH = "🟡 WATCH"
    ALERT = "🔴 ALERT"

# Seuils
ENTROPY_WATCH = 0.65
ENTROPY_ALERT = 0.85
STAGNATION_DAYS_WATCH = 7
STAGNATION_DAYS_ALERT = 14
ERROR_RATE_WATCH = 0.15
ERROR_RATE_ALERT = 0.30
CONCENTRATION_WATCH = 0.40   # 40% des commits sur <10% des fichiers
CONCENTRATION_ALERT = 0.60
DRIFT_WATCH_PERCENT = 20
DRIFT_ALERT_PERCENT = 40

# Fenêtre d'analyse
DEFAULT_WINDOW_DAYS = 30


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Metric:
    """Une métrique mesurée."""
    name: str
    value: float
    level: str
    detail: str = ""
    trend: str = ""   # ↑ ↓ →

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": round(self.value, 4),
            "level": self.level,
            "detail": self.detail,
            "trend": self.trend,
        }


@dataclass
class EarlyWarningReport:
    """Rapport complet d'alerte précoce."""
    metrics: list[Metric] = field(default_factory=list)
    entropy_score: float = 0.0
    overall_level: str = Level.NOMINAL
    phase: str = "stable"    # stable | growth | turbulence | crisis
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    git_available: bool = False

    def compute_overall(self):
        levels = [m.level for m in self.metrics]
        if any(Level.ALERT in lv for lv in levels):
            self.overall_level = Level.ALERT
            self.phase = "crisis" if levels.count(Level.ALERT) > 2 else "turbulence"
        elif any(Level.WATCH in lv for lv in levels):
            self.overall_level = Level.WATCH
            self.phase = "turbulence" if levels.count(Level.WATCH) > 2 else "growth"
        else:
            self.overall_level = Level.NOMINAL
            self.phase = "stable"


# ── Git Helpers ──────────────────────────────────────────────────────────────

def _git_available(project_root: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=project_root, timeout=5,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _git_log_stat(project_root: Path, days: int) -> list[dict]:
    """Récupère les stats de commits récents."""
    try:
        r = subprocess.run(
            ["git", "log", f"--since={days} days ago",
             "--pretty=format:%H|%ai|%s", "--name-only"],
            capture_output=True, text=True, cwd=project_root, timeout=15,
        )
        if r.returncode != 0:
            return []

        commits = []
        current: dict | None = None
        for line in r.stdout.split("\n"):
            line = line.strip()
            if "|" in line and len(line.split("|")) >= 3:
                parts = line.split("|", 2)
                current = {
                    "hash": parts[0],
                    "date": parts[1].strip(),
                    "message": parts[2].strip(),
                    "files": [],
                }
                commits.append(current)
            elif line and current is not None:
                current["files"].append(line)
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _git_file_counts(commits: list[dict]) -> Counter:
    """Compte les modifications par fichier."""
    counts: Counter = Counter()
    for c in commits:
        for f in c.get("files", []):
            counts[f] += 1
    return counts


# ── Metric Calculators ───────────────────────────────────────────────────────

def measure_error_velocity(project_root: Path, commits: list[dict]) -> Metric:
    """Taux d'erreurs/fixes dans les commits récents."""
    if not commits:
        return Metric(
            name="Vélocité d'erreurs",
            value=0.0,
            level=Level.NOMINAL,
            detail="Pas de commits analysables",
            trend="→",
        )

    error_keywords = {"fix", "bug", "hotfix", "revert", "broken", "crash",
                      "error", "regression", "patch", "urgent", "critical"}
    total = len(commits)
    errors = sum(
        1 for c in commits
        if any(kw in c.get("message", "").lower() for kw in error_keywords)
    )
    rate = errors / total if total > 0 else 0.0

    # Tendance : comparer 1ère moitié vs 2ème moitié
    mid = total // 2
    if mid > 0:
        first_half = sum(
            1 for c in commits[:mid]
            if any(kw in c.get("message", "").lower() for kw in error_keywords)
        )
        second_half = sum(
            1 for c in commits[mid:]
            if any(kw in c.get("message", "").lower() for kw in error_keywords)
        )
        rate1 = first_half / mid
        rate2 = second_half / (total - mid)
        trend = "↑" if rate2 > rate1 * 1.2 else ("↓" if rate2 < rate1 * 0.8 else "→")
    else:
        trend = "→"

    if rate >= ERROR_RATE_ALERT:
        level = Level.ALERT
    elif rate >= ERROR_RATE_WATCH:
        level = Level.WATCH
    else:
        level = Level.NOMINAL

    return Metric(
        name="Vélocité d'erreurs",
        value=rate,
        level=level,
        detail=f"{errors}/{total} commits liés à des erreurs ({rate:.0%})",
        trend=trend,
    )


def measure_entropy(project_root: Path) -> Metric:
    """Score d'entropie/complexité du projet."""
    score_components = []

    # 1. Nombre de fichiers mémoire vs taille
    memory_dir = project_root / "_bmad" / "_memory"
    if memory_dir.exists():
        memory_files = list(memory_dir.rglob("*"))
        file_count = len([f for f in memory_files if f.is_file()])
        total_size = sum(f.stat().st_size for f in memory_files if f.is_file())
        # Normaliser : > 50 fichiers ou > 500KB = entropie élevée
        file_entropy = min(1.0, file_count / 50)
        size_entropy = min(1.0, total_size / (500 * 1024))
        score_components.append(file_entropy * 0.3)
        score_components.append(size_entropy * 0.2)

    # 2. Diversité des types de fichiers dans _bmad
    bmad_dir = project_root / "_bmad"
    if bmad_dir.exists():
        extensions: Counter = Counter()
        for f in bmad_dir.rglob("*"):
            if f.is_file():
                extensions[f.suffix] += 1
        if extensions:
            total_ext = sum(extensions.values())
            # Shannon entropy normalisée
            shannon = 0.0
            for count in extensions.values():
                p = count / total_ext
                if p > 0:
                    shannon -= p * math.log2(p)
            max_shannon = math.log2(len(extensions)) if len(extensions) > 1 else 1
            norm_entropy = shannon / max_shannon if max_shannon > 0 else 0
            score_components.append(norm_entropy * 0.25)

    # 3. Contradictions / items non résolus
    unresolved = 0
    if memory_dir.exists():
        for md in memory_dir.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
                unresolved += content.count("- [ ]")
                unresolved += content.count("TODO")
                unresolved += content.count("⚠️")
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    unresolved_norm = min(1.0, unresolved / 30)
    score_components.append(unresolved_norm * 0.25)

    entropy = sum(score_components) if score_components else 0.0

    if entropy >= ENTROPY_ALERT:
        level = Level.ALERT
    elif entropy >= ENTROPY_WATCH:
        level = Level.WATCH
    else:
        level = Level.NOMINAL

    return Metric(
        name="Entropie projet",
        value=entropy,
        level=level,
        detail=f"Score {entropy:.2f}/1.0 — {unresolved} items non résolus",
        trend="→",
    )


def measure_concentration(project_root: Path, commits: list[dict]) -> Metric:
    """Concentration de risque — trop de changements sur peu de fichiers."""
    if not commits:
        return Metric(
            name="Concentration risque",
            value=0.0,
            level=Level.NOMINAL,
            detail="Pas de données git",
            trend="→",
        )

    file_counts = _git_file_counts(commits)
    if not file_counts:
        return Metric(
            name="Concentration risque",
            value=0.0,
            level=Level.NOMINAL,
            detail="Aucun fichier modifié",
            trend="→",
        )

    total_changes = sum(file_counts.values())
    total_files = len(file_counts)

    # Top 10% des fichiers : quel % des changements ?
    top_n = max(1, total_files // 10)
    top_files = file_counts.most_common(top_n)
    top_changes = sum(c for _, c in top_files)
    concentration = top_changes / total_changes if total_changes > 0 else 0

    if concentration >= CONCENTRATION_ALERT:
        level = Level.ALERT
    elif concentration >= CONCENTRATION_WATCH:
        level = Level.WATCH
    else:
        level = Level.NOMINAL

    hotspots = ", ".join(f for f, _ in top_files[:3])

    return Metric(
        name="Concentration risque",
        value=concentration,
        level=level,
        detail=f"Top {top_n} fichier(s) = {concentration:.0%} des changements. "
               f"Hotspots : {hotspots}",
        trend="→",
    )


def measure_stagnation(project_root: Path, commits: list[dict]) -> Metric:
    """Stagnation — pas de progression récente."""
    if not commits:
        # Fallback : vérifier mtime des fichiers mémoire
        memory_dir = project_root / "_bmad" / "_memory"
        if memory_dir.exists():
            now = datetime.now().timestamp()
            most_recent = 0.0
            for f in memory_dir.rglob("*"):
                if f.is_file():
                    most_recent = max(most_recent, f.stat().st_mtime)
            if most_recent > 0:
                days_since = (now - most_recent) / 86400
            else:
                days_since = 999
        else:
            days_since = 999
    else:
        # Date du commit le plus récent
        try:
            latest = commits[0].get("date", "")
            dt = datetime.fromisoformat(latest[:19])
            days_since = (datetime.now() - dt).days
        except (ValueError, IndexError):
            days_since = 999

    if days_since >= STAGNATION_DAYS_ALERT:
        level = Level.ALERT
    elif days_since >= STAGNATION_DAYS_WATCH:
        level = Level.WATCH
    else:
        level = Level.NOMINAL

    return Metric(
        name="Stagnation",
        value=days_since,
        level=level,
        detail=f"Dernière activité il y a {days_since:.0f} jour(s)",
        trend="↑" if days_since > STAGNATION_DAYS_WATCH else "→",
    )


def measure_drift(project_root: Path) -> Metric:
    """Divergence artefacts — drift entre design et implémentation."""
    # Heuristique : comparer les dates de modification des artefacts clés
    pairs = [
        ("_bmad/_memory/shared-context.md", "_bmad/_memory/session-state.md"),
    ]

    # Trouver les paires PRD / stories
    planning_dir = project_root / "_bmad-output" / "planning-artifacts"
    impl_dir = project_root / "_bmad-output" / "implementation-artifacts"

    if planning_dir.exists() and impl_dir.exists():
        for prd in planning_dir.glob("PRD-*.md"):
            # Chercher stories correspondantes
            stories = list(impl_dir.glob("STORY-*.md"))
            if stories:
                pairs.append((str(prd.relative_to(project_root)),
                              str(stories[0].relative_to(project_root))))

    max_drift = 0.0
    drift_details = []

    for path_a, path_b in pairs:
        file_a = project_root / path_a
        file_b = project_root / path_b
        if file_a.exists() and file_b.exists():
            try:
                mtime_a = file_a.stat().st_mtime
                mtime_b = file_b.stat().st_mtime
                drift_days = abs(mtime_a - mtime_b) / 86400
                max_drift = max(max_drift, drift_days)
                if drift_days > 3:
                    drift_details.append(f"{Path(path_a).name}↔{Path(path_b).name}: {drift_days:.0f}j")
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    norm_drift = min(1.0, max_drift / 30)  # 30 jours = drift max

    if norm_drift * 100 >= DRIFT_ALERT_PERCENT:
        level = Level.ALERT
    elif norm_drift * 100 >= DRIFT_WATCH_PERCENT:
        level = Level.WATCH
    else:
        level = Level.NOMINAL

    detail = f"Drift max : {max_drift:.0f} jour(s)"
    if drift_details:
        detail += f" — {'; '.join(drift_details[:3])}"

    return Metric(
        name="Divergence artefacts",
        value=norm_drift,
        level=level,
        detail=detail,
        trend="→",
    )


# ── Report Assembly ──────────────────────────────────────────────────────────

def build_report(project_root: Path, window_days: int = DEFAULT_WINDOW_DAYS) -> EarlyWarningReport:
    """Construit le rapport complet."""
    report = EarlyWarningReport()
    report.git_available = _git_available(project_root)

    commits = []
    if report.git_available:
        commits = _git_log_stat(project_root, window_days)

    report.metrics.append(measure_error_velocity(project_root, commits))
    report.metrics.append(measure_entropy(project_root))
    report.metrics.append(measure_concentration(project_root, commits))
    report.metrics.append(measure_stagnation(project_root, commits))
    report.metrics.append(measure_drift(project_root))

    report.entropy_score = next(
        (m.value for m in report.metrics if m.name == "Entropie projet"), 0.0
    )
    report.compute_overall()

    # Recommandations
    for m in report.metrics:
        if "ALERT" in m.level:
            report.recommendations.append(f"🔴 {m.name} : {m.detail}")
        elif "WATCH" in m.level:
            report.recommendations.append(f"🟡 {m.name} : {m.detail}")

    if report.phase == "crisis":
        report.recommendations.append(
            "⚡ Phase de crise détectée — prioriser la stabilisation avant de nouvelles features"
        )
    elif report.phase == "turbulence":
        report.recommendations.append(
            "🌊 Phase de turbulence — surveiller étroitement les métriques"
        )

    return report


# ── Formatters ───────────────────────────────────────────────────────────────

def format_report(report: EarlyWarningReport) -> str:
    lines = [
        "🦋 Early Warning System — BMAD",
        f"   {report.overall_level}   Phase : {report.phase}",
        f"   Entropie : {report.entropy_score:.2f}/1.0",
        f"   Git : {'oui' if report.git_available else 'non'}",
        "",
        "   📊 MÉTRIQUES :",
    ]

    for m in report.metrics:
        trend_str = f" {m.trend}" if m.trend else ""
        lines.append(f"      {m.level}  {m.name} = {m.value:.2f}{trend_str}")
        if m.detail:
            lines.append(f"            {m.detail}")
    lines.append("")

    if report.recommendations:
        lines.append("   📋 RECOMMANDATIONS :")
        for r in report.recommendations:
            lines.append(f"      {r}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: EarlyWarningReport) -> dict:
    return {
        "overall_level": report.overall_level,
        "phase": report.phase,
        "entropy_score": round(report.entropy_score, 4),
        "git_available": report.git_available,
        "timestamp": report.timestamp,
        "metrics": [m.to_dict() for m in report.metrics],
        "recommendations": report.recommendations,
    }


# ── Stigmergy Emission ──────────────────────────────────────────────────────

def emit_alerts(project_root: Path, report: EarlyWarningReport) -> int:
    """Émet des phéromones ALERT via stigmergy.py."""
    stigmergy_py = project_root / "framework" / "tools" / "stigmergy.py"
    if not stigmergy_py.exists():
        print("   ⚠️ stigmergy.py non trouvé — émission impossible")
        return 0

    emitted = 0
    for m in report.metrics:
        if "ALERT" in m.level:
            try:
                subprocess.run(
                    [sys.executable, str(stigmergy_py),
                     "--project-root", str(project_root),
                     "emit",
                     "--type", "ALERT",
                     "--location", "early-warning",
                     "--text", f"{m.name}: {m.detail}",
                     "--agent", "early-warning"],
                    capture_output=True, text=True, cwd=project_root, timeout=10,
                )
                emitted += 1
            except (subprocess.TimeoutExpired, FileNotFoundError) as _exc:
                _log.debug("subprocess.TimeoutExpired, FileNotFoundError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    return emitted


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_report(project_root, window_days=args.window)

    if args.emit:
        emitted = emit_alerts(project_root, report)
        if emitted:
            print(f"   📡 {emitted} alerte(s) émise(s) via stigmergy\n")

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))

    return 1 if "ALERT" in report.overall_level else 0


def cmd_entropy(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    metric = measure_entropy(project_root)

    if args.json:
        print(json.dumps(metric.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"🧮 Entropie : {metric.value:.2f}/1.0  {metric.level}")
        print(f"   {metric.detail}")

    return 0


def cmd_trends(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    use_git = _git_available(project_root)

    if not use_git:
        print("⚠️ Git non disponible — tendances limitées")
        return 1

    # Comparer 2 fenêtres
    window = args.window
    commits_recent = _git_log_stat(project_root, window)
    commits_older = _git_log_stat(project_root, window * 2)
    # Soustraire les récents des plus anciens
    old_only = commits_older[len(commits_recent):]

    print(f"📈 Tendances (fenêtre : {window} jours)\n")
    print(f"   Commits récents : {len(commits_recent)}")
    print(f"   Commits période précédente : {len(old_only)}")

    if len(commits_recent) > len(old_only) * 1.3:
        print("   Tendance : ↑ Accélération")
    elif len(commits_recent) < len(old_only) * 0.7:
        print("   Tendance : ↓ Décélération")
    else:
        print("   Tendance : → Stable")

    # Error velocity trend
    error_kw = {"fix", "bug", "hotfix", "revert", "error", "regression"}
    errors_recent = sum(1 for c in commits_recent if any(kw in c.get("message", "").lower() for kw in error_kw))
    errors_old = sum(1 for c in old_only if any(kw in c.get("message", "").lower() for kw in error_kw))
    print(f"\n   Erreurs récentes : {errors_recent} vs précédentes : {errors_old}")
    if errors_recent > errors_old * 1.5:
        print("   ⚠️ Taux d'erreur en hausse significative")

    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Early Warning System — Détection précoce de problèmes",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                        help=f"Fenêtre d'analyse en jours (défaut: {DEFAULT_WINDOW_DAYS})")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("scan", help="Scan complet")
    p.add_argument("--emit", action="store_true", help="Émettre les alertes via stigmergy")
    p.set_defaults(func=cmd_scan)

    p = subs.add_parser("entropy", help="Score d'entropie uniquement")
    p.set_defaults(func=cmd_entropy)

    p = subs.add_parser("trends", help="Analyse des tendances")
    p.set_defaults(func=cmd_trends)

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
