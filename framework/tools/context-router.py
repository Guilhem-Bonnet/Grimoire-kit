#!/usr/bin/env python3
"""
context-router.py — Routeur de contexte intelligent BMAD.
============================================================

Analyse les fichiers qu'un agent DOIT charger, DEVRAIT charger, et PEUT
ignorer — en fonction de la tâche, du modèle LLM, et du budget tokens.

Remplace le chargement aveugle par un chargement sémantique priorisé :
  - P0 ALWAYS : persona, agent-base, shared-context
  - P1 SESSION : decisions-log, learnings récents, failure-museum
  - P2 TASK : fichiers liés à la story/tâche courante
  - P3 LAZY : archives, repo-map, knowledge-digest
  - P4 ON-REQUEST : fichiers volumineux explicitement demandés

Produit un plan de chargement avec estimation de tokens et recommandations.

Usage :
  python3 context-router.py --project-root . plan --agent atlas
  python3 context-router.py --project-root . plan --agent forge --task "TF apply"
  python3 context-router.py --project-root . plan --agent forge --model gpt-4o
  python3 context-router.py --project-root . budget                 # Vue globale
  python3 context-router.py --project-root . budget --detail
  python3 context-router.py --project-root . suggest --agent atlas  # Recommandations d'optimisation
  python3 context-router.py --project-root . relevance --agent forge --query "terraform drift"

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
import logging

_log = logging.getLogger("grimoire.context_router")

# ── Constantes ────────────────────────────────────────────────────────────────

CONTEXT_ROUTER_VERSION = "1.0.0"

# Fenêtres de contexte par modèle (tokens)
MODEL_WINDOWS: dict[str, int] = {
    "claude-opus-4":       200_000,
    "claude-sonnet-4":     200_000,
    "claude-haiku":        200_000,
    "gpt-4o":              128_000,
    "gpt-4o-mini":         128_000,
    "o3":                  200_000,
    "codex":               192_000,
    "gemini-1.5-pro":    1_000_000,
    "gemini-2.0-flash":  1_000_000,
    "copilot":             200_000,
    "codestral":            32_000,
    "llama3":                8_000,
    "mistral":              32_000,
}

DEFAULT_MODEL = "copilot"

# Estimation : ~4 chars/token pour markdown/code mixé
CHARS_PER_TOKEN = 4

# Seuils
WARNING_THRESHOLD = 0.60   # 60% → avertissement
CRITICAL_THRESHOLD = 0.80  # 80% → suggestions agressives


# ── Priority Levels ──────────────────────────────────────────────────────────

class Priority:
    """Niveaux de priorité de chargement."""
    P0_ALWAYS = 0       # Persona, agent-base, rules
    P1_SESSION = 1      # Shared-context, decisions, learnings
    P2_TASK = 2         # Fichiers liés à la tâche courante
    P3_LAZY = 3         # Archives, repo-map, digests
    P4_ON_REQUEST = 4   # Fichiers volumineux ou rarement utiles

    LABELS = {
        0: "P0-ALWAYS",
        1: "P1-SESSION",
        2: "P2-TASK",
        3: "P3-LAZY",
        4: "P4-ON_REQUEST",
    }


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class FileEntry:
    """Un fichier candidat au chargement."""
    path: str
    priority: int
    estimated_tokens: int = 0
    reason: str = ""
    relevance_score: float = 1.0  # 0.0 à 1.0
    loaded: bool = False

    @property
    def priority_label(self) -> str:
        return Priority.LABELS.get(self.priority, f"P{self.priority}")


@dataclass
class LoadPlan:
    """Plan de chargement calculé."""
    agent: str
    model: str
    model_window: int
    entries: list[FileEntry] = field(default_factory=list)
    total_tokens: int = 0
    loaded_tokens: int = 0
    skipped_tokens: int = 0
    recommendations: list[str] = field(default_factory=list)

    @property
    def usage_pct(self) -> float:
        return (self.loaded_tokens / self.model_window * 100) if self.model_window else 0

    @property
    def status(self) -> str:
        pct = self.usage_pct
        if pct >= CRITICAL_THRESHOLD * 100:
            return "🔴 CRITICAL"
        elif pct >= WARNING_THRESHOLD * 100:
            return "🟡 WARNING"
        return "🟢 OK"


@dataclass
class BudgetReport:
    """Rapport de budget pour tous les agents."""
    plans: list[LoadPlan] = field(default_factory=list)
    overbudget_count: int = 0


# ── File Discovery ───────────────────────────────────────────────────────────

def estimate_tokens(filepath: Path) -> int:
    """Estime le nombre de tokens d'un fichier."""
    try:
        size = filepath.stat().st_size
        return size // CHARS_PER_TOKEN
    except (OSError, FileNotFoundError):
        return 0


