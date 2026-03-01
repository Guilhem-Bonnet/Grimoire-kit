#!/usr/bin/env python3
"""
confidence-scores.py — Framework de scoring de confiance BMAD.
================================================================

Les "signaux silencieux" : chaque output agent peut être accompagné
d'un score de confiance transparent. Quand un agent dit quelque chose,
à quel point est-il sûr ?

Dimensions de confiance :
  1. Evidence    — nombre de sources qui soutiennent l'affirmation
  2. Consensus   — accord entre agents (si multi-agent)
  3. Freshness   — fraîcheur des données sous-jacentes
  4. Completeness — couverture du sujet
  5. Precedent   — existance de cas similaires réussis

Score final : 0-100 avec label qualificatif :
  95-100  🟢 Très haute confiance
  80-94   🟢 Haute confiance
  60-79   🟡 Confiance modérée
  40-59   🟡 Confiance basse
  20-39   🟠 Très basse confiance
  0-19    🔴 Spéculatif

Usage :
  python3 confidence-scores.py --project-root . score --text "Architecture micro-services recommandée" --sources 3 --agent architect
  python3 confidence-scores.py --project-root . score --file decisions/ADR-001.md
  python3 confidence-scores.py --project-root . audit --dir _bmad/_memory
  python3 confidence-scores.py --project-root . calibrate --agent dev
  python3 confidence-scores.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

CONFIDENCE_VERSION = "1.0.0"

# Labels de confiance
LABELS = [
    (95, "🟢 Très haute confiance"),
    (80, "🟢 Haute confiance"),
    (60, "🟡 Confiance modérée"),
    (40, "🟡 Confiance basse"),
    (20, "🟠 Très basse confiance"),
    (0, "🔴 Spéculatif"),
]

# Poids des dimensions (total = 1.0)
WEIGHTS = {
    "evidence": 0.30,
    "consensus": 0.15,
    "freshness": 0.20,
    "completeness": 0.20,
    "precedent": 0.15,
}

# Marqueurs de confiance dans le texte
HIGH_CONFIDENCE_MARKERS = [
    "testé", "validé", "confirmé", "prouvé", "vérifié",
    "tested", "validated", "confirmed", "proven", "verified",
    "d'après", "selon", "based on", "evidence", "data shows",
]
LOW_CONFIDENCE_MARKERS = [
    "peut-être", "probablement", "je pense", "il semble",
    "maybe", "probably", "I think", "it seems", "might",
    "hypothèse", "hypothesis", "speculation", "gut feeling",
    "à confirmer", "to confirm", "unclear", "incertain",
]
HEDGE_WORDS = [
    "pourrait", "devrait", "semble", "apparemment",
    "could", "should", "seems", "apparently", "likely",
]

# Freshness decay
FRESHNESS_HALF_LIFE_DAYS = 60


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ConfidenceDimension:
    """Score d'une dimension individuelle."""
    name: str
    score: float      # 0.0 - 1.0
    weight: float
    detail: str = ""

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass
class ConfidenceScore:
    """Score de confiance complet."""
    text: str = ""
    source: str = ""
    agent: str = ""
    dimensions: list[ConfidenceDimension] = field(default_factory=list)
    raw_score: float = 0.0
    final_score: int = 0     # 0-100
    label: str = ""

    def compute(self):
        self.raw_score = sum(d.weighted for d in self.dimensions)
        self.final_score = max(0, min(100, round(self.raw_score * 100)))
        self.label = self._get_label()

    def _get_label(self) -> str:
        for threshold, lbl in LABELS:
            if self.final_score >= threshold:
                return lbl
        return LABELS[-1][1]

    def to_dict(self) -> dict:
        return {
            "text": self.text[:200],
            "source": self.source,
            "agent": self.agent,
            "final_score": self.final_score,
            "label": self.label,
            "dimensions": [
                {
                    "name": d.name,
                    "score": round(d.score, 3),
                    "weight": d.weight,
                    "weighted": round(d.weighted, 3),
                    "detail": d.detail,
                }
                for d in self.dimensions
            ],
        }


