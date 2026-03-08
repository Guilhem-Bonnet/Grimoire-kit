#!/usr/bin/env python3
"""
semantic-chain.py — Chaîne du froid sémantique Grimoire.
======================================================

Garantit que l'information ne se dégrade jamais au fil de la chaîne :
  PRD → Architecture → Stories → Code → Tests → Docs

Détecte le "drift sémantique" : quand un concept change de sens ou
disparaît silencieusement entre les artefacts.

Features :
  1. `extract`  — Extraction des concepts-clés d'un artefact
  2. `trace`    — Traçabilité d'un concept à travers les artefacts
  3. `drift`    — Détection de drift sémantique entre 2 artefacts
  4. `chain`    — Rapport de chaîne complète PRD→Code
  5. `impact`   — Propagation d'impact d'un changement

Usage :
  python3 semantic-chain.py --project-root . extract --file PRD.md
  python3 semantic-chain.py --project-root . trace --concept "auth"
  python3 semantic-chain.py --project-root . drift --from PRD.md --to STORY-1.md
  python3 semantic-chain.py --project-root . chain
  python3 semantic-chain.py --project-root . impact --file arch.md --concept "auth"
  python3 semantic-chain.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.semantic_chain")

# ── Constantes ────────────────────────────────────────────────────────────────

SEMANTIC_CHAIN_VERSION = "1.0.0"

# Types d'artefacts dans l'ordre de la chaîne
CHAIN_ORDER = ["prd", "architecture", "story", "code", "test", "doc"]

# Globs par type d'artefact
ARTIFACT_GLOBS = {
    "prd": ["**/PRD*.md", "**/prd*.md"],
    "architecture": ["**/ARCH*.md", "**/arch*.md", "**/architecture*.md"],
    "story": ["**/STORY*.md", "**/story*.md", "**/stories/**/*.md"],
    "code": ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.yaml"],
    "test": ["**/test_*.py", "**/*_test.py", "**/*.test.ts", "**/*.test.js"],
    "doc": ["**/docs/**/*.md", "**/README.md"],
}

# Seuils
DRIFT_THRESHOLD_WARN = 0.30    # >30% de concepts manquants = warning
DRIFT_THRESHOLD_ALERT = 0.50   # >50% = alert
MIN_CONCEPT_LENGTH = 3
MAX_CONCEPTS = 100

# Stop words (FR + EN)
STOP_WORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "his", "how", "its", "may",
    "les", "des", "une", "que", "est", "sur", "par", "pour", "dans", "avec",
    "sont", "pas", "plus", "qui", "aux", "ces", "cet", "cette", "nous",
    "vous", "leur", "être", "avoir", "faire", "dire", "tout", "bien",
    "from", "this", "that", "have", "will", "been", "each", "make",
    "like", "them", "then", "than", "into", "over", "such", "when",
    "very", "some", "would", "there", "what", "which", "their", "other",
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Concept:
    """Un concept extrait d'un artefact."""
    term: str
    frequency: int = 1
    context: str = ""       # Phrase/section d'où il vient
    source: str = ""        # Fichier source

    @property
    def weight(self) -> float:
        """Poids basé sur la fréquence (log scale)."""
        return 1.0 + math.log2(self.frequency) if self.frequency > 1 else 1.0


@dataclass
class DriftResult:
    """Résultat de détection de drift entre 2 artefacts."""
    source: str
    target: str
    source_concepts: int = 0
    target_concepts: int = 0
    shared: int = 0
    missing_in_target: list[str] = field(default_factory=list)
    new_in_target: list[str] = field(default_factory=list)
    drift_score: float = 0.0   # 0.0 = identique, 1.0 = complètement divergé
    level: str = "🟢 OK"

    def compute_drift(self):
        if self.source_concepts == 0:
            self.drift_score = 0.0
        else:
            self.drift_score = len(self.missing_in_target) / self.source_concepts
        if self.drift_score >= DRIFT_THRESHOLD_ALERT:
            self.level = "🔴 ALERT"
        elif self.drift_score >= DRIFT_THRESHOLD_WARN:
            self.level = "🟡 WARNING"
        else:
            self.level = "🟢 OK"


@dataclass
class ImpactNode:
    """Noeud dans le graphe d'impact."""
    file: str
    artifact_type: str
    concepts_affected: list[str] = field(default_factory=list)
    impact_score: float = 0.0


