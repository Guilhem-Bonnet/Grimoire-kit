#!/usr/bin/env python3
"""
mcp-proxy.py — MCP Proxy for routing to external MCP servers (D14).
═══════════════════════════════════════════════════════════════════

Routeur MCP qui permet aux agents Grimoire d'accéder à des serveurs MCP
externes (Godot, Figma, Browser, etc.) via une interface unifiée.

Configuration : _grimoire/_config/mcp-servers.yaml
  servers:
    - name: browser
      command: npx @anthropic/mcp-browser
      enabled: true
    - name: godot
      command: godot-mcp
      enabled: false

Modes :
  list    — Lister les serveurs configurés
  status  — Vérifier l'état des serveurs
  config  — Afficher/modifier la configuration

MCP interface :
  mcp_proxy_list() → list of configured servers
  mcp_proxy_status() → connectivity status

Usage :
  python3 mcp-proxy.py --project-root . list
  python3 mcp-proxy.py --project-root . status
  python3 mcp-proxy.py --project-root . config

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

MCP_PROXY_VERSION = "2.0.0"

# ── Server Categories ────────────────────────────────────────────

SERVER_CATEGORIES = {
    "general": "General purpose tools",
    "3d": "3D modeling and rendering (Blender, etc.)",
    "vector": "Vector graphics (Inkscape, Figma, etc.)",
    "evaluation": "Visual evaluation and quality assessment",
    "browser": "Browser automation and web access",
    "filesystem": "File system operations",
}

_DEFAULT_CONFIG = {
    "servers": [
        {
            "name": "browser",
            "command": "npx @anthropic/mcp-browser",
            "description": "Browser automation via MCP",
            "category": "browser",
            "enabled": False,
            "capabilities": {
                "tools": ["navigate", "click", "type", "screenshot"],
                "input_types": ["text", "url"],
                "output_types": ["screenshot", "html", "text"],
            },
            "agent_affinity": ["web-browser", "qa"],
        },
        {
            "name": "filesystem",
            "command": "npx @anthropic/mcp-filesystem",
            "description": "File system access via MCP",
            "category": "filesystem",
            "enabled": False,
            "capabilities": {
                "tools": ["read_file", "write_file", "list_dir"],
                "input_types": ["path"],
                "output_types": ["text", "binary"],
            },
            "agent_affinity": ["*"],
        },
        {
            "name": "blender-mcp",
            "command": "blender --background --python blender_mcp_server.py",
            "description": "Blender 3D modeling, materials, lighting, rendering via MCP",
            "category": "3d",
            "enabled": False,
            "capabilities": {
                "tools": ["create_mesh", "edit_mesh", "apply_material", "set_lighting",
                          "set_camera", "render_scene", "export_format"],
                "input_types": ["text", "json", "python_script"],
                "output_types": ["mesh", "image/png", "scene_file", "gltf", "fbx", "obj"],
            },
            "agent_affinity": ["blender-expert", "3d-artist", "art-director"],
            "health_check": {"method": "command", "test": "blender --version", "timeout": 5},
        },
        {
            "name": "inkscape-mcp",
            "command": "inkscape-mcp-server",
            "description": "Inkscape vector graphics — paths, filters, SVG export via MCP",
            "category": "vector",
            "enabled": False,
            "capabilities": {
                "tools": ["create_path", "edit_path", "create_svg", "apply_filter",
                          "export_svg", "export_png"],
                "input_types": ["text", "svg", "json"],
                "output_types": ["svg", "image/png", "pdf"],
            },
            "agent_affinity": ["illustration-expert", "brand-designer", "art-director"],
            "health_check": {"method": "command", "test": "inkscape --version", "timeout": 5},
        },
        {
            "name": "vision-provider",
            "command": "python -m grimoire.mcp.vision_server",
            "description": "Visual quality assessment for agent outputs via multimodal LLM",
            "category": "evaluation",
            "enabled": False,
            "capabilities": {
                "tools": ["evaluate_image", "compare_images", "validate_svg"],
                "input_types": ["image/png", "image/svg+xml", "image/jpeg"],
                "output_types": ["json"],
            },
            "agent_affinity": ["*"],
        },
    ],
}


# ── Config ───────────────────────────────────────────────────────

def _config_path(project_root: Path) -> Path:
    return project_root / "_grimoire" / "_config" / "mcp-servers.json"


def _load_config(project_root: Path) -> dict[str, Any]:
    cfg = _config_path(project_root)
    if not cfg.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)


def _save_config(project_root: Path, config: dict[str, Any]) -> None:
    cfg = _config_path(project_root)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")


# ── Core ─────────────────────────────────────────────────────────

def list_servers(project_root: Path) -> list[dict[str, Any]]:
    """Liste tous les serveurs MCP configurés."""
    config = _load_config(project_root)
    return config.get("servers", [])


def check_server_status(server: dict[str, Any]) -> dict[str, Any]:
    """Vérifie si un serveur est accessible."""
    result = {
        "name": server["name"],
        "enabled": server.get("enabled", False),
        "command": server.get("command", ""),
        "category": server.get("category", "general"),
        "available": False,
        "capabilities": server.get("capabilities", {}),
        "agent_affinity": server.get("agent_affinity", []),
    }

    if not server.get("enabled", False):
        result["status"] = "disabled"
        return result

    # Check if the command executable exists
    cmd_parts = server.get("command", "").split()
    if not cmd_parts:
        result["status"] = "no command configured"
        return result

    executable = cmd_parts[0]
    if shutil.which(executable):
        result["available"] = True
        result["status"] = "available"
    else:
        result["status"] = f"executable '{executable}' not found"

    # Health check if configured and enabled
    hc = server.get("health_check")
    if hc and result["available"] and hc.get("method") == "command":
        test_cmd = hc.get("test", "")
        if test_cmd:
            import subprocess
            try:
                cp = subprocess.run(
                    test_cmd.split(),
                    capture_output=True, timeout=hc.get("timeout", 5),
                )
                result["health"] = "healthy" if cp.returncode == 0 else "unhealthy"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                result["health"] = "unreachable"

    return result


def get_all_status(project_root: Path) -> list[dict[str, Any]]:
    """Statut de tous les serveurs."""
    servers = list_servers(project_root)
    return [check_server_status(s) for s in servers]


# ── MCP Interface ────────────────────────────────────────────────

def mcp_proxy_list(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: liste les serveurs MCP proxy configurés.

    Args:
        project_root: Racine du projet.

    Returns:
        {servers: [...], count: N}
    """
    servers = list_servers(Path(project_root))
    return {"servers": servers, "count": len(servers)}


