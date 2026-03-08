#!/usr/bin/env python3
"""
reasoning-stream.py — Flux de raisonnement structuré pour Grimoire.
================================================================

Extension du Grimoire_TRACE avec des types hypothesis/doubt/reasoning/assumption.
Capture le POURQUOI des décisions, pas seulement le QUOI. Permet l'analyse
des chaînes de raisonnement, la détection des doutes non résolus et des
hypothèses non validées.

Types d'entrées du reasoning stream :
  - HYPOTHESIS : hypothèse formulée (à valider)
  - DOUBT      : doute explicite (signal d'incertitude)
  - REASONING  : chaîne de raisonnement pour une décision
  - ASSUMPTION : hypothèse implicite posée comme vraie
  - ALTERNATIVE: option envisagée puis écartée

Usage :
  python3 reasoning-stream.py --project-root . log --agent dev --type HYPOTHESIS --text "..."
  python3 reasoning-stream.py --project-root . query --agent dev --type DOUBT
  python3 reasoning-stream.py --project-root . analyze
  python3 reasoning-stream.py --project-root . compact --before 2026-01-01
  python3 reasoning-stream.py --project-root . stats

Stdlib only — aucune dépendance externe.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

STREAM_FILE = "reasoning-stream.jsonl"
COMPACT_FILE = "reasoning-stream-compacted.md"
MAX_STREAM_ENTRIES = 5000        # Au-delà → recommander compaction
COMPACT_THRESHOLD_DAYS = 30      # Entrées > N jours → compactables

# Types valides
VALID_TYPES = {"HYPOTHESIS", "DOUBT", "REASONING", "ASSUMPTION", "ALTERNATIVE"}

# Statuts des hypothèses
VALID_STATUSES = {"open", "validated", "invalidated", "abandoned"}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReasoningEntry:
    """Une entrée du flux de raisonnement."""
    timestamp: str
    agent: str
    entry_type: str         # HYPOTHESIS | DOUBT | REASONING | ASSUMPTION | ALTERNATIVE
    text: str
    context: str = ""       # Contexte additionnel (story, decision, etc.)
    status: str = "open"    # open | validated | invalidated | abandoned
    confidence: float = 0.5 # 0.0 - 1.0
    related_to: str = ""    # ID de l'entrée parente (pour les chaînes)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "agent": self.agent,
            "type": self.entry_type,
            "text": self.text,
            "context": self.context,
            "status": self.status,
            "confidence": self.confidence,
            "related_to": self.related_to,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReasoningEntry":
        return cls(
            timestamp=data.get("timestamp", ""),
            agent=data.get("agent", "unknown"),
            entry_type=data.get("type", "REASONING"),
            text=data.get("text", ""),
            context=data.get("context", ""),
            status=data.get("status", "open"),
            confidence=data.get("confidence", 0.5),
            related_to=data.get("related_to", ""),
            tags=data.get("tags", []),
        )


@dataclass
class StreamAnalysis:
    """Résultat de l'analyse du flux de raisonnement."""
    total_entries: int
    by_type: dict[str, int]
    by_agent: dict[str, int]
    by_status: dict[str, int]
    open_hypotheses: list[ReasoningEntry]
    unresolved_doubts: list[ReasoningEntry]
    abandoned_alternatives: list[ReasoningEntry]
    unvalidated_assumptions: list[ReasoningEntry]
    reasoning_chains: list[list[ReasoningEntry]]
    avg_confidence: float
    needs_compaction: bool
    recommendations: list[str]


# ── Lecture / Écriture ────────────────────────────────────────────────────────

def _get_stream_path(project_root: Path) -> Path:
    """Retourne le chemin du fichier stream."""
    return project_root / "_grimoire-output" / STREAM_FILE


def log_entry(entry: ReasoningEntry, project_root: Path) -> Path:
    """Ajoute une entrée au flux de raisonnement."""
    output_dir = project_root / "_grimoire-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / STREAM_FILE

    line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    return path


def read_stream(project_root: Path, agent: str | None = None,
                entry_type: str | None = None,
                status: str | None = None,
                since: str | None = None,
                limit: int | None = None) -> list[ReasoningEntry]:
    """Lit et filtre le flux de raisonnement."""
    path = _get_stream_path(project_root)
    if not path.exists():
        return []

    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entry = ReasoningEntry.from_dict(data)

                # Filtres
                if agent and agent.lower() not in entry.agent.lower():
                    continue
                if entry_type and entry.entry_type != entry_type:
                    continue
                if status and entry.status != status:
                    continue
                if since and entry.timestamp[:10] < since:
                    continue

                entries.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    except OSError:
        return []

    if limit:
        entries = entries[-limit:]
    return entries


