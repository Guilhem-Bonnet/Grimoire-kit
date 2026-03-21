#!/usr/bin/env python3
"""
fitness-tracker.py — Suivi de la fitness globale du projet Grimoire.
=================================================================

Agrège les métriques de santé du projet (antifragile score, early-warning,
self-healing, tests) en un score de fitness unique (0-100).
Suit l'évolution dans le temps via un historique JSONL.

Dimensions :
  - antifragile : score d'anti-fragilité (0-100)
  - warnings    : nombre d'alertes early-warning (0=bon)
  - healing_rate: taux de succès self-healing (0-100%)
  - test_health : présence/santé des tests
  - memory_fresh: fraîcheur de la mémoire

Usage :
  python3 fitness-tracker.py --project-root . check
  python3 fitness-tracker.py --project-root . trend
  python3 fitness-tracker.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

FITNESS_VERSION = "1.0.0"
HISTORY_FILE = "_grimoire/_memory/fitness-history.jsonl"
MAX_HISTORY = 500

# Seuils de fitness
HEALTHY_THRESHOLD = 70
WARNING_THRESHOLD = 40

# Poids des dimensions (total = 1.0)
WEIGHTS = {
    "antifragile": 0.30,
    "warnings": 0.25,
    "healing_rate": 0.20,
    "test_health": 0.15,
    "memory_fresh": 0.10,
}


# ── Data Model ────────────────────────────────────────────────────────────────

@dataclass
class DimensionResult:
    """Résultat d'une dimension de fitness."""
    name: str
    score: float       # 0.0 - 1.0
    weight: float
    weighted: float    # score * weight
    detail: str = ""


@dataclass
class FitnessSnapshot:
    """Snapshot de fitness à un instant donné."""
    timestamp: str
    fitness_score: float       # 0-100
    level: str                 # HEALTHY | WARNING | CRITICAL
    dimensions: list[DimensionResult] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "fitness_score": self.fitness_score,
            "level": self.level,
            "dimensions": [asdict(d) for d in self.dimensions],
            "recommendations": self.recommendations,
        }


# ── Dynamic Imports ──────────────────────────────────────────────────────────

def _import_tool(tool_name: str, module_name: str):
    """Import dynamique d'un outil Grimoire (stdlib only)."""
    tools_dir = Path(__file__).resolve().parent
    tool_path = tools_dir / tool_name
    if not tool_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ── Dimension Scorers ────────────────────────────────────────────────────────

def measure_antifragile(project_root: Path) -> DimensionResult:
    """Score antifragile (0-100 → normalisé 0-1)."""
    mod = _import_tool("antifragile-score.py", "antifragile_score")
    if mod is None:
        return DimensionResult(
            name="antifragile", score=0.5, weight=WEIGHTS["antifragile"],
            weighted=0.5 * WEIGHTS["antifragile"],
            detail="Tool antifragile-score.py indisponible — score neutre",
        )

    try:
        result = mod.compute_antifragile_score(project_root)
        raw = result.global_score / 100.0  # normaliser 0-1
        return DimensionResult(
            name="antifragile", score=raw, weight=WEIGHTS["antifragile"],
            weighted=raw * WEIGHTS["antifragile"],
            detail=f"{result.level} ({result.global_score}/100, {result.total_evidence} signaux)",
        )
    except Exception as exc:
        return DimensionResult(
            name="antifragile", score=0.5, weight=WEIGHTS["antifragile"],
            weighted=0.5 * WEIGHTS["antifragile"],
            detail=f"Erreur: {str(exc)[:100]}",
        )