@dataclass
class ChainReport:
    """Rapport de chaîne sémantique complète."""
    drifts: list[DriftResult] = field(default_factory=list)
    total_concepts: int = 0
    chain_integrity: float = 0.0   # 0.0-1.0
    weakest_link: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── Concept Extraction ───────────────────────────────────────────────────────

def extract_concepts(text: str, source: str = "") -> list[Concept]:
    """Extrait les concepts-clés d'un texte."""
    # Tokeniser
    words = re.findall(r'[a-zA-ZÀ-ÿ_-]{3,}', text.lower())
    # Filtrer stop words
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) >= MIN_CONCEPT_LENGTH]

    # Compter les fréquences
    freq = Counter(meaningful)

    # Bigrams significatifs
    bigrams = []
    for i in range(len(meaningful) - 1):
        bg = f"{meaningful[i]}_{meaningful[i+1]}"
        bigrams.append(bg)
    bg_freq = Counter(bigrams)

    concepts = []

    # Unigrams les plus fréquents
    for term, count in freq.most_common(MAX_CONCEPTS):
        if count >= 2 or len(term) >= 6:  # Fréquent ou long
            # Trouver le contexte (première occurrence dans le texte)
            ctx_match = re.search(
                rf'[^.]*\b{re.escape(term)}\b[^.]*', text, re.IGNORECASE
            )
            ctx = ctx_match.group(0).strip()[:120] if ctx_match else ""
            concepts.append(Concept(
                term=term, frequency=count, context=ctx, source=source,
            ))

    # Bigrams fréquents
    for bg, count in bg_freq.most_common(20):
        if count >= 2:
            concepts.append(Concept(
                term=bg, frequency=count, source=source,
            ))

    return concepts[:MAX_CONCEPTS]


def extract_concepts_from_file(fpath: Path) -> list[Concept]:
    """Extrait les concepts d'un fichier."""
    try:
        content = fpath.read_text(encoding="utf-8")
        return extract_concepts(content, source=str(fpath))
    except (OSError, UnicodeDecodeError):
        return []


# ── Drift Detection ────────────────────────────────────────────────────────

def detect_drift(source_concepts: list[Concept], target_concepts: list[Concept],
                 source_label: str = "", target_label: str = "") -> DriftResult:
    """Détecte le drift entre deux ensembles de concepts."""
    src_terms = {c.term for c in source_concepts}
    tgt_terms = {c.term for c in target_concepts}

    shared = src_terms & tgt_terms
    missing = sorted(src_terms - tgt_terms)
    new_terms = sorted(tgt_terms - src_terms)

    result = DriftResult(
        source=source_label,
        target=target_label,
        source_concepts=len(src_terms),
        target_concepts=len(tgt_terms),
        shared=len(shared),
        missing_in_target=missing[:20],
        new_in_target=new_terms[:20],
    )
    result.compute_drift()
    return result


# ── Artifact Discovery ──────────────────────────────────────────────────────

def discover_artifacts(project_root: Path) -> dict[str, list[Path]]:
    """Découvre les artefacts par type."""
    found: dict[str, list[Path]] = defaultdict(list)

    # Chercher dans _grimoire-output d'abord
    output_dir = project_root / "_grimoire-output"
    if output_dir.exists():
        for md in output_dir.rglob("*.md"):
            name = md.name.upper()
            if "PRD" in name:
                found["prd"].append(md)
            elif "STORY" in name:
                found["story"].append(md)
            elif "ARCH" in name:
                found["architecture"].append(md)

    # Docs
    docs_dir = project_root / "docs"
    if docs_dir.exists():
        for md in docs_dir.rglob("*.md"):
            found["doc"].append(md)

    # Tests
    tests_dir = project_root / "tests"
    if tests_dir.exists():
        for f in tests_dir.rglob("test_*"):
            found["test"].append(f)

    # Code source (heuristique : src/ ou framework/)
    for code_dir_name in ["src", "framework", "lib", "app"]:
        code_dir = project_root / code_dir_name
        if code_dir.exists():
            for f in code_dir.rglob("*.py"):
                if "test" not in f.name.lower():
                    found["code"].append(f)
            for f in code_dir.rglob("*.ts"):
                if "test" not in f.name.lower():
                    found["code"].append(f)

    return dict(found)


# ── Impact Analysis ─────────────────────────────────────────────────────────

