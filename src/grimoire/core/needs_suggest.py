"""Suggestions de needs pilotées par le projet réel (brique B3).

``grimoire init``/``up`` détectent déjà la stack (scanner) et le standard sait
résoudre des needs en profil + patterns (needs-catalog). Ce module fait le
**pont** : des signaux déterministes du projet (stacks détectées, CI,
conteneurs, surfaces d'agents/hooks, corpus documentaire, configs MCP) vers des
suggestions de needs **du catalogue** — jamais un id inventé — avec la raison
et la preuve de chaque suggestion.

Le fallback est ``solo-prototyping`` : ne rien suggérer serait pire que
suggérer le point d'entrée recommandé.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.scanner import ScanResult


@dataclass(frozen=True, slots=True)
class NeedSuggestion:
    """Une suggestion : need du catalogue + pourquoi + preuve."""

    need_id: str
    reason: str
    evidence: tuple[str, ...]


def _has_any(root: Path, patterns: tuple[str, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for pattern in patterns:
        for hit in root.glob(pattern):
            found.append(str(hit.relative_to(root)))
            break
    return tuple(found)


def _docs_corpus(root: Path) -> tuple[str, ...]:
    docs = root / "docs"
    if docs.is_dir() and any(docs.rglob("*.md")):
        return ("docs/",)
    md_files = [p.name for p in root.glob("*.md")][:6]
    return tuple(md_files) if len(md_files) >= 4 else ()


def suggest_needs(
    scan: ScanResult, catalog: dict[str, Any]
) -> list[NeedSuggestion]:
    """Suggestions déterministes, validées contre le needs-catalog."""
    known = {str(n.get("id")) for n in catalog.get("needs", [])}
    root = scan.root
    raw: list[NeedSuggestion] = []

    docs = _docs_corpus(root)
    if docs:
        raw.append(
            NeedSuggestion(
                "semantic-memory-rag",
                "corpus documentaire détecté — la mémoire sémantique le rend interrogeable",
                docs,
            )
        )

    ci = _has_any(root, (".github/workflows/*.yml", ".github/workflows/*.yaml"))
    containers = _has_any(root, ("Dockerfile", "docker-compose*.yml", "compose*.yaml"))
    if ci and containers:
        raw.append(
            NeedSuggestion(
                "production-release-gating",
                "CI + conteneurs — un flow de release mérite des gates de production",
                ci + containers,
            )
        )

    hook_surfaces = _has_any(
        root, (".github/hooks/*", ".claude/settings.json", ".github/skills/*")
    )
    if hook_surfaces:
        raw.append(
            NeedSuggestion(
                "hooks-skills-governance",
                "hooks/skills présents — les gouverner évite la dérive des surfaces de contrôle",
                hook_surfaces,
            )
        )

    mcp = _has_any(root, (".mcp.json", "mcp.json", ".vscode/mcp.json"))
    if mcp:
        raw.append(
            NeedSuggestion(
                "tool-mediation-security",
                "configuration MCP détectée — médiation des appels outils (menaces agentiques)",
                mcp,
            )
        )

    agents = _has_any(root, (".github/agents/*.agent.md",))
    if agents:
        raw.append(
            NeedSuggestion(
                "multi-agent-orchestration",
                "agents déclarés — orchestration avec handoff/escalade",
                agents,
            )
        )

    if len(scan.stacks) >= 3:
        raw.append(
            NeedSuggestion(
                "knowledge-graph",
                f"{len(scan.stacks)} stacks détectées — un graphe de code vérifiable aide à s'y retrouver",
                tuple(s.name for s in scan.stacks),
            )
        )

    suggestions = [s for s in raw if s.need_id in known]
    if not suggestions:
        # Projet vierge : cadrer avant de foncer, puis le point d'entrée.
        if "project-discovery" in known:
            suggestions.append(
                NeedSuggestion(
                    "project-discovery",
                    "projet vierge — cadrez avant de construire "
                    "(grimoire cadrage : brief → exigences → cahier des charges)",
                    (scan.project_type,),
                )
            )
        if "solo-prototyping" in known:
            suggestions.append(
                NeedSuggestion(
                    "solo-prototyping",
                    "aucun signal particulier — le point d'entrée recommandé",
                    (scan.project_type,),
                )
            )
    return suggestions
