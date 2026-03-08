"""BMAD MCP Server — expose BMAD tools via Model Context Protocol.

Start with::

    python -m bmad.mcp.server

Or configure in your MCP client (Claude Desktop, VS Code, etc.)::

    {
      "mcpServers": {
        "bmad": {
          "command": "python",
          "args": ["-m", "bmad.mcp.server"],
          "cwd": "/path/to/project"
        }
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bmad.__version__ import __version__
from bmad.core.config import BmadConfig
from bmad.core.exceptions import BmadConfigError, BmadError

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    msg = "MCP SDK not installed. Run: pip install bmad-kit[mcp]"
    raise ImportError(msg) from _exc

mcp = FastMCP(
    name="bmad",
    instructions="BMAD Kit — Composable AI agent platform. "
    "Use these tools to inspect and manage BMAD projects.",
)


def _find_config() -> BmadConfig:
    """Find and load the project config from cwd upward."""
    return BmadConfig.find_and_load()


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def bmad_project_context(project_path: str = ".") -> str:
    """Return the full project context (parsed project-context.yaml) as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    try:
        path = Path(project_path).resolve()
        config_file = path / "project-context.yaml"
        if config_file.is_file():
            cfg = BmadConfig.from_yaml(config_file)
        else:
            cfg = BmadConfig.find_and_load(path)
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
            "bmad_kit_version": __version__,
        }, indent=2, ensure_ascii=False)
    except BmadError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def bmad_status(project_path: str = ".") -> str:
    """Return project health status as JSON — config validity, structure, agents.

    Args:
        project_path: Path to project root (default: current directory).
    """
    target = Path(project_path).resolve()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    config_path = target / "project-context.yaml"
    check("config_exists", config_path.is_file())

    cfg = None
    if config_path.is_file():
        try:
            cfg = BmadConfig.from_yaml(config_path)
            check("config_valid", True, f"project: {cfg.project.name}")
        except BmadConfigError as exc:
            check("config_valid", False, str(exc))

    for d in ("_bmad", "_bmad-output", "_bmad/_memory"):
        check(f"dir_{d}", (target / d).is_dir())

    passed = sum(1 for c in checks if c["ok"])
    return json.dumps({
        "project_root": str(target),
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "healthy": passed == len(checks),
        "bmad_kit_version": __version__,
    }, indent=2)


@mcp.tool()
def bmad_agent_list(project_path: str = ".") -> str:
    """List all agents available in the project's archetype.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from bmad.registry.agents import AgentRegistry

    target = Path(project_path).resolve()
    try:
        cfg = BmadConfig.find_and_load(target)
    except BmadError as exc:
        return json.dumps({"error": str(exc)})

    # Find kit root (where archetypes/ lives)
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
    except BmadError as exc:
        return json.dumps({"error": str(exc), "agents": []})


@mcp.tool()
def bmad_harmony_check(project_path: str = ".") -> str:
    """Run architecture harmony check and return score + dissonances.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from bmad.tools.harmony_check import HarmonyCheck

    target = Path(project_path).resolve()
    hc = HarmonyCheck(target)
    result = hc.run()
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


@mcp.tool()
def bmad_config(project_path: str = ".") -> str:
    """Return the raw parsed project-context.yaml as JSON.

    Args:
        project_path: Path to project root (default: current directory).
    """
    from bmad.tools._common import load_yaml

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
def bmad_memory_store(text: str, user_id: str = "", project_path: str = ".") -> str:
    """Store a memory entry in the project's configured memory backend.

    Args:
        text: The text to remember.
        user_id: Optional user ID to scope the memory.
        project_path: Path to project root (default: current directory).
    """
    from bmad.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = BmadConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg)
        entry = mgr.store(text, user_id=user_id)
        return json.dumps(entry.to_dict(), indent=2, ensure_ascii=False)
    except BmadError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def bmad_memory_search(query: str, user_id: str = "", limit: int = 5, project_path: str = ".") -> str:
    """Search project memories by keyword or semantic similarity.

    Args:
        query: Search query text.
        user_id: Optional user ID to filter results.
        limit: Maximum number of results (default: 5).
        project_path: Path to project root (default: current directory).
    """
    from bmad.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = BmadConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg)
        entries = mgr.search(query, user_id=user_id, limit=limit)
        return json.dumps({
            "query": query,
            "results": [e.to_dict() for e in entries],
            "count": len(entries),
        }, indent=2, ensure_ascii=False)
    except BmadError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def bmad_add_agent(agent_id: str, project_path: str = ".") -> str:
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
        yaml.preserve_quotes = True  # type: ignore[assignment]
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_kit_root(start: Path) -> Path | None:
    """Walk up to find the directory containing archetypes/."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / "archetypes").is_dir():
            return parent
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
