#!/usr/bin/env python3
"""
tool-registry.py — Registry unifié des outils Grimoire (BM-45 Story 6.1).
============================================================

Registry qui expose les outils Grimoire à la fois comme :
  - MCP tools (pour IDE — Copilot, Cursor, Windsurf)
  - Anthropic tool_use (pour Claude API directe)
  - OpenAI function_calling (pour OpenAI/Ollama/LiteLLM)

Auto-découverte des outils dans framework/tools/ par inspection des docstrings
et des argparse parsers.

Modes :
  list       — Liste tous les outils enregistrés
  export     — Exporte en format MCP, Anthropic ou OpenAI
  validate   — Valide les schémas JSON de tous les outils
  inspect    — Détail d'un outil spécifique

Usage :
  python3 tool-registry.py --project-root . list
  python3 tool-registry.py --project-root . export --format mcp
  python3 tool-registry.py --project-root . export --format anthropic --json
  python3 tool-registry.py --project-root . validate
  python3 tool-registry.py --project-root . inspect --tool context-router

Stdlib only — pas de dépendances externes.

Références :
  - Anthropic Tool Use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview
  - OpenAI Function Calling: https://platform.openai.com/docs/guides/function-calling
  - MCP Tool Protocol: https://modelcontextprotocol.io/docs/concepts/tools
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.tool_registry")

# ── Version ──────────────────────────────────────────────────────────────────

TOOL_REGISTRY_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

TOOLS_DIR = "framework/tools"

# Tools known to be CLI-based Python tools (auto-discovered)
KNOWN_TOOL_EXTENSIONS = {".py", ".sh"}

# Parameter type mapping for JSON Schema
PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "Path": "string",
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ToolParameter:
    """Un paramètre d'un outil Grimoire."""
    name: str
    param_type: str = "string"
    description: str = ""
    required: bool = False
    default: str | None = None
    enum: list[str] | None = None


