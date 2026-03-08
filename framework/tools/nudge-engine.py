#!/usr/bin/env python3
"""
nudge-engine.py — Moteur de suggestions contextuelles Grimoire.
=============================================================

Suggestions proactives intelligentes pour les agents :
  - Analyse failure-museum, learnings, decisions, dream-journal
  - Détecte les patterns récurrents (erreurs, succès, opportunités)
  - Génère des "nudges" contextuels : rappels, warnings, suggestions
  - Sérendipité programmée : connexions cross-module inattendues

3 modes :
  1. `suggest`   — suggestions pertinentes pour un agent/contexte donné
  2. `serendip`  — connexions surprenantes entre domaines
  3. `recall`    — rappels depuis failure-museum pour éviter les erreurs

Usage :
  python3 nudge-engine.py --project-root . suggest --agent dev
  python3 nudge-engine.py --project-root . suggest --agent dev --context "auth module"
  python3 nudge-engine.py --project-root . serendip
  python3 nudge-engine.py --project-root . serendip --domains "security,ux"
  python3 nudge-engine.py --project-root . recall --query "deployment"
  python3 nudge-engine.py --project-root . recall --agent dev --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

NUDGE_VERSION = "1.0.0"

# Sources de données
MEMORY_DIRS = ["_grimoire/_memory"]
LEARNINGS_GLOB = "**/learnings*.md"
DECISIONS_GLOB = "**/decisions*.md"
FAILURE_GLOB = "**/failure-museum*.md"
DREAM_GLOB = "**/dream-journal*.md"
SHARED_GLOB = "**/shared-context*.md"

# Nudge types
class NudgeType:
    REMINDER = "💡 REMINDER"      # Rappel d'une leçon apprise
    WARNING = "⚠️ WARNING"        # Risque basé sur un échec passé
    OPPORTUNITY = "🌟 OPPORTUNITY" # Connexion cross-module détectée
    PATTERN = "🔄 PATTERN"        # Pattern récurrent identifié
    SERENDIP = "✨ SERENDIPITY"   # Connexion surprenante

# Scoring
MAX_NUDGES = 10
RELEVANCE_THRESHOLD = 0.3
RECENCY_HALF_LIFE_DAYS = 30
FREQUENCY_BOOST = 0.15  # Par occurrence supplémentaire


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """Entrée mémoire brute (learning, failure, decision)."""
    source: str        # fichier source
    kind: str          # learning | failure | decision | dream | shared
    text: str          # contenu
    agent: str = ""    # agent concerné
    date: str = ""     # date si trouvée
    tags: list[str] = field(default_factory=list)

    def age_days(self) -> float:
        """Âge en jours — fallback gros si pas de date."""
        if not self.date:
            return 90.0
        try:
            dt = datetime.fromisoformat(self.date[:10])
            return max(0.1, (datetime.now() - dt).days)
        except (ValueError, TypeError):
            return 90.0


@dataclass
class Nudge:
    """Suggestion générée."""
    nudge_type: str
    title: str
    message: str
    relevance: float = 0.0   # 0.0 - 1.0
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class NudgeReport:
    """Rapport de nudges."""
    mode: str
    agent: str = ""
    context: str = ""
    nudges: list[Nudge] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_entries_scanned: int = 0


# ── Parsing mémoire ─────────────────────────────────────────────────────────

def _extract_date(text: str) -> str:
    """Extrait une date ISO depuis du texte."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    return m.group(1) if m else ""


def _extract_tags(text: str) -> list[str]:
    """Extrait les tags (#mot, [TAG], etc)."""
    tags = re.findall(r'#(\w+)', text)
    tags += re.findall(r'\[([A-Z_]+)\]', text)
    return [t.lower() for t in tags]