@dataclass
class AuditResult:
    """Résultat d'audit de confiance sur un ensemble de fichiers."""
    files_scanned: int = 0
    scores: list[ConfidenceScore] = field(default_factory=list)
    avg_score: float = 0.0
    min_score: int = 100
    max_score: int = 0
    low_confidence_items: list[ConfidenceScore] = field(default_factory=list)


# ── Dimension Calculators ────────────────────────────────────────────────────

def calc_evidence(text: str, explicit_sources: int = 0) -> ConfidenceDimension:
    """Score de preuves — combien de sources soutiennent l'affirmation."""
    score = 0.0

    # Sources explicites
    if explicit_sources > 0:
        score = min(1.0, explicit_sources / 5)  # 5 sources = 100%
    else:
        # Heuristique textuelle
        # Références (liens, citations, numéros de tickets)
        refs = len(re.findall(r'(?:https?://|#\d+|\[.*?\]\(.*?\)|cf\.|voir|see)', text, re.I))
        score += min(0.4, refs * 0.15)

        # Marqueurs haute confiance
        for marker in HIGH_CONFIDENCE_MARKERS:
            if marker.lower() in text.lower():
                score += 0.1
        # Marqueurs basse confiance
        for marker in LOW_CONFIDENCE_MARKERS:
            if marker.lower() in text.lower():
                score -= 0.1

    return ConfidenceDimension(
        name="evidence",
        score=max(0.0, min(1.0, score)),
        weight=WEIGHTS["evidence"],
        detail=f"{explicit_sources} sources explicites" if explicit_sources else "analyse textuelle",
    )


def calc_consensus(text: str, agent_count: int = 1) -> ConfidenceDimension:
    """Score de consensus — accord multi-agent."""
    if agent_count <= 1:
        # Pas de consensus possible — neutre
        return ConfidenceDimension(
            name="consensus",
            score=0.5,
            weight=WEIGHTS["consensus"],
            detail="agent unique — consensus N/A",
        )

    # Plus d'agents = plus de confiance (diminishing returns)
    score = min(1.0, 0.3 + 0.7 * (1 - math.exp(-agent_count / 3)))

    return ConfidenceDimension(
        name="consensus",
        score=score,
        weight=WEIGHTS["consensus"],
        detail=f"{agent_count} agents impliqués",
    )


def calc_freshness(text: str, file_path: str = "") -> ConfidenceDimension:
    """Score de fraîcheur — âge des données."""
    age_days = 90.0  # Défaut si pas de date

    # Chercher une date dans le texte
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if date_match:
        try:
            dt = datetime.fromisoformat(date_match.group(1))
            age_days = max(0.1, (datetime.now() - dt).days)
        except ValueError:
            pass
    elif file_path:
        # Fallback : mtime du fichier
        try:
            fpath = Path(file_path)
            if fpath.exists():
                age_days = max(0.1, (datetime.now().timestamp() - fpath.stat().st_mtime) / 86400)
        except OSError:
            pass

    # Décroissance exponentielle
    score = math.pow(0.5, age_days / FRESHNESS_HALF_LIFE_DAYS)

    return ConfidenceDimension(
        name="freshness",
        score=max(0.0, min(1.0, score)),
        weight=WEIGHTS["freshness"],
        detail=f"~{age_days:.0f} jour(s) d'ancienneté",
    )


def calc_completeness(text: str) -> ConfidenceDimension:
    """Score de complétude — couverture du sujet."""
    score = 0.5  # Base

    # Longueur du texte (plus = plus complet, avec diminishing returns)
    word_count = len(text.split())
    length_score = min(0.3, word_count / 500 * 0.3)
    score += length_score

    # Structure (sections, listes)
    has_headers = bool(re.search(r'^#{1,3}\s', text, re.M))
    has_lists = bool(re.search(r'^[-*]\s', text, re.M))
    has_code = bool(re.search(r'```', text))
    structure_score = (has_headers * 0.05 + has_lists * 0.05 + has_code * 0.05)
    score += structure_score

    # Pénalité pour TODOs / lacunes
    todos = text.lower().count("todo") + text.lower().count("tbd") + text.lower().count("à compléter")
    score -= todos * 0.05

    # Hedge words réduisent la complétude perçue
    hedges = sum(1 for hw in HEDGE_WORDS if hw.lower() in text.lower())
    score -= hedges * 0.03

    return ConfidenceDimension(
        name="completeness",
        score=max(0.0, min(1.0, score)),
        weight=WEIGHTS["completeness"],
        detail=f"{word_count} mots, {todos} TODO(s)",
    )