@dataclass
class GrimoireTool:
    """Représentation unifiée d'un outil Grimoire."""
    name: str
    description: str
    source_file: str
    tool_type: str = "cli"  # cli | python | shell | mcp
    version: str = ""
    parameters: list[ToolParameter] = field(default_factory=list)
    subcommands: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_mcp(self) -> dict:
        """Export au format MCP tool."""
        properties = {}
        required = []

        for param in self.parameters:
            prop = {"type": param.param_type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        schema = {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["inputSchema"]["required"] = required
        return schema

    def to_anthropic(self) -> dict:
        """Export au format Anthropic tool_use."""
        properties = {}
        required = []

        for param in self.parameters:
            prop = {"type": param.param_type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        tool_def = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            tool_def["input_schema"]["required"] = required
        return tool_def

    def to_openai(self) -> dict:
        """Export au format OpenAI function calling."""
        properties = {}
        required = []

        for param in self.parameters:
            prop = {"type": param.param_type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        func_def = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            func_def["function"]["parameters"]["required"] = required
        return func_def


@dataclass
class ValidationResult:
    """Résultat de validation d'un outil."""
    tool_name: str
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RegistryStats:
    """Statistiques du registry."""
    total_tools: int = 0
    python_tools: int = 0
    shell_tools: int = 0
    markdown_tools: int = 0
    total_parameters: int = 0
    tools_with_subcommands: int = 0


# ── Tool Discovery ─────────────────────────────────────────────────────────

class ToolDiscoverer:
    """Découvre les outils dans framework/tools/ par inspection statique."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.tools_dir = project_root / TOOLS_DIR

    def discover_all(self) -> list[GrimoireTool]:
        """Découvre tous les outils Grimoire."""
        tools: list[GrimoireTool] = []

        if not self.tools_dir.exists():
            return tools

        for f in sorted(self.tools_dir.iterdir()):
            if f.suffix == ".py" and not f.name.startswith("_"):
                tool = self._inspect_python_tool(f)
                if tool:
                    tools.append(tool)
            elif f.suffix == ".sh" and not f.name.startswith("_"):
                tool = self._inspect_shell_tool(f)
                if tool:
                    tools.append(tool)
            elif f.suffix == ".md" and not f.name.startswith("_"):
                tool = self._inspect_markdown_tool(f)
                if tool:
                    tools.append(tool)

        return tools

    def _inspect_python_tool(self, filepath: Path) -> GrimoireTool | None:
        """Inspecte un outil Python par analyse AST du module."""
        try:
            source = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        name = filepath.stem
        description = ""
        version = ""
        parameters: list[ToolParameter] = []
        subcommands: list[str] = []
        tags: list[str] = []

        # Extract module docstring
        try:
            tree = ast.parse(source)
            docstring = ast.get_docstring(tree)
            if docstring:
                # First paragraph as description
                paragraphs = docstring.strip().split("\n\n")
                first_line = paragraphs[0].split("\n")[0].strip()
                # Remove the "module.py — " prefix
                if "—" in first_line:
                    description = first_line.split("—", 1)[1].strip()
                elif "---" in first_line:
                    description = first_line.split("---", 1)[1].strip()
                else:
                    description = first_line

                # Clean up description
                description = description.rstrip(".")
                if len(description) > 200:
                    description = description[:197] + "..."
        except SyntaxError as _exc:
            _log.debug("SyntaxError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

        # Extract version
        version_match = re.search(r'(\w+_VERSION)\s*=\s*["\']([^"\']+)["\']', source)
        if version_match:
            version = version_match.group(2)

        # Extract argparse subcommands
        subcommand_matches = re.findall(
            r'sub\.add_parser\(\s*["\'](\w[\w-]*)["\']',
            source,
        )
        if subcommand_matches:
            subcommands = subcommand_matches

        # Extract argparse arguments
        arg_pattern = re.compile(
            r'\.add_argument\(\s*'
            r'["\']--?([\w-]+)["\']'
            r'(?:.*?help\s*=\s*["\']([^"\']*)["\'])?'
            r'(?:.*?type\s*=\s*(\w+))?'
            r'(?:.*?required\s*=\s*(True|False))?'
            r'(?:.*?default\s*=\s*([^,\)]+))?',
            re.DOTALL,
        )
        for match in arg_pattern.finditer(source):
            param_name = match.group(1)
            if param_name in ("version", "help", "h"):
                continue
            help_text = match.group(2) or ""
            py_type = match.group(3) or "str"
            is_required = match.group(4) == "True" if match.group(4) else False
            default_val = match.group(5).strip().strip("\"'") if match.group(5) else None

            json_type = PYTHON_TO_JSON_TYPE.get(py_type, "string")
            parameters.append(ToolParameter(
                name=param_name,
                param_type=json_type,
                description=help_text,
                required=is_required,
                default=default_val,
            ))

        # Tags from filename/content
        if "mcp" in name.lower():
            tags.append("mcp")
        if "rag" in name.lower() or "qdrant" in source[:500].lower():
            tags.append("rag")
        if "memory" in name.lower() or "sync" in name.lower():
            tags.append("memory")
        if "llm" in name.lower() or "router" in name.lower():
            tags.append("routing")
        if "token" in name.lower() or "budget" in name.lower():
            tags.append("optimization")
        if "cache" in name.lower():
            tags.append("cache")
        if "tool" in name.lower() or "registry" in name.lower():
            tags.append("tooling")
        if "agent" in name.lower() or "caller" in name.lower():
            tags.append("multi-agent")

        relative = str(filepath.relative_to(self.project_root))
        return GrimoireTool(
            name=name,
            description=description or f"Grimoire tool: {name}",
            source_file=relative,
            tool_type="cli",
            version=version,
            parameters=parameters,
            subcommands=subcommands,
            tags=tags,
        )

    def _inspect_shell_tool(self, filepath: Path) -> GrimoireTool | None:
        """Inspecte un outil shell."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        name = filepath.stem
        description = ""

        # Extract first comment block
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("#!"):
                description = stripped[2:].strip()
                break

        relative = str(filepath.relative_to(self.project_root))
        return GrimoireTool(
            name=name,
            description=description or f"Shell tool: {name}",
            source_file=relative,
            tool_type="shell",
            tags=["shell"],
        )

    def _inspect_markdown_tool(self, filepath: Path) -> GrimoireTool | None:
        """Inspecte un document Markdown comme 'tool' documentaire."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        name = filepath.stem
        description = ""

        # Extract first H1 content
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1_match:
            description = h1_match.group(1).strip()

        relative = str(filepath.relative_to(self.project_root))
        return GrimoireTool(
            name=name,
            description=description or f"Documentation: {name}",
            source_file=relative,
            tool_type="doc",
            tags=["documentation"],
        )


# ── Tool Registry ──────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Registry unifié des outils Grimoire.

    Fournit une vue unifiée de tous les outils avec export
    multi-format (MCP, Anthropic, OpenAI).
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._tools: list[GrimoireTool] = []
        self._discovered = False

    def discover(self) -> int:
        """Découvre tous les outils. Retourne le nombre d'outils trouvés."""
        discoverer = ToolDiscoverer(self.project_root)
        self._tools = discoverer.discover_all()
        self._discovered = True
        return len(self._tools)

    @property
    def tools(self) -> list[GrimoireTool]:
        if not self._discovered:
            self.discover()
        return self._tools

    def get(self, name: str) -> GrimoireTool | None:
        """Récupère un outil par nom."""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def filter_by_tag(self, tag: str) -> list[GrimoireTool]:
        """Filtre les outils par tag."""
        return [t for t in self.tools if tag in t.tags]

    def filter_by_type(self, tool_type: str) -> list[GrimoireTool]:
        """Filtre les outils par type."""
        return [t for t in self.tools if t.tool_type == tool_type]

    def export_all(self, fmt: str = "mcp") -> list[dict]:
        """Exporte tous les outils CLI dans le format spécifié."""
        exported = []
        for tool in self.tools:
            if tool.tool_type in ("cli", "shell"):
                if fmt == "mcp":
                    exported.append(tool.to_mcp())
                elif fmt == "anthropic":
                    exported.append(tool.to_anthropic())
                elif fmt == "openai":
                    exported.append(tool.to_openai())
        return exported

    def validate_all(self) -> list[ValidationResult]:
        """Valide les schémas de tous les outils."""
        results = []
        for tool in self.tools:
            result = self._validate_tool(tool)
            results.append(result)
        return results

    def _validate_tool(self, tool: GrimoireTool) -> ValidationResult:
        """Valide un outil individuel."""
        result = ValidationResult(tool_name=tool.name)

        # Name validation
        if not tool.name:
            result.valid = False
            result.errors.append("Nom manquant")

        if not re.match(r"^[\w-]+$", tool.name):
            result.valid = False
            result.errors.append(f"Nom invalide: {tool.name} (alphanumeric + hyphens only)")

        # Description
        if not tool.description:
            result.warnings.append("Pas de description")
        elif len(tool.description) > 200:
            result.warnings.append("Description > 200 chars")

        # Source file exists
        source = self.project_root / tool.source_file
        if not source.exists():
            result.valid = False
            result.errors.append(f"Fichier source introuvable: {tool.source_file}")

        # Parameters validation
        param_names = set()
        for param in tool.parameters:
            if param.name in param_names:
                result.errors.append(f"Paramètre dupliqué: {param.name}")
                result.valid = False
            param_names.add(param.name)

            valid_types = {"string", "integer", "number", "boolean", "array", "object"}
            if param.param_type not in valid_types:
                result.warnings.append(
                    f"Type inconnu pour {param.name}: {param.param_type}"
                )

        # Schema export test
        try:
            mcp = tool.to_mcp()
            json.dumps(mcp)  # Ensure JSON-serializable
        except Exception as e:
            result.valid = False
            result.errors.append(f"Export MCP invalide: {e}")

        try:
            anthropic = tool.to_anthropic()
            json.dumps(anthropic)
        except Exception as e:
            result.valid = False
            result.errors.append(f"Export Anthropic invalide: {e}")

        try:
            openai = tool.to_openai()
            json.dumps(openai)
        except Exception as e:
            result.valid = False
            result.errors.append(f"Export OpenAI invalide: {e}")

        return result

    def stats(self) -> RegistryStats:
        """Statistiques du registry."""
        tools = self.tools
        return RegistryStats(
            total_tools=len(tools),
            python_tools=sum(1 for t in tools if t.tool_type == "cli"),
            shell_tools=sum(1 for t in tools if t.tool_type == "shell"),
            markdown_tools=sum(1 for t in tools if t.tool_type == "doc"),
            total_parameters=sum(len(t.parameters) for t in tools),
            tools_with_subcommands=sum(1 for t in tools if t.subcommands),
        )


# ── Config Loading ──────────────────────────────────────────────────────────

def load_registry_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [project_root / "project-context.yaml", project_root / "grimoire.yaml"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("tool_registry", {})
    return {}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_tool_list(tools: list[GrimoireTool]) -> None:
    print(f"\n  🔧 Grimoire Tool Registry — {len(tools)} outils")
    print(f"  {'─' * 70}")

    for tool in tools:
        tags = " ".join(f"[{t}]" for t in tool.tags) if tool.tags else ""
        ver = f" v{tool.version}" if tool.version else ""
        subs = f" ({', '.join(tool.subcommands)})" if tool.subcommands else ""
        print(f"  {tool.name:30s} │ {tool.tool_type:5s}{ver}")
        print(f"    {tool.description[:70]}")
        if subs:
            print(f"    Subcommands: {subs}")
        if tags:
            print(f"    Tags: {tags}")
        print()


def _print_tool_detail(tool: GrimoireTool) -> None:
    print(f"\n  🔧 {tool.name}")
    print(f"  {'─' * 60}")
    print(f"  Description : {tool.description}")
    print(f"  Type        : {tool.tool_type}")
    print(f"  Source      : {tool.source_file}")
    if tool.version:
        print(f"  Version     : {tool.version}")
    if tool.tags:
        print(f"  Tags        : {', '.join(tool.tags)}")
    if tool.subcommands:
        print(f"  Subcommands : {', '.join(tool.subcommands)}")
    if tool.parameters:
        print(f"\n  Paramètres ({len(tool.parameters)}) :")
        for p in tool.parameters:
            req = " *" if p.required else ""
            default = f" (défaut: {p.default})" if p.default else ""
            print(f"    --{p.name:20s} {p.param_type:8s}{req}{default}")
            if p.description:
                print(f"      {p.description}")
    print()


def _print_validation(results: list[ValidationResult]) -> None:
    valid_count = sum(1 for r in results if r.valid)
    total = len(results)
    icon = "✅" if valid_count == total else "⚠️"

    print(f"\n  {icon} Validation — {valid_count}/{total} outils valides")
    print(f"  {'─' * 60}")

    for r in results:
        status = "✅" if r.valid else "❌"
        print(f"  {status} {r.tool_name}")
        for err in r.errors:
            print(f"      ❌ {err}")
        for warn in r.warnings:
            print(f"      ⚠️ {warn}")
    print()


def _print_stats(stats: RegistryStats) -> None:
    print("\n  📊 Registry Stats")
    print(f"  {'─' * 40}")
    print(f"  Total outils       : {stats.total_tools}")
    print(f"  Python CLI         : {stats.python_tools}")
    print(f"  Shell scripts      : {stats.shell_tools}")
    print(f"  Documentation (MD) : {stats.markdown_tools}")
    print(f"  Paramètres totaux  : {stats.total_parameters}")
    print(f"  Avec subcommands   : {stats.tools_with_subcommands}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tool Registry — Registry unifié des outils Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"tool-registry {TOOL_REGISTRY_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # list
    list_p = sub.add_parser("list", help="Lister tous les outils")
    list_p.add_argument("--type", default="", help="Filtrer par type (cli/shell/doc)")
    list_p.add_argument("--tag", default="", help="Filtrer par tag")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # export
    exp_p = sub.add_parser("export", help="Exporter les schémas")
    exp_p.add_argument("--format", choices=["mcp", "anthropic", "openai"],
                       default="mcp", help="Format d'export (défaut: mcp)")
    exp_p.add_argument("--json", action="store_true", help="Output JSON")

    # validate
    sub.add_parser("validate", help="Valider les schémas")

    # inspect
    ins_p = sub.add_parser("inspect", help="Détail d'un outil")
    ins_p.add_argument("--tool", required=True, help="Nom de l'outil")
    ins_p.add_argument("--format", choices=["mcp", "anthropic", "openai"],
                       default="", help="Afficher le schéma export")

    # stats
    sub.add_parser("stats", help="Statistiques du registry")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    registry = ToolRegistry(project_root)

    if args.command == "list":
        tools = registry.tools
        if getattr(args, "type", ""):
            tools = [t for t in tools if t.tool_type == args.type]
        if getattr(args, "tag", ""):
            tools = [t for t in tools if args.tag in t.tags]
        if getattr(args, "json", False):
            print(json.dumps([asdict(t) for t in tools], ensure_ascii=False, indent=2))
        else:
            _print_tool_list(tools)

    elif args.command == "export":
        fmt = getattr(args, "format", "mcp")
        exported = registry.export_all(fmt)
        if getattr(args, "json", False) or True:  # Always JSON for export
            print(json.dumps(exported, ensure_ascii=False, indent=2))

    elif args.command == "validate":
        results = registry.validate_all()
        _print_validation(results)

    elif args.command == "inspect":
        tool = registry.get(args.tool)
        if not tool:
            print(f"\n  ❌ Outil '{args.tool}' introuvable\n")
            sys.exit(1)
        fmt = getattr(args, "format", "")
        if fmt:
            if fmt == "mcp":
                print(json.dumps(tool.to_mcp(), ensure_ascii=False, indent=2))
            elif fmt == "anthropic":
                print(json.dumps(tool.to_anthropic(), ensure_ascii=False, indent=2))
            elif fmt == "openai":
                print(json.dumps(tool.to_openai(), ensure_ascii=False, indent=2))
        else:
            _print_tool_detail(tool)

    elif args.command == "stats":
        s = registry.stats()
        _print_stats(s)


if __name__ == "__main__":
    main()
