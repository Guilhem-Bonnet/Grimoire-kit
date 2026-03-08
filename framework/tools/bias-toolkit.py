#!/usr/bin/env python3
"""
bias-toolkit.py — Catalogue de biais cognitifs Grimoire.
=====================================================

Toolkit pour exploiter les biais cognitifs de manière éthique dans
la conception d'agents IA et d'interactions utilisateur :

  1. `catalog`   — Catalogue complet des biais exploitables
  2. `audit`     — Audit d'un agent/workflow pour biais involontaires
  3. `suggest`   — Suggestions de biais à exploiter pour un objectif donné
  4. `ethics`    — Vérification éthique d'une utilisation de biais
  5. `report`    — Rapport complet d'audit + recommandations

Chaque biais est documenté avec :
  - Description, mécanisme psychologique
  - Exploitation éthique (nudge positif)
  - Exploitation dangereuse (à éviter)
  - Exemples d'intégration agent-base

Principe : "Comprendre les biais pour aider, pas pour manipuler."

Usage :
  python3 bias-toolkit.py catalog
  python3 bias-toolkit.py audit --target framework/agent-base.md
  python3 bias-toolkit.py suggest --goal "onboarding"
  python3 bias-toolkit.py ethics --bias anchoring --usage "default estimates"
  python3 bias-toolkit.py report --project-root .

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

ETHICAL_LEVELS = {
    "green": "✅ Usage éthique — nudge positif, transparent",
    "yellow": "⚠️ Usage prudent — risque de manipulation douce",
    "red": "🔴 Usage dangereux — manipulation non consentie",
}


# ── Biais Catalog ────────────────────────────────────────────────────────────

@dataclass
class Bias:
    id: str
    name: str
    category: str               # cognitive, social, decision, memory
    mechanism: str              # Mécanisme psychologique
    ethical_use: str            # Comment l'utiliser éthiquement
    dangerous_use: str          # Ce qu'il faut éviter
    agent_integration: str      # Comment l'intégrer dans un agent
    detection_patterns: list[str] = field(default_factory=list)  # regex pour détecter
    keywords: list[str] = field(default_factory=list)


BIAS_CATALOG: list[Bias] = [
    Bias(
        id="B01", name="Ancrage (Anchoring)",
        category="decision",
        mechanism="La première information reçue sert de point de référence pour toutes les suivantes",
        ethical_use="Fournir des estimations réalistes en premier pour cadrer la discussion",
        dangerous_use="Proposer des valeurs extrêmes pour biaiser les estimations",
        agent_integration="Dans les menus, lister l'option recommandée en premier",
        detection_patterns=[r"default\s*[\:=]", r"recommand|suggest|proposé"],
        keywords=["ancrage", "default", "première impression"],
    ),
    Bias(
        id="B02", name="Effet de cadrage (Framing)",
        category="cognitive",
        mechanism="La façon dont une information est présentée influence la décision",
        ethical_use="Présenter les choix de manière positive et constructive",
        dangerous_use="Formuler les options pour pousser vers un choix prédéterminé",
        agent_integration="Formuler les résumés d'étape en termes de progression, pas d'échec",
        detection_patterns=[r"attention|warning|danger", r"✅|❌|⚠️"],
        keywords=["cadrage", "framing", "formulation"],
    ),
    Bias(
        id="B03", name="Biais de confirmation",
        category="cognitive",
        mechanism="Tendance à chercher des informations qui confirment nos croyances existantes",
        ethical_use="L'adversarial review challenge explicitement les hypothèses",
        dangerous_use="Ne présenter que les infos qui supportent une décision déjà prise",
        agent_integration="Intégrer un devil's advocate systématique dans les workflows de décision",
        detection_patterns=[r"confirm|valid|vérifié", r"comme prévu|attendu"],
        keywords=["confirmation", "challenge", "adversarial"],
    ),
    Bias(
        id="B04", name="Effet Zeigarnik",
        category="memory",
        mechanism="Les tâches inachevées restent en mémoire plus longtemps que les tâches terminées",
        ethical_use="Montrer la progression pour motiver la complétion",
        dangerous_use="Créer artificiellement du suspense pour forcer l'engagement",
        agent_integration="Progress bars, compteurs d'étapes restantes, récapitulatif de session",
        detection_patterns=[r"étape\s+\d+\s*/\s*\d+", r"progress|avancement|restant"],
        keywords=["zeigarnik", "progression", "inachevé"],
    ),
    Bias(
        id="B05", name="Paradoxe du choix",
        category="decision",
        mechanism="Trop d'options paralysent la décision",
        ethical_use="Chunking 7±2 : limiter les menus à 5-9 items",
        dangerous_use="Réduire les choix pour cacher des alternatives valides",
        agent_integration="Submenus pour items secondaires, recommandation mise en avant",
        detection_patterns=[r"<menu>", r"choisir|sélectionner|option"],
        keywords=["choix", "chunking", "parallysis"],
    ),
    Bias(
        id="B06", name="Peak-End Rule",
        category="memory",
        mechanism="On juge une expérience par son pic émotionnel et sa fin",
        ethical_use="Soigner les résumés de fin de session et les moments de réussite",
        dangerous_use="Masquer les problèmes en terminant toujours positivement",
        agent_integration="Résumé de fin de session célébrant les accomplissements",
        detection_patterns=[r"récapitul|résumé|conclusion|final"],
        keywords=["peak-end", "conclusion", "expérience"],
    ),
    Bias(
        id="B07", name="Effet de dotation (Endowment)",
        category="decision",
        mechanism="On valorise davantage ce qu'on possède déjà",
        ethical_use="Valoriser le travail déjà accompli pour motiver la suite",
        dangerous_use="Rendre le changement artificiellement coûteux (lock-in)",
        agent_integration="Montrer le patrimoine de connaissances accumulé dans le projet",
        detection_patterns=[r"déjà\s+(fait|créé|existant)", r"acquis|patrimoine"],
        keywords=["dotation", "acquis", "lock-in"],
    ),
    Bias(
        id="B08", name="Effet de halo",
        category="social",
        mechanism="L'impression positive sur un aspect influence le jugement global",
        ethical_use="Design soigné des outputs pour renforcer la confiance dans le contenu",
        dangerous_use="Beau formatage pour cacher un contenu médiocre",
        agent_integration="Formatage Markdown soigné, emojis pertinents, structure claire",
        detection_patterns=[r"format|style|présentation", r"emoji|icon"],
        keywords=["halo", "formatage", "perception"],
    ),
    Bias(
        id="B09", name="Sunk Cost Fallacy",
        category="decision",
        mechanism="Tendance à continuer un investissement à cause du passé, pas du futur",
        ethical_use="Rappeler explicitement que l'effort passé ne justifie pas de continuer",
        dangerous_use="Invoquer le temps déjà investi pour pousser à la complétion",
        agent_integration="Dans les reviews, toujours évaluer le futur, pas le passé",
        detection_patterns=[r"déjà investi|on a déjà|temps passé"],
        keywords=["sunk cost", "investissement", "passé"],
    ),
    Bias(
        id="B10", name="Biais d'autorité",
        category="social",
        mechanism="Tendance à suivre l'avis d'une figure d'autorité sans évaluation critique",
        ethical_use="Les agents assument un rôle d'expert avec humilité et transparence",
        dangerous_use="Utiliser le ton d'autorité pour imposer des décisions sans justification",
        agent_integration="Chaque recommandation doit être accompagnée de son raisonnement",
        detection_patterns=[r"doit|obligatoire|impératif|toujours|jamais"],
        keywords=["autorité", "expertise", "justification"],
    ),
    Bias(
        id="B11", name="Effet de récence",
        category="memory",
        mechanism="Les informations les plus récentes ont plus de poids",
        ethical_use="Placer les informations clés en fin de message (position stratégique)",
        dangerous_use="Noyer les avertissements au milieu pour qu'ils soient oubliés",
        agent_integration="Les actions requises toujours en fin de message",
        detection_patterns=[r"en résumé|en conclusion|pour finir"],
        keywords=["récence", "position", "dernier"],
    ),
    Bias(
        id="B12", name="Affordance (Gibson)",
        category="cognitive",
        mechanism="Les propriétés perçues d'un objet suggèrent comment l'utiliser",
        ethical_use="Commandes slash, menus numérotés, emojis indicatifs",
        dangerous_use="Affordances trompeuses (bouton qui fait le contraire de ce qu'il suggère)",
        agent_integration="Slash commands avec noms explicites, emojis-guide dans les menus",
        detection_patterns=[r"slash|commande|menu|bouton|cliquer"],
        keywords=["affordance", "interface", "découvrabilité"],
    ),
]


# ── Audit Engine ─────────────────────────────────────────────────────────────

@dataclass
class BiasDetection:
    bias_id: str
    bias_name: str
    file: str
    line: int
    context: str
    ethical_level: str   # green, yellow, red
    suggestion: str = ""


def audit_file(fpath: Path) -> list[BiasDetection]:
    """Audite un fichier pour détecter des patterns de biais."""
    detections = []
    try:
        lines = fpath.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    for bias in BIAS_CATALOG:
        for pat_str in bias.detection_patterns:
            pat = re.compile(pat_str, re.IGNORECASE)
            for i, line in enumerate(lines, 1):
                if pat.search(line):
                    detections.append(BiasDetection(
                        bias_id=bias.id,
                        bias_name=bias.name,
                        file=str(fpath),
                        line=i,
                        context=line.strip()[:120],
                        ethical_level="green",
                        suggestion=bias.ethical_use,
                    ))
                    break  # Un seul match par biais par fichier

    return detections


def audit_project(project_root: Path) -> list[BiasDetection]:
    """Audite tout le projet."""
    detections = []
    for pattern in ["**/*.md", "**/*.yaml", "**/*.py"]:
        for fpath in project_root.rglob(pattern.split("/")[-1]):
            if ".git" in str(fpath) or "__pycache__" in str(fpath):
                continue
            detections.extend(audit_file(fpath))
    return detections


# ── Suggest Engine ───────────────────────────────────────────────────────────

GOAL_BIAS_MAP = {
    "onboarding": ["B01", "B04", "B05", "B06", "B12"],
    "decision": ["B01", "B02", "B03", "B05", "B09"],
    "motivation": ["B04", "B06", "B07", "B11"],
    "quality": ["B03", "B08", "B10"],
    "retention": ["B04", "B06", "B07"],
    "trust": ["B08", "B10", "B03"],
}


def suggest_for_goal(goal: str) -> list[Bias]:
    """Suggère des biais exploitables pour un objectif."""
    goal_lower = goal.lower()
    bias_ids = set()
    for key, ids in GOAL_BIAS_MAP.items():
        if key in goal_lower:
            bias_ids.update(ids)

    # Fallback : chercher dans les keywords
    if not bias_ids:
        for bias in BIAS_CATALOG:
            for kw in bias.keywords:
                if kw in goal_lower:
                    bias_ids.add(bias.id)

    catalog_map = {b.id: b for b in BIAS_CATALOG}
    return [catalog_map[bid] for bid in sorted(bias_ids) if bid in catalog_map]


# ── Ethics Check ─────────────────────────────────────────────────────────────

def check_ethics(bias_id: str, usage: str) -> dict:
    """Vérifie l'éthique d'un usage de biais."""
    catalog_map = {b.id: b for b in BIAS_CATALOG}
    bias = catalog_map.get(bias_id.upper())
    if not bias:
        return {"error": f"Biais {bias_id} inconnu"}

    usage_lower = usage.lower()
    # Heuristique simple : mots-clés dangereux
    danger_words = ["forcer", "obliger", "cacher", "masquer", "manipuler", "tromper", "piéger"]
    ethical_words = ["aider", "guider", "suggérer", "informer", "transparence", "choix"]

    danger_score = sum(1 for w in danger_words if w in usage_lower)
    ethical_score = sum(1 for w in ethical_words if w in usage_lower)

    if danger_score > ethical_score:
        level = "red"
    elif danger_score > 0:
        level = "yellow"
    else:
        level = "green"

    return {
        "bias": bias.name,
        "usage": usage,
        "ethical_level": level,
        "verdict": ETHICAL_LEVELS[level],
        "ethical_alternative": bias.ethical_use,
        "danger_to_avoid": bias.dangerous_use,
    }


# ── Formatters ───────────────────────────────────────────────────────────────

def format_catalog(biases: list[Bias]) -> str:
    lines = [f"🧠 Catalogue de biais cognitifs — {len(biases)} biais\n"]
    cats = {}
    for b in biases:
        cats.setdefault(b.category, []).append(b)
    for cat, items in sorted(cats.items()):
        lines.append(f"  📁 {cat.upper()}")
        for b in items:
            lines.append(f"    [{b.id}] {b.name}")
            lines.append(f"         Mécanisme : {b.mechanism[:80]}")
            lines.append(f"         ✅ Éthique : {b.ethical_use[:80]}")
            lines.append(f"         🔴 Danger  : {b.dangerous_use[:80]}")
            lines.append("")
    return "\n".join(lines)


def format_audit(detections: list[BiasDetection]) -> str:
    lines = [f"🔍 Audit biais — {len(detections)} patterns détectés\n"]
    by_bias = {}
    for d in detections:
        by_bias.setdefault(d.bias_name, []).append(d)
    for name, dets in sorted(by_bias.items()):
        lines.append(f"  {name} ({len(dets)} occurrences)")
        for d in dets[:3]:
            lines.append(f"    📄 {d.file}:{d.line}")
            lines.append(f"       {d.context}")
        if len(dets) > 3:
            lines.append(f"    ... et {len(dets) - 3} autres")
        lines.append("")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_catalog(args: argparse.Namespace) -> int:
    if args.json:
        data = [{"id": b.id, "name": b.name, "category": b.category,
                 "mechanism": b.mechanism, "ethical_use": b.ethical_use,
                 "dangerous_use": b.dangerous_use}
                for b in BIAS_CATALOG]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(format_catalog(BIAS_CATALOG))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve() if args.target else Path(args.project_root).resolve()
    if target.is_file():
        dets = audit_file(target)
    else:
        dets = audit_project(target)
    if args.json:
        print(json.dumps([{"bias": d.bias_name, "file": d.file, "line": d.line,
                           "context": d.context, "level": d.ethical_level}
                          for d in dets], indent=2, ensure_ascii=False))
    else:
        print(format_audit(dets))
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    biases = suggest_for_goal(args.goal)
    if args.json:
        print(json.dumps([{"id": b.id, "name": b.name, "integration": b.agent_integration}
                          for b in biases], indent=2, ensure_ascii=False))
    else:
        print(f"💡 Biais suggérés pour « {args.goal} » :\n")
        for b in biases:
            print(f"  [{b.id}] {b.name}")
            print(f"    → {b.agent_integration}")
            print()
        if not biases:
            print("  Aucun biais trouvé — essayez : onboarding, decision, motivation, quality, trust")
    return 0


def cmd_ethics(args: argparse.Namespace) -> int:
    result = check_ethics(args.bias, args.usage)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if "error" in result:
            print(f"❌ {result['error']}")
            return 1
        print(f"⚖️ Vérification éthique : {result['bias']}")
        print(f"   Usage proposé : {result['usage']}")
        print(f"   Verdict : {result['verdict']}")
        print(f"   Alternative éthique : {result['ethical_alternative']}")
        print(f"   Danger à éviter : {result['danger_to_avoid']}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    detections = audit_project(project_root)
    if args.json:
        print(json.dumps({
            "total_biases_catalogued": len(BIAS_CATALOG),
            "detections": len(detections),
            "by_category": {cat: len([b for b in BIAS_CATALOG if b.category == cat])
                           for cat in set(b.category for b in BIAS_CATALOG)},
        }, indent=2, ensure_ascii=False))
    else:
        print(format_catalog(BIAS_CATALOG))
        print("\n" + "─" * 60 + "\n")
        print(format_audit(detections))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Bias Toolkit — Biais cognitifs éthiques",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("catalog", help="Catalogue des biais").set_defaults(func=cmd_catalog)

    p_audit = subs.add_parser("audit", help="Audit d'un fichier/projet")
    p_audit.add_argument("--target", help="Fichier ou dossier cible")
    p_audit.set_defaults(func=cmd_audit)

    p_suggest = subs.add_parser("suggest", help="Suggérer des biais pour un objectif")
    p_suggest.add_argument("--goal", required=True, help="Objectif (ex: onboarding, decision)")
    p_suggest.set_defaults(func=cmd_suggest)

    p_ethics = subs.add_parser("ethics", help="Vérification éthique")
    p_ethics.add_argument("--bias", required=True, help="ID du biais (ex: B01)")
    p_ethics.add_argument("--usage", required=True, help="Description de l'usage proposé")
    p_ethics.set_defaults(func=cmd_ethics)

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
