#!/usr/bin/env python3
"""
rosetta.py — Glossaire cross-domain Rosetta Stone BMAD.
=========================================================

Traduction automatique Business ↔ Tech ↔ UX.
Génère un glossaire unifié à partir des artefacts du projet,
détecte les termes ambigus utilisés différemment par chaque domaine,
et trace l'étymologie des décisions.

Features :
  1. `build`    — Construire le glossaire depuis les artefacts
  2. `lookup`   — Chercher un terme avec ses traductions cross-domain
  3. `ambiguity`— Détecter les termes ambigus (même mot, sens différent)
  4. `etymology`— Étymologie d'une décision (pourquoi ce choix ?)
  5. `export`   — Exporter le glossaire en Markdown

Usage :
  python3 rosetta.py --project-root . build
  python3 rosetta.py --project-root . lookup --term "authentication"
  python3 rosetta.py --project-root . ambiguity
  python3 rosetta.py --project-root . etymology --term "microservices"
  python3 rosetta.py --project-root . export
  python3 rosetta.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

ROSETTA_VERSION = "1.0.0"

# Domaines
DOMAINS = ["business", "tech", "ux"]

# Patterns de détection de domaine par fichier
DOMAIN_PATTERNS = {
    "business": [
        r"PRD", r"requirement", r"stakeholder", r"market", r"revenue",
        r"user[-_]story", r"epic", r"feature[-_]request",
    ],
    "tech": [
        r"ARCH", r"architecture", r"api", r"database", r"infrastructure",
        r"deploy", r"code", r"service", r"module", r"framework",
    ],
    "ux": [
        r"UX", r"wireframe", r"mockup", r"persona", r"journey",
        r"design", r"interface", r"interaction", r"usability",
    ],
}

# Fichiers sources par domaine
DOMAIN_GLOBS = {
    "business": ["**/PRD*.md", "**/requirements/**", "**/planning-artifacts/*.md"],
    "tech": ["**/ARCH*.md", "**/architecture/**", "**/*.py", "**/framework/**/*.md"],
    "ux": ["**/UX*.md", "**/wireframe*", "**/design/**", "**/persona*"],
}

# Stop words
STOPS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "les", "des", "une", "que", "est", "sur", "par", "pour", "dans",
    "avec", "from", "this", "that", "have", "will", "been", "was",
    "should", "could", "would", "also", "more", "when", "which",
    "être", "avoir", "faire", "plus", "tout", "bien", "comme",
}

MIN_TERM_LEN = 4
MAX_GLOSSARY_ENTRIES = 200


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class GlossaryEntry:
    """Entrée du glossaire."""
    term: str
    domains: dict[str, str] = field(default_factory=dict)  # domain -> definition/context
    frequency: dict[str, int] = field(default_factory=dict)  # domain -> count
    sources: list[str] = field(default_factory=list)
    is_ambiguous: bool = False
    etymology: str = ""

    @property
    def total_freq(self) -> int:
        return sum(self.frequency.values())

    @property
    def domain_count(self) -> int:
        return len(self.domains)


@dataclass
class Glossary:
    """Glossaire complet."""
    entries: dict[str, GlossaryEntry] = field(default_factory=dict)
    ambiguities: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class EtymologyRecord:
    """Étymologie d'une décision / terme."""
    term: str
    first_seen: str = ""        # date
    first_source: str = ""      # fichier
    evolution: list[str] = field(default_factory=list)  # changements
    rationale: str = ""         # raison si trouvée


# ── Domain Detection ────────────────────────────────────────────────────────

def detect_domain(filepath: Path, content: str = "") -> str:
    """Détecte le domaine d'un fichier."""
    fname = filepath.name.upper()
    fpath = str(filepath).lower()

    for domain, patterns in DOMAIN_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, fname, re.I) or re.search(pattern, fpath, re.I):
                return domain

    # Fallback par extension
    if filepath.suffix in (".py", ".ts", ".js", ".go", ".yaml"):
        return "tech"
    if "ux" in fpath or "design" in fpath:
        return "ux"

    return "business"  # Défaut