def mcp_proxy_status(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: statut des serveurs MCP proxy.

    Args:
        project_root: Racine du projet.

    Returns:
        {status: [...], available: N, total: N}
    """
    statuses = get_all_status(Path(project_root))
    available = sum(1 for s in statuses if s["available"])
    return {"status": statuses, "available": available, "total": len(statuses)}


def mcp_proxy_capabilities(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: retourne les capabilities de tous les serveurs MCP.

    Args:
        project_root: Racine du projet.

    Returns:
        {servers: [{name, category, capabilities, agent_affinity}]}
    """
    servers = list_servers(Path(project_root))
    return {
        "servers": [
            {
                "name": s["name"],
                "category": s.get("category", "general"),
                "capabilities": s.get("capabilities", {}),
                "agent_affinity": s.get("agent_affinity", []),
                "enabled": s.get("enabled", False),
            }
            for s in servers
        ],
    }


def mcp_proxy_find_for_agent(agent_id: str, project_root: str = ".") -> dict[str, Any]:
    """MCP tool: trouve les serveurs MCP adaptés à un agent donné.

    Args:
        agent_id: ID de l'agent.
        project_root: Racine du projet.

    Returns:
        {agent: str, servers: [{name, status, capabilities}]}
    """
    all_status = get_all_status(Path(project_root))
    matched = []
    for s in all_status:
        affinity = s.get("agent_affinity", [])
        if "*" in affinity or agent_id in affinity:
            matched.append(s)
    return {"agent": agent_id, "servers": matched, "count": len(matched)}


# ── Commands ─────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    servers = list_servers(project_root)

    if args.json:
        print(json.dumps({"servers": servers}, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔌 MCP Proxy Servers — {len(servers)} configured\n")
        for s in servers:
            enabled = "✅" if s.get("enabled") else "⬜"
            desc = s.get("description", "")
            print(f"  {enabled} {s['name']:20s}  {desc}")
            print(f"     cmd: {s.get('command', 'N/A')}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    statuses = get_all_status(project_root)

    if args.json:
        available = sum(1 for s in statuses if s["available"])
        print(json.dumps({"statuses": statuses, "available": available},
                          indent=2, ensure_ascii=False))
    else:
        print("\n  🔌 MCP Proxy Status\n")
        for s in statuses:
            icon = "🟢" if s["available"] else ("⬜" if s["status"] == "disabled" else "🔴")
            print(f"  {icon} {s['name']:20s}  {s['status']}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    config = _load_config(project_root)

    if args.init:
        _save_config(project_root, _DEFAULT_CONFIG)
        print(f"  ✅ Config initialized at {_config_path(project_root)}")
        return 0

    if args.json:
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print(f"\n  ⚙️ MCP Proxy Config: {_config_path(project_root)}\n")
        print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Grimoire MCP Proxy")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_list = subs.add_parser("list", help="List configured servers")
    p_list.set_defaults(func=cmd_list)

    p_status = subs.add_parser("status", help="Check server status")
    p_status.set_defaults(func=cmd_status)

    p_config = subs.add_parser("config", help="Show/init configuration")
    p_config.add_argument("--init", action="store_true", help="Initialize default config")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
