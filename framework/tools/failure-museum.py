#!/usr/bin/env python3
"""Failure Museum — Catalogue structuré des échecs du projet.

Archive chaque échec avec sa cause racine, son correctif, et la règle
ajoutée pour l'éviter à l'avenir. Produit un catalogue consultable
en markdown ou JSON.

Usage:
    python failure-museum.py --project-root . add \
        --title "Import crash" \
        --severity high \
        --agents "dev,architect" \
        --description "Module chargé sans sys.modules pre-registration" \
        --root-cause "@dataclass crash when module not in sys.modules" \
        --fix "Register module in sys.modules before exec_module" \
        --rule "Always sys.modules[name]=mod before spec.loader.exec_module(mod)"

    python failure-museum.py --project-root . list
    python failure-museum.py --project-root . list --severity high
    python failure-museum.py --project-root . search --query "dataclass"
    python failure-museum.py --project-root . stats
    python failure-museum.py --project-root . export --format json
    python failure-museum.py --project-root . lessons
    python failure-museum.py --project-root . check --description "my new change"

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

VERSION = "1.0.0"
MUSEUM_DIR = "_grimoire/_memory"
MUSEUM_FILE = "failure-museum.jsonl"
MUSEUM_MD = "failure-museum.md"

# ── Severities ────────────────────────────────────────────────────────────────

SEVERITIES = ("low", "medium", "high")

# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class Failure:
    """Un échec catalogué dans le musée."""

    failure_id: str = ""
    sequence: int = 0
    timestamp: str = ""
    title: str = ""
    severity: str = "medium"
    agents: list[str] = field(default_factory=list)
    description: str = ""
    root_cause: str = ""
    fix: str = ""
    rule_added: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "resolved"  # resolved, open, wontfix


# ── Persistence ───────────────────────────────────────────────────────────────


def _museum_dir(root: Path) -> Path:
    d = root / MUSEUM_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jsonl_path(root: Path) -> Path:
    return _museum_dir(root) / MUSEUM_FILE


def _md_path(root: Path) -> Path:
    return _museum_dir(root) / MUSEUM_MD


def load_failures(root: Path) -> list[Failure]:
    """Load all failures from JSONL."""
    path = _jsonl_path(root)
    if not path.exists():
        return []
    entries: list[Failure] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        entries.append(Failure(**data))
    return entries


def save_failure(root: Path, failure: Failure) -> None:
    """Append a single failure to JSONL."""
    path = _jsonl_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(failure), ensure_ascii=False) + "\n")


def next_failure_id(entries: list[Failure]) -> tuple[str, int]:
    """Generate next FM-XXX id."""
    if not entries:
        return "FM-001", 1
    max_seq = max(e.sequence for e in entries)
    seq = max_seq + 1
    return f"FM-{seq:03d}", seq


# ── Markdown export ───────────────────────────────────────────────────────────


def render_markdown(entries: list[Failure]) -> str:
    """Render the full museum as markdown."""
    lines = [
        "# Failure Museum",
        "",
        "> Catalogue des défaillances passées du projet.",
        "> Chaque entrée documente un échec réel, sa cause, et le correctif appliqué.",
        "> **CONSULTER CE FICHIER AVANT TOUTE IMPLÉMENTATION** — "
        "pour éviter de répéter les mêmes erreurs.",
        "",
        "---",
        "",
    ]
    for e in entries:
        agents_str = ", ".join(e.agents) if e.agents else "unknown"
        lines.append(f"## {e.failure_id} — {e.title}")
        lines.append(f"- **Date**: {e.timestamp[:10]}")
        lines.append(f"- **Sévérité**: {e.severity}")
        lines.append(f"- **Agent(s) impliqué(s)**: {agents_str}")
        lines.append(f"- **Description**: {e.description}")
        lines.append(f"- **Cause racine**: {e.root_cause}")
        lines.append(f"- **Correctif**: {e.fix}")
        lines.append(f"- **Règle ajoutée**: {e.rule_added}")
        if e.tags:
            lines.append(f"- **Tags**: {', '.join(e.tags)}")
        lines.append(f"- **Statut**: {e.status}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def sync_markdown(root: Path) -> None:
    """Regenerate the markdown file from JSONL."""
    entries = load_failures(root)
    md = render_markdown(entries)
    _md_path(root).write_text(md, encoding="utf-8")


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_add(root: Path, args: argparse.Namespace) -> int:
    """Add a new failure to the museum."""
    entries = load_failures(root)
    fid, seq = next_failure_id(entries)

    agents_list = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else []
    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    if args.severity not in SEVERITIES:
        print(f"ERROR: severity must be one of {SEVERITIES}", file=sys.stderr)
        return 1

    failure = Failure(
        failure_id=fid,
        sequence=seq,
        timestamp=datetime.now(UTC).isoformat(),
        title=args.title,
        severity=args.severity,
        agents=agents_list,
        description=args.description,
        root_cause=args.root_cause,
        fix=args.fix,
        rule_added=args.rule or "",
        tags=tags_list,
        status=args.status if hasattr(args, "status") and args.status else "resolved",
    )
    save_failure(root, failure)
    sync_markdown(root)
    print(f"✅ {fid} — {args.title} (severity={args.severity})")
    return 0


def cmd_list(root: Path, args: argparse.Namespace) -> int:
    """List all failures, optionally filtered."""
    entries = load_failures(root)
    if not entries:
        print("Museum vide — aucun échec catalogué.")
        return 0

    if args.severity:
        entries = [e for e in entries if e.severity == args.severity]
    if args.status:
        entries = [e for e in entries if e.status == args.status]

    for e in entries:
        sev_icon = {"low": "🟡", "medium": "🟠", "high": "🔴"}.get(e.severity, "⚪")
        print(f"  {sev_icon} {e.failure_id} — {e.title} [{e.severity}] ({e.status})")
    print(f"\nTotal: {len(entries)} entrée(s)")
    return 0


def cmd_search(root: Path, args: argparse.Namespace) -> int:
    """Search failures by keyword."""
    entries = load_failures(root)
    query = args.query.lower()
    matches = []
    for e in entries:
        searchable = f"{e.title} {e.description} {e.root_cause} {e.fix} {e.rule_added} {' '.join(e.tags)}"
        if query in searchable.lower():
            matches.append(e)

    if not matches:
        print(f"Aucun résultat pour '{args.query}'")
        return 0

    for e in matches:
        print(f"  {e.failure_id} — {e.title}")
        print(f"    Cause: {e.root_cause}")
        print(f"    Fix:   {e.fix}")
        print()
    print(f"Trouvé: {len(matches)} entrée(s)")
    return 0


def cmd_stats(root: Path, _args: argparse.Namespace) -> int:
    """Show museum statistics."""
    entries = load_failures(root)
    if not entries:
        print("Museum vide.")
        return 0

    by_sev = {}
    by_status = {}
    for e in entries:
        by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
        by_status[e.status] = by_status.get(e.status, 0) + 1

    print("📊 Failure Museum — Stats")
    print(f"  Total: {len(entries)} entrée(s)")
    print(f"  Par sévérité: {dict(sorted(by_sev.items()))}")
    print(f"  Par statut:   {dict(sorted(by_status.items()))}")

    # Most common root causes
    causes: dict[str, int] = {}
    for e in entries:
        key = e.root_cause[:60] if e.root_cause else "unknown"
        causes[key] = causes.get(key, 0) + 1
    if causes:
        top = sorted(causes.items(), key=lambda x: -x[1])[:5]
        print("  Top causes:")
        for c, n in top:
            print(f"    [{n}x] {c}")
    return 0


def cmd_export(root: Path, args: argparse.Namespace) -> int:
    """Export museum in the requested format."""
    entries = load_failures(root)
    if args.format == "json":
        print(json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(render_markdown(entries))
    else:
        print(f"Format inconnu: {args.format}", file=sys.stderr)
        return 1
    return 0


def cmd_lessons(root: Path, _args: argparse.Namespace) -> int:
    """Extract lessons and rules from all failures."""
    entries = load_failures(root)
    rules = [(e.failure_id, e.rule_added) for e in entries if e.rule_added]

    if not rules:
        print("Aucune règle extraite du museum.")
        return 0

    print("📚 Leçons extraites du Failure Museum")
    print("=" * 50)
    for fid, rule in rules:
        print(f"  [{fid}] {rule}")
    print(f"\nTotal: {len(rules)} règle(s)")
    return 0


def cmd_check(root: Path, args: argparse.Namespace) -> int:
    """Check if a planned change risks repeating a known failure.

    Compares the description against all known failure descriptions,
    root causes, and rules using simple keyword overlap scoring.
    """
    entries = load_failures(root)
    if not entries:
        print("Museum vide — aucun risque historique.")
        return 0

    desc_words = set(args.description.lower().split())
    warnings: list[tuple[Failure, float]] = []

    for e in entries:
        corpus = f"{e.description} {e.root_cause} {e.fix} {e.rule_added}".lower()
        corpus_words = set(corpus.split())
        overlap = len(desc_words & corpus_words)
        if overlap >= 2:
            score = overlap / max(len(desc_words), 1)
            warnings.append((e, score))

    warnings.sort(key=lambda x: -x[1])

    if not warnings:
        print("✅ Aucun risque historique détecté.")
        return 0

    print("⚠️  Risques historiques potentiels :")
    for e, score in warnings[:5]:
        pct = int(score * 100)
        print(f"  [{pct}%] {e.failure_id} — {e.title}")
        print(f"    Cause: {e.root_cause}")
        print(f"    Règle: {e.rule_added}")
        print()
    return 1 if any(s >= 0.5 for _, s in warnings) else 0


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_failure_museum_check(
    project_root: str,
    description: str = "",
) -> dict:
    """MCP tool ``grimoire_failure_museum_check`` — vérifie un changement contre l'historique.

    Retourne les échecs passés similaires pour éviter de répéter les mêmes erreurs.

    Args:
        project_root: Racine du projet.
        description: Description du changement prévu.

    Returns:
        dict avec ``status``, ``warnings`` (liste de risques), ``risk_level``.
    """
    if not description:
        return {"status": "error", "error": "description required"}

    root = Path(project_root)
    entries = load_failures(root)
    if not entries:
        return {"status": "ok", "warnings": [], "risk_level": "none"}

    desc_words = set(description.lower().split())
    warnings: list[dict] = []

    for e in entries:
        corpus = f"{e.description} {e.root_cause} {e.fix} {e.rule_added}".lower()
        corpus_words = set(corpus.split())
        overlap = len(desc_words & corpus_words)
        if overlap >= 2:
            score = overlap / max(len(desc_words), 1)
            warnings.append({
                "failure_id": e.failure_id,
                "title": e.title,
                "root_cause": e.root_cause,
                "rule": e.rule_added,
                "relevance": round(score, 2),
            })

    warnings.sort(key=lambda x: -x["relevance"])
    warnings = warnings[:5]

    if not warnings:
        risk = "none"
    elif any(w["relevance"] >= 0.5 for w in warnings):
        risk = "high"
    else:
        risk = "low"

    return {"status": "ok", "warnings": warnings, "risk_level": risk}


def mcp_failure_museum_lessons(project_root: str) -> dict:
    """MCP tool ``grimoire_failure_museum_lessons`` — extrait les règles/leçons apprises.

    Returns:
        dict avec ``status`` et ``lessons`` (liste de règles).
    """
    root = Path(project_root)
    entries = load_failures(root)
    lessons = []
    for e in entries:
        if e.rule_added:
            lessons.append({
                "failure_id": e.failure_id,
                "severity": e.severity,
                "rule": e.rule_added,
            })
    return {"status": "ok", "lessons": lessons, "count": len(lessons)}


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="failure-museum",
        description="Failure Museum — Catalogue structuré des échecs du projet",
    )
    p.add_argument("--project-root", type=Path, default=Path(), help="Racine du projet")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    # add
    add_p = sub.add_parser("add", help="Ajouter un échec au musée")
    add_p.add_argument("--title", required=True, help="Titre court de l'échec")
    add_p.add_argument("--severity", required=True, choices=SEVERITIES, help="low|medium|high")
    add_p.add_argument("--agents", default="", help="Agents impliqués (comma-separated)")
    add_p.add_argument("--description", required=True, help="Ce qui s'est passé")
    add_p.add_argument("--root-cause", required=True, help="Pourquoi ça s'est produit")
    add_p.add_argument("--fix", required=True, help="Ce qui a été corrigé")
    add_p.add_argument("--rule", default="", help="Règle ajoutée pour l'éviter")
    add_p.add_argument("--tags", default="", help="Tags (comma-separated)")
    add_p.add_argument("--status", default="resolved", help="resolved|open|wontfix")

    # list
    list_p = sub.add_parser("list", help="Lister les échecs")
    list_p.add_argument("--severity", choices=SEVERITIES, help="Filtrer par sévérité")
    list_p.add_argument("--status", help="Filtrer par statut")

    # search
    search_p = sub.add_parser("search", help="Chercher dans le musée")
    search_p.add_argument("--query", required=True, help="Mot-clé de recherche")

    # stats
    sub.add_parser("stats", help="Statistiques du musée")

    # export
    export_p = sub.add_parser("export", help="Exporter le musée")
    export_p.add_argument("--format", choices=["json", "markdown"], default="markdown")

    # lessons
    sub.add_parser("lessons", help="Extraire les règles/leçons")

    # check
    check_p = sub.add_parser("check", help="Vérifier un changement contre l'historique")
    check_p.add_argument("--description", required=True, help="Description du changement prévu")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "search": cmd_search,
        "stats": cmd_stats,
        "export": cmd_export,
        "lessons": cmd_lessons,
        "check": cmd_check,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(root, args)


if __name__ == "__main__":
    sys.exit(main())
