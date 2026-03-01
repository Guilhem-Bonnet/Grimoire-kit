#!/usr/bin/env python3
"""
incubator.py — Incubation & Dormance BMAD.
============================================

Système de gestion du cycle de vie des idées, features et expérimentations :

  1. `submit`    — Soumettre une idée à l'incubateur
  2. `status`    — État de l'incubateur
  3. `viable`    — Vérifier la viabilité d'une idée
  4. `wake`      — Réveiller une idée dormante
  5. `prune`     — Nettoyer les idées non-viables

Cycle de vie :
  SEED       → Idée brute, pas encore évaluée
  INCUBATING → En évaluation, conditions de viabilité testées
  VIABLE     → Prête pour implémentation
  DORMANT    → Mise en sommeil (pas le moment, pas la priorité)
  DEAD       → Abandonnée (conditions jamais remplies)
  HATCHED    → Implémentée, sortie de l'incubateur

Conditions de viabilité :
  - Alignement avec les objectifs du projet
  - Faisabilité technique (dépendances satisfaites)
  - Pas de duplication avec l'existant
  - Sponsor (au moins un agent l'a validée)

Principe : "Les meilleures idées ont besoin de temps pour mûrir."

Usage :
  python3 incubator.py --project-root . submit --title "API GraphQL" --description "Ajouter support GraphQL"
  python3 incubator.py --project-root . status
  python3 incubator.py --project-root . viable --id IDEA-001
  python3 incubator.py --project-root . wake --id IDEA-003
  python3 incubator.py --project-root . prune

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
INCUBATOR_FILE = "incubator.json"

LIFECYCLE = {
    "SEED": {"emoji": "🌰", "description": "Idée brute"},
    "INCUBATING": {"emoji": "🥚", "description": "En évaluation"},
    "VIABLE": {"emoji": "🐣", "description": "Prête pour implémentation"},
    "DORMANT": {"emoji": "💤", "description": "En sommeil"},
    "DEAD": {"emoji": "💀", "description": "Abandonnée"},
    "HATCHED": {"emoji": "🐥", "description": "Implémentée"},
}

VIABILITY_CHECKS = [
    "alignment",    # Alignement avec le projet
    "feasibility",  # Faisabilité technique
    "uniqueness",   # Pas de duplication
    "sponsor",      # Au moins un agent sponsor
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Idea:
    id: str
    title: str
    description: str
    status: str = "SEED"
    created_at: str = ""
    updated_at: str = ""
    sponsor: str = ""
    tags: list[str] = field(default_factory=list)
    viability: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dormant_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "status": self.status, "created_at": self.created_at,
            "updated_at": self.updated_at, "sponsor": self.sponsor,
            "tags": self.tags, "viability": self.viability,
            "notes": self.notes, "dormant_reason": self.dormant_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Idea:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Persistence ──────────────────────────────────────────────────────────────

def _incubator_path(project_root: Path) -> Path:
    return project_root / "_bmad" / "_memory" / INCUBATOR_FILE


def load_incubator(project_root: Path) -> list[Idea]:
    fpath = _incubator_path(project_root)
    if not fpath.exists():
        return []
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        return [Idea.from_dict(d) for d in data]
    except (json.JSONDecodeError, OSError):
        return []


def save_incubator(project_root: Path, ideas: list[Idea]) -> None:
    fpath = _incubator_path(project_root)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(
        json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def next_id(ideas: list[Idea]) -> str:
    """Génère le prochain ID."""
    nums = []
    for idea in ideas:
        try:
            nums.append(int(idea.id.split("-")[1]))
        except (IndexError, ValueError):
            pass
    next_num = max(nums, default=0) + 1
    return f"IDEA-{next_num:03d}"


# ── Viability Engine ─────────────────────────────────────────────────────────

def check_viability(idea: Idea, project_root: Path) -> dict[str, bool]:
    """Vérifie les conditions de viabilité d'une idée."""
    checks = {}

    # Alignment — a des tags pertinents ou description non-vide
    checks["alignment"] = len(idea.description) > 20 and len(idea.tags) > 0

    # Feasibility — pas de blockers explicites
    blockers = ["impossible", "blocked", "dépend de", "requires external"]
    checks["feasibility"] = not any(b in idea.description.lower() for b in blockers)

    # Uniqueness — pas de duplication (check dans l'incubateur)
    ideas = load_incubator(project_root)
    other_titles = [i.title.lower() for i in ideas if i.id != idea.id and i.status != "DEAD"]
    checks["uniqueness"] = idea.title.lower() not in other_titles

    # Sponsor
    checks["sponsor"] = bool(idea.sponsor)

    return checks


def is_viable(viability: dict[str, bool]) -> bool:
    """Une idée est viable si toutes les conditions sont remplies."""
    return all(viability.values())


# ── Auto-Prune ───────────────────────────────────────────────────────────────

def auto_prune(ideas: list[Idea], max_dormant_days: int = 90) -> list[Idea]:
    """Marque comme DEAD les idées dormantes trop longtemps."""
    now = datetime.now()
    pruned = []
    for idea in ideas:
        if idea.status == "DORMANT" and idea.updated_at:
            try:
                updated = datetime.fromisoformat(idea.updated_at)
                if (now - updated).days > max_dormant_days:
                    idea.status = "DEAD"
                    idea.notes.append(f"Auto-pruned après {max_dormant_days}j de dormance")
                    pruned.append(idea)
            except ValueError:
                pass
    return pruned


