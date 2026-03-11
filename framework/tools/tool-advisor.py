#!/usr/bin/env python3
"""
tool-advisor.py — Recommandation proactive d'outils Grimoire.
============================================================

Analyse le contexte projet (fichiers récents, erreurs, patterns de travail)
et suggère les outils Grimoire les plus pertinents. Aide les utilisateurs à
découvrir des outils qu'ils n'utilisent pas encore.

Catégories de conseil :
  - context   : outils pertinents pour le contexte actuel (fichiers récents)
  - unused    : outils disponibles mais jamais invoqués
  - workflow  : séquences d'outils recommandées pour un workflow donné

Usage :
  python3 tool-advisor.py --project-root . suggest
  python3 tool-advisor.py --project-root . suggest --context "écrire des tests"
  python3 tool-advisor.py --project-root . unused
  python3 tool-advisor.py --project-root . workflows
  python3 tool-advisor.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

ADVISOR_VERSION = "1.0.0"
USAGE_LOG = "_grimoire-output/.router-stats.jsonl"

# Mappage contexte → outils pertinents
CONTEXT_RULES: list[dict] = [
    {
        "id": "CTX-001",
        "pattern": r"(?:test|tdd|pytest|coverage|qa|qualité)",
        "tools": ["preflight-check.py", "cc-verify.sh", "schema-validator.py"],
        "reason": "Contexte tests/QA — outils de vérification recommandés",
    },
    {
        "id": "CTX-002",
        "pattern": r"(?:mémoire|memory|consolidat|dream|oubli|stale)",
        "tools": ["dream.py", "memory-lint.py", "context-summarizer.py"],
        "reason": "Contexte mémoire — outils de maintenance mémoire",
    },
    {
        "id": "CTX-003",
        "pattern": r"(?:architect|design|infra|structure|refactor)",
        "tools": ["digital-twin.py", "project-graph.py", "desire-paths.py"],
        "reason": "Contexte architecture — outils d'analyse structurelle",
    },
    {
        "id": "CTX-004",
        "pattern": r"(?:debug|erreur|error|crash|fix|bug|broken)",
        "tools": ["self-healing.py", "early-warning.py", "failure-museum.py"],
        "reason": "Contexte debugging — outils de diagnostic",
    },
    {
        "id": "CTX-005",
        "pattern": r"(?:release|deploy|push|version|changelog|tag)",
        "tools": ["preflight-check.py", "cc-verify.sh", "sil-collect.sh"],
        "reason": "Contexte release — outils de validation pre-push",
    },
    {
        "id": "CTX-006",
        "pattern": r"(?:perf|optimi|token|budget|coût|cost|lent|slow)",
        "tools": ["token-budget.py", "llm-router.py", "synapse-dashboard.py"],
        "reason": "Contexte performance — outils d'optimisation",
    },
    {
        "id": "CTX-007",
        "pattern": r"(?:agent|persona|workflow|orchestrat|dispatch)",
        "tools": ["orchestrator.py", "tool-registry.py", "tool-resolver.py", "grimoire-mcp-tools.py"],
        "reason": "Contexte agents/workflows — outils d'orchestration et résolution",
    },
    {
        "id": "CTX-008",
        "pattern": r"(?:innovate|brainstorm|idée|idea|experiment|prototype)",
        "tools": ["incubator.py", "quantum-branch.py", "rd-engine.py"],
        "reason": "Contexte innovation — outils de R&D",
    },
    {
        "id": "CTX-009",
        "pattern": r"(?:santé|health|dashboard|overview|status|monitoring)",
        "tools": ["synapse-dashboard.py", "fitness-tracker.py", "early-warning.py"],
        "reason": "Contexte monitoring — outils de surveillance",
    },
    {
        "id": "CTX-010",
        "pattern": r"(?:session|bootstrap|resume|context|chain)",
        "tools": ["session-state.py", "shared-context.py", "context-summarizer.py"],
        "reason": "Contexte session — outils de gestion de contexte",
    },
    {
        "id": "CTX-011",
        "pattern": r"(?:outil|tool|install|discover|provision|mcp|résoud|resolver|capability)",
        "tools": ["tool-resolver.py", "tool-registry.py", "mcp-proxy.py"],
        "reason": "Contexte outillage — découverte et provision d'outils",
    },
]

# Séquences de workflow recommandées
WORKFLOWS: list[dict] = [
    {
        "name": "🔍 Diagnostic complet",
        "description": "Audit global du projet",
        "steps": [
            "synapse-dashboard.py",
            "early-warning.py",
            "fitness-tracker.py check",
            "memory-lint.py",
            "preflight-check.py",
        ],
    },
    {
        "name": "🚀 Pre-release",
        "description": "Validation avant push",
        "steps": [
            "cc-verify.sh",
            "preflight-check.py --strict",
            "schema-validator.py validate",
            "sil-collect.sh",
        ],
    },
    {
        "name": "🧠 Maintenance mémoire",
        "description": "Nettoyage et consolidation de la mémoire",
        "steps": [
            "dream.py --quick",
            "memory-lint.py",
            "stigmergy.py evaporate",
            "rag-indexer.py index --all",
        ],
    },
    {
        "name": "📊 Bilan de santé",
        "description": "Score de fitness + tendance",
        "steps": [
            "fitness-tracker.py check",
            "antifragile-score.py",
            "early-warning.py",
        ],
    },
    {
        "name": "🔧 Auto-réparation",
        "description": "Détecter et réparer les problèmes",
        "steps": [
            "self-healing.py status",
            "self-healing.py diagnose --error '<msg>'",
            "self-healing.py heal --error '<msg>'",
        ],
    },
]


# ── Data Model ────────────────────────────────────────────────────────────────

@dataclass
class ToolSuggestion:
    """Suggestion d'outil Grimoire."""
    tool: str
    reason: str
    priority: str = "medium"  # high | medium | low
    context_rule: str = ""