def analyze_impact(project_root: Path, target_file: str, concept: str) -> list[ImpactNode]:
    """Analyse la propagation d'impact d'un changement de concept."""
    nodes = []
    artifacts = discover_artifacts(project_root)
    concept_lower = concept.lower()

    for atype, files in artifacts.items():
        for fpath in files:
            try:
                content = fpath.read_text(encoding="utf-8").lower()
                count = content.count(concept_lower)
                if count > 0:
                    nodes.append(ImpactNode(
                        file=str(fpath.relative_to(project_root)),
                        artifact_type=atype,
                        concepts_affected=[concept],
                        impact_score=min(1.0, count / 10),
                    ))
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    # Trier par impact décroissant
    nodes.sort(key=lambda n: n.impact_score, reverse=True)
    return nodes


# ── Chain Analysis ──────────────────────────────────────────────────────────

def analyze_chain(project_root: Path) -> ChainReport:
    """Analyse la chaîne sémantique complète."""
    report = ChainReport()
    artifacts = discover_artifacts(project_root)

    # Extraire concepts par type
    concepts_by_type: dict[str, list[Concept]] = {}
    for atype in CHAIN_ORDER:
        if atype in artifacts:
            all_concepts = []
            for fpath in artifacts[atype][:10]:  # Cap à 10 fichiers par type
                all_concepts.extend(extract_concepts_from_file(fpath))
            concepts_by_type[atype] = all_concepts

    # Comparer les maillons adjacents
    types_with_data = [t for t in CHAIN_ORDER if t in concepts_by_type and concepts_by_type[t]]

    for i in range(len(types_with_data) - 1):
        src_type = types_with_data[i]
        tgt_type = types_with_data[i + 1]
        drift = detect_drift(
            concepts_by_type[src_type],
            concepts_by_type[tgt_type],
            source_label=src_type,
            target_label=tgt_type,
        )
        report.drifts.append(drift)

    # Calculer l'intégrité globale
    if report.drifts:
        avg_drift = sum(d.drift_score for d in report.drifts) / len(report.drifts)
        report.chain_integrity = 1.0 - avg_drift
        worst = max(report.drifts, key=lambda d: d.drift_score)
        report.weakest_link = f"{worst.source}→{worst.target}"
    else:
        report.chain_integrity = 1.0

    report.total_concepts = sum(len(c) for c in concepts_by_type.values())
    return report


# ── Formatters ───────────────────────────────────────────────────────────────

def format_concepts(concepts: list[Concept], source: str = "") -> str:
    lines = [f"📝 Concepts extraits{f' de {source}' if source else ''}",
             f"   Total : {len(concepts)}", ""]
    for c in concepts[:30]:
        freq_bar = "█" * min(c.frequency, 20)
        lines.append(f"   {c.term:30s} ×{c.frequency:3d} {freq_bar}")
        if c.context:
            lines.append(f"      → {c.context[:80]}")
    if len(concepts) > 30:
        lines.append(f"   ... et {len(concepts) - 30} de plus")
    return "\n".join(lines)


def format_drift(drift: DriftResult) -> str:
    lines = [
        f"🔍 Drift : {drift.source} → {drift.target}",
        f"   {drift.level}  Score : {drift.drift_score:.0%}",
        f"   Concepts source : {drift.source_concepts}  |  cible : {drift.target_concepts}  |  partagés : {drift.shared}",
    ]
    if drift.missing_in_target:
        lines.append(f"   ❌ Manquants ({len(drift.missing_in_target)}) : {', '.join(drift.missing_in_target[:10])}")
    if drift.new_in_target:
        lines.append(f"   ✨ Nouveaux ({len(drift.new_in_target)}) : {', '.join(drift.new_in_target[:10])}")
    return "\n".join(lines)


def format_chain(report: ChainReport) -> str:
    lines = [
        "🧊 Chaîne du Froid Sémantique — Rapport",
        f"   Intégrité : {report.chain_integrity:.0%}",
        f"   Concepts totaux : {report.total_concepts}",
        f"   Maillons analysés : {len(report.drifts)}",
    ]
    if report.weakest_link:
        lines.append(f"   Maillon faible : {report.weakest_link}")
    lines.append("")

    for drift in report.drifts:
        lines.append(format_drift(drift))
        lines.append("")

    return "\n".join(lines)