def _extract_agent(text: str, source: str) -> str:
    """Extrait l'agent concerné."""
    # Depuis le nom de fichier (learnings-dev.md → dev)
    m = re.search(r'(?:learnings|decisions|failure)[-_](\w+)', source)
    if m:
        return m.group(1)
    # Depuis le contenu
    m = re.search(r'agent:\s*(\w+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def parse_markdown_entries(filepath: Path, kind: str) -> list[MemoryEntry]:
    """Parse un fichier markdown en entrées individuelles."""
    entries = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError:
        return entries

    # Découper par sections (## ou ###) ou par listes (- / *)
    sections = re.split(r'\n(?=#{2,3}\s)', content)

    for section in sections:
        if len(section.strip()) < 20:
            continue
        # Sous-découper par items de liste
        items = re.split(r'\n(?=[-*]\s)', section)
        for item in items:
            text = item.strip()
            if len(text) < 15:
                continue
            entries.append(MemoryEntry(
                source=str(filepath),
                kind=kind,
                text=text[:500],  # Cap
                agent=_extract_agent(text, filepath.name),
                date=_extract_date(text),
                tags=_extract_tags(text),
            ))

    return entries


def load_all_memory(project_root: Path) -> list[MemoryEntry]:
    """Charge toutes les sources mémoire."""
    entries = []
    globs = {
        LEARNINGS_GLOB: "learning",
        DECISIONS_GLOB: "decision",
        FAILURE_GLOB: "failure",
        DREAM_GLOB: "dream",
        SHARED_GLOB: "shared",
    }
    for memory_dir in MEMORY_DIRS:
        base = project_root / memory_dir
        if not base.exists():
            continue
        for glob_pattern, kind in globs.items():
            for fpath in base.glob(glob_pattern):
                entries.extend(parse_markdown_entries(fpath, kind))

    return entries


# ── Scoring ──────────────────────────────────────────────────────────────────

def _word_tokens(text: str) -> set[str]:
    """Tokenise en mots significatifs."""
    words = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text.lower())
    # Filtrer les stop words courants
    stop = {"the", "and", "for", "are", "but", "not", "you", "all", "can",
            "her", "was", "one", "our", "les", "des", "une", "que", "est",
            "sur", "par", "pour", "dans", "avec", "sont", "pas", "plus",
            "qui", "this", "that", "from", "have", "has", "had"}
    return {w for w in words if w not in stop}


def compute_relevance(entry: MemoryEntry, context: str, agent: str) -> float:
    """Score de pertinence d'une entrée pour un contexte donné."""
    score = 0.0

    # Correspondance agent
    if agent and entry.agent and entry.agent.lower() == agent.lower():
        score += 0.3

    # Correspondance textuelle (Jaccard)
    if context:
        ctx_tokens = _word_tokens(context)
        entry_tokens = _word_tokens(entry.text)
        if ctx_tokens and entry_tokens:
            intersection = ctx_tokens & entry_tokens
            union = ctx_tokens | entry_tokens
            if union:
                jaccard = len(intersection) / len(union)
                score += jaccard * 0.5

    # Bonus récence
    age = entry.age_days()
    recency = math.pow(0.5, age / RECENCY_HALF_LIFE_DAYS)
    score += recency * 0.2

    # Bonus failure (plus important à rappeler)
    if entry.kind == "failure":
        score += 0.15

    return min(1.0, score)


# ── Nudge Generators ─────────────────────────────────────────────────────────

