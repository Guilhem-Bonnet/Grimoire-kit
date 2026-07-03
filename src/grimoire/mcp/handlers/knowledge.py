"""MCP tools — project knowledge, status, config, and memory tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError
from grimoire.core.project_layout import detect_project_layout
from grimoire.mcp._helpers import (
    _dump_json,
    _error,
    _find_kit_root,
    _parse_csv_or_lines,
    _relative_display_path,
)
from grimoire.mcp._instance import (
    _DEFAULT_REPO_KNOWLEDGE_SCOPES,
    _MAX_KNOWLEDGE_FILE_BYTES,
    _REPO_KNOWLEDGE_SCOPES,
    mcp,
)

# ── Knowledge scope helpers ───────────────────────────────────────────────────

def _parse_knowledge_scopes(scopes: str) -> tuple[list[str], list[str]]:
    """Resolve scope names while preserving user ordering."""
    requested = [item.lower() for item in _parse_csv_or_lines(scopes)]
    if not requested:
        requested = list(_DEFAULT_REPO_KNOWLEDGE_SCOPES)
    if "all" in requested:
        requested = list(_REPO_KNOWLEDGE_SCOPES)

    resolved: list[str] = []
    invalid: list[str] = []
    for item in requested:
        if item in _REPO_KNOWLEDGE_SCOPES and item not in resolved:
            resolved.append(item)
        elif item not in _REPO_KNOWLEDGE_SCOPES and item not in invalid:
            invalid.append(item)
    return resolved, invalid


def _knowledge_scope_files(project_root: Path, scopes: list[str]) -> list[tuple[str, Path, int]]:
    """Enumerate searchable files for curated knowledge scopes."""
    files: list[tuple[str, Path, int]] = []
    seen: set[str] = set()
    for scope_name in scopes:
        scope = _REPO_KNOWLEDGE_SCOPES[scope_name]
        for pattern in scope.patterns:
            for candidate in project_root.glob(pattern):
                if not candidate.is_file():
                    continue
                try:
                    if candidate.stat().st_size > _MAX_KNOWLEDGE_FILE_BYTES:
                        continue
                except OSError:
                    continue
                relative = _relative_display_path(candidate, base=project_root)
                if relative in seen:
                    continue
                seen.add(relative)
                files.append((scope_name, candidate, scope.weight))
    return files


def _search_knowledge_file(
    path: Path,
    *,
    project_root: Path,
    scope_name: str,
    scope_weight: int,
    query: str,
    tokens: list[str],
) -> list[dict[str, Any]]:
    """Return ranked line-level matches for a curated knowledge file."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    relative = _relative_display_path(path, base=project_root)
    path_lower = relative.lower()
    path_score = 2 if query in path_lower else 0
    matches: list[dict[str, Any]] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        exact = query in lowered
        token_hits = {token for token in tokens if token in lowered}
        if not exact and not token_hits:
            continue

        snippet = text[:240]
        matches.append(
            {
                "path": relative,
                "scope": scope_name,
                "line": line_number,
                "text": snippet,
                "snippet": snippet,
                "score": scope_weight + path_score + len(token_hits) * 3 + (5 if exact else 0),
            }
        )
        if len(matches) >= 3:
            break

    return matches


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def grimoire_project_context(project_path: str = ".") -> str:
    """Return the full project context (parsed project-context.yaml) as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    try:
        path = Path(project_path).resolve()
        config_file = path / "project-context.yaml"
        cfg = GrimoireConfig.from_yaml(config_file) if config_file.is_file() else GrimoireConfig.find_and_load(path)
        return json.dumps({
            "project": {
                "name": cfg.project.name,
                "type": cfg.project.type,
                "description": cfg.project.description,
                "stack": list(cfg.project.stack),
                "repos": [{"name": r.name, "path": r.path, "branch": r.default_branch} for r in cfg.project.repos],
            },
            "user": {
                "name": cfg.user.name,
                "language": cfg.user.language,
                "skill_level": cfg.user.skill_level,
            },
            "memory": {"backend": cfg.memory.backend},
            "agents": {
                "archetype": cfg.agents.archetype,
                "custom_agents": list(cfg.agents.custom_agents),
            },
            "grimoire_kit_version": __version__,
        }, indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_status(project_path: str = ".") -> str:
    """Return project health status as JSON — config validity, structure, agents.

    Args:
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    layout = detect_project_layout(target)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    config_path = target / "project-context.yaml"
    check("config_exists", config_path.is_file())

    cfg = None
    if config_path.is_file():
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
            check("config_valid", True, f"project: {cfg.project.name}")
        except GrimoireConfigError as exc:
            check("config_valid", False, str(exc))

    for d in layout.required_dirs:
        check(f"dir_{d}", (target / d).is_dir())

    passed = sum(1 for c in checks if c["ok"])
    return json.dumps({
        "project_root": str(target),
        "layout": layout.name,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "healthy": passed == len(checks),
        "grimoire_kit_version": __version__,
    }, indent=2)