def calc_precedent(text: str, project_root: Path | None = None) -> ConfidenceDimension:
    """Score de précédent — existence de cas similaires."""
    score = 0.5  # Base neutre

    # Marqueurs de précédent dans le texte
    precedent_markers = [
        "précédent", "precedent", "déjà fait", "already done",
        "pattern établi", "established pattern", "best practice",
        "convention", "standard", "comme", "similar to",
    ]
    for marker in precedent_markers:
        if marker.lower() in text.lower():
            score += 0.1

    # Chercher des références à des décisions passées
    decision_refs = len(re.findall(r'ADR[-_]\d+|DECISION[-_]', text, re.I))
    score += min(0.2, decision_refs * 0.1)

    return ConfidenceDimension(
        name="precedent",
        score=max(0.0, min(1.0, score)),
        weight=WEIGHTS["precedent"],
        detail=f"{decision_refs} référence(s) à des décisions passées",
    )


# ── Scoring Engine ───────────────────────────────────────────────────────────

def compute_confidence(
    text: str,
    agent: str = "",
    sources: int = 0,
    agents_involved: int = 1,
    file_path: str = "",
    project_root: Path | None = None,
) -> ConfidenceScore:
    """Calcule le score de confiance complet."""
    cs = ConfidenceScore(
        text=text[:200],
        source=file_path,
        agent=agent,
    )

    cs.dimensions = [
        calc_evidence(text, explicit_sources=sources),
        calc_consensus(text, agent_count=agents_involved),
        calc_freshness(text, file_path=file_path),
        calc_completeness(text),
        calc_precedent(text, project_root=project_root),
    ]

    cs.compute()
    return cs


def audit_directory(project_root: Path, directory: str) -> AuditResult:
    """Audit de confiance sur un répertoire entier."""
    result = AuditResult()
    target = project_root / directory

    if not target.exists():
        return result

    for md_file in target.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            if len(content.strip()) < 50:
                continue

            # Découper en sections
            sections = re.split(r'\n(?=#{1,3}\s)', content)
            for section in sections:
                if len(section.strip()) < 30:
                    continue

                cs = compute_confidence(
                    text=section,
                    file_path=str(md_file),
                    project_root=project_root,
                )
                result.scores.append(cs)
                result.min_score = min(result.min_score, cs.final_score)
                result.max_score = max(result.max_score, cs.final_score)
                if cs.final_score < 40:
                    result.low_confidence_items.append(cs)

            result.files_scanned += 1
        except OSError:
            pass

    if result.scores:
        result.avg_score = sum(s.final_score for s in result.scores) / len(result.scores)

    return result


# ── Formatters ───────────────────────────────────────────────────────────────

def format_score(cs: ConfidenceScore) -> str:
    """Formatage texte d'un score."""
    lines = [
        f"📊 Confidence Score : {cs.final_score}/100  {cs.label}",
    ]
    if cs.agent:
        lines.append(f"   Agent : {cs.agent}")
    if cs.source:
        lines.append(f"   Source : {cs.source}")
    lines.append(f"   Texte : {cs.text[:100]}...")
    lines.append("")
    lines.append("   Dimensions :")
    for d in cs.dimensions:
        bar = "█" * int(d.score * 10) + "░" * (10 - int(d.score * 10))
        lines.append(f"      {d.name:14s} {bar} {d.score:.0%}  (×{d.weight:.0%}) → {d.weighted:.0%}")
        if d.detail:
            lines.append(f"                    {d.detail}")
    lines.append("")
    return "\n".join(lines)