def find_agent_files(project_root: Path) -> list[Path]:
    """Trouve tous les fichiers agents dans le projet."""
    agents = []
    custom_dir = project_root / "_bmad" / "_config" / "custom"
    if not custom_dir.exists():
        # Fallback : chercher dans les patterns courants
        for pattern in ["_bmad/_config/agents/*.md", "_bmad/*/agents/*.md"]:
            agents.extend(project_root.glob(pattern))
    else:
        agents.extend(custom_dir.glob("*.md"))
        # Exclure agent-base.md lui-même
        agents = [a for a in agents if a.name != "agent-base.md"]
    return sorted(agents)


def extract_agent_tag(agent_file: Path) -> str:
    """Extrait l'AGENT_TAG d'un fichier agent."""
    try:
        content = agent_file.read_text(encoding="utf-8")
        # Chercher dans le frontmatter YAML
        m = re.search(r'^name:\s*"?([^"\n]+)"?', content, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except OSError as _exc:
        _log.debug("OSError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    return agent_file.stem


def discover_context_files(project_root: Path, agent_tag: str) -> list[FileEntry]:
    """Découvre et priorise les fichiers de contexte pour un agent."""
    entries: list[FileEntry] = []
    memory_dir = project_root / "_bmad" / "_memory"
    config_dir = project_root / "_bmad" / "_config" / "custom"

    # ── P0 : ALWAYS ──────────────────────────────────────────────
    p0_files = [
        (config_dir / "agent-base.md", "Base protocol"),
    ]
    # L'agent lui-même
    for agent_file in find_agent_files(project_root):
        if extract_agent_tag(agent_file) == agent_tag:
            p0_files.append((agent_file, "Agent persona"))
            break

    for fpath, reason in p0_files:
        if fpath.exists():
            entries.append(FileEntry(
                path=str(fpath.relative_to(project_root)),
                priority=Priority.P0_ALWAYS,
                estimated_tokens=estimate_tokens(fpath),
                reason=reason,
            ))

    # ── P1 : SESSION ─────────────────────────────────────────────
    p1_files = [
        (memory_dir / "shared-context.md", "Project context"),
        (memory_dir / "decisions-log.md", "Decisions history"),
        (memory_dir / "failure-museum.md", "Failure patterns"),
    ]
    # Agent-specific learnings
    learnings_glob = memory_dir / "agent-learnings"
    if learnings_glob.exists():
        for lf in learnings_glob.glob("*.md"):
            if agent_tag.lower() in lf.stem.lower():
                p1_files.append((lf, f"Agent learnings ({lf.stem})"))

    for fpath, reason in p1_files:
        if fpath.exists():
            entries.append(FileEntry(
                path=str(fpath.relative_to(project_root)),
                priority=Priority.P1_SESSION,
                estimated_tokens=estimate_tokens(fpath),
                reason=reason,
            ))

    # ── P2 : TASK (fichiers liés à la tâche) ─────────────────────
    # Détectés dynamiquement via --task, sinon vide
    session_state = memory_dir / "session-state.md"
    if session_state.exists():
        entries.append(FileEntry(
            path=str(session_state.relative_to(project_root)),
            priority=Priority.P2_TASK,
            estimated_tokens=estimate_tokens(session_state),
            reason="Session state (tâches en cours)",
        ))

    # ── P3 : LAZY ────────────────────────────────────────────────
    p3_files = [
        (memory_dir / "knowledge-digest.md", "Knowledge digest"),
        (memory_dir / "network-topology.md", "Network topology"),
        (memory_dir / "dependency-graph.md", "Dependency graph"),
        (memory_dir / "oss-references.md", "OSS references"),
    ]
    for fpath, reason in p3_files:
        if fpath.exists():
            entries.append(FileEntry(
                path=str(fpath.relative_to(project_root)),
                priority=Priority.P3_LAZY,
                estimated_tokens=estimate_tokens(fpath),
                reason=reason,
            ))

    # ── P4 : ON_REQUEST ──────────────────────────────────────────
    archives = memory_dir / "archives"
    if archives.exists():
        for af in sorted(archives.glob("*.md")):
            entries.append(FileEntry(
                path=str(af.relative_to(project_root)),
                priority=Priority.P4_ON_REQUEST,
                estimated_tokens=estimate_tokens(af),
                reason=f"Archive ({af.stem})",
            ))

    return entries


# ── Relevance Scoring ────────────────────────────────────────────────────────

def compute_relevance(entries: list[FileEntry], query: str) -> list[FileEntry]:
    """Score de pertinence simple basé sur les mots-clés de la tâche."""
    if not query:
        return entries

    keywords = set(query.lower().split())
    for entry in entries:
        path_words = set(entry.path.lower().replace("/", " ").replace("-", " ").replace("_", " ").split())
        reason_words = set(entry.reason.lower().split())
        all_words = path_words | reason_words

        if keywords & all_words:
            entry.relevance_score = min(1.0, 0.5 + 0.2 * len(keywords & all_words))
        elif entry.priority <= Priority.P1_SESSION:
            entry.relevance_score = 0.8  # Session files toujours pertinents
        else:
            entry.relevance_score = 0.3

    return entries


# ── Plan Calculation ─────────────────────────────────────────────────────────

def calculate_plan(
    project_root: Path,
    agent_tag: str,
    model: str = DEFAULT_MODEL,
    task_query: str = "",
    max_priority: int = Priority.P2_TASK,
) -> LoadPlan:
    """Calcule le plan de chargement optimal."""
    model_window = MODEL_WINDOWS.get(model, MODEL_WINDOWS[DEFAULT_MODEL])
    entries = discover_context_files(project_root, agent_tag)

    if task_query:
        entries = compute_relevance(entries, task_query)

    # Trier par priorité puis par pertinence décroissante
    entries.sort(key=lambda e: (e.priority, -e.relevance_score))

    plan = LoadPlan(
        agent=agent_tag,
        model=model,
        model_window=model_window,
        entries=entries,
    )

    budget_remaining = int(model_window * CRITICAL_THRESHOLD)
    for entry in entries:
        plan.total_tokens += entry.estimated_tokens
        if entry.priority <= max_priority and budget_remaining >= entry.estimated_tokens:
            entry.loaded = True
            plan.loaded_tokens += entry.estimated_tokens
            budget_remaining -= entry.estimated_tokens
        else:
            plan.skipped_tokens += entry.estimated_tokens

    # Recommandations
    if plan.usage_pct >= CRITICAL_THRESHOLD * 100:
        plan.recommendations.append(
            "🔴 Budget critique — Résumer les fichiers > 30 jours via [THINK]"
        )
        plan.recommendations.append(
            "💡 Passer les P1-SESSION en LAZY si non pertinents pour la tâche actuelle"
        )
    elif plan.usage_pct >= WARNING_THRESHOLD * 100:
        plan.recommendations.append(
            "🟡 Budget élevé — Ne charger les P2/P3 que sur demande explicite"
        )

    # Charge cognitive audit (#98)
    loaded_count = sum(1 for e in entries if e.loaded)
    if loaded_count > 7:
        plan.recommendations.append(
            f"📦 {loaded_count} fichiers chargés — la loi de Miller (7±2) suggère "
            f"de consolider les plus petits en un seul digest"
        )

    return plan


# ── Formatters ───────────────────────────────────────────────────────────────

def format_plan(plan: LoadPlan, detail: bool = False) -> str:
    """Formate un plan de chargement pour affichage."""
    lines = [
        f"📡 Context Router — Plan pour [{plan.agent}]",
        f"   Modèle : {plan.model} ({plan.model_window:,} tokens)",
        f"   Budget utilisé : {plan.loaded_tokens:,} / {plan.model_window:,} "
        f"({plan.usage_pct:.1f}%) {plan.status}",
        "",
    ]

    if detail:
        lines.append("   Fichiers chargés :")
        for e in plan.entries:
            if e.loaded:
                lines.append(
                    f"   {'✅' if e.loaded else '⏸️'} [{e.priority_label}] "
                    f"{e.path} ({e.estimated_tokens:,} tok) — {e.reason}"
                )
        lines.append("")
        skipped = [e for e in plan.entries if not e.loaded]
        if skipped:
            lines.append("   Fichiers différés :")
            for e in skipped:
                lines.append(
                    f"   ⏸️ [{e.priority_label}] "
                    f"{e.path} ({e.estimated_tokens:,} tok) — {e.reason}"
                )
            lines.append("")

    if plan.recommendations:
        lines.append("   Recommandations :")
        for rec in plan.recommendations:
            lines.append(f"   {rec}")
        lines.append("")

    return "\n".join(lines)


def format_budget_report(report: BudgetReport) -> str:
    """Formate un rapport de budget pour tous les agents."""
    lines = [
        "📊 Context Budget Report",
        f"   Agents analysés : {len(report.plans)}",
        f"   Agents en surbudget : {report.overbudget_count}",
        "",
        "   | Agent | Tokens chargés | Budget % | Status |",
        "   |-------|---------------|----------|--------|",
    ]
    for plan in sorted(report.plans, key=lambda p: -p.usage_pct):
        lines.append(
            f"   | {plan.agent:<15} | {plan.loaded_tokens:>13,} | "
            f"{plan.usage_pct:>6.1f}% | {plan.status} |"
        )
    lines.append("")
    return "\n".join(lines)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_plan(args: argparse.Namespace, project_root: Path) -> int:
    """Calcule et affiche le plan de chargement pour un agent."""
    if not args.agent:
        print("❌ --agent requis pour la commande plan", file=sys.stderr)
        return 1

    plan = calculate_plan(
        project_root=project_root,
        agent_tag=args.agent,
        model=args.model,
        task_query=args.task or "",
    )
    print(format_plan(plan, detail=args.detail))

    if args.json:
        result = {
            "agent": plan.agent,
            "model": plan.model,
            "model_window": plan.model_window,
            "loaded_tokens": plan.loaded_tokens,
            "total_tokens": plan.total_tokens,
            "usage_pct": round(plan.usage_pct, 1),
            "status": plan.status,
            "files": [
                {
                    "path": e.path,
                    "priority": e.priority_label,
                    "tokens": e.estimated_tokens,
                    "loaded": e.loaded,
                    "reason": e.reason,
                    "relevance": round(e.relevance_score, 2),
                }
                for e in plan.entries
            ],
            "recommendations": plan.recommendations,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0


def cmd_budget(args: argparse.Namespace, project_root: Path) -> int:
    """Rapport de budget pour tous les agents."""
    agent_files = find_agent_files(project_root)
    if not agent_files:
        print("⚠️ Aucun agent trouvé dans le projet.", file=sys.stderr)
        return 1

    report = BudgetReport()
    for af in agent_files:
        tag = extract_agent_tag(af)
        plan = calculate_plan(project_root, tag, model=args.model)
        report.plans.append(plan)
        if plan.usage_pct >= WARNING_THRESHOLD * 100:
            report.overbudget_count += 1

    print(format_budget_report(report))

    if args.detail:
        for plan in report.plans:
            print(format_plan(plan, detail=True))

    return 0


def cmd_suggest(args: argparse.Namespace, project_root: Path) -> int:
    """Suggestions d'optimisation pour un agent."""
    if not args.agent:
        print("❌ --agent requis pour la commande suggest", file=sys.stderr)
        return 1

    plan = calculate_plan(project_root, args.agent, model=args.model)

    print(f"💡 Optimisations pour [{args.agent}] ({plan.status})\n")

    suggestions = []

    # Fichiers volumineux
    big_files = [e for e in plan.entries if e.estimated_tokens > 3000 and e.loaded]
    if big_files:
        suggestions.append("📏 Fichiers volumineux (>3000 tokens) chargés automatiquement :")
        for bf in sorted(big_files, key=lambda f: -f.estimated_tokens):
            suggestions.append(
                f"   - {bf.path} ({bf.estimated_tokens:,} tok) → "
                f"envisager un résumé ou passage en P3-LAZY"
            )

    # Duplication de contexte
    loaded = [e for e in plan.entries if e.loaded]
    if len(loaded) > 5:
        suggestions.append(
            f"\n📦 {len(loaded)} fichiers chargés — consolider les petits fichiers "
            f"(<500 tok) en un seul digest"
        )

    # P1 files rarement modifiés
    for e in plan.entries:
        if e.priority == Priority.P1_SESSION and e.estimated_tokens < 100:
            suggestions.append(
                f"\n🔍 {e.path} fait < 100 tokens — fusionner avec shared-context"
            )

    if not suggestions:
        suggestions.append("✅ Configuration optimale — pas de suggestion.")

    print("\n".join(suggestions))
    return 0


def cmd_relevance(args: argparse.Namespace, project_root: Path) -> int:
    """Score de pertinence pour une requête donnée."""
    if not args.agent or not args.query:
        print("❌ --agent et --query requis pour relevance", file=sys.stderr)
        return 1

    entries = discover_context_files(project_root, args.agent)
    entries = compute_relevance(entries, args.query)
    entries.sort(key=lambda e: -e.relevance_score)

    print(f"🎯 Pertinence pour [{args.agent}] — requête : \"{args.query}\"\n")
    for e in entries:
        bar = "█" * int(e.relevance_score * 10) + "░" * (10 - int(e.relevance_score * 10))
        print(f"   {bar} {e.relevance_score:.2f} [{e.priority_label}] {e.path}")

    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Context Router — Routage intelligent du contexte agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=str, default=".",
        help="Racine du projet BMAD (défaut: .)",
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        choices=list(MODEL_WINDOWS.keys()),
        help=f"Modèle LLM cible (défaut: {DEFAULT_MODEL})",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # plan
    p_plan = sub.add_parser("plan", help="Plan de chargement pour un agent")
    p_plan.add_argument("--agent", type=str, help="Tag de l'agent")
    p_plan.add_argument("--task", type=str, help="Description de la tâche (scoring pertinence)")
    p_plan.add_argument("--detail", action="store_true", help="Détail fichier par fichier")

    # budget
    p_budget = sub.add_parser("budget", help="Vue budget de tous les agents")
    p_budget.add_argument("--detail", action="store_true", help="Afficher les plans détaillés")

    # suggest
    p_suggest = sub.add_parser("suggest", help="Suggestions d'optimisation")
    p_suggest.add_argument("--agent", type=str, help="Tag de l'agent")

    # relevance
    p_rel = sub.add_parser("relevance", help="Score de pertinence")
    p_rel.add_argument("--agent", type=str, help="Tag de l'agent")
    p_rel.add_argument("--query", type=str, help="Requête de pertinence")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    project_root = Path(args.project_root).resolve()
    if not (project_root / "_bmad").exists():
        print(f"❌ Pas de dossier _bmad dans {project_root}", file=sys.stderr)
        return 1

    commands = {
        "plan": cmd_plan,
        "budget": cmd_budget,
        "suggest": cmd_suggest,
        "relevance": cmd_relevance,
    }

    return commands[args.command](args, project_root)


if __name__ == "__main__":
    sys.exit(main())