# ── Formatters ───────────────────────────────────────────────────────────────

def format_idea(idea: Idea) -> str:
    info = LIFECYCLE.get(idea.status, {"emoji": "?", "description": "?"})
    lines = [
        f"   {info['emoji']} [{idea.id}] {idea.title}",
        f"      Status : {idea.status} — {info['description']}",
        f"      Description : {idea.description[:80]}",
    ]
    if idea.sponsor:
        lines.append(f"      Sponsor : {idea.sponsor}")
    if idea.tags:
        lines.append(f"      Tags : {', '.join(idea.tags)}")
    if idea.viability:
        checks = " ".join(f"{'✅' if v else '❌'}{k}" for k, v in idea.viability.items())
        lines.append(f"      Viabilité : {checks}")
    return "\n".join(lines)


def format_status(ideas: list[Idea]) -> str:
    lines = [f"🥚 Incubateur BMAD — {len(ideas)} idées\n"]

    status_counts = {}
    for idea in ideas:
        status_counts[idea.status] = status_counts.get(idea.status, 0) + 1

    for status, info in LIFECYCLE.items():
        count = status_counts.get(status, 0)
        bar = "█" * count
        lines.append(f"   {info['emoji']} {status:12s} {bar} {count}")

    lines.append("")

    # Détail par statut actif
    for status in ["SEED", "INCUBATING", "VIABLE"]:
        group = [i for i in ideas if i.status == status]
        if group:
            info = LIFECYCLE[status]
            lines.append(f"\n   {info['emoji']} {status} :")
            for idea in group:
                lines.append(f"      [{idea.id}] {idea.title}")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_submit(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    ideas = load_incubator(project_root)
    now = datetime.now().isoformat()

    idea = Idea(
        id=next_id(ideas),
        title=args.title,
        description=args.description or "",
        status="SEED",
        created_at=now,
        updated_at=now,
        sponsor=args.sponsor or "",
        tags=[t.strip() for t in args.tags.split(",")] if args.tags else [],
    )

    ideas.append(idea)
    save_incubator(project_root, ideas)

    if args.json:
        print(json.dumps(idea.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"🌰 Idée soumise : {idea.id}")
        print(format_idea(idea))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    ideas = load_incubator(project_root)
    if args.json:
        print(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
    else:
        if ideas:
            print(format_status(ideas))
        else:
            print("🥚 Incubateur vide — soumettez une idée avec 'submit'")
    return 0


def cmd_viable(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    ideas = load_incubator(project_root)
    target = None
    for idea in ideas:
        if idea.id == args.id:
            target = idea
            break
    if not target:
        print(f"❌ Idée {args.id} non trouvée")
        return 1

    viability = check_viability(target, project_root)
    target.viability = viability
    target.updated_at = datetime.now().isoformat()

    if is_viable(viability):
        target.status = "VIABLE"
        target.notes.append("Toutes les conditions de viabilité remplies")
    else:
        target.status = "INCUBATING"
        failed = [k for k, v in viability.items() if not v]
        target.notes.append(f"Conditions manquantes : {', '.join(failed)}")

    save_incubator(project_root, ideas)

    if args.json:
        print(json.dumps({"id": target.id, "viable": is_viable(viability),
                          "checks": viability}, indent=2, ensure_ascii=False))
    else:
        print(format_idea(target))
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    ideas = load_incubator(project_root)
    for idea in ideas:
        if idea.id == args.id:
            if idea.status == "DORMANT":
                idea.status = "INCUBATING"
                idea.updated_at = datetime.now().isoformat()
                idea.notes.append("Réveillée de dormance")
                save_incubator(project_root, ideas)
                if args.json:
                    print(json.dumps(idea.to_dict(), indent=2, ensure_ascii=False))
                else:
                    print(f"☀️ Idée {idea.id} réveillée")
                    print(format_idea(idea))
                return 0
            else:
                print(f"⚠️ {idea.id} n'est pas dormante (status: {idea.status})")
                return 1
    print(f"❌ Idée {args.id} non trouvée")
    return 1


def cmd_prune(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    ideas = load_incubator(project_root)
    pruned = auto_prune(ideas)
    save_incubator(project_root, ideas)
    if args.json:
        print(json.dumps({"pruned": [p.id for p in pruned]}, indent=2))
    else:
        if pruned:
            print(f"✂️ {len(pruned)} idées éliminées :")
            for p in pruned:
                print(f"   💀 [{p.id}] {p.title}")
        else:
            print("✅ Aucune idée à éliminer")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Incubator — Gestion du cycle de vie des idées",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_submit = subs.add_parser("submit", help="Soumettre une idée")
    p_submit.add_argument("--title", required=True)
    p_submit.add_argument("--description", default="")
    p_submit.add_argument("--sponsor", default="")
    p_submit.add_argument("--tags", default="")
    p_submit.set_defaults(func=cmd_submit)

    subs.add_parser("status", help="État de l'incubateur").set_defaults(func=cmd_status)

    p_viable = subs.add_parser("viable", help="Vérifier la viabilité")
    p_viable.add_argument("--id", required=True)
    p_viable.set_defaults(func=cmd_viable)

    p_wake = subs.add_parser("wake", help="Réveiller une idée dormante")
    p_wake.add_argument("--id", required=True)
    p_wake.set_defaults(func=cmd_wake)

    subs.add_parser("prune", help="Nettoyer les idées mortes").set_defaults(func=cmd_prune)

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