def update_entry_status(project_root: Path, timestamp: str,
                        new_status: str) -> bool:
    """Met à jour le statut d'une entrée spécifique."""
    path = _get_stream_path(project_root)
    if not path.exists():
        return False

    lines = path.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []

    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        try:
            data = json.loads(line)
            if data.get("timestamp") == timestamp:
                data["status"] = new_status
                updated = True
            new_lines.append(json.dumps(data, ensure_ascii=False))
        except (json.JSONDecodeError, KeyError):
            new_lines.append(line)

    if updated:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


# ── Analyse ───────────────────────────────────────────────────────────────────

def analyze_stream(project_root: Path,
                   since: str | None = None) -> StreamAnalysis:
    """Analyse le flux de raisonnement complet."""
    entries = read_stream(project_root, since=since)

    by_type: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    by_status: dict[str, int] = {}
    open_hypotheses: list[ReasoningEntry] = []
    unresolved_doubts: list[ReasoningEntry] = []
    abandoned_alternatives: list[ReasoningEntry] = []
    unvalidated_assumptions: list[ReasoningEntry] = []

    total_confidence = 0.0

    for entry in entries:
        by_type[entry.entry_type] = by_type.get(entry.entry_type, 0) + 1
        by_agent[entry.agent] = by_agent.get(entry.agent, 0) + 1
        by_status[entry.status] = by_status.get(entry.status, 0) + 1
        total_confidence += entry.confidence

        if entry.entry_type == "HYPOTHESIS" and entry.status == "open":
            open_hypotheses.append(entry)
        elif entry.entry_type == "DOUBT" and entry.status == "open":
            unresolved_doubts.append(entry)
        elif entry.entry_type == "ALTERNATIVE" and entry.status == "abandoned":
            abandoned_alternatives.append(entry)
        elif entry.entry_type == "ASSUMPTION" and entry.status == "open":
            unvalidated_assumptions.append(entry)

    # Détecter les chaînes de raisonnement (entrées liées par related_to)
    chains: list[list[ReasoningEntry]] = []
    chain_heads: dict[str, list[ReasoningEntry]] = {}
    for entry in entries:
        if entry.related_to:
            chain_heads.setdefault(entry.related_to, []).append(entry)

    for head_ts, chain_entries in chain_heads.items():
        head = next((e for e in entries if e.timestamp == head_ts), None)
        if head:
            chains.append([head] + chain_entries)

    # Recommandations
    recommendations = []
    needs_compaction = len(entries) > MAX_STREAM_ENTRIES

    if needs_compaction:
        recommendations.append(
            f"Le stream contient {len(entries)} entrées (> {MAX_STREAM_ENTRIES}) "
            "— lancer `compact` pour archiver les anciennes")

    if len(open_hypotheses) > 5:
        recommendations.append(
            f"{len(open_hypotheses)} hypothèses non validées — les prioriser")

    if len(unresolved_doubts) > 3:
        recommendations.append(
            f"{len(unresolved_doubts)} doutes non résolus — les adresser "
            "ou les clore comme abandonnés")

    if len(unvalidated_assumptions) > 3:
        recommendations.append(
            f"{len(unvalidated_assumptions)} assumptions non validées — "
            "risque de dette de raisonnement")

    avg_confidence = total_confidence / len(entries) if entries else 0.0
    if avg_confidence < 0.4 and len(entries) > 5:
        recommendations.append(
            f"Confiance moyenne basse ({avg_confidence:.0%}) — "
            "les agents manquent de certitude, envisager plus de validation")

    return StreamAnalysis(
        total_entries=len(entries),
        by_type=by_type,
        by_agent=by_agent,
        by_status=by_status,
        open_hypotheses=open_hypotheses,
        unresolved_doubts=unresolved_doubts,
        abandoned_alternatives=abandoned_alternatives,
        unvalidated_assumptions=unvalidated_assumptions,
        reasoning_chains=chains,
        avg_confidence=round(avg_confidence, 2),
        needs_compaction=needs_compaction,
        recommendations=recommendations,
    )


# ── Compaction ────────────────────────────────────────────────────────────────