def generate_suggestions(
    entries: list[MemoryEntry],
    agent: str = "",
    context: str = "",
    max_nudges: int = MAX_NUDGES,
) -> list[Nudge]:
    """Génère des suggestions contextuelles."""
    scored = []
    for entry in entries:
        rel = compute_relevance(entry, context, agent)
        if rel >= RELEVANCE_THRESHOLD:
            scored.append((rel, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    nudges = []

    for rel, entry in scored[:max_nudges]:
        if entry.kind == "failure":
            ntype = NudgeType.WARNING
            title = "Échec passé pertinent"
        elif entry.kind == "learning":
            ntype = NudgeType.REMINDER
            title = "Leçon apprise"
        elif entry.kind == "dream":
            ntype = NudgeType.OPPORTUNITY
            title = "Insight dream"
        elif entry.kind == "decision":
            ntype = NudgeType.PATTERN
            title = "Décision passée"
        else:
            ntype = NudgeType.REMINDER
            title = "Rappel contextuel"

        # Extrait un résumé (première phrase ou 120 chars)
        summary = entry.text.split("\n")[0][:120]
        if summary.startswith(("#", "-", "*")):
            summary = summary.lstrip("#-* ")

        nudges.append(Nudge(
            nudge_type=ntype,
            title=title,
            message=summary,
            relevance=round(rel, 3),
            sources=[entry.source],
            tags=entry.tags[:5],
        ))

    return nudges


def generate_serendipity(
    entries: list[MemoryEntry],
    domains: list[str] | None = None,
    max_nudges: int = 5,
) -> list[Nudge]:
    """Connexions surprenantes entre domaines/agents différents."""
    # Grouper par agent
    by_agent: dict[str, list[MemoryEntry]] = defaultdict(list)
    for e in entries:
        key = e.agent or "unknown"
        by_agent[key].append(e)

    # Chercher des mots partagés entre agents différents
    agent_vocabs: dict[str, Counter] = {}
    for agent, agent_entries in by_agent.items():
        vocab: Counter = Counter()
        for e in agent_entries:
            vocab.update(_word_tokens(e.text))
        agent_vocabs[agent] = vocab

    nudges = []
    agents = list(agent_vocabs.keys())

    for i in range(len(agents)):
        for j in range(i + 1, len(agents)):
            a1, a2 = agents[i], agents[j]
            v1, v2 = agent_vocabs[a1], agent_vocabs[a2]

            # Mots partagés mais non triviaux
            common = set(v1.keys()) & set(v2.keys())
            if domains:
                domain_tokens = set()
                for d in domains:
                    domain_tokens.update(_word_tokens(d))
                common = common & domain_tokens if domain_tokens else common

            # Score : mots partagés rares (présents dans peu d'agents)
            for word in sorted(common, key=lambda w: v1[w] + v2[w], reverse=True)[:2]:
                if len(word) < 4:
                    continue
                # Trouver les entrées concernées
                src1 = [e for e in by_agent[a1] if word in _word_tokens(e.text)][:1]
                src2 = [e for e in by_agent[a2] if word in _word_tokens(e.text)][:1]
                if src1 and src2:
                    nudges.append(Nudge(
                        nudge_type=NudgeType.SERENDIP,
                        title=f"Connexion {a1}↔{a2} via '{word}'",
                        message=f"Agent {a1} et {a2} partagent le concept '{word}'. "
                                f"Exploration croisée recommandée.",
                        relevance=0.5,
                        sources=[src1[0].source, src2[0].source],
                        tags=[word, a1, a2],
                    ))

            if len(nudges) >= max_nudges:
                break
        if len(nudges) >= max_nudges:
            break

    return nudges[:max_nudges]


def generate_recalls(
    entries: list[MemoryEntry],
    query: str,
    agent: str = "",
    max_nudges: int = MAX_NUDGES,
) -> list[Nudge]:
    """Rappels depuis failure-museum pour un contexte donné."""
    failures = [e for e in entries if e.kind == "failure"]
    scored = []

    query_tokens = _word_tokens(query)
    for entry in failures:
        entry_tokens = _word_tokens(entry.text)
        if not query_tokens or not entry_tokens:
            continue
        intersection = query_tokens & entry_tokens
        if intersection:
            score = len(intersection) / len(query_tokens)
            # Bonus agent match
            if agent and entry.agent.lower() == agent.lower():
                score += 0.2
            scored.append((min(1.0, score), entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    nudges = []

    for rel, entry in scored[:max_nudges]:
        summary = entry.text.split("\n")[0][:150].lstrip("#-* ")
        nudges.append(Nudge(
            nudge_type=NudgeType.WARNING,
            title="Failure Museum — Ne pas répéter",
            message=summary,
            relevance=round(rel, 3),
            sources=[entry.source],
            tags=entry.tags[:5],
        ))

    return nudges


# ── Formatters ───────────────────────────────────────────────────────────────

def format_report(report: NudgeReport) -> str:
    """Formatage texte du rapport."""
    lines = [
        f"💡 Nudge Engine — {report.mode.upper()}",
    ]
    if report.agent:
        lines.append(f"   Agent : {report.agent}")
    if report.context:
        lines.append(f"   Contexte : {report.context[:80]}")
    lines.append(f"   Entrées scannées : {report.total_entries_scanned}")
    lines.append(f"   Nudges générés : {len(report.nudges)}")
    lines.append("")

    if not report.nudges:
        lines.append("   Aucune suggestion pertinente trouvée.")
        lines.append("   💡 Astuce : enrichir failure-museum et learnings pour de meilleurs nudges.")
        return "\n".join(lines)

    for i, nudge in enumerate(report.nudges, 1):
        lines.append(f"   {i}. {nudge.nudge_type}")
        lines.append(f"      {nudge.title}")
        lines.append(f"      {nudge.message}")
        lines.append(f"      Pertinence : {nudge.relevance:.0%}")
        if nudge.tags:
            lines.append(f"      Tags : {', '.join(nudge.tags[:5])}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: NudgeReport) -> dict:
    """Convertit en dict pour JSON."""
    return {
        "mode": report.mode,
        "agent": report.agent,
        "context": report.context,
        "timestamp": report.timestamp,
        "total_entries_scanned": report.total_entries_scanned,
        "nudges": [
            {
                "type": n.nudge_type,
                "title": n.title,
                "message": n.message,
                "relevance": n.relevance,
                "sources": n.sources,
                "tags": n.tags,
            }
            for n in report.nudges
        ],
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_suggest(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    entries = load_all_memory(project_root)
    nudges = generate_suggestions(entries, agent=args.agent or "", context=args.context or "")
    report = NudgeReport(
        mode="suggest",
        agent=args.agent or "",
        context=args.context or "",
        nudges=nudges,
        total_entries_scanned=len(entries),
    )
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_serendip(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    entries = load_all_memory(project_root)
    domains = args.domains.split(",") if args.domains else None
    nudges = generate_serendipity(entries, domains=domains)
    report = NudgeReport(
        mode="serendipity",
        nudges=nudges,
        total_entries_scanned=len(entries),
    )
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    entries = load_all_memory(project_root)
    nudges = generate_recalls(entries, query=args.query, agent=args.agent or "")
    report = NudgeReport(
        mode="recall",
        agent=args.agent or "",
        context=args.query,
        nudges=nudges,
        total_entries_scanned=len(entries),
    )
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Nudge Engine — Suggestions contextuelles intelligentes",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    # suggest
    p_suggest = subs.add_parser("suggest", help="Suggestions contextuelles pour un agent")
    p_suggest.add_argument("--agent", type=str, help="Agent cible")
    p_suggest.add_argument("--context", type=str, help="Contexte textuel (module, feature, etc.)")
    p_suggest.set_defaults(func=cmd_suggest)

    # serendip
    p_serendip = subs.add_parser("serendip", help="Connexions cross-module surprenantes")
    p_serendip.add_argument("--domains", type=str, help="Domaines à croiser (comma-separated)")
    p_serendip.set_defaults(func=cmd_serendip)

    # recall
    p_recall = subs.add_parser("recall", help="Rappels failure-museum pour un contexte")
    p_recall.add_argument("--query", type=str, required=True, help="Terme de recherche")
    p_recall.add_argument("--agent", type=str, help="Agent cible")
    p_recall.set_defaults(func=cmd_recall)

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
