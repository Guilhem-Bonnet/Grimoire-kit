#!/usr/bin/env python3
"""
new-game-plus.py — New Game+ Grimoire.
====================================

Création d'un nouveau projet à partir d'un projet existant, en important
sélectivement les acquis, la mémoire, et les customisations :

  1. `scan`       — Analyser un projet source pour détection des assets
  2. `similarity` — Trouver les archétypes les plus proches
  3. `plan`       — Planifier l'import sélectif
  4. `export`     — Exporter les assets réutilisables
  5. `recommend`  — Recommandations de customisation

Inspiré du concept "New Game+" des jeux vidéo : recommencer avec les
acquis du run précédent.

Usage :
  python3 new-game-plus.py --source-project ./old-project scan
  python3 new-game-plus.py --source-project ./old-project similarity
  python3 new-game-plus.py --source-project ./old-project plan
  python3 new-game-plus.py --source-project ./old-project export --output ./ng-export
  python3 new-game-plus.py --source-project ./old-project recommend

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

ASSET_CATEGORIES = {
    "agents": {"patterns": ["**/agents/*.md"], "priority": "high"},
    "workflows": {"patterns": ["**/workflows/**/*.yaml", "**/workflows/**/*.md"], "priority": "high"},
    "tools": {"patterns": ["**/tools/*.py", "**/tools/*.sh"], "priority": "high"},
    "memory": {"patterns": ["**/_memory/**/*.md", "**/_memory/**/*.json"], "priority": "medium"},
    "config": {"patterns": ["**/config.yaml", "project-context*"], "priority": "medium"},
    "docs": {"patterns": ["docs/**/*.md", "README.md", "CONTRIBUTING.md"], "priority": "low"},
    "tests": {"patterns": ["tests/**/*"], "priority": "low"},
    "archetypes": {"patterns": ["archetypes/**/*"], "priority": "medium"},
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Asset:
    """Un asset réutilisable."""
    category: str
    path: str
    size_bytes: int = 0
    reusable: bool = True
    reason: str = ""

@dataclass
class SimilarityResult:
    archetype: str
    score: float       # 0.0-1.0
    matching_files: list[str] = field(default_factory=list)

@dataclass
class ImportPlan:
    assets_to_import: list[Asset] = field(default_factory=list)
    assets_to_skip: list[Asset] = field(default_factory=list)
    estimated_size: int = 0
    recommendations: list[str] = field(default_factory=list)

@dataclass
class ProjectScan:
    total_files: int = 0
    assets: list[Asset] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)
    tech_stack: list[str] = field(default_factory=list)


# ── Scanner ──────────────────────────────────────────────────────────────────

def scan_project(source: Path) -> ProjectScan:
    """Scanne un projet source pour inventorier les assets."""
    scan = ProjectScan()
    all_files = [f for f in source.rglob("*") if f.is_file()
                 and ".git" not in str(f) and "__pycache__" not in str(f)]
    scan.total_files = len(all_files)

    for cat, info in ASSET_CATEGORIES.items():
        count = 0
        for pattern in info["patterns"]:
            parts = pattern.split("/")
            glob_pat = parts[-1]
            for fpath in source.rglob(glob_pat):
                if ".git" in str(fpath):
                    continue
                rel = str(fpath.relative_to(source))
                # Vérifier que le pattern dir match
                if "**" in pattern or any(p in rel for p in parts[:-1]):
                    scan.assets.append(Asset(
                        category=cat,
                        path=rel,
                        size_bytes=fpath.stat().st_size,
                    ))
                    count += 1
        scan.categories[cat] = count

    # Detect tech stack
    extensions = Counter(f.suffix for f in all_files)
    stack_map = {
        ".py": "Python", ".ts": "TypeScript", ".js": "JavaScript",
        ".yaml": "YAML", ".yml": "YAML", ".md": "Markdown",
        ".sh": "Bash", ".tf": "Terraform", ".go": "Go",
    }
    for ext, _count in extensions.most_common(5):
        if ext in stack_map:
            scan.tech_stack.append(stack_map[ext])

    return scan


# ── Similarity ───────────────────────────────────────────────────────────────

def find_similarity(source: Path, framework_root: Path) -> list[SimilarityResult]:
    """Trouve les archétypes les plus similaires au projet source."""
    archetypes_dir = framework_root / "archetypes"
    if not archetypes_dir.exists():
        return []

    results = []
    source_files = {str(f.relative_to(source)) for f in source.rglob("*") if f.is_file()}
    source_extensions = Counter(Path(f).suffix for f in source_files)

    for arch_dir in archetypes_dir.iterdir():
        if not arch_dir.is_dir():
            continue
        arch_files = {str(f.relative_to(arch_dir)) for f in arch_dir.rglob("*") if f.is_file()}

        # Jaccard similarity sur les noms de fichiers
        if not arch_files:
            continue

        # Comparer les extensions et structures
        arch_extensions = Counter(Path(f).suffix for f in arch_files)
        common_ext = set(source_extensions.keys()) & set(arch_extensions.keys())
        all_ext = set(source_extensions.keys()) | set(arch_extensions.keys())
        ext_score = len(common_ext) / max(1, len(all_ext))

        # Comparer les fichiers communs (par nom)
        source_names = {Path(f).name for f in source_files}
        arch_names = {Path(f).name for f in arch_files}
        common_names = source_names & arch_names
        name_score = len(common_names) / max(1, len(source_names | arch_names))

        score = (ext_score * 0.4 + name_score * 0.6)
        results.append(SimilarityResult(
            archetype=arch_dir.name,
            score=score,
            matching_files=sorted(common_names)[:10],
        ))

    return sorted(results, key=lambda r: r.score, reverse=True)


# ── Import Planner ───────────────────────────────────────────────────────────

def plan_import(scan: ProjectScan) -> ImportPlan:
    """Planifie l'import sélectif."""
    plan = ImportPlan()

    for asset in scan.assets:
        cat_info = ASSET_CATEGORIES.get(asset.category, {})
        priority = cat_info.get("priority", "low")

        if priority in ("high", "medium"):
            plan.assets_to_import.append(asset)
            plan.estimated_size += asset.size_bytes
        else:
            plan.assets_to_skip.append(asset)
            asset.reason = f"Priorité {priority} — import optionnel"

    # Recommendations
    if scan.categories.get("memory", 0) > 0:
        plan.recommendations.append("💾 Mémoire collective détectée — import recommandé pour ne pas perdre les apprentissages")
    if scan.categories.get("tools", 0) > 3:
        plan.recommendations.append("🔧 Outils custom détectés — vérifier la compatibilité avec le nouveau projet")
    if scan.categories.get("agents", 0) > 5:
        plan.recommendations.append("🤖 Nombreux agents — évaluer quels agents sont pertinents pour le nouveau contexte")

    return plan