@dataclass
class AdvisorReport:
    """Rapport du tool advisor."""
    timestamp: str
    suggestions: list[ToolSuggestion] = field(default_factory=list)
    unused_tools: list[str] = field(default_factory=list)
    workflows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "suggestions": [asdict(s) for s in self.suggestions],
            "unused_tools": self.unused_tools,
            "workflows": self.workflows,
        }


# ── Analyse ──────────────────────────────────────────────────────────────────

def _discover_tools(project_root: Path) -> set[str]:
    """Découvre les outils disponibles dans framework/tools/."""
    tools_dir = project_root / "framework" / "tools"
    if not tools_dir.exists():
        return set()
    return {
        f.name for f in tools_dir.iterdir()
        if f.is_file() and f.suffix in (".py", ".sh")
        and not f.name.startswith("_")
    }


def _load_usage_stats(project_root: Path) -> dict[str, int]:
    """Charge les statistiques d'utilisation depuis le router stats."""
    stats_path = project_root / USAGE_LOG
    if not stats_path.exists():
        return {}

    usage: dict[str, int] = {}
    try:
        for line in stats_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                tool = entry.get("tool", "")
                if tool:
                    usage[tool] = usage.get(tool, 0) + 1
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return usage


def suggest_for_context(context: str) -> list[ToolSuggestion]:
    """Suggère des outils basés sur le contexte textuel."""
    suggestions: list[ToolSuggestion] = []
    seen_tools: set[str] = set()

    for rule in CONTEXT_RULES:
        if re.search(rule["pattern"], context, re.IGNORECASE):
            for tool in rule["tools"]:
                if tool not in seen_tools:
                    seen_tools.add(tool)
                    suggestions.append(ToolSuggestion(
                        tool=tool,
                        reason=rule["reason"],
                        priority="high",
                        context_rule=rule["id"],
                    ))

    return suggestions


def find_unused_tools(project_root: Path) -> list[str]:
    """Trouve les outils disponibles mais jamais utilisés."""
    available = _discover_tools(project_root)
    usage = _load_usage_stats(project_root)

    used_names = set()
    for tool_key in usage:
        # Normalize tool names (may be stored with or without path)
        name = tool_key.rsplit("/", 1)[-1] if "/" in tool_key else tool_key
        used_names.add(name)

    unused = sorted(available - used_names)
    return unused


def build_advice(project_root: Path, context: str = "") -> AdvisorReport:
    """Construit un rapport de conseils complet."""
    timestamp = datetime.now().isoformat()

    suggestions = suggest_for_context(context) if context else []
    unused = find_unused_tools(project_root)

    # Si pas de contexte spécifique, suggérer les outils les plus utiles
    if not suggestions:
        core_tools = [
            ("synapse-dashboard.py", "Vue d'ensemble de l'infrastructure Grimoire"),
            ("fitness-tracker.py", "Score de santé global du projet"),
            ("early-warning.py", "Détection précoce des problèmes"),
        ]
        for tool, reason in core_tools:
            suggestions.append(ToolSuggestion(
                tool=tool, reason=reason, priority="low",
            ))

    return AdvisorReport(
        timestamp=timestamp,
        suggestions=suggestions,
        unused_tools=unused,
        workflows=WORKFLOWS,
    )