def compact_stream(project_root: Path,
                   before: str | None = None,
                   dry_run: bool = False) -> dict:
    """Compacte les anciennes entrées en résumé Markdown."""
    if before is None:
        cutoff = (datetime.now() - timedelta(days=COMPACT_THRESHOLD_DAYS)).strftime("%Y-%m-%d")
    else:
        cutoff = before

    entries = read_stream(project_root)
    old_entries = [e for e in entries if e.timestamp[:10] < cutoff]
    keep_entries = [e for e in entries if e.timestamp[:10] >= cutoff]

    if not old_entries:
        return {"compacted": 0, "kept": len(keep_entries), "summary": "Rien à compacter"}

    # Générer le résumé
    summary_lines = [
        f"# Reasoning Stream — Compaction {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"> {len(old_entries)} entrées compactées (avant {cutoff})",
        "",
    ]

    # Résumé par type
    by_type: dict[str, list[ReasoningEntry]] = {}
    for e in old_entries:
        by_type.setdefault(e.entry_type, []).append(e)

    for etype, etype_entries in sorted(by_type.items()):
        summary_lines.append(f"## {etype} ({len(etype_entries)})")
        summary_lines.append("")

        # Regrouper par statut
        by_status: dict[str, list[ReasoningEntry]] = {}
        for e in etype_entries:
            by_status.setdefault(e.status, []).append(e)

        for status, status_entries in sorted(by_status.items()):
            summary_lines.append(f"### {status} ({len(status_entries)})")
            for e in status_entries[:10]:  # Max 10 par sous-groupe
                summary_lines.append(
                    f"- [{e.agent}] {e.text[:120]}{'...' if len(e.text) > 120 else ''}"
                )
            if len(status_entries) > 10:
                summary_lines.append(f"  _... et {len(status_entries) - 10} de plus_")
            summary_lines.append("")

    if dry_run:
        return {
            "compacted": len(old_entries),
            "kept": len(keep_entries),
            "summary": "\n".join(summary_lines),
            "dry_run": True,
        }

    # Écrire le résumé
    output_dir = project_root / "_grimoire-output"
    compact_path = output_dir / COMPACT_FILE

    # Append au fichier compact existant
    mode = "a" if compact_path.exists() else "w"
    with open(compact_path, mode, encoding="utf-8") as f:
        f.write("\n".join(summary_lines))
        f.write("\n\n---\n\n")

    # Réécrire le stream avec seulement les entrées récentes
    stream_path = output_dir / STREAM_FILE
    with open(stream_path, "w", encoding="utf-8") as f:
        for entry in keep_entries:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    return {
        "compacted": len(old_entries),
        "kept": len(keep_entries),
        "summary": f"Compacté {len(old_entries)} entrées → {COMPACT_FILE}",
        "dry_run": False,
    }


# ── Rendu ─────────────────────────────────────────────────────────────────────

TYPE_ICONS = {
    "HYPOTHESIS": "🔬",
    "DOUBT": "❓",
    "REASONING": "🧠",
    "ASSUMPTION": "📌",
    "ALTERNATIVE": "🔀",
}

STATUS_ICONS = {
    "open": "⏳",
    "validated": "✅",
    "invalidated": "❌",
    "abandoned": "🚫",
}


def render_entries(entries: list[ReasoningEntry]) -> str:
    """Affiche une liste d'entrées en format lisible."""
    if not entries:
        return "Aucune entrée trouvée."

    lines = []
    for e in entries:
        t_icon = TYPE_ICONS.get(e.entry_type, "📝")
        s_icon = STATUS_ICONS.get(e.status, "?")
        conf_bar = "█" * int(e.confidence * 5) + "░" * (5 - int(e.confidence * 5))
        lines.append(
            f"{t_icon} [{e.timestamp[:16]}] [{e.agent}] {s_icon} {e.status} "
            f"| `{conf_bar}` {e.confidence:.0%}"
        )
        lines.append(f"   {e.text}")
        if e.context:
            lines.append(f"   📎 {e.context}")
        if e.tags:
            lines.append(f"   🏷️ {', '.join(e.tags)}")
        lines.append("")
    return "\n".join(lines)


