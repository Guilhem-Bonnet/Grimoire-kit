#!/usr/bin/env python3
"""
workflow-snippets.py — Workflow Snippets composables BMAD.
===========================================================

Système de snippets de workflow réutilisables qu'on peut composer
pour créer des workflows complets :

  1. `list`     — Lister les snippets disponibles
  2. `compose`  — Composer un workflow à partir de snippets
  3. `validate` — Valider une composition
  4. `create`   — Créer un nouveau snippet
  5. `catalog`  — Catalogue avec métadonnées

Chaque snippet :
  - A des inputs/outputs typés
  - Déclare ses prérequis
  - Peut se chaîner avec d'autres via les types d'I/O
  - Est testable isolément

Principe : "Les LEGO du workflow — petites briques, grandes constructions."

Usage :
  python3 workflow-snippets.py --project-root . list
  python3 workflow-snippets.py --project-root . compose --snippets "gather-reqs,define-arch,implement"
  python3 workflow-snippets.py --project-root . validate --snippets "gather-reqs,implement"
  python3 workflow-snippets.py --project-root . create --name "code-review" --input "code" --output "review-report"
  python3 workflow-snippets.py --project-root . catalog

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
SNIPPETS_DIR = "framework/workflows/snippets"
SNIPPETS_CATALOG = "workflow-snippets-catalog.json"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Snippet:
    """Un snippet de workflow composable."""
    id: str
    name: str
    description: str
    inputs: list[str] = field(default_factory=list)       # types d'entrée
    outputs: list[str] = field(default_factory=list)      # types de sortie
    agent: str = ""                                        # agent recommandé
    estimated_minutes: int = 0
    prerequisites: list[str] = field(default_factory=list)  # IDs d'autres snippets
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "inputs": self.inputs, "outputs": self.outputs, "agent": self.agent,
            "estimated_minutes": self.estimated_minutes,
            "prerequisites": self.prerequisites, "tags": self.tags,
        }


@dataclass
class CompositionResult:
    valid: bool = True
    snippets: list[Snippet] = field(default_factory=list)
    total_minutes: int = 0
    chain_issues: list[str] = field(default_factory=list)
    generated_yaml: str = ""


# ── Built-in Snippets ───────────────────────────────────────────────────────

BUILTIN_SNIPPETS: list[Snippet] = [
    Snippet("gather-reqs", "Collecte des exigences", "Rassembler et documenter les exigences",
            inputs=["project-brief"], outputs=["requirements-doc"],
            agent="analyst", estimated_minutes=60, tags=["planning"]),

    Snippet("define-arch", "Définir l'architecture", "Concevoir l'architecture technique",
            inputs=["requirements-doc"], outputs=["architecture-doc"],
            agent="architect", estimated_minutes=90, prerequisites=["gather-reqs"], tags=["design"]),

    Snippet("write-stories", "Écrire les user stories", "Découper en stories implémentables",
            inputs=["requirements-doc", "architecture-doc"], outputs=["stories"],
            agent="pm", estimated_minutes=45, prerequisites=["gather-reqs"], tags=["planning"]),

    Snippet("implement", "Implémenter", "Coder une story",
            inputs=["stories", "architecture-doc"], outputs=["code"],
            agent="dev", estimated_minutes=120, prerequisites=["write-stories"], tags=["build"]),

    Snippet("code-review", "Code review", "Revue de code qualité",
            inputs=["code"], outputs=["review-report"],
            agent="qa", estimated_minutes=30, prerequisites=["implement"], tags=["quality"]),

    Snippet("write-tests", "Écrire les tests", "Créer les tests automatisés",
            inputs=["code", "stories"], outputs=["test-suite"],
            agent="qa", estimated_minutes=60, prerequisites=["implement"], tags=["quality"]),

    Snippet("write-docs", "Documenter", "Écrire la documentation",
            inputs=["code", "architecture-doc"], outputs=["documentation"],
            agent="tech-writer", estimated_minutes=45, tags=["docs"]),

    Snippet("security-scan", "Scan sécurité", "Vérification sécurité du code",
            inputs=["code"], outputs=["security-report"],
            agent="qa", estimated_minutes=20, prerequisites=["implement"], tags=["security"]),

    Snippet("deploy-plan", "Plan de déploiement", "Planifier le déploiement",
            inputs=["code", "architecture-doc"], outputs=["deploy-plan"],
            agent="architect", estimated_minutes=30, tags=["ops"]),

    Snippet("retrospective", "Rétrospective", "Analyser et tirer les leçons",
            inputs=["review-report", "stories"], outputs=["retro-notes"],
            agent="sm", estimated_minutes=30, tags=["process"]),

    Snippet("ux-review", "Revue UX", "Évaluer l'expérience utilisateur",
            inputs=["code", "requirements-doc"], outputs=["ux-report"],
            agent="ux-designer", estimated_minutes=40, tags=["design"]),

    Snippet("acceptance-test", "Test d'acceptation", "Valider les critères d'acceptation",
            inputs=["code", "stories"], outputs=["acceptance-report"],
            agent="qa", estimated_minutes=30, prerequisites=["implement"], tags=["quality"]),
]


# ── Snippet Discovery ───────────────────────────────────────────────────────

def discover_snippets(project_root: Path) -> list[Snippet]:
    """Combine les snippets built-in avec ceux définis dans le projet."""
    all_snippets = list(BUILTIN_SNIPPETS)

    snippets_dir = project_root / SNIPPETS_DIR
    if snippets_dir.exists():
        for fpath in snippets_dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    all_snippets.append(Snippet(**{k: v for k, v in data.items()
                                                   if k in Snippet.__dataclass_fields__}))
            except (json.JSONDecodeError, OSError, TypeError):
                pass

    return all_snippets


# ── Composition Engine ───────────────────────────────────────────────────────

def compose_workflow(snippet_ids: list[str], all_snippets: list[Snippet]) -> CompositionResult:
    """Compose un workflow à partir de snippet IDs."""
    result = CompositionResult()
    snippet_map = {s.id: s for s in all_snippets}

    for sid in snippet_ids:
        if sid not in snippet_map:
            result.valid = False
            result.chain_issues.append(f"Snippet inconnu : {sid}")
            continue
        result.snippets.append(snippet_map[sid])

    if not result.snippets:
        result.valid = False
        return result

    # Validate chain : les outputs du précédent doivent matcher les inputs du suivant
    available_outputs: set[str] = set()
    for i, snippet in enumerate(result.snippets):
        if i > 0:
            missing_inputs = set(snippet.inputs) - available_outputs
            if missing_inputs:
                result.chain_issues.append(
                    f"⚠️ {snippet.id} nécessite {missing_inputs} — non produit par les étapes précédentes"
                )
        available_outputs.update(snippet.outputs)

    # Check prerequisites
    seen_ids = set()
    for snippet in result.snippets:
        for prereq in snippet.prerequisites:
            if prereq not in seen_ids and prereq not in [s.id for s in result.snippets]:
                result.chain_issues.append(f"⚠️ {snippet.id} requiert {prereq} (absent de la composition)")
        seen_ids.add(snippet.id)

    if result.chain_issues:
        result.valid = False

    result.total_minutes = sum(s.estimated_minutes for s in result.snippets)

    # Generate YAML
    yaml_lines = [
        "# Workflow composé automatiquement",
        f"# Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# Durée estimée : {result.total_minutes} min",
        "",
        "name: composed-workflow",
        "steps:",
    ]
    for i, s in enumerate(result.snippets, 1):
        yaml_lines.append(f"  - id: step-{i}")
        yaml_lines.append(f"    snippet: {s.id}")
        yaml_lines.append(f"    name: \"{s.name}\"")
        yaml_lines.append(f"    agent: {s.agent}")
        yaml_lines.append(f"    inputs: [{', '.join(s.inputs)}]")
        yaml_lines.append(f"    outputs: [{', '.join(s.outputs)}]")
        yaml_lines.append(f"    estimated_minutes: {s.estimated_minutes}")
        yaml_lines.append("")

    result.generated_yaml = "\n".join(yaml_lines)
    return result


# ── Formatters ───────────────────────────────────────────────────────────────

def format_snippet_list(snippets: list[Snippet]) -> str:
    lines = [f"🧩 Snippets disponibles : {len(snippets)}\n"]
    by_tag = {}
    for s in snippets:
        for tag in s.tags or ["other"]:
            by_tag.setdefault(tag, []).append(s)

    for tag, items in sorted(by_tag.items()):
        lines.append(f"   📁 {tag}")
        for s in items:
            io = f"({', '.join(s.inputs)}) → ({', '.join(s.outputs)})"
            lines.append(f"      [{s.id}] {s.name} — {io}")
            lines.append(f"            Agent: {s.agent}, ~{s.estimated_minutes}min")
        lines.append("")
    return "\n".join(lines)


def format_composition(result: CompositionResult) -> str:
    status = "✅ Valide" if result.valid else "❌ Invalide"
    lines = [
        f"🔗 Composition — {status}",
        f"   Étapes : {len(result.snippets)}",
        f"   Durée estimée : {result.total_minutes} min",
        "",
    ]
    for i, s in enumerate(result.snippets, 1):
        lines.append(f"   {i}. [{s.id}] {s.name}")
        lines.append(f"      Agent: {s.agent}")
        lines.append(f"      In: {s.inputs} → Out: {s.outputs}")
    if result.chain_issues:
        lines.append("\n   ⚠️ Problèmes :")
        for issue in result.chain_issues:
            lines.append(f"      {issue}")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> int:
    snippets = discover_snippets(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps([s.to_dict() for s in snippets], indent=2, ensure_ascii=False))
    else:
        print(format_snippet_list(snippets))
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    snippets = discover_snippets(Path(args.project_root).resolve())
    ids = [s.strip() for s in args.snippets.split(",")]
    result = compose_workflow(ids, snippets)
    if args.json:
        print(json.dumps({
            "valid": result.valid, "total_minutes": result.total_minutes,
            "issues": result.chain_issues, "yaml": result.generated_yaml,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_composition(result))
        if result.valid:
            print(f"\n--- YAML généré ---\n\n{result.generated_yaml}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    snippets = discover_snippets(Path(args.project_root).resolve())
    ids = [s.strip() for s in args.snippets.split(",")]
    result = compose_workflow(ids, snippets)
    if args.json:
        print(json.dumps({"valid": result.valid, "issues": result.chain_issues}, indent=2))
    else:
        status = "✅ Composition valide" if result.valid else "❌ Composition invalide"
        print(status)
        for issue in result.chain_issues:
            print(f"   {issue}")
    return 0 if result.valid else 1


def cmd_create(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    snippets_dir = project_root / SNIPPETS_DIR
    snippets_dir.mkdir(parents=True, exist_ok=True)

    snippet = Snippet(
        id=args.name,
        name=args.name.replace("-", " ").title(),
        description=args.description or "",
        inputs=[i.strip() for i in args.input.split(",")] if args.input else [],
        outputs=[o.strip() for o in args.output.split(",")] if args.output else [],
        agent=args.agent or "",
        estimated_minutes=args.minutes or 30,
    )

    fpath = snippets_dir / f"{args.name}.json"
    fpath.write_text(json.dumps(snippet.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(snippet.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"🧩 Snippet créé : {fpath}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    snippets = discover_snippets(project_root)
    catalog = {
        "updated_at": datetime.now().isoformat(),
        "total": len(snippets),
        "snippets": [s.to_dict() for s in snippets],
    }
    catalog_path = project_root / "_bmad" / "_memory" / SNIPPETS_CATALOG
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
    else:
        print(format_snippet_list(snippets))
        print(f"\n   📁 Catalogue sauvé : {catalog_path}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Workflow Snippets — Composition de workflows",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    subs.add_parser("list", help="Lister les snippets").set_defaults(func=cmd_list)

    p_compose = subs.add_parser("compose", help="Composer un workflow")
    p_compose.add_argument("--snippets", required=True, help="IDs séparés par virgules")
    p_compose.set_defaults(func=cmd_compose)

    p_validate = subs.add_parser("validate", help="Valider une composition")
    p_validate.add_argument("--snippets", required=True, help="IDs séparés par virgules")
    p_validate.set_defaults(func=cmd_validate)

    p_create = subs.add_parser("create", help="Créer un snippet")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--description", default="")
    p_create.add_argument("--input", default="")
    p_create.add_argument("--output", default="")
    p_create.add_argument("--agent", default="")
    p_create.add_argument("--minutes", type=int, default=30)
    p_create.set_defaults(func=cmd_create)

    subs.add_parser("catalog", help="Mettre à jour le catalogue").set_defaults(func=cmd_catalog)

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
