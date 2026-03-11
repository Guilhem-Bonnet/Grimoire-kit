#!/usr/bin/env python3
"""
agent-build.py — Agent Build System Grimoire.
==============================================

Système de build pour agents : valide un agent proposé (DNA schema,
dépendances MCP, capabilities, persona), résout les dépendances,
et génère un baseline pour le monitoring.

Modes :
  validate   — Valide un fichier agent (.md ou .dna.yaml)
  deps       — Résout les dépendances (MCP servers, tools, base agents)
  build      — Validation complète + résolution deps + baseline
  list       — Liste les agents installés avec leur statut de build

Usage :
  python3 agent-build.py --project-root . validate --agent blender-expert.md
  python3 agent-build.py --project-root . deps --agent blender-expert.md
  python3 agent-build.py --project-root . build --agent blender-expert.md
  python3 agent-build.py --project-root . list

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.agent_build")

AGENT_BUILD_VERSION = "1.0.0"

BUILD_DIR = "_grimoire-output/.agent-builds"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class DependencyCheck:
    """Résultat de vérification d'une dépendance."""

    dep_type: str = ""     # mcp_server | tool | base_agent | skill
    name: str = ""
    required: bool = True
    resolved: bool = False
    location: str = ""
    message: str = ""


@dataclass
class ValidationResult:
    """Résultat de validation d'un agent."""

    agent_file: str = ""
    agent_name: str = ""
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BuildResult:
    """Résultat complet de build."""

    agent_file: str = ""
    agent_name: str = ""
    build_id: str = ""
    timestamp: str = ""
    validation: dict[str, Any] = field(default_factory=dict)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    all_deps_resolved: bool = False
    build_status: str = "pending"  # pending | success | warning | failed


# ── Agent Parsing ────────────────────────────────────────────────────────────


def parse_agent_file(agent_path: Path) -> dict[str, Any]:
    """Parse un fichier agent et en extrait les métadonnées."""
    if not agent_path.exists():
        return {"error": f"File not found: {agent_path}"}

    content = agent_path.read_text(encoding="utf-8")
    result: dict[str, Any] = {
        "file": str(agent_path),
        "name": "",
        "description": "",
        "has_persona": False,
        "has_menu": False,
        "has_rules": False,
        "has_activation": False,
        "mcp_servers": [],
        "capabilities": [],
        "inter_agent_refs": [],
    }

    # YAML frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for line in fm.split("\n"):
            if line.startswith("name:"):
                result["name"] = line.split(":", 1)[1].strip().strip('"')
            elif line.startswith("description:"):
                result["description"] = line.split(":", 1)[1].strip().strip('"')

    # Structure checks
    result["has_persona"] = "<persona>" in content
    result["has_menu"] = "<menu>" in content
    result["has_rules"] = "<rules>" in content or "<r>" in content
    result["has_activation"] = "<activation" in content

    # MCP servers
    for m in re.finditer(r'<server\s+name="([^"]+)"', content):
        required = 'required="true"' in content[m.start():m.start() + 200]
        result["mcp_servers"].append({"name": m.group(1), "required": required})

    # Capabilities
    for m in re.finditer(r'<cap\s+id="([^"]+)">(.*?)</cap>', content):
        result["capabilities"].append({"id": m.group(1), "description": m.group(2).strip()})

    # Inter-agent references
    for m in re.finditer(r'\[(\w[\w-]+)→(\w[\w-]+)\]', content):
        result["inter_agent_refs"].append({"from": m.group(1), "to": m.group(2)})

    return result


# ── Validation ───────────────────────────────────────────────────────────────