# ── Text Processing ─────────────────────────────────────────────────────────

def extract_terms(text: str) -> Counter:
    """Extrait les termes significatifs d'un texte."""
    words = re.findall(r'[a-zA-ZÀ-ÿ_-]{4,}', text.lower())
    meaningful = [w for w in words if w not in STOPS and len(w) >= MIN_TERM_LEN]
    return Counter(meaningful)


def extract_context(text: str, term: str, max_len: int = 120) -> str:
    """Extrait le contexte d'utilisation d'un terme."""
    pattern = rf'[^.]*\b{re.escape(term)}\b[^.]*'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(0).strip()[:max_len]
    return ""


# ── Glossary Builder ────────────────────────────────────────────────────────

def build_glossary(project_root: Path) -> Glossary:
    """Construit le glossaire depuis les artefacts du projet."""
    glossary = Glossary()

    # Collecter les termes par domaine
    domain_terms: dict[str, Counter] = {d: Counter() for d in DOMAINS}
    domain_contexts: dict[str, dict[str, str]] = {d: {} for d in DOMAINS}
    term_sources: dict[str, list[str]] = defaultdict(list)

    # Scanner les fichiers
    scanned = 0
    for _glob_domain, globs in DOMAIN_GLOBS.items():
        for glob_pattern in globs:
            for fpath in project_root.glob(glob_pattern):
                if not fpath.is_file() or fpath.stat().st_size > 500_000:
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8")
                    domain = detect_domain(fpath, content)
                    terms = extract_terms(content)
                    domain_terms[domain] += terms

                    for term in terms:
                        if term not in domain_contexts[domain]:
                            ctx = extract_context(content, term)
                            if ctx:
                                domain_contexts[domain][term] = ctx
                        rel_path = str(fpath.relative_to(project_root))
                        if rel_path not in term_sources[term]:
                            term_sources[term].append(rel_path)

                    scanned += 1
                except (OSError, UnicodeDecodeError):
                    pass

    # Construire les entrées
    all_terms: set[str] = set()
    for terms in domain_terms.values():
        all_terms.update(terms.keys())

    for term in sorted(all_terms):
        entry = GlossaryEntry(term=term)

        for domain in DOMAINS:
            freq = domain_terms[domain].get(term, 0)
            if freq > 0:
                entry.frequency[domain] = freq
                ctx = domain_contexts[domain].get(term, "")
                if ctx:
                    entry.domains[domain] = ctx

        entry.sources = term_sources.get(term, [])[:5]

        # Détection d'ambiguïté : même terme, contextes très différents
        if entry.domain_count >= 2:
            contexts = list(entry.domains.values())
            if len(contexts) >= 2:
                # Heuristique simple : mots partagés entre contextes
                ctx_words = [set(re.findall(r'\w+', c.lower())) for c in contexts]
                if len(ctx_words) >= 2:
                    overlap = len(ctx_words[0] & ctx_words[1]) / max(1, len(ctx_words[0] | ctx_words[1]))
                    if overlap < 0.3:  # Peu de mots en commun = potentiellement ambigu
                        entry.is_ambiguous = True

        if entry.total_freq >= 2:
            glossary.entries[term] = entry

    # Trier par fréquence totale, garder les top N
    sorted_entries = sorted(glossary.entries.values(), key=lambda e: e.total_freq, reverse=True)
    glossary.entries = {e.term: e for e in sorted_entries[:MAX_GLOSSARY_ENTRIES]}
    glossary.ambiguities = [e.term for e in glossary.entries.values() if e.is_ambiguous]

    return glossary


# ── Etymology ───────────────────────────────────────────────────────────────