# ── MCP Interface ────────────────────────────────────────────────────────────

def mcp_tool_advisor(
    project_root: str,
    action: str = "suggest",
    context: str = "",
) -> dict:
    """MCP tool ``bmad_tool_advisor`` — conseil proactif d'outils.

    Args:
        project_root: Racine du projet.
        action: suggest | unused | workflows.
        context: Contexte textuel pour affiner les suggestions.

    Returns:
        dict avec les suggestions ou infos demandées.
    """
    root = Path(project_root)

    if action == "suggest":
        report = build_advice(root, context)
        return {
            "status": "ok",
            "suggestions": [asdict(s) for s in report.suggestions],
            "suggestion_count": len(report.suggestions),
        }

    if action == "unused":
        unused = find_unused_tools(root)
        return {
            "status": "ok",
            "unused_tools": unused,
            "count": len(unused),
        }

    if action == "workflows":
        return {
            "status": "ok",
            "workflows": WORKFLOWS,
        }

    return {"status": "error", "error": f"Unknown action: {action}"}


# ── Display ──────────────────────────────────────────────────────────────────

def render_suggestions(report: AdvisorReport) -> str:
    """Rendu texte des suggestions."""
    lines = [
        "\n💡 Tool Advisor — Suggestions",
        "=" * 50,
        "",
    ]

    if report.suggestions:
        lines.append("📋 Outils recommandés :")
        for s in report.suggestions:
            icon = "🔴" if s.priority == "high" else "🟡" if s.priority == "medium" else "🟢"
            lines.append(f"   {icon} {s.tool}")
            lines.append(f"      {s.reason}")
            lines.append("")
    else:
        lines.append("   ✅ Aucune suggestion spécifique")
        lines.append("")

    if report.unused_tools:
        lines.append(f"🔇 Outils non utilisés ({len(report.unused_tools)}) :")
        for tool in report.unused_tools[:10]:
            lines.append(f"   • {tool}")
        if len(report.unused_tools) > 10:
            lines.append(f"   ... et {len(report.unused_tools) - 10} autres")
        lines.append("")

    return "\n".join(lines)


def render_workflows() -> str:
    """Rendu texte des workflows recommandés."""
    lines = [
        "\n🔄 Workflows Grimoire recommandés",
        "=" * 50,
        "",
    ]

    for wf in WORKFLOWS:
        lines.append(f"   {wf['name']}")
        lines.append(f"   {wf['description']}")
        for i, step in enumerate(wf["steps"], 1):
            lines.append(f"      {i}. {step}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tool-advisor",
        description="Grimoire Tool Advisor — Recommandation proactive d'outils",
    )
    p.add_argument("--project-root", type=Path, default=Path("."))
    p.add_argument("--json", action="store_true", help="Sortie JSON")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {ADVISOR_VERSION}")

    sub = p.add_subparsers(dest="command")
    suggest = sub.add_parser("suggest", help="Suggérer des outils")
    suggest.add_argument("--context", type=str, default="",
                         help="Contexte textuel pour affiner les suggestions")
    sub.add_parser("unused", help="Lister les outils non utilisés")
    sub.add_parser("workflows", help="Afficher les workflows recommandés")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    command = args.command or "suggest"

    if command == "suggest":
        context = getattr(args, "context", "")
        report = build_advice(root, context)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_suggestions(report))
        return 0

    if command == "unused":
        unused = find_unused_tools(root)
        if args.json:
            print(json.dumps({"unused_tools": unused}, indent=2, ensure_ascii=False))
        else:
            if unused:
                print(f"\n🔇 Outils non utilisés ({len(unused)}) :")
                for t in unused:
                    print(f"   • {t}")
            else:
                print("\n✅ Tous les outils ont été utilisés au moins une fois.")
        return 0

    if command == "workflows":
        if args.json:
            print(json.dumps({"workflows": WORKFLOWS}, indent=2, ensure_ascii=False))
        else:
            print(render_workflows())
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