def format_impact(nodes: list[ImpactNode], concept: str) -> str:
    lines = [
        f"💥 Impact du concept '{concept}'",
        f"   Fichiers affectés : {len(nodes)}",
        "",
    ]
    for node in nodes[:15]:
        bar = "█" * int(node.impact_score * 10)
        lines.append(f"   {node.impact_score:.0%} {bar} [{node.artifact_type}] {node.file}")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_extract(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    fpath = project_root / args.file
    if not fpath.exists():
        print(f"❌ Fichier non trouvé : {args.file}")
        return 1
    concepts = extract_concepts_from_file(fpath)
    if args.json:
        print(json.dumps([{"term": c.term, "freq": c.frequency, "ctx": c.context} for c in concepts],
                         indent=2, ensure_ascii=False))
    else:
        print(format_concepts(concepts, source=args.file))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    concept = args.concept.lower()
    artifacts = discover_artifacts(project_root)

    print(f"🔎 Traçabilité du concept '{args.concept}'\n")
    found_in = []
    for atype in CHAIN_ORDER:
        if atype not in artifacts:
            continue
        for fpath in artifacts[atype]:
            try:
                content = fpath.read_text(encoding="utf-8").lower()
                count = content.count(concept)
                if count > 0:
                    rel = str(fpath.relative_to(project_root))
                    found_in.append((atype, rel, count))
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    if args.json:
        print(json.dumps([{"type": t, "file": f, "count": c} for t, f, c in found_in],
                         indent=2, ensure_ascii=False))
    else:
        if found_in:
            for atype, fname, count in found_in:
                print(f"   [{atype:12s}] {fname} (×{count})")
        else:
            print(f"   ⚠️ Concept '{args.concept}' non trouvé dans la chaîne")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    src = project_root / getattr(args, "from")
    tgt = project_root / args.to

    if not src.exists():
        print(f"❌ Source non trouvée : {getattr(args, 'from')}")
        return 1
    if not tgt.exists():
        print(f"❌ Cible non trouvée : {args.to}")
        return 1

    src_concepts = extract_concepts_from_file(src)
    tgt_concepts = extract_concepts_from_file(tgt)
    drift = detect_drift(src_concepts, tgt_concepts,
                         source_label=getattr(args, "from"), target_label=args.to)

    if args.json:
        print(json.dumps({
            "source": drift.source, "target": drift.target,
            "drift_score": round(drift.drift_score, 3), "level": drift.level,
            "shared": drift.shared, "missing": drift.missing_in_target,
            "new": drift.new_in_target,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_drift(drift))
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = analyze_chain(project_root)
    if args.json:
        print(json.dumps({
            "integrity": round(report.chain_integrity, 3),
            "total_concepts": report.total_concepts,
            "weakest_link": report.weakest_link,
            "drifts": [{
                "source": d.source, "target": d.target,
                "drift_score": round(d.drift_score, 3), "level": d.level,
                "shared": d.shared, "missing": d.missing_in_target[:10],
            } for d in report.drifts],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_chain(report))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    nodes = analyze_impact(project_root, args.file, args.concept)
    if args.json:
        print(json.dumps([{
            "file": n.file, "type": n.artifact_type,
            "impact": round(n.impact_score, 3),
        } for n in nodes], indent=2, ensure_ascii=False))
    else:
        print(format_impact(nodes, args.concept))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Semantic Chain — Chaîne du froid sémantique",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("extract", help="Extraire les concepts d'un artefact")
    p.add_argument("--file", type=str, required=True, help="Fichier à analyser")
    p.set_defaults(func=cmd_extract)

    p = subs.add_parser("trace", help="Tracer un concept à travers la chaîne")
    p.add_argument("--concept", type=str, required=True, help="Concept à tracer")
    p.set_defaults(func=cmd_trace)

    p = subs.add_parser("drift", help="Détecter le drift entre 2 artefacts")
    p.add_argument("--from", type=str, required=True, help="Artefact source")
    p.add_argument("--to", type=str, required=True, help="Artefact cible")
    p.set_defaults(func=cmd_drift)

    p = subs.add_parser("chain", help="Rapport de chaîne complète")
    p.set_defaults(func=cmd_chain)

    p = subs.add_parser("impact", help="Analyse de propagation d'impact")
    p.add_argument("--file", type=str, required=True, help="Fichier modifié")
    p.add_argument("--concept", type=str, required=True, help="Concept changé")
    p.set_defaults(func=cmd_impact)

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