def trace_etymology(project_root: Path, term: str) -> EtymologyRecord:
    """Trace l'étymologie d'un terme/décision."""
    record = EtymologyRecord(term=term)

    # Chercher dans les décisions
    decisions_files = list(project_root.rglob("*decision*"))
    decisions_files += list(project_root.rglob("*ADR*"))
    decisions_files += list(project_root.rglob("*learnings*"))

    mentions = []
    for fpath in decisions_files:
        if not fpath.is_file() or fpath.suffix not in (".md", ".yaml", ".txt"):
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            if term.lower() in content.lower():
                # Extraire le contexte + date
                ctx = extract_context(content, term, 200)
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', content)
                date_str = date_match.group(1) if date_match else ""
                mentions.append({
                    "file": str(fpath.relative_to(project_root)),
                    "context": ctx,
                    "date": date_str,
                })
        except (OSError, UnicodeDecodeError):
            pass

    # Construire l'étymologie
    if mentions:
        # Trier par date
        dated = [m for m in mentions if m["date"]]
        dated.sort(key=lambda m: m["date"])

        if dated:
            record.first_seen = dated[0]["date"]
            record.first_source = dated[0]["file"]

        record.evolution = [
            f"[{m.get('date', '?')}] {m['file']}: {m['context'][:80]}"
            for m in (dated or mentions)
        ]

        # Chercher une rationale
        for m in mentions:
            ctx = m["context"].lower()
            if any(kw in ctx for kw in ["because", "parce que", "reason", "raison",
                                         "chosen", "choisi", "decided", "décidé"]):
                record.rationale = m["context"]
                break

    return record


# ── Formatters ───────────────────────────────────────────────────────────────

def format_glossary(glossary: Glossary) -> str:
    lines = [
        "📖 Rosetta Stone — Glossaire Cross-Domain",
        f"   Entrées : {len(glossary.entries)}",
        f"   Ambiguïtés : {len(glossary.ambiguities)}",
        "",
    ]

    # Top cross-domain terms
    cross = [e for e in glossary.entries.values() if e.domain_count >= 2]
    if cross:
        lines.append(f"   🔄 Termes cross-domain ({len(cross)}) :")
        for entry in cross[:15]:
            domains_str = " | ".join(
                f"{d}(×{entry.frequency.get(d, 0)})" for d in DOMAINS if d in entry.frequency
            )
            amb = " ⚠️" if entry.is_ambiguous else ""
            lines.append(f"      {entry.term:25s} {domains_str}{amb}")
        lines.append("")

    # Ambiguities
    if glossary.ambiguities:
        lines.append(f"   ⚠️ Termes ambigus ({len(glossary.ambiguities)}) :")
        for term in glossary.ambiguities[:10]:
            entry = glossary.entries[term]
            lines.append(f"      {term}:")
            for domain, ctx in entry.domains.items():
                lines.append(f"         [{domain}] {ctx[:80]}")
        lines.append("")

    return "\n".join(lines)


def format_lookup(entry: GlossaryEntry | None, term: str) -> str:
    if not entry:
        return f"❌ Terme '{term}' non trouvé dans le glossaire"

    lines = [f"📖 {entry.term}", ""]
    for domain in DOMAINS:
        if domain in entry.domains:
            lines.append(f"   [{domain:8s}] (×{entry.frequency.get(domain, 0)}) {entry.domains[domain]}")
    if entry.is_ambiguous:
        lines.append("\n   ⚠️ ATTENTION : terme ambigu — sens différent selon le domaine")
    if entry.sources:
        lines.append(f"\n   Sources : {', '.join(entry.sources[:3])}")
    return "\n".join(lines)


def format_etymology(record: EtymologyRecord) -> str:
    lines = [f"📜 Étymologie : {record.term}", ""]
    if record.first_seen:
        lines.append(f"   Première apparition : {record.first_seen} dans {record.first_source}")
    if record.rationale:
        lines.append(f"   Raison : {record.rationale[:150]}")
    if record.evolution:
        lines.append(f"\n   Évolution ({len(record.evolution)} mentions) :")
        for ev in record.evolution[:10]:
            lines.append(f"      {ev}")
    return "\n".join(lines)