def format_audit(result: AuditResult) -> str:
    lines = [
        "🔍 Audit de Confiance",
        f"   Fichiers scannés : {result.files_scanned}",
        f"   Sections analysées : {len(result.scores)}",
        f"   Score moyen : {result.avg_score:.0f}/100",
        f"   Min : {result.min_score}  Max : {result.max_score}",
        "",
    ]

    if result.low_confidence_items:
        lines.append(f"   ⚠️ {len(result.low_confidence_items)} section(s) à basse confiance (<40) :")
        for cs in result.low_confidence_items[:10]:
            lines.append(f"      [{cs.final_score}] {cs.source} — {cs.text[:60]}...")
        lines.append("")

    # Distribution
    bins = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for cs in result.scores:
        if cs.final_score < 20:
            bins["0-19"] += 1
        elif cs.final_score < 40:
            bins["20-39"] += 1
        elif cs.final_score < 60:
            bins["40-59"] += 1
        elif cs.final_score < 80:
            bins["60-79"] += 1
        else:
            bins["80-100"] += 1

    lines.append("   Distribution :")
    total = len(result.scores) or 1
    for label, count in bins.items():
        bar = "█" * int(count / total * 30)
        lines.append(f"      {label:8s} {bar} {count}")
    lines.append("")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_score(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()

    if args.file:
        fpath = project_root / args.file
        if not fpath.exists():
            print(f"❌ Fichier non trouvé : {args.file}")
            return 1
        text = fpath.read_text(encoding="utf-8")
        file_path = str(fpath)
    elif args.text:
        text = args.text
        file_path = ""
    else:
        print("❌ --text ou --file requis")
        return 1

    cs = compute_confidence(
        text=text,
        agent=args.agent or "",
        sources=args.sources or 0,
        file_path=file_path,
        project_root=project_root,
    )

    if args.json:
        print(json.dumps(cs.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_score(cs))

    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    result = audit_directory(project_root, args.dir)

    if args.json:
        print(json.dumps({
            "files_scanned": result.files_scanned,
            "sections": len(result.scores),
            "avg_score": round(result.avg_score, 1),
            "min_score": result.min_score,
            "max_score": result.max_score,
            "low_confidence_count": len(result.low_confidence_items),
            "low_confidence": [cs.to_dict() for cs in result.low_confidence_items[:20]],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_audit(result))

    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Calibre les seuils de confiance pour un agent."""
    project_root = Path(args.project_root).resolve()
    agent = args.agent

    # Analyser les outputs passés de cet agent
    memory_dir = project_root / "_bmad" / "_memory"
    agent_files = list(memory_dir.rglob(f"*{agent}*")) if memory_dir.exists() else []

    scores = []
    for fpath in agent_files:
        if fpath.is_file() and fpath.suffix == ".md":
            try:
                content = fpath.read_text(encoding="utf-8")
                cs = compute_confidence(text=content, agent=agent, file_path=str(fpath),
                                        project_root=project_root)
                scores.append(cs.final_score)
            except OSError:
                pass

    if not scores:
        print(f"⚠️ Aucun output trouvé pour l'agent '{agent}'")
        return 1

    avg = sum(scores) / len(scores)
    mn, mx = min(scores), max(scores)

    print(f"📐 Calibration — Agent '{agent}'")
    print(f"   Fichiers analysés : {len(scores)}")
    print(f"   Score moyen : {avg:.0f}/100")
    print(f"   Plage : {mn}-{mx}")
    print()

    if avg < 50:
        print("   💡 Recommandation : enrichir les sources et références dans les outputs")
    elif avg > 80:
        print("   ✅ Agent bien calibré — confiance élevée dans ses outputs")
    else:
        print("   🟡 Confiance modérée — ajouter plus de preuves et références")

    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Confidence Scores — Scoring de confiance des outputs agents",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("score", help="Scorer un texte ou fichier")
    p.add_argument("--text", type=str, help="Texte à scorer")
    p.add_argument("--file", type=str, help="Fichier à scorer")
    p.add_argument("--agent", type=str, help="Agent source")
    p.add_argument("--sources", type=int, help="Nombre de sources explicites")
    p.set_defaults(func=cmd_score)

    p = subs.add_parser("audit", help="Audit de confiance sur un répertoire")
    p.add_argument("--dir", type=str, required=True, help="Répertoire à auditer")
    p.set_defaults(func=cmd_audit)

    p = subs.add_parser("calibrate", help="Calibrer les seuils pour un agent")
    p.add_argument("--agent", type=str, required=True, help="Agent à calibrer")
    p.set_defaults(func=cmd_calibrate)

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