def validate_agent(agent_path: Path) -> ValidationResult:
    """Valide un fichier agent."""
    parsed = parse_agent_file(agent_path)

    result = ValidationResult(
        agent_file=str(agent_path),
        agent_name=parsed.get("name", agent_path.stem),
    )

    if "error" in parsed:
        result.valid = False
        result.errors.append(parsed["error"])
        return result

    # Frontmatter
    if not parsed["name"]:
        result.warnings.append("No 'name' in YAML frontmatter")
    if not parsed["description"]:
        result.warnings.append("No 'description' in YAML frontmatter")

    # Structure
    checks = [
        ("persona", parsed["has_persona"], "Agent has <persona> block", True),
        ("menu", parsed["has_menu"], "Agent has <menu> block", True),
        ("rules", parsed["has_rules"], "Agent has <rules> block", True),
        ("activation", parsed["has_activation"], "Agent has <activation> block", True),
    ]

    for name, passed, desc, required in checks:
        result.checks.append({"check": name, "passed": passed, "description": desc})
        if not passed:
            if required:
                result.errors.append(f"Missing required: {desc}")
                result.valid = False
            else:
                result.warnings.append(f"Missing optional: {desc}")

    # Capabilities
    if parsed["capabilities"]:
        result.checks.append({
            "check": "capabilities",
            "passed": True,
            "count": len(parsed["capabilities"]),
        })
    else:
        result.warnings.append("No capabilities declared")

    return result


# ── Dependency Resolution ────────────────────────────────────────────────────


def resolve_dependencies(
    agent_path: Path,
    project_root: Path,
) -> list[DependencyCheck]:
    """Résout les dépendances d'un agent."""
    parsed = parse_agent_file(agent_path)
    deps: list[DependencyCheck] = []

    # MCP servers
    for srv in parsed.get("mcp_servers", []):
        dep = DependencyCheck(
            dep_type="mcp_server",
            name=srv["name"],
            required=srv.get("required", True),
        )
        # Check if configured in mcp-servers.json
        mcp_config = project_root / "_grimoire" / "_config" / "mcp-servers.json"
        if mcp_config.exists():
            try:
                config = json.loads(mcp_config.read_text(encoding="utf-8"))
                for s in config.get("servers", []):
                    if s["name"] == srv["name"]:
                        dep.resolved = True
                        dep.location = str(mcp_config)
                        dep.message = "Configured in MCP servers"
                        break
            except (json.JSONDecodeError, OSError):
                pass
        if not dep.resolved:
            dep.message = f"MCP server '{srv['name']}' not found in mcp-servers.json"
        deps.append(dep)

    # Tools (check framework/tools/)
    tools_dir = project_root / "framework" / "tools"
    if tools_dir.is_dir():
        # Check for vision-judge dependency if vision_loop capability exists
        caps = [c["id"] for c in parsed.get("capabilities", [])]
        if "vision-loop" in caps:
            vj = tools_dir / "vision-judge.py"
            deps.append(DependencyCheck(
                dep_type="tool",
                name="vision-judge",
                required=True,
                resolved=vj.exists(),
                location=str(vj) if vj.exists() else "",
                message="Required for vision loop capability" if not vj.exists() else "Found",
            ))

    return deps


# ── Build ────────────────────────────────────────────────────────────────────