@mcp.tool()
def grimoire_agent_list(project_path: str = ".") -> str:
    """List all agents available in the project's archetype.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.registry.agents import AgentRegistry

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})

    kit_root = _find_kit_root(target)
    if not kit_root:
        return json.dumps({"error": "Cannot find archetypes/ directory", "agents": []})

    registry = AgentRegistry(kit_root)
    archetype = cfg.agents.archetype
    try:
        dna = registry.get_dna(archetype)
        agents = [
            {
                "id": a.id,
                "path": str(a.path),
                "required": a.required,
                "exists": a.exists,
                "description": a.description,
            }
            for a in dna.agents
        ]
        return json.dumps({
            "archetype": archetype,
            "archetype_name": dna.name,
            "agents": agents,
            "total": len(agents),
        }, indent=2)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc), "agents": []})


@mcp.tool()
def grimoire_harmony_check(project_path: str = ".") -> str:
    """Run architecture harmony check and return score + dissonances.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools.harmony_check import HarmonyCheck

    target = Path(project_path).resolve()
    hc = HarmonyCheck(target)
    result = hc.run()
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


@mcp.tool()
def grimoire_config(project_path: str = ".") -> str:
    """Return the raw parsed project-context.yaml as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools._common import load_yaml

    target = Path(project_path).resolve()
    config_file = target / "project-context.yaml"
    if not config_file.is_file():
        return json.dumps({"error": f"No project-context.yaml found at {target}"})
    try:
        raw = load_yaml(config_file)
        return json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_memory_store(text: str, user_id: str = "", project_path: str = ".") -> str:
    """Store a memory entry in the project's configured memory backend.

    Args:
        text: The text to remember.
        user_id: Optional user ID to scope the memory.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg)
        entry = mgr.store(text, user_id=user_id)
        return json.dumps(entry.to_dict(), indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_memory_search(query: str, user_id: str = "", limit: int = 5, project_path: str = ".") -> str:
    """Search project memories by keyword or semantic similarity.

    Args:
        query: Search query text.
        user_id: Optional user ID to filter results.
        limit: Maximum number of results (default: 5).
        project_path: Path to project root (default: current directory).
    """
    from grimoire.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg)
        entries = mgr.search(query, user_id=user_id, limit=limit)
        return json.dumps({
            "query": query,
            "results": [e.to_dict() for e in entries],
            "count": len(entries),
        }, indent=2, ensure_ascii=False)
    except GrimoireError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_add_agent(agent_id: str, project_path: str = ".") -> str:
    """Add a custom agent to the project configuration.

    Args:
        agent_id: The agent identifier to add.
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        return json.dumps({"error": "No project-context.yaml found"})

    try:
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.preserve_quotes = True
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.load(fh)

        agents = data.get("agents") or {}
        custom: list[str] = agents.get("custom_agents") or []

        if agent_id in custom:
            return json.dumps({"status": "already_present", "agent_id": agent_id})

        custom.append(agent_id)
        agents["custom_agents"] = custom
        data["agents"] = agents

        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh)

        return json.dumps({"status": "added", "agent_id": agent_id})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def grimoire_repo_knowledge_search(
    query: str,
    project_path: str = ".",
    scopes: str = "docs,plans,skills,instructions,runtime",
    limit: int = 10,
) -> str:
    """Search curated repository knowledge scopes and return ranked snippets."""
    target = Path(project_path).resolve()
    normalized_query = query.strip().lower()
    if not normalized_query:
        return _error(
            "Query must not be empty",
            tool="grimoire_repo_knowledge_search",
            project_root=str(target),
        )

    requested_scopes, invalid_scopes = _parse_knowledge_scopes(scopes)
    if invalid_scopes:
        return _error(
            "Unknown knowledge scopes",
            tool="grimoire_repo_knowledge_search",
            project_root=str(target),
            invalid_scopes=invalid_scopes,
            available_scopes=sorted(_REPO_KNOWLEDGE_SCOPES),
        )

    tokens = re.findall(r"[a-z0-9]{2,}", normalized_query)
    max_results = max(1, min(limit, 100))
    matches: list[dict[str, Any]] = []
    searchable_files = _knowledge_scope_files(target, requested_scopes)

    for scope_name, file_path, weight in searchable_files:
        matches.extend(
            _search_knowledge_file(
                file_path,
                project_root=target,
                scope_name=scope_name,
                scope_weight=weight,
                query=normalized_query,
                tokens=tokens,
            )
        )

    matches.sort(key=lambda item: (-item["score"], item["path"], item["line"]))
    sliced_matches = matches[:max_results]

    return _dump_json(
        {
            "tool": "grimoire_repo_knowledge_search",
            "project_root": str(target),
            "query": query,
            "scopes": requested_scopes,
            "searched_files": len(searchable_files),
            "count": len(sliced_matches),
            "truncated": len(matches) > max_results,
            "results": sliced_matches,
        }
    )