def render_analysis(analysis: StreamAnalysis) -> str:
    """Génère le rapport d'analyse du stream."""
    lines = [
        "# 🧠 Analyse du Reasoning Stream",
        "",
        f"> **Entrées totales** : {analysis.total_entries}",
        f"> **Confiance moyenne** : {analysis.avg_confidence:.0%}",
    ]
    if analysis.needs_compaction:
        lines.append("> ⚠️ **Compaction recommandée**")
    lines.extend(["", "---", ""])

    # Par type
    lines.append("## 📊 Par Type")
    lines.append("")
    lines.append("| Type | Count | % |")
    lines.append("|------|-------|---|")
    for etype, count in sorted(analysis.by_type.items(),
                                key=lambda x: x[1], reverse=True):
        icon = TYPE_ICONS.get(etype, "📝")
        pct = count / analysis.total_entries * 100 if analysis.total_entries else 0
        lines.append(f"| {icon} {etype} | {count} | {pct:.0f}% |")
    lines.extend(["", "---", ""])

    # Par agent
    lines.append("## 👤 Par Agent")
    lines.append("")
    for agent, count in sorted(analysis.by_agent.items(),
                                key=lambda x: x[1], reverse=True):
        bar = "█" * min(20, count) + f" ({count})"
        lines.append(f"- **{agent}** : {bar}")
    lines.extend(["", "---", ""])

    # Par statut
    lines.append("## 📋 Par Statut")
    lines.append("")
    for status, count in sorted(analysis.by_status.items()):
        icon = STATUS_ICONS.get(status, "?")
        lines.append(f"- {icon} **{status}** : {count}")
    lines.extend(["", "---", ""])

    # Éléments actionnables
    if analysis.open_hypotheses:
        lines.append(f"## 🔬 Hypothèses ouvertes ({len(analysis.open_hypotheses)})")
        lines.append("")
        for h in analysis.open_hypotheses[:10]:
            lines.append(f"- [{h.agent}] {h.text[:100]}")
        lines.extend(["", "---", ""])

    if analysis.unresolved_doubts:
        lines.append(f"## ❓ Doutes non résolus ({len(analysis.unresolved_doubts)})")
        lines.append("")
        for d in analysis.unresolved_doubts[:10]:
            lines.append(f"- [{d.agent}] {d.text[:100]}")
        lines.extend(["", "---", ""])

    if analysis.unvalidated_assumptions:
        lines.append(f"## 📌 Assumptions non validées ({len(analysis.unvalidated_assumptions)})")
        lines.append("")
        for a in analysis.unvalidated_assumptions[:10]:
            lines.append(f"- [{a.agent}] {a.text[:100]}")
        lines.extend(["", "---", ""])

    if analysis.reasoning_chains:
        lines.append(f"## 🔗 Chaînes de raisonnement ({len(analysis.reasoning_chains)})")
        lines.append("")
        for chain in analysis.reasoning_chains[:5]:
            lines.append(f"**Chaîne ({len(chain)} étapes)** :")
            for step in chain:
                icon = TYPE_ICONS.get(step.entry_type, "📝")
                lines.append(f"  {icon} {step.text[:80]}")
            lines.append("")
        lines.extend(["---", ""])

    # Recommandations
    if analysis.recommendations:
        lines.append("## 🎯 Recommandations")
        lines.append("")
        for i, rec in enumerate(analysis.recommendations, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    return "\n".join(lines)


def render_stats(entries: list[ReasoningEntry]) -> str:
    """Statistiques compactes."""
    if not entries:
        return "Aucune entrée dans le reasoning stream."

    by_type = {}
    by_status = {}
    by_agent = {}
    total_conf = 0.0

    for e in entries:
        by_type[e.entry_type] = by_type.get(e.entry_type, 0) + 1
        by_status[e.status] = by_status.get(e.status, 0) + 1
        by_agent[e.agent] = by_agent.get(e.agent, 0) + 1
        total_conf += e.confidence

    avg_conf = total_conf / len(entries)
    lines = [
        "## 📊 Reasoning Stream Stats",
        "",
        f"- **Total** : {len(entries)} entrées",
        f"- **Confiance moy.** : {avg_conf:.0%}",
        f"- **Agents** : {len(by_agent)}",
        "",
        "**Par type** :",
    ]
    for t, c in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        icon = TYPE_ICONS.get(t, "📝")
        lines.append(f"  {icon} {t}: {c}")

    lines.append("")
    lines.append("**Par statut** :")
    for s, c in sorted(by_status.items()):
        icon = STATUS_ICONS.get(s, "?")
        lines.append(f"  {icon} {s}: {c}")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Grimoire Reasoning Stream — flux de raisonnement structuré",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet Grimoire")

    subparsers = parser.add_subparsers(dest="command", help="Commande")

    # log
    log_parser = subparsers.add_parser("log", help="Ajouter une entrée")
    log_parser.add_argument("--agent", required=True, help="Agent émetteur")
    log_parser.add_argument("--type", required=True, choices=sorted(VALID_TYPES),
                            help="Type d'entrée")
    log_parser.add_argument("--text", required=True, help="Contenu")
    log_parser.add_argument("--context", default="", help="Contexte")
    log_parser.add_argument("--confidence", type=float, default=0.5,
                            help="Confiance (0.0-1.0)")
    log_parser.add_argument("--related-to", default="", help="Timestamp parent")
    log_parser.add_argument("--tags", default="", help="Tags (comma-sep)")

    # query
    query_parser = subparsers.add_parser("query", help="Interroger le stream")
    query_parser.add_argument("--agent", default=None, help="Filtrer par agent")
    query_parser.add_argument("--type", default=None, choices=sorted(VALID_TYPES),
                              help="Filtrer par type")
    query_parser.add_argument("--status", default=None,
                              choices=sorted(VALID_STATUSES),
                              help="Filtrer par statut")
    query_parser.add_argument("--since", default=None, help="Date début")
    query_parser.add_argument("--limit", type=int, default=20,
                              help="Nombre max d'entrées")
    query_parser.add_argument("--json", action="store_true",
                              help="Sortie JSON")

    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="Analyser le stream")
    analyze_parser.add_argument("--since", default=None, help="Date début")
    analyze_parser.add_argument("--json", action="store_true",
                                help="Sortie JSON")

    # compact
    compact_parser = subparsers.add_parser("compact",
                                            help="Compacter les anciennes entrées")
    compact_parser.add_argument("--before", default=None,
                                help="Compacter avant cette date (YYYY-MM-DD)")
    compact_parser.add_argument("--dry-run", action="store_true",
                                help="Preview sans modifier")

    # stats
    subparsers.add_parser("stats", help="Statistiques rapides")

    # resolve
    resolve_parser = subparsers.add_parser("resolve",
                                            help="Changer le statut d'une entrée")
    resolve_parser.add_argument("--timestamp", required=True,
                                help="Timestamp de l'entrée")
    resolve_parser.add_argument("--status", required=True,
                                choices=sorted(VALID_STATUSES),
                                help="Nouveau statut")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "log":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        entry = ReasoningEntry(
            timestamp=datetime.now().isoformat(),
            agent=args.agent,
            entry_type=args.type,
            text=args.text,
            context=args.context,
            confidence=max(0.0, min(1.0, args.confidence)),
            related_to=args.related_to,
            tags=tags,
        )
        path = log_entry(entry, project_root)
        icon = TYPE_ICONS.get(args.type, "📝")
        print(f"{icon} Entrée '{args.type}' ajoutée au reasoning stream")
        print(f"   → {path}")

    elif args.command == "query":
        entries = read_stream(
            project_root, agent=args.agent, entry_type=args.type,
            status=args.status, since=args.since, limit=args.limit,
        )
        if args.json:
            print(json.dumps([e.to_dict() for e in entries],
                             indent=2, ensure_ascii=False))
        else:
            print(render_entries(entries))

    elif args.command == "analyze":
        analysis = analyze_stream(project_root, since=args.since)
        if args.json:
            data = {
                "total": analysis.total_entries,
                "by_type": analysis.by_type,
                "by_agent": analysis.by_agent,
                "by_status": analysis.by_status,
                "open_hypotheses": len(analysis.open_hypotheses),
                "unresolved_doubts": len(analysis.unresolved_doubts),
                "avg_confidence": analysis.avg_confidence,
                "needs_compaction": analysis.needs_compaction,
                "recommendations": analysis.recommendations,
            }
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(render_analysis(analysis))

    elif args.command == "compact":
        result = compact_stream(project_root, before=args.before,
                                dry_run=args.dry_run)
        if args.dry_run:
            print("🔍 Preview compaction :")
            print(f"   Entrées à compacter : {result['compacted']}")
            print(f"   Entrées conservées : {result['kept']}")
            print()
            print(result.get("summary", ""))
        else:
            print(f"✅ {result['summary']}")
            print(f"   Conservées : {result['kept']}")

    elif args.command == "stats":
        entries = read_stream(project_root)
        print(render_stats(entries))

    elif args.command == "resolve":
        success = update_entry_status(project_root, args.timestamp,
                                      args.status)
        if success:
            icon = STATUS_ICONS.get(args.status, "?")
            print(f"{icon} Entrée mise à jour → {args.status}")
        else:
            print("❌ Entrée non trouvée", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