def export_markdown(glossary: Glossary) -> str:
    """Exporte en Markdown structuré."""
    lines = [
        "# 📖 Glossaire Rosetta Stone",
        f"\n> Généré le {glossary.timestamp[:10]}",
        f"> {len(glossary.entries)} entrées | {len(glossary.ambiguities)} ambiguïtés\n",
        "| Terme | Business | Tech | UX | Ambigu |",
        "|-------|----------|------|----|--------|",
    ]
    for entry in sorted(glossary.entries.values(), key=lambda e: e.total_freq, reverse=True):
        biz = f"×{entry.frequency.get('business', 0)}" if "business" in entry.frequency else "—"
        tech = f"×{entry.frequency.get('tech', 0)}" if "tech" in entry.frequency else "—"
        ux = f"×{entry.frequency.get('ux', 0)}" if "ux" in entry.frequency else "—"
        amb = "⚠️" if entry.is_ambiguous else ""
        lines.append(f"| {entry.term} | {biz} | {tech} | {ux} | {amb} |")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_build(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    glossary = build_glossary(project_root)
    if args.json:
        print(json.dumps({
            "entries": {t: {"domains": e.domains, "frequency": e.frequency,
                            "ambiguous": e.is_ambiguous, "sources": e.sources}
                        for t, e in glossary.entries.items()},
            "ambiguities": glossary.ambiguities,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_glossary(glossary))
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    glossary = build_glossary(project_root)
    term = args.term.lower()
    entry = glossary.entries.get(term)
    # Fuzzy match si pas exact
    if not entry:
        for key in glossary.entries:
            if term in key or key in term:
                entry = glossary.entries[key]
                break
    if args.json and entry:
        print(json.dumps({"term": entry.term, "domains": entry.domains,
                          "frequency": entry.frequency, "ambiguous": entry.is_ambiguous},
                         indent=2, ensure_ascii=False))
    else:
        print(format_lookup(entry, args.term))
    return 0


def cmd_ambiguity(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    glossary = build_glossary(project_root)
    ambiguous = [glossary.entries[t] for t in glossary.ambiguities if t in glossary.entries]
    if args.json:
        print(json.dumps([{"term": e.term, "domains": e.domains} for e in ambiguous],
                         indent=2, ensure_ascii=False))
    else:
        if ambiguous:
            print(f"⚠️ {len(ambiguous)} termes ambigus détectés :\n")
            for entry in ambiguous:
                print(f"   {entry.term}:")
                for d, ctx in entry.domains.items():
                    print(f"      [{d}] {ctx[:80]}")
                print()
        else:
            print("✅ Aucune ambiguïté détectée")
    return 0


def cmd_etymology(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    record = trace_etymology(project_root, args.term)
    if args.json:
        print(json.dumps({
            "term": record.term, "first_seen": record.first_seen,
            "first_source": record.first_source, "rationale": record.rationale,
            "evolution": record.evolution,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_etymology(record))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    glossary = build_glossary(project_root)
    md = export_markdown(glossary)
    output = project_root / "_bmad-output" / "glossary-rosetta.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    print(f"📖 Glossaire exporté → {output}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Rosetta Stone — Glossaire cross-domain",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("build", help="Construire le glossaire")
    p.set_defaults(func=cmd_build)

    p = subs.add_parser("lookup", help="Chercher un terme")
    p.add_argument("--term", type=str, required=True, help="Terme à chercher")
    p.set_defaults(func=cmd_lookup)

    p = subs.add_parser("ambiguity", help="Détecter les termes ambigus")
    p.set_defaults(func=cmd_ambiguity)

    p = subs.add_parser("etymology", help="Étymologie d'une décision")
    p.add_argument("--term", type=str, required=True, help="Terme à tracer")
    p.set_defaults(func=cmd_etymology)

    p = subs.add_parser("export", help="Exporter en Markdown")
    p.set_defaults(func=cmd_export)

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
