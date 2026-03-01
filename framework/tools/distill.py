#!/usr/bin/env python3
"""
distill.py — Réduction & Director's Cut BMAD.
===============================================

Templates de condensation et modes de verbosité pour tous les outputs :

  1. `condense`  — Condenser un document (résumé, abstract, TL;DR)
  2. `expand`    — Étendre un résumé en détails
  3. `modes`     — Afficher les modes de verbosité disponibles
  4. `transform` — Transformer un document selon un template
  5. `compare`   — Comparer deux niveaux de détail

Modes de verbosité :
  - TLDR       : 1-3 phrases, l'essentiel uniquement
  - SUMMARY    : Un paragraphe, points clés
  - STANDARD   : Niveau normal, détails utiles
  - VERBOSE    : Tous les détails, exemples inclus
  - DIRECTORS  : Version étendue avec notes, alternatives, raisonnement

Templates de condensation par type :
  - PRD → Executive Summary
  - Architecture → Decision Record (ADR)
  - Story → Acceptance Criteria only
  - Code Review → Issues only
  - Meeting notes → Actions only

Principe : "L'information parfaite est celle qui donne exactement ce dont
l'utilisateur a besoin à ce moment, ni plus ni moins."

Usage :
  python3 distill.py condense --input document.md --mode TLDR
  python3 distill.py condense --input document.md --mode SUMMARY
  python3 distill.py modes
  python3 distill.py transform --input prd.md --template executive-summary
  python3 distill.py compare --input document.md

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

VERBOSITY_MODES = {
    "TLDR": {
        "description": "1-3 phrases, l'essentiel",
        "max_sentences": 3,
        "include_headers": False,
        "include_examples": False,
        "include_rationale": False,
    },
    "SUMMARY": {
        "description": "Un paragraphe, points clés",
        "max_sentences": 10,
        "include_headers": True,
        "include_examples": False,
        "include_rationale": False,
    },
    "STANDARD": {
        "description": "Niveau normal, détails utiles",
        "max_sentences": 50,
        "include_headers": True,
        "include_examples": True,
        "include_rationale": False,
    },
    "VERBOSE": {
        "description": "Tous les détails, exemples inclus",
        "max_sentences": 200,
        "include_headers": True,
        "include_examples": True,
        "include_rationale": True,
    },
    "DIRECTORS": {
        "description": "Version étendue, notes + alternatives + raisonnement",
        "max_sentences": 500,
        "include_headers": True,
        "include_examples": True,
        "include_rationale": True,
    },
}

TEMPLATES = {
    "executive-summary": {
        "name": "Executive Summary",
        "sections": ["Objectif", "Contexte", "Décisions clés", "Next steps"],
        "max_lines_per_section": 5,
    },
    "adr": {
        "name": "Architecture Decision Record",
        "sections": ["Contexte", "Décision", "Conséquences", "Alternatives considérées"],
        "max_lines_per_section": 10,
    },
    "acceptance-only": {
        "name": "Critères d'acceptation",
        "sections": ["Given/When/Then", "Edge cases"],
        "max_lines_per_section": 15,
    },
    "issues-only": {
        "name": "Issues détectées",
        "sections": ["Bloquants", "Améliorations", "Suggestions"],
        "max_lines_per_section": 10,
    },
    "actions-only": {
        "name": "Actions",
        "sections": ["Qui fait quoi", "Deadline", "Dépendances"],
        "max_lines_per_section": 10,
    },
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DocumentAnalysis:
    title: str = ""
    total_lines: int = 0
    total_words: int = 0
    total_sentences: int = 0
    headers: list[str] = field(default_factory=list)
    key_sentences: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)

@dataclass
class CondensedOutput:
    mode: str
    original_words: int = 0
    condensed_words: int = 0
    ratio: float = 0.0
    content: str = ""


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze_document(content: str) -> DocumentAnalysis:
    """Analyse un document Markdown."""
    analysis = DocumentAnalysis()
    lines = content.splitlines()
    analysis.total_lines = len(lines)
    analysis.total_words = len(content.split())

    # Extract headers
    current_section = ""
    section_lines: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip()
            analysis.headers.append(header)
            current_section = header
            section_lines[current_section] = []
        elif current_section and stripped:
            section_lines[current_section].append(stripped)

        # Count sentences
        analysis.total_sentences += stripped.count(".") + stripped.count("!") + stripped.count("?")

    analysis.sections = section_lines

    # Extract key sentences (first sentence of each section + bold lines)
    for _section, slines in section_lines.items():
        for sline in slines[:2]:
            if sline and not sline.startswith("|") and not sline.startswith("```"):
                analysis.key_sentences.append(sline)
        # Bold or important lines
        for sline in slines:
            if "**" in sline or sline.startswith("- [ ]") or sline.startswith("- [x]"):
                analysis.key_sentences.append(sline)

    if lines:
        # Title
        for line in lines:
            if line.strip().startswith("#"):
                analysis.title = line.strip().lstrip("#").strip()
                break

    return analysis


# ── Condensation Engine ──────────────────────────────────────────────────────

def condense(content: str, mode: str) -> CondensedOutput:
    """Condense un document selon le mode de verbosité."""
    analysis = analyze_document(content)
    config = VERBOSITY_MODES.get(mode, VERBOSITY_MODES["STANDARD"])

    output_lines = []

    if mode == "TLDR":
        # Titre + premières phrases clés
        if analysis.title:
            output_lines.append(f"**{analysis.title}**")
        output_lines.extend(analysis.key_sentences[:config["max_sentences"]])

    elif mode == "SUMMARY":
        if analysis.title:
            output_lines.append(f"# {analysis.title}\n")
        # Première phrase de chaque section
        for header in analysis.headers[:7]:
            section_content = analysis.sections.get(header, [])
            if section_content:
                output_lines.append(f"**{header}** : {section_content[0]}")

    elif mode == "STANDARD":
        if analysis.title:
            output_lines.append(f"# {analysis.title}\n")
        for header in analysis.headers:
            section_content = analysis.sections.get(header, [])
            output_lines.append(f"## {header}")
            for line in section_content[:config.get("max_lines_per_section", 10)]:
                output_lines.append(line)
            output_lines.append("")

    elif mode in ("VERBOSE", "DIRECTORS"):
        # Return original with annotations for DIRECTORS
        if mode == "DIRECTORS":
            output_lines.append("<!-- Director's Cut — version enrichie -->\n")
        output_lines.append(content)
        if mode == "DIRECTORS":
            output_lines.append("\n---\n## Notes du Director's Cut")
            output_lines.append(f"- Document original : {analysis.total_words} mots, {analysis.total_lines} lignes")
            output_lines.append(f"- Sections : {len(analysis.headers)}")
            output_lines.append(f"- Phrases clés identifiées : {len(analysis.key_sentences)}")

    result_content = "\n".join(output_lines)
    result_words = len(result_content.split())

    return CondensedOutput(
        mode=mode,
        original_words=analysis.total_words,
        condensed_words=result_words,
        ratio=result_words / analysis.total_words if analysis.total_words > 0 else 0,
        content=result_content,
    )


# ── Transform Engine ────────────────────────────────────────────────────────

def transform_document(content: str, template_id: str) -> str:
    """Transforme un document selon un template."""
    template = TEMPLATES.get(template_id)
    if not template:
        return f"❌ Template inconnu : {template_id}"

    analysis = analyze_document(content)
    lines = [f"# {template['name']}\n"]
    lines.append(f"> Source : {analysis.title}\n")

    for section in template["sections"]:
        lines.append(f"## {section}")
        # Essayer de trouver des contenus pertinents
        matched = False
        for header, section_content in analysis.sections.items():
            if any(kw.lower() in header.lower() for kw in section.split("/")):
                for line in section_content[:template["max_lines_per_section"]]:
                    lines.append(line)
                matched = True
                break
        if not matched:
            lines.append(f"_À compléter — section « {section} » non trouvée dans le document source_")
        lines.append("")

    return "\n".join(lines)


# ── Formatters ───────────────────────────────────────────────────────────────

def format_modes() -> str:
    lines = ["📐 Modes de verbosité BMAD\n"]
    for mode, config in VERBOSITY_MODES.items():
        lines.append(f"   [{mode}] {config['description']}")
        lines.append(f"      Max sentences: {config['max_sentences']}")
        lines.append(f"      Exemples: {'✅' if config['include_examples'] else '❌'}")
        lines.append(f"      Raisonnement: {'✅' if config['include_rationale'] else '❌'}")
        lines.append("")
    lines.append("📋 Templates disponibles :")
    for tid, tmpl in TEMPLATES.items():
        lines.append(f"   [{tid}] {tmpl['name']} — sections: {', '.join(tmpl['sections'])}")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_condense(args: argparse.Namespace) -> int:
    try:
        content = Path(args.input).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"❌ Impossible de lire {args.input} : {e}")
        return 1

    result = condense(content, args.mode)
    if args.json:
        print(json.dumps({
            "mode": result.mode, "original_words": result.original_words,
            "condensed_words": result.condensed_words,
            "ratio": result.ratio, "content": result.content,
        }, indent=2, ensure_ascii=False))
    else:
        print(f"📐 Condensation {result.mode} ({result.original_words} → {result.condensed_words} mots, "
              f"{result.ratio:.0%})\n")
        print(result.content)
    return 0


def cmd_modes(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps({
            "modes": {k: v["description"] for k, v in VERBOSITY_MODES.items()},
            "templates": {k: v["name"] for k, v in TEMPLATES.items()},
        }, indent=2, ensure_ascii=False))
    else:
        print(format_modes())
    return 0


def cmd_transform(args: argparse.Namespace) -> int:
    try:
        content = Path(args.input).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"❌ Impossible de lire {args.input} : {e}")
        return 1

    result = transform_document(content, args.template)
    if args.json:
        print(json.dumps({"template": args.template, "content": result}, indent=2, ensure_ascii=False))
    else:
        print(result)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        content = Path(args.input).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"❌ Impossible de lire {args.input} : {e}")
        return 1

    if args.json:
        results = {}
        for mode in VERBOSITY_MODES:
            r = condense(content, mode)
            results[mode] = {"words": r.condensed_words, "ratio": r.ratio}
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("📊 Comparaison des modes de condensation\n")
        for mode in VERBOSITY_MODES:
            r = condense(content, mode)
            bar = "█" * int(r.ratio * 20)
            print(f"   {mode:12s} {bar:20s} {r.condensed_words:5d} mots ({r.ratio:.0%})")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Distill — Réduction & Director's Cut",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_condense = subs.add_parser("condense", help="Condenser un document")
    p_condense.add_argument("--input", required=True, help="Fichier source")
    p_condense.add_argument("--mode", choices=list(VERBOSITY_MODES.keys()), default="SUMMARY")
    p_condense.set_defaults(func=cmd_condense)

    subs.add_parser("modes", help="Modes de verbosité").set_defaults(func=cmd_modes)

    p_transform = subs.add_parser("transform", help="Transformer selon un template")
    p_transform.add_argument("--input", required=True)
    p_transform.add_argument("--template", choices=list(TEMPLATES.keys()), required=True)
    p_transform.set_defaults(func=cmd_transform)

    p_compare = subs.add_parser("compare", help="Comparer les niveaux")
    p_compare.add_argument("--input", required=True)
    p_compare.set_defaults(func=cmd_compare)

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