# ── Export ───────────────────────────────────────────────────────────────────

def export_assets(source: Path, plan: ImportPlan, output: Path) -> int:
    """Exporte les assets vers un répertoire."""
    output.mkdir(parents=True, exist_ok=True)
    exported = 0
    for asset in plan.assets_to_import:
        src = source / asset.path
        dst = output / asset.path
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            exported += 1
    # Manifest
    manifest = {
        "source": str(source),
        "exported_at": datetime.now().isoformat(),
        "assets": [{"category": a.category, "path": a.path} for a in plan.assets_to_import],
        "total": exported,
    }
    (output / "ng-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return exported


# ── Formatters ───────────────────────────────────────────────────────────────

def format_scan(scan: ProjectScan) -> str:
    lines = [
        "🎮 New Game+ — Scan du projet source",
        f"   Fichiers total : {scan.total_files}",
        f"   Stack détectée : {', '.join(scan.tech_stack) if scan.tech_stack else 'N/A'}",
        "",
        "   Assets par catégorie :",
    ]
    for cat, count in sorted(scan.categories.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * min(20, count)
        prio = ASSET_CATEGORIES.get(cat, {}).get("priority", "?")
        lines.append(f"      {cat:15s} {bar} {count:3d}  [{prio}]")
    return "\n".join(lines)


def format_similarity(results: list[SimilarityResult]) -> str:
    if not results:
        return "🎯 Aucun archétype trouvé"
    lines = ["🎯 Similarité avec les archétypes :\n"]
    for r in results[:5]:
        bar = "█" * int(r.score * 10)
        lines.append(f"   {bar} {r.archetype} ({r.score:.0%})")
        if r.matching_files:
            lines.append(f"      Fichiers communs : {', '.join(r.matching_files[:5])}")
    return "\n".join(lines)


def format_plan(plan: ImportPlan) -> str:
    lines = [
        "📋 Plan d'import New Game+",
        f"   À importer : {len(plan.assets_to_import)} assets ({plan.estimated_size / 1024:.1f} KB)",
        f"   À ignorer : {len(plan.assets_to_skip)} assets",
        "",
    ]
    if plan.recommendations:
        lines.append("   Recommandations :")
        for r in plan.recommendations:
            lines.append(f"      {r}")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> int:
    source = Path(args.source_project).resolve()
    scan = scan_project(source)
    if args.json:
        print(json.dumps({"total_files": scan.total_files, "categories": scan.categories,
                          "tech_stack": scan.tech_stack, "assets": len(scan.assets)},
                         indent=2, ensure_ascii=False))
    else:
        print(format_scan(scan))
    return 0


def cmd_similarity(args: argparse.Namespace) -> int:
    source = Path(args.source_project).resolve()
    fw_root = Path(args.project_root).resolve()
    results = find_similarity(source, fw_root)
    if args.json:
        print(json.dumps([{"archetype": r.archetype, "score": r.score}
                          for r in results], indent=2, ensure_ascii=False))
    else:
        print(format_similarity(results))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    source = Path(args.source_project).resolve()
    scan = scan_project(source)
    plan = plan_import(scan)
    if args.json:
        print(json.dumps({"to_import": len(plan.assets_to_import),
                          "to_skip": len(plan.assets_to_skip),
                          "size_kb": plan.estimated_size / 1024,
                          "recommendations": plan.recommendations},
                         indent=2, ensure_ascii=False))
    else:
        print(format_plan(plan))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    source = Path(args.source_project).resolve()
    output = Path(args.output).resolve()
    scan = scan_project(source)
    plan = plan_import(scan)
    exported = export_assets(source, plan, output)
    if args.json:
        print(json.dumps({"exported": exported, "output": str(output)}, indent=2))
    else:
        print(f"✅ {exported} assets exportés vers {output}")
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    source = Path(args.source_project).resolve()
    scan = scan_project(source)
    plan = plan_import(scan)
    fw_root = Path(args.project_root).resolve()
    similarities = find_similarity(source, fw_root)

    if args.json:
        print(json.dumps({
            "recommendations": plan.recommendations,
            "best_archetype": similarities[0].archetype if similarities else None,
            "stack": scan.tech_stack,
        }, indent=2, ensure_ascii=False))
    else:
        print("💡 Recommandations New Game+\n")
        print(f"   Stack : {', '.join(scan.tech_stack)}")
        if similarities:
            print(f"   Archétype recommandé : {similarities[0].archetype} ({similarities[0].score:.0%})")
        for r in plan.recommendations:
            print(f"   {r}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire New Game+ — Nouveau projet depuis l'existant",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-project", default=".", help="Projet source")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("scan", help="Scanner le projet source").set_defaults(func=cmd_scan)
    subs.add_parser("similarity", help="Similarité avec archétypes").set_defaults(func=cmd_similarity)
    subs.add_parser("plan", help="Plan d'import").set_defaults(func=cmd_plan)

    p_export = subs.add_parser("export", help="Exporter les assets")
    p_export.add_argument("--output", required=True, help="Répertoire de sortie")
    p_export.set_defaults(func=cmd_export)

    subs.add_parser("recommend", help="Recommandations").set_defaults(func=cmd_recommend)

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
