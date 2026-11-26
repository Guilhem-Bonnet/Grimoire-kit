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

from grimoire.core.ci_markers import github_ci_workflows
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
            found.append(hit.relative_to(root).as_posix())
            break
    return tuple(found)


#: The MCP server the kit writes into host configs itself (``hosts/collect.py``).
_KIT_MCP_SERVER = "grimoire"


def _foreign_mcp_configs(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """MCP config files that declare a server other than the kit's own.

    ``grimoire up`` writes ``.mcp.json`` with its own ``grimoire`` server; that
    file alone says nothing about the project's tool surface, so it must not
    trigger the tool-mediation suggestion. A file that cannot be parsed is kept
    (it is not provably the kit's).
    """
    import json

    found: list[str] = []
    for rel in paths:
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append(rel)
            continue
        servers: dict[str, Any] = {}
        if isinstance(data, dict):
            for key in ("mcpServers", "servers"):
                section = data.get(key)
                if isinstance(section, dict):
                    servers.update(section)
        if any(name != _KIT_MCP_SERVER for name in servers):
            found.append(rel)
    return tuple(found)


def _hook_commands(node: Any) -> list[str]:
    """Every ``command`` string found anywhere under a parsed hooks section."""
    if isinstance(node, dict):
        found = [v for k, v in node.items() if k == "command" and isinstance(v, str)]
        for value in node.values():
            found.extend(_hook_commands(value))
        return found
    if isinstance(node, list):
        return [c for item in node for c in _hook_commands(item)]
    return []


def _foreign_hook_surfaces(root: Path) -> tuple[str, ...]:
    """Hook and skill surfaces that are not the kit's own.

    ``grimoire up`` writes its hooks into ``.claude/settings.json`` and
    ``.github/hooks/``, and ships ``grimoire-*`` skills; finding them says
    nothing about the project's own control surfaces. Only a hook that does not
    call the kit, or a skill the kit did not ship, counts. An unparseable
    settings file is kept (not provably the kit's).
    """
    import json

    found: list[str] = []
    settings = root / ".claude" / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            commands = _hook_commands(data.get("hooks") if isinstance(data, dict) else None)
            foreign = any("grimoire" not in c for c in commands)
        except (OSError, ValueError):
            foreign = True
        if foreign:
            found.append(".claude/settings.json")
    for hook in sorted((root / ".github" / "hooks").glob("*")):
        try:
            foreign_hook = "grimoire" not in hook.read_text(encoding="utf-8", errors="replace")
        except OSError:
            foreign_hook = True
        if foreign_hook:
            found.append(hook.relative_to(root).as_posix())
            break
    for skill in sorted((root / ".github" / "skills").glob("*")):
        if not skill.name.startswith("grimoire"):
            found.append(skill.relative_to(root).as_posix())
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
    known = {
        str(n.get("id"))
        for n in (catalog.get("needs") or [])
        if isinstance(n, dict)
    }
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

    ci = github_ci_workflows(root)[:1]
    containers = _has_any(root, ("Dockerfile", "docker-compose*.yml", "compose*.yaml"))
    if ci and containers:
        raw.append(
            NeedSuggestion(
                "production-release-gating",
                "CI + conteneurs — un flow de release mérite des gates de production",
                ci + containers,
            )
        )

    hook_surfaces = _foreign_hook_surfaces(root)
    if hook_surfaces:
        raw.append(
            NeedSuggestion(
                "hooks-skills-governance",
                "hooks/skills présents — les gouverner évite la dérive des surfaces de contrôle",
                hook_surfaces,
            )
        )

    mcp = _foreign_mcp_configs(root, (".mcp.json", "mcp.json", ".vscode/mcp.json"))
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