def build_agent(agent_path: Path, project_root: Path) -> BuildResult:
    """Build complet : validation + dépendances + baseline."""
    validation = validate_agent(agent_path)
    deps = resolve_dependencies(agent_path, project_root)

    all_required_resolved = all(
        d.resolved for d in deps if d.required
    )

    status = "success"
    if not validation.valid:
        status = "failed"
    elif not all_required_resolved:
        status = "warning"
    elif validation.warnings:
        status = "warning"

    build_result = BuildResult(
        agent_file=str(agent_path),
        agent_name=validation.agent_name,
        build_id=f"build-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
        validation=asdict(validation),
        dependencies=[asdict(d) for d in deps],
        all_deps_resolved=all_required_resolved,
        build_status=status,
    )

    # Save build result
    builds_dir = project_root / BUILD_DIR
    builds_dir.mkdir(parents=True, exist_ok=True)
    build_file = builds_dir / f"{validation.agent_name}-{build_result.build_id}.json"
    build_file.write_text(
        json.dumps(asdict(build_result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return build_result


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_agent_build(
    agent_file: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: build complet d'un agent (validation + deps + baseline).

    Args:
        agent_file: Chemin vers le fichier agent.
        project_root: Racine du projet.

    Returns:
        BuildResult sérialisé.
    """
    root = Path(project_root).resolve()
    agent_path = root / agent_file if not Path(agent_file).is_absolute() else Path(agent_file)
    result = build_agent(agent_path, root)
    return asdict(result)


def mcp_agent_validate(
    agent_file: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: valide un fichier agent.

    Args:
        agent_file: Chemin vers le fichier agent.
        project_root: Racine du projet.

    Returns:
        ValidationResult sérialisé.
    """
    root = Path(project_root).resolve()
    agent_path = root / agent_file if not Path(agent_file).is_absolute() else Path(agent_file)
    result = validate_agent(agent_path)
    return asdict(result)


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_validate(args: argparse.Namespace) -> int:
    agent_path = Path(args.project_root).resolve() / args.agent
    result = validate_agent(agent_path)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        icon = "✅" if result.valid else "❌"
        print(f"\n  {icon} Agent Validation: {result.agent_name}")
        for c in result.checks:
            ci = "✅" if c["passed"] else "❌"
            print(f"    {ci} {c.get('description', c['check'])}")
        if result.errors:
            print("\n  ❌ Errors:")
            for e in result.errors:
                print(f"    - {e}")
        if result.warnings:
            print("\n  ⚠️  Warnings:")
            for w in result.warnings:
                print(f"    - {w}")
    return 0 if result.valid else 1


def cmd_deps(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_path = root / args.agent
    deps = resolve_dependencies(agent_path, root)

    if args.json:
        print(json.dumps([asdict(d) for d in deps], indent=2, ensure_ascii=False))
    else:
        print(f"\n  📦 Dependencies for {args.agent}\n")
        for d in deps:
            icon = "✅" if d.resolved else ("❌" if d.required else "⚠️")
            req = "required" if d.required else "optional"
            print(f"    {icon} [{d.dep_type}] {d.name} ({req})")
            print(f"       {d.message}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_path = root / args.agent
    result = build_agent(agent_path, root)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        icons = {"success": "✅", "warning": "⚠️", "failed": "❌", "pending": "⏳"}
        icon = icons.get(result.build_status, "❓")
        print(f"\n  {icon} Build: {result.agent_name} [{result.build_status}]")
        print(f"  Build ID: {result.build_id}")

        # Validation summary
        v = result.validation
        vi = "✅" if v.get("valid") else "❌"
        print(f"  Validation: {vi} ({len(v.get('errors', []))} errors, {len(v.get('warnings', []))} warnings)")

        # Deps summary
        deps = result.dependencies
        resolved = sum(1 for d in deps if d["resolved"])
        print(f"  Dependencies: {resolved}/{len(deps)} resolved")

        if not result.all_deps_resolved:
            print("\n  ❌ Unresolved required dependencies:")
            for d in deps:
                if not d["resolved"] and d["required"]:
                    print(f"    - [{d['dep_type']}] {d['name']}: {d['message']}")

    return 0 if result.build_status != "failed" else 1


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    builds_dir = root / BUILD_DIR
    if not builds_dir.is_dir():
        print("  No builds found.")
        return 0

    builds = sorted(builds_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if args.json:
        data = []
        for b in builds[:20]:
            try:
                data.append(json.loads(b.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🏗️  Agent Builds ({len(builds)} total)\n")
        for b in builds[:20]:
            try:
                d = json.loads(b.read_text(encoding="utf-8"))
                icons = {"success": "✅", "warning": "⚠️", "failed": "❌"}
                icon = icons.get(d.get("build_status", ""), "❓")
                print(f"  {icon} {d.get('agent_name', '?'):25s} {d.get('build_id', '')}  {d.get('timestamp', '')[:19]}")
            except json.JSONDecodeError:
                continue
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Build System")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=f"agent-build {AGENT_BUILD_VERSION}")

    sub = parser.add_subparsers(dest="command")

    p_val = sub.add_parser("validate", help="Validate an agent file")
    p_val.add_argument("--agent", required=True)

    p_deps = sub.add_parser("deps", help="Resolve agent dependencies")
    p_deps.add_argument("--agent", required=True)

    p_build = sub.add_parser("build", help="Full build: validate + deps + baseline")
    p_build.add_argument("--agent", required=True)

    sub.add_parser("list", help="List agent builds")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "validate": cmd_validate,
        "deps": cmd_deps,
        "build": cmd_build,
        "list": cmd_list,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