def measure_warnings(project_root: Path) -> DimensionResult:
    """Alertes early-warning (0 ALERT = 1.0, beaucoup = 0.0)."""
    mod = _import_tool("early-warning.py", "early_warning")
    if mod is None:
        return DimensionResult(
            name="warnings", score=0.5, weight=WEIGHTS["warnings"],
            weighted=0.5 * WEIGHTS["warnings"],
            detail="Tool early-warning.py indisponible — score neutre",
        )

    try:
        report = mod.build_report(project_root)
        alerts = sum(1 for m in report.metrics if "ALERT" in m.level)
        watches = sum(1 for m in report.metrics if "WATCH" in m.level)
        total_metrics = max(len(report.metrics), 1)

        # pénalité : ALERT=-0.2, WATCH=-0.1 par métrique
        penalty = (alerts * 0.2 + watches * 0.1) / total_metrics
        raw = max(0.0, min(1.0, 1.0 - penalty * total_metrics))

        return DimensionResult(
            name="warnings", score=raw, weight=WEIGHTS["warnings"],
            weighted=raw * WEIGHTS["warnings"],
            detail=f"Phase: {report.phase}, {alerts} ALERT, {watches} WATCH",
        )
    except Exception as exc:
        return DimensionResult(
            name="warnings", score=0.5, weight=WEIGHTS["warnings"],
            weighted=0.5 * WEIGHTS["warnings"],
            detail=f"Erreur: {str(exc)[:100]}",
        )


def measure_healing(project_root: Path) -> DimensionResult:
    """Taux de succès self-healing."""
    mod = _import_tool("self-healing.py", "self_healing")
    if mod is None:
        return DimensionResult(
            name="healing_rate", score=0.5, weight=WEIGHTS["healing_rate"],
            weighted=0.5 * WEIGHTS["healing_rate"],
            detail="Tool self-healing.py indisponible — score neutre",
        )

    try:
        records = mod.load_history(project_root)
        if not records:
            return DimensionResult(
                name="healing_rate", score=0.7, weight=WEIGHTS["healing_rate"],
                weighted=0.7 * WEIGHTS["healing_rate"],
                detail="Aucun historique de guérison — score par défaut",
            )

        total = len(records)
        success = sum(1 for r in records if r.success)
        raw = success / total if total > 0 else 0.5

        return DimensionResult(
            name="healing_rate", score=raw, weight=WEIGHTS["healing_rate"],
            weighted=raw * WEIGHTS["healing_rate"],
            detail=f"{success}/{total} guérisons réussies ({raw*100:.0f}%)",
        )
    except Exception as exc:
        return DimensionResult(
            name="healing_rate", score=0.5, weight=WEIGHTS["healing_rate"],
            weighted=0.5 * WEIGHTS["healing_rate"],
            detail=f"Erreur: {str(exc)[:100]}",
        )


def measure_test_health(project_root: Path) -> DimensionResult:
    """Santé des tests — vérifie la présence d'un dossier tests/ avec des fichiers."""
    tests_dir = project_root / "tests"
    if not tests_dir.exists():
        return DimensionResult(
            name="test_health", score=0.2, weight=WEIGHTS["test_health"],
            weighted=0.2 * WEIGHTS["test_health"],
            detail="Dossier tests/ absent",
        )

    test_files = list(tests_dir.glob("test_*.py"))
    count = len(test_files)

    if count == 0:
        raw = 0.3
        detail = "Aucun fichier test_*.py trouvé"
    elif count < 10:
        raw = 0.6
        detail = f"{count} fichiers de tests (couverture faible)"
    elif count < 50:
        raw = 0.8
        detail = f"{count} fichiers de tests (bonne couverture)"
    else:
        raw = 1.0
        detail = f"{count} fichiers de tests (excellente couverture)"

    return DimensionResult(
        name="test_health", score=raw, weight=WEIGHTS["test_health"],
        weighted=raw * WEIGHTS["test_health"],
        detail=detail,
    )


