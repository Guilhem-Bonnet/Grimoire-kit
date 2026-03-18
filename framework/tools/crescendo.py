#!/usr/bin/env python3
"""
crescendo.py — Onboarding progressif Grimoire.
============================================

Système d'onboarding adaptatif qui ajuste la complexité en fonction
du niveau de maturité de l'utilisateur et du projet :

  1. `assess`     — Évalue le niveau actuel (beginner → expert)
  2. `guide`      — Guide personnalisé pour le prochain palier
  3. `adapt`      — Adapte les menus/outputs au niveau détecté
  4. `milestones` — Jalons de progression
  5. `report`     — Rapport de progression

Niveaux :
  L0 — Découverte (0-20%)   : Guidage maximal, vocabulaire simplifié
  L1 — Apprenti (20-40%)    : Introduction des workflows, premiers outils
  L2 — Praticien (40-60%)   : Workflows complets, personnalisation
  L3 — Expert (60-80%)      : Outils avancés, optimisation
  L4 — Maître (80-100%)     : Full power, contribution, extension

Principe : "Le système grandit avec l'utilisateur — ni trop, ni trop peu."

Usage :
  python3 crescendo.py --project-root . assess
  python3 crescendo.py --project-root . guide
  python3 crescendo.py --project-root . adapt --level L2
  python3 crescendo.py --project-root . milestones
  python3 crescendo.py --project-root . report

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

LEVELS = {
    "L0": {"name": "Découverte", "range": (0, 20), "emoji": "🌱"},
    "L1": {"name": "Apprenti", "range": (20, 40), "emoji": "🌿"},
    "L2": {"name": "Praticien", "range": (40, 60), "emoji": "🌳"},
    "L3": {"name": "Expert", "range": (60, 80), "emoji": "🏔️"},
    "L4": {"name": "Maître", "range": (80, 100), "emoji": "🏆"},
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Milestone:
    """Jalon de progression."""
    id: str
    name: str
    level: str           # L0-L4
    description: str
    check: str           # Description du critère de validation
    achieved: bool = False

@dataclass
class AssessmentResult:
    level: str = "L0"
    score: int = 0
    details: dict[str, int] = field(default_factory=dict)
    achieved_milestones: list[str] = field(default_factory=list)
    next_milestones: list[str] = field(default_factory=list)

@dataclass
class GuidanceItem:
    title: str
    action: str
    why: str
    command: str = ""


# ── Milestones ───────────────────────────────────────────────────────────────

MILESTONES: list[Milestone] = [
    # L0
    Milestone("M01", "Premier projet", "L0", "Initialiser un premier projet", "project-context.yaml existe"),
    Milestone("M02", "Premier agent", "L0", "Activer un agent", "Au moins 1 agent accessible"),
    Milestone("M03", "README compris", "L0", "Lire le README", "README.md existe et > 50 lignes"),

    # L1
    Milestone("M04", "Workflow basique", "L1", "Exécuter un workflow complet", "Au moins 1 workflow dans le projet"),
    Milestone("M05", "Mémoire initiée", "L1", "Premier fichier mémoire", "Au moins 1 fichier dans _memory"),
    Milestone("M06", "Tests passants", "L1", "Smoke tests passent", "smoke-test.sh existe et exécutable"),

    # L2
    Milestone("M07", "Multi-agents", "L2", "Utiliser 3+ agents dans un projet", "3+ agents définis"),
    Milestone("M08", "Outil CLI maîtrisé", "L2", "Utiliser un outil CLI framework", "Au moins 1 outil dans framework/tools"),
    Milestone("M09", "Convention nommage", "L2", "Conventions de commit respectées", "cc-verify.sh existe"),

    # L3
    Milestone("M10", "Archétype custom", "L3", "Créer ou modifier un archétype", "Archétype personnalisé détecté"),
    Milestone("M11", "Pipeline intégré", "L3", "CI/CD configuré", "Workflow GitHub/GitLab existe"),
    Milestone("M12", "Team manifest", "L3", "Équipe multi-agents configurée", "team-manifest existe"),

    # L4
    Milestone("M13", "Extension créée", "L4", "Créer un outil ou agent custom", "Fichier custom dans le projet"),
    Milestone("M14", "Contribution", "L4", "Contribuer au framework", "CONTRIBUTING.md lu + PR ou fork"),
    Milestone("M15", "Écosystème", "L4", "Multi-projets avec partage", "Plusieurs projets avec archétypes partagés"),
]


# ── Assessment Engine ────────────────────────────────────────────────────────

def assess_project(project_root: Path) -> AssessmentResult:
    """Évalue le niveau actuel du projet/utilisateur."""
    result = AssessmentResult()
    checks: dict[str, bool] = {}

    # M01 — project-context.yaml
    checks["M01"] = any(project_root.rglob("project-context*"))

    # M02 — agents
    agent_files = list(project_root.rglob("**/agents/*.md"))
    checks["M02"] = len(agent_files) > 0

    # M03 — README
    readme = project_root / "README.md"
    checks["M03"] = readme.exists() and len(readme.read_text(encoding="utf-8", errors="ignore").splitlines()) > 50

    # M04 — workflows
    workflows = list(project_root.rglob("**/workflows/**/*.yaml")) + list(project_root.rglob("**/workflows/**/*.md"))
    checks["M04"] = len(workflows) > 0

    # M05 — mémoire
    memory = project_root / "_grimoire" / "_memory"
    mem_files = list(memory.rglob("*.md")) if memory.exists() else []
    checks["M05"] = len(mem_files) > 0

    # M06 — tests
    checks["M06"] = (project_root / "tests" / "smoke-test.sh").exists()

    # M07 — multi-agents
    checks["M07"] = len(agent_files) >= 3

    # M08 — outils CLI
    tools_dir = project_root / "framework" / "tools"
    checks["M08"] = tools_dir.exists() and len(list(tools_dir.glob("*.py"))) > 0

    # M09 — conventions
    checks["M09"] = (project_root / "framework" / "cc-verify.sh").exists()

    # M10 — archétype custom
    archetypes = list(project_root.glob("archetypes/*/"))
    checks["M10"] = len(archetypes) > 3

    # M11 — CI/CD
    checks["M11"] = any(project_root.rglob(".github/workflows/*.yml")) or (project_root / ".gitlab-ci.yml").exists()

    # M12 — team manifest
    checks["M12"] = any(project_root.rglob("**/teams/*.yaml")) or any(project_root.rglob("team-manifest*"))

    # M13 — extension custom
    checks["M13"] = (project_root / "framework" / "tools").exists() and len(list((project_root / "framework" / "tools").glob("*.py"))) > 5

    # M14 — contribution
    checks["M14"] = (project_root / "CONTRIBUTING.md").exists()

    # M15 — écosystème
    checks["M15"] = len(archetypes) > 5

    # Calcul du score
    achieved = [mid for mid, ok in checks.items() if ok]
    score = int(len(achieved) / len(MILESTONES) * 100)

    # Déterminer le niveau
    level = "L0"
    for lid, info in LEVELS.items():
        lo, hi = info["range"]
        if lo <= score < hi or (lid == "L4" and score >= 80):
            level = lid

    # Next milestones
    not_achieved = [mid for mid, ok in checks.items() if not ok]

    result.level = level
    result.score = score
    result.achieved_milestones = achieved
    result.next_milestones = not_achieved[:5]
    result.details = {mid: (1 if ok else 0) for mid, ok in checks.items()}

    return result


# ── Guidance Generator ───────────────────────────────────────────────────────

GUIDANCE_BY_LEVEL: dict[str, list[GuidanceItem]] = {
    "L0": [
        GuidanceItem("Initialise ton projet", "Crée un project-context.yaml",
                      "C'est la carte d'identité de ton projet", "grimoire-init.sh"),
        GuidanceItem("Découvre les agents", "Active un agent et explore son menu",
                      "Chaque agent a une personnalité et des compétences uniques"),
        GuidanceItem("Lis la doc", "Parcours getting-started.md",
                      "10 minutes qui te font gagner des heures"),
    ],
    "L1": [
        GuidanceItem("Lance ton premier workflow", "Choisis un workflow adapté à ton archétype",
                      "Les workflows structurent ta progression"),
        GuidanceItem("Active la mémoire", "Crée un premier fichier dans _memory",
                      "La mémoire collective fait du système un apprenti permanent"),
        GuidanceItem("Vérifie les tests", "Lance les smoke tests",
                      "Les tests sont ton filet de sécurité", "bash tests/smoke-test.sh"),
    ],
    "L2": [
        GuidanceItem("Utilise les outils CLI", "Explore les 20+ outils dans framework/tools",
                      "Chaque outil résout un problème spécifique"),
        GuidanceItem("Configure les conventions", "Active cc-verify.sh",
                      "La cohérence vient des conventions respectées"),
        GuidanceItem("Multi-agents", "Utilise 3+ agents dans le même projet",
                      "La synergie entre agents multiplie la valeur"),
    ],
    "L3": [
        GuidanceItem("Crée ton archétype", "Personnalise ou crée un archétype",
                      "Les archétypes encodent l'ADN de tes projets types"),
        GuidanceItem("CI/CD", "Configure un pipeline automatique",
                      "L'automatisation est le signe de maturité"),
        GuidanceItem("Team manifest", "Configure une équipe multi-agents",
                      "Le team-of-teams est la structure de collaboration"),
    ],
    "L4": [
        GuidanceItem("Contribue", "Crée un outil ou agent custom",
                      "Le framework grandit avec ses utilisateurs"),
        GuidanceItem("Multi-projets", "Partage des archétypes entre projets",
                      "L'écosystème crée de la valeur exponentielle"),
        GuidanceItem("Mentore", "Aide d'autres à progresser avec le framework",
                      "L'enseignement est le meilleur apprentissage"),
    ],
}


def generate_guidance(level: str) -> list[GuidanceItem]:
    return GUIDANCE_BY_LEVEL.get(level, GUIDANCE_BY_LEVEL["L0"])


# ── Formatters ───────────────────────────────────────────────────────────────

def format_assessment(result: AssessmentResult) -> str:
    info = LEVELS[result.level]
    lines = [
        f"{info['emoji']} Niveau : {result.level} — {info['name']}  ({result.score}%)",
        "",
        "   Jalons atteints :",
    ]
    milestone_map = {m.id: m for m in MILESTONES}
    for mid in result.achieved_milestones:
        m = milestone_map.get(mid)
        if m:
            lines.append(f"      ✅ [{mid}] {m.name}")

    if result.next_milestones:
        lines.append("\n   Prochains jalons :")
        for mid in result.next_milestones:
            m = milestone_map.get(mid)
            if m:
                lines.append(f"      ⬜ [{mid}] {m.name} — {m.description}")

    # Progress bar
    bar = "█" * (result.score // 10) + "░" * (10 - result.score // 10)
    lines.append(f"\n   Progression : {bar} {result.score}%")

    return "\n".join(lines)


def format_guidance(level: str, items: list[GuidanceItem]) -> str:
    info = LEVELS.get(level, LEVELS["L0"])
    lines = [f"📚 Guide pour {info['emoji']} {info['name']} (niveau {level})\n"]
    for i, item in enumerate(items, 1):
        lines.append(f"   {i}. {item.title}")
        lines.append(f"      Action : {item.action}")
        lines.append(f"      Pourquoi : {item.why}")
        if item.command:
            lines.append(f"      Commande : {item.command}")
        lines.append("")
    return "\n".join(lines)


def format_milestones() -> str:
    lines = ["🏁 Jalons de progression Grimoire\n"]
    for level, info in LEVELS.items():
        lines.append(f"  {info['emoji']} {level} — {info['name']}")
        for m in MILESTONES:
            if m.level == level:
                lines.append(f"      [{m.id}] {m.name}")
                lines.append(f"            {m.description}")
        lines.append("")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_assess(args: argparse.Namespace) -> int:
    result = assess_project(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({
            "level": result.level, "score": result.score,
            "achieved": result.achieved_milestones,
            "next": result.next_milestones,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_assessment(result))
    return 0


def cmd_guide(args: argparse.Namespace) -> int:
    result = assess_project(Path(args.project_root).resolve())
    items = generate_guidance(result.level)
    if args.json:
        print(json.dumps([{"title": g.title, "action": g.action, "why": g.why}
                          for g in items], indent=2, ensure_ascii=False))
    else:
        print(format_guidance(result.level, items))
    return 0


def cmd_adapt(args: argparse.Namespace) -> int:
    level = args.level or assess_project(Path(args.project_root).resolve()).level
    items = generate_guidance(level)
    if args.json:
        print(json.dumps({"level": level, "guidance": [
            {"title": g.title, "action": g.action} for g in items
        ]}, indent=2, ensure_ascii=False))
    else:
        print(format_guidance(level, items))
    return 0


def cmd_milestones(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([{"id": m.id, "name": m.name, "level": m.level,
                           "description": m.description} for m in MILESTONES],
                         indent=2, ensure_ascii=False))
    else:
        print(format_milestones())
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    result = assess_project(Path(args.project_root).resolve())
    items = generate_guidance(result.level)
    if args.json:
        print(json.dumps({
            "assessment": {"level": result.level, "score": result.score,
                           "achieved": result.achieved_milestones},
            "guidance": [{"title": g.title, "action": g.action} for g in items],
            "milestones_total": len(MILESTONES),
            "milestones_achieved": len(result.achieved_milestones),
        }, indent=2, ensure_ascii=False))
    else:
        print(format_assessment(result))
        print("\n" + "─" * 50 + "\n")
        print(format_guidance(result.level, items))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Crescendo — Onboarding progressif",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("assess", help="Évaluer le niveau").set_defaults(func=cmd_assess)
    subs.add_parser("guide", help="Guide personnalisé").set_defaults(func=cmd_guide)

    p_adapt = subs.add_parser("adapt", help="Adapter au niveau")
    p_adapt.add_argument("--level", choices=list(LEVELS.keys()), help="Forcer un niveau")
    p_adapt.set_defaults(func=cmd_adapt)

    subs.add_parser("milestones", help="Liste des jalons").set_defaults(func=cmd_milestones)
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