def measure_memory_freshness(project_root: Path) -> DimensionResult:
    """Fraîcheur de la mémoire — fichiers modifiés récemment dans _grimoire/_memory/."""
    memory_dir = project_root / "_grimoire" / "_memory"
    if not memory_dir.exists():
        return DimensionResult(
            name="memory_fresh", score=0.3, weight=WEIGHTS["memory_fresh"],
            weighted=0.3 * WEIGHTS["memory_fresh"],
            detail="Dossier _grimoire/_memory/ absent",
        )

    now = datetime.now().timestamp()
    day_seconds = 86400
    files = [f for f in memory_dir.iterdir() if f.is_file()]

    if not files:
        return DimensionResult(
            name="memory_fresh", score=0.3, weight=WEIGHTS["memory_fresh"],
            weighted=0.3 * WEIGHTS["memory_fresh"],
            detail="Aucun fichier mémoire trouvé",
        )

    # Score basé sur la fraîcheur du fichier le plus récent
    most_recent = max(f.stat().st_mtime for f in files)
    age_days = (now - most_recent) / day_seconds

    if age_days < 1:
        raw = 1.0
        detail = f"Mémoire à jour (modifiée il y a {age_days*24:.0f}h)"
    elif age_days < 7:
        raw = 0.8
        detail = f"Mémoire récente ({age_days:.0f} jours)"
    elif age_days < 30:
        raw = 0.5
        detail = f"Mémoire vieillissante ({age_days:.0f} jours)"
    else:
        raw = 0.2
        detail = f"Mémoire stale ({age_days:.0f} jours)"

    return DimensionResult(
        name="memory_fresh", score=raw, weight=WEIGHTS["memory_fresh"],
        weighted=raw * WEIGHTS["memory_fresh"],
        detail=detail,
    )


# ── Core Engine ──────────────────────────────────────────────────────────────

def compute_fitness(project_root: Path) -> FitnessSnapshot:
    """Calcule le score de fitness global du projet."""
    timestamp = datetime.now().isoformat()

    dimensions = [
        measure_antifragile(project_root),
        measure_warnings(project_root),
        measure_healing(project_root),
        measure_test_health(project_root),
        measure_memory_freshness(project_root),
    ]

    # Score global 0-100
    fitness_score = sum(d.weighted for d in dimensions) * 100
    fitness_score = max(0.0, min(100.0, round(fitness_score, 1)))

    # Niveau
    if fitness_score >= HEALTHY_THRESHOLD:
        level = "HEALTHY"
    elif fitness_score >= WARNING_THRESHOLD:
        level = "WARNING"
    else:
        level = "CRITICAL"

    # Recommandations basées sur les dimensions faibles
    recommendations = []
    for d in dimensions:
        if d.score < 0.5:
            recommendations.append(f"⚠️ {d.name}: {d.detail}")

    return FitnessSnapshot(
        timestamp=timestamp,
        fitness_score=fitness_score,
        level=level,
        dimensions=dimensions,
        recommendations=recommendations,
    )


# ── Historique JSONL ─────────────────────────────────────────────────────────

def save_snapshot(snapshot: FitnessSnapshot, project_root: Path) -> Path:
    """Sauvegarde le snapshot dans l'historique JSONL."""
    path = project_root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": snapshot.timestamp,
        "score": snapshot.fitness_score,
        "level": snapshot.level,
        "dims": {d.name: round(d.score * 100, 1) for d in snapshot.dimensions},
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Pruning si trop d'entrées
    _prune_history(path)
    return path


def _prune_history(path: Path) -> None:
    """Garde les MAX_HISTORY entrées les plus récentes."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_HISTORY:
        return
    kept = lines[-MAX_HISTORY:]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def load_history(project_root: Path) -> list[dict]:
    """Charge l'historique complet des snapshots."""
    path = project_root / HISTORY_FILE
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def compute_trend(project_root: Path, last_n: int = 10) -> dict:
    """Calcule la tendance sur les N derniers snapshots."""
    history = load_history(project_root)
    if len(history) < 2:
        return {"trend": "insufficient_data", "points": len(history)}

    recent = history[-last_n:]
    scores = [e.get("score", 0) for e in recent]

    first_half = scores[:len(scores) // 2]
    second_half = scores[len(scores) // 2:]

    avg_first = sum(first_half) / len(first_half) if first_half else 0
    avg_second = sum(second_half) / len(second_half) if second_half else 0

    delta = avg_second - avg_first
    if delta > 2:
        trend = "improving"
    elif delta < -2:
        trend = "declining"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "delta": round(delta, 1),
        "points": len(recent),
        "latest": scores[-1] if scores else 0,
        "min": round(min(scores), 1),
        "max": round(max(scores), 1),
    }


# ── MCP Interface ────────────────────────────────────────────────────────────

def mcp_fitness_check(project_root: str) -> dict:
    """MCP tool ``grimoire_fitness_check`` — calcule et enregistre la fitness.

    Args:
        project_root: Racine du projet.

    Returns:
        dict avec le score de fitness, les dimensions et les recommandations.
    """
    root = Path(project_root)
    snapshot = compute_fitness(root)
    save_snapshot(snapshot, root)
    trend = compute_trend(root)

    return {
        "status": "ok",
        "fitness_score": snapshot.fitness_score,
        "level": snapshot.level,
        "dimensions": {d.name: round(d.score * 100, 1) for d in snapshot.dimensions},
        "recommendations": snapshot.recommendations,
        "trend": trend,
    }


def mcp_fitness_trend(project_root: str, last_n: int = 10) -> dict:
    """MCP tool ``grimoire_fitness_trend`` — tendance de fitness.

    Args:
        project_root: Racine du projet.
        last_n: Nombre de snapshots à analyser.

    Returns:
        dict avec la tendance.
    """
    root = Path(project_root)
    return {"status": "ok", **compute_trend(root, last_n)}


# ── Display ──────────────────────────────────────────────────────────────────

def render_report(snapshot: FitnessSnapshot) -> str:
    """Rendu texte du rapport de fitness."""
    icon = "🟢" if snapshot.level == "HEALTHY" else "🟡" if snapshot.level == "WARNING" else "🔴"

    lines = [
        f"\n{icon} Fitness Tracker — Score: {snapshot.fitness_score}/100 ({snapshot.level})",
        "=" * 60,
        "",
    ]

    for d in snapshot.dimensions:
        bar_len = int(d.score * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"  {d.name:17s} [{bar}] {d.score*100:5.1f}%  (×{d.weight:.2f})")
        lines.append(f"                    {d.detail}")
        lines.append("")

    if snapshot.recommendations:
        lines.append("📋 Recommandations :")
        for r in snapshot.recommendations:
            lines.append(f"   {r}")
        lines.append("")

    return "\n".join(lines)


def render_trend(project_root: Path) -> str:
    """Rendu texte de la tendance."""
    trend = compute_trend(project_root)
    if trend["trend"] == "insufficient_data":
        return "📊 Pas assez de données pour la tendance (min 2 snapshots)."

    arrows = {"improving": "📈", "declining": "📉", "stable": "➡️"}
    arrow = arrows.get(trend["trend"], "")

    lines = [
        f"\n{arrow} Tendance Fitness — {trend['trend'].upper()}",
        "-" * 40,
        f"  Dernière valeur : {trend['latest']}/100",
        f"  Delta moyen     : {trend['delta']:+.1f}",
        f"  Min / Max       : {trend['min']} / {trend['max']}",
        f"  Points analysés : {trend['points']}",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fitness-tracker",
        description="Grimoire Fitness Tracker — Score de santé global",
    )
    p.add_argument("--project-root", type=Path, default=Path())
    p.add_argument("--json", action="store_true", help="Sortie JSON")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {FITNESS_VERSION}")

    sub = p.add_subparsers(dest="command")
    sub.add_parser("check", help="Calculer le score de fitness")
    sub.add_parser("trend", help="Afficher la tendance")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    command = args.command or "check"

    if command == "check":
        snapshot = compute_fitness(root)
        save_snapshot(snapshot, root)
        if args.json:
            print(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_report(snapshot))
        return 0

    if command == "trend":
        if args.json:
            print(json.dumps(compute_trend(root), indent=2, ensure_ascii=False))
        else:
            print(render_trend(root))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
