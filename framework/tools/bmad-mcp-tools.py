#!/usr/bin/env python3
"""
bmad-mcp-tools.py — MCP Server BMAD Synapse Intelligence Layer (BM-40/42/47).
============================================================

Expose les outils BMAD Intelligence Layer comme MCP tools pour
intégration transparente dans VS Code Copilot, Cursor, Cline.

Deux modes d'exposition :
  1. **Legacy** (hardcoded) — 8 outils originaux (Lot 1) avec handlers dédiés
  2. **Auto-discovery** (v2) — scanne tous les `mcp_*` dans framework/tools/
     et les expose automatiquement via un dispatcher générique

Tools legacy (Lot 1) :
  - bmad_route_request, bmad_classify_task, bmad_router_stats
  - bmad_rag_search, bmad_rag_augment, bmad_rag_status
  - bmad_memory_push, bmad_memory_diff

Tools auto-discovered (Lots 2-4) :
  - bmad_context_budget, bmad_orchestrate, bmad_agent_worker
  - bmad_message_bus_send, bmad_message_bus_status
  - bmad_conversation_branch, bmad_conversation_history
  - bmad_context_merge, bmad_background_task
  - bmad_validate_contract, bmad_list_contracts
  - bmad_synapse_config, bmad_synapse_trace
  - bmad_synapse_dashboard

Transport : stdio (standard MCP)

Usage :
  python3 bmad-mcp-tools.py                # MCP stdio server
  python3 bmad-mcp-tools.py --list-tools   # Liste tous les outils
  python3 bmad-mcp-tools.py --discover     # Affiche les outils auto-découverts

Dépendances :
  pip install mcp  (ou: pip install "mcp[cli]")

Références :
  - Anthropic MCP SDK Python : https://github.com/modelcontextprotocol/python-sdk
  - VS Code MCP integration  : https://code.visualstudio.com/docs/copilot/chat/mcp-servers
  - MCP Spec                  : https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
import logging

_log = logging.getLogger("grimoire.bmad_mcp_tools")

# ── Version ──────────────────────────────────────────────────────────────────

BMAD_MCP_TOOLS_VERSION = "2.0.0"

# ── Rate Limiting ────────────────────────────────────────────────────────────

# Prevents accidental DOS from rapid-fire MCP tool calls
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_CALLS = 120  # max calls per window per tool
_call_timestamps: dict[str, list[float]] = {}


def _rate_limit_check(tool_name: str) -> str | None:
    """Returns error message if rate limited, None if OK."""
    import time as _time
    now = _time.monotonic()
    if tool_name not in _call_timestamps:
        _call_timestamps[tool_name] = []

    # Purge old entries
    _call_timestamps[tool_name] = [
        t for t in _call_timestamps[tool_name]
        if now - t < _RATE_LIMIT_WINDOW
    ]

    if len(_call_timestamps[tool_name]) >= _RATE_LIMIT_MAX_CALLS:
        return (
            f"⚠️ Rate limit atteint pour {tool_name}: "
            f"{_RATE_LIMIT_MAX_CALLS} appels/{_RATE_LIMIT_WINDOW}s. "
            f"Réessayez dans quelques secondes."
        )

    _call_timestamps[tool_name].append(now)
    return None


# ── Project Root ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(os.environ.get("BMAD_PROJECT_ROOT", ".")).resolve()
TOOLS_DIR = Path(__file__).parent


# ── Lazy Module Imports ──────────────────────────────────────────────────────

def _import_tool(filename: str, module_name: str):
    """Import un outil BMAD par chemin de fichier."""
    tool_path = TOOLS_DIR / filename
    if not tool_path.exists():
        return None
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_router():
    mod = _import_tool("llm-router.py", "llm_router_mcp")
    if not mod:
        return None
    return mod.build_router_from_config(PROJECT_ROOT)


def _get_retriever():
    mod = _import_tool("rag-retriever.py", "rag_retriever_mcp")
    if not mod:
        return None
    return mod.build_retriever_from_config(PROJECT_ROOT)


def _get_syncer():
    mod = _import_tool("memory-sync.py", "memory_sync_mcp")
    if not mod:
        return None
    return mod.build_syncer_from_config(PROJECT_ROOT)


# ── Auto-Discovery Engine (Story 8.5R) ──────────────────────────────────────

# Tools that are already hardcoded in the legacy section (skip for auto-discovery)
LEGACY_TOOL_FILES = frozenset({
    "bmad-mcp-tools.py",    # Self
    "llm-router.py",        # Legacy hardcoded handlers
    "rag-retriever.py",     # Legacy
    "rag-indexer.py",       # No mcp_ function
    "memory-sync.py",       # Legacy
})

# Registry of discovered MCP tools: {tool_name: (module, function, description, params)}
_DISCOVERED_TOOLS: dict[str, dict] = {}
_DISCOVERY_DONE = False


def _python_type_to_json(type_hint: str) -> str:
    """Convertit un type hint Python en type JSON Schema."""
    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
    }
    return mapping.get(type_hint, "string")


def _extract_tool_info(func) -> dict:
    """
    Extrait les métadonnées d'une fonction mcp_* pour l'exposer via MCP.

    Retourne un dict avec name, description, et properties JSON Schema.
    """
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""
    # MCP name: mcp_orchestrate → bmad_orchestrate
    func_name = func.__name__
    tool_name = func_name.replace("mcp_", "bmad_")

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        # JSON type from annotation
        annotation = param.annotation
        if annotation != inspect.Parameter.empty:
            type_name = getattr(annotation, "__name__", str(annotation))
            # Handle 'str | Path' etc.
            type_name = type_name.replace("Path", "str").split("|")[0].strip().split("[")[0]
        else:
            type_name = "str"

        json_type = _python_type_to_json(type_name)

        prop: dict = {"type": json_type}

        # Description from param name
        prop["description"] = param_name.replace("_", " ").title()

        # Default
        if param.default != inspect.Parameter.empty:
            default = param.default
            if default != "":
                prop["default"] = default
        else:
            required.append(param_name)

        properties[param_name] = prop

    return {
        "tool_name": tool_name,
        "func_name": func_name,
        "description": doc.split("\n")[0] if doc else f"MCP tool {tool_name}",
        "properties": properties,
        "required": required,
    }


def discover_synapse_tools() -> dict[str, dict]:
    """
    Scanne framework/tools/ pour trouver toutes les fonctions mcp_*.

    Retourne un dict {tool_name: {module, func, info}} pour chaque outil découvert.
    Ignore les fichiers legacy (déjà exposés avec handlers dédiés).
    """
    global _DISCOVERED_TOOLS, _DISCOVERY_DONE

    if _DISCOVERY_DONE:
        return _DISCOVERED_TOOLS

    _DISCOVERED_TOOLS.clear()

    for py_file in sorted(TOOLS_DIR.glob("*.py")):
        if py_file.name in LEGACY_TOOL_FILES:
            continue

        mod_name = f"_mcp_disc_{py_file.stem.replace('-', '_')}"

        try:
            mod = _import_tool(py_file.name, mod_name)
            if not mod:
                continue

            for attr_name in dir(mod):
                if not attr_name.startswith("mcp_"):
                    continue

                func = getattr(mod, attr_name)
                if not callable(func):
                    continue

                info = _extract_tool_info(func)
                tool_name = info["tool_name"]

                _DISCOVERED_TOOLS[tool_name] = {
                    "module": mod,
                    "func": func,
                    "info": info,
                    "source_file": py_file.name,
                }
        except Exception:
            # Graceful degradation: skip tools that fail to import
            continue

    _DISCOVERY_DONE = True
    return _DISCOVERED_TOOLS


def _call_discovered_tool(tool_name: str, args: dict) -> str:
    """Dispatch un appel vers un outil auto-découvert."""
    # Rate limiting
    rl_error = _rate_limit_check(tool_name)
    if rl_error:
        return json.dumps({"error": rl_error, "tool": tool_name}, ensure_ascii=False)

    tools = discover_synapse_tools()
    if tool_name not in tools:
        return f"❌ Unknown discovered tool: {tool_name}"

    entry = tools[tool_name]
    func = entry["func"]

    # Map args to function params, injecting project_root if needed
    sig = inspect.signature(func)
    call_args = {}

    for param_name, param in sig.parameters.items():
        if param_name == "project_root" and param_name not in args:
            call_args[param_name] = str(PROJECT_ROOT)
        elif param_name in args:
            val = args[param_name]
            # Type coercion
            annotation = param.annotation
            if annotation is bool and isinstance(val, str):
                val = val.lower() in ("true", "1", "yes")
            elif annotation is int and isinstance(val, str):
                try:
                    val = int(val)
                except ValueError as _exc:
                    _log.debug("ValueError suppressed: %s", _exc)
                    # Silent exception — add logging when investigating issues
            call_args[param_name] = val
        elif param.default != inspect.Parameter.empty:
            pass  # Let the function use its default
        # If required and missing, the function will raise TypeError — that's fine

    try:
        result = func(**call_args)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, indent=2)
        return str(result)
    except Exception as e:
        return json.dumps({"error": str(e), "tool": tool_name}, ensure_ascii=False, indent=2)


def get_all_tool_names() -> list[str]:
    """Retourne la liste de TOUS les outils MCP (legacy + discovered)."""
    legacy = [
        "bmad_route_request", "bmad_classify_task", "bmad_router_stats",
        "bmad_rag_search", "bmad_rag_augment", "bmad_rag_status",
        "bmad_memory_push", "bmad_memory_diff",
    ]
    discovered = list(discover_synapse_tools().keys())
    return legacy + discovered


# ── MCP Server ──────────────────────────────────────────────────────────────

def create_server():
    """Crée et configure le serveur MCP BMAD Intelligence."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError:
        print(
            "❌ MCP SDK non installé.\n"
            "   pip install mcp\n"
            "   ou: pip install 'mcp[cli]'",
            file=sys.stderr,
        )
        sys.exit(1)

    server = Server("bmad-intelligence")

    # ── Tool Definitions ─────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        legacy_tools = [
            Tool(
                name="bmad_route_request",
                description=(
                    "Route une requête agent vers le modèle LLM optimal. "
                    "Retourne le modèle recommandé, le fallback, la complexité, "
                    "le type de tâche et le coût estimé."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "description": "ID de l'agent (architect, dev, qa, pm, etc.)",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Prompt ou description de la tâche à router",
                        },
                    },
                    "required": ["agent", "prompt"],
                },
            ),
            Tool(
                name="bmad_classify_task",
                description=(
                    "Classifie la complexité d'une tâche "
                    "(trivial/standard/complex/expert) et son type "
                    "(coding/reasoning/formatting/summarization/embedding)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Prompt ou description de la tâche",
                        },
                        "agent": {
                            "type": "string",
                            "description": "ID de l'agent (optionnel, pour boost contextuel)",
                            "default": "",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            Tool(
                name="bmad_router_stats",
                description=(
                    "Retourne les statistiques d'utilisation du LLM Router : "
                    "requêtes par modèle, coûts estimés, recommandations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "recommend": {
                            "type": "boolean",
                            "description": "Inclure les recommandations d'optimisation",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="bmad_rag_search",
                description=(
                    "Recherche sémantique dans l'index Qdrant BMAD. "
                    "Retourne les chunks les plus pertinents avec scores et metadata."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Requête en langage naturel",
                        },
                        "agent": {
                            "type": "string",
                            "description": "ID de l'agent (pour reranking boost)",
                            "default": "",
                        },
                        "collection": {
                            "type": "string",
                            "enum": ["agents", "memory", "docs", "code"],
                            "description": "Filtrer par collection (optionnel)",
                        },
                        "max_chunks": {
                            "type": "integer",
                            "description": "Nombre max de résultats",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="bmad_rag_augment",
                description=(
                    "Augmente un prompt avec du contexte RAG pertinent. "
                    "Retourne le prompt enrichi avec les chunks Qdrant les plus pertinents."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Prompt à augmenter",
                        },
                        "agent": {
                            "type": "string",
                            "description": "ID de l'agent",
                            "default": "",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            Tool(
                name="bmad_rag_status",
                description=(
                    "État des collections Qdrant : nombre de chunks indexés, "
                    "modèle d'embedding, santé du système RAG."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="bmad_memory_push",
                description=(
                    "Synchronise les fichiers mémoire BMAD (decisions-log, learnings, "
                    "failure-museum) vers Qdrant. Push uniquement les fichiers modifiés."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "Fichier spécifique à pousser (optionnel)",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Forcer le push même si non modifié",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="bmad_memory_diff",
                description=(
                    "Affiche les différences entre les fichiers mémoire MD et l'index Qdrant. "
                    "Montre les fichiers modifiés, nouveaux ou synchronisés."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

        # ── Auto-Discovered Synapse Tools ────────────────────────────────
        discovered = discover_synapse_tools()
        for tool_name, entry in sorted(discovered.items()):
            info = entry["info"]
            schema: dict = {"type": "object", "properties": info["properties"]}
            if info["required"]:
                schema["required"] = info["required"]
            legacy_tools.append(
                Tool(
                    name=tool_name,
                    description=info["description"],
                    inputSchema=schema,
                )
            )

        return legacy_tools

    # ── Tool Handlers ────────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            result = _handle_tool(name, arguments)
            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"❌ Error: {e}")]

    return server, stdio_server


def _handle_tool(name: str, args: dict) -> str:
    """Dispatch tool calls vers les implémentations."""

    if name == "bmad_route_request":
        router = _get_router()
        if not router:
            return "❌ LLM Router non disponible (llm-router.py introuvable)"
        decision = router.route(args["prompt"], args.get("agent", ""))
        return json.dumps(asdict(decision), ensure_ascii=False, indent=2)

    elif name == "bmad_classify_task":
        mod = _import_tool("llm-router.py", "llm_router_mcp")
        if not mod:
            return "❌ LLM Router non disponible"
        classifier = mod.TaskClassifier()
        result = classifier.classify(args["prompt"], args.get("agent", ""))
        return json.dumps(asdict(result), ensure_ascii=False, indent=2)

    elif name == "bmad_router_stats":
        router = _get_router()
        if not router:
            return "❌ LLM Router non disponible"
        stats = router.get_stats()
        output = {"stats": [asdict(s) for s in stats]}
        if args.get("recommend"):
            output["recommendations"] = router.get_recommendations()
        return json.dumps(output, ensure_ascii=False, indent=2)

    elif name == "bmad_rag_search":
        retriever = _get_retriever()
        if not retriever:
            # Fallback file-based
            mod = _import_tool("rag-retriever.py", "rag_retriever_mcp")
            if mod:
                result = mod.file_based_fallback(
                    PROJECT_ROOT, args["query"], args.get("agent", ""),
                    args.get("max_chunks", 5),
                )
                return json.dumps(asdict(result), ensure_ascii=False, indent=2)
            return "❌ RAG Retriever non disponible"

        collections = [args["collection"]] if args.get("collection") else None
        result = retriever.retrieve(
            query=args["query"],
            agent_id=args.get("agent", ""),
            collections=collections,
            max_chunks=args.get("max_chunks"),
        )

        if not result.qdrant_available:
            mod = _import_tool("rag-retriever.py", "rag_retriever_mcp")
            if mod:
                result = mod.file_based_fallback(
                    PROJECT_ROOT, args["query"], args.get("agent", ""),
                )

        return json.dumps(asdict(result), ensure_ascii=False, indent=2)

    elif name == "bmad_rag_augment":
        retriever = _get_retriever()
        if not retriever:
            return json.dumps({
                "augmented_prompt": args["prompt"],
                "chunks_count": 0,
                "fallback": True,
                "note": "RAG non disponible — prompt original retourné",
            })

        aug = retriever.augment_prompt(
            prompt=args["prompt"],
            agent_id=args.get("agent", ""),
        )
        return json.dumps({
            "augmented_prompt": aug.augmented_prompt,
            "chunks_count": len(aug.retrieval.chunks),
            "tokens_used": aug.budget_tokens_used,
            "budget_pct": aug.budget_pct,
            "retrieval_time_ms": aug.retrieval.retrieval_time_ms,
            "fallback_used": aug.retrieval.fallback_used,
        }, ensure_ascii=False, indent=2)

    elif name == "bmad_rag_status":
        retriever = _get_retriever()
        if not retriever:
            return json.dumps({"error": "RAG non disponible", "qdrant_reachable": False})
        report = retriever.preflight()
        return json.dumps(asdict(report), ensure_ascii=False, indent=2)

    elif name == "bmad_memory_push":
        syncer = _get_syncer()
        if not syncer:
            return "❌ Memory Sync non disponible"
        report = syncer.push(
            specific_file=args.get("file"),
            force=args.get("force", False),
        )
        return json.dumps(asdict(report), ensure_ascii=False, indent=2)

    elif name == "bmad_memory_diff":
        syncer = _get_syncer()
        if not syncer:
            return "❌ Memory Sync non disponible"
        diffs = syncer.diff()
        return json.dumps([asdict(d) for d in diffs], ensure_ascii=False, indent=2)

    else:
        # Try auto-discovered Synapse tools
        result = _call_discovered_tool(name, args)
        if not result.startswith("❌ Unknown discovered tool:"):
            return result
        return f"❌ Unknown tool: {name}"


# ── Main ────────────────────────────────────────────────────────────────────

async def _run_server():
    server, stdio_server_factory = create_server()
    async with stdio_server_factory() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Point d'entrée."""
    # Mode info si --version ou --help
    if "--version" in sys.argv:
        print(f"bmad-mcp-tools {BMAD_MCP_TOOLS_VERSION}")
        sys.exit(0)

    if "--help" in sys.argv or "-h" in sys.argv:
        legacy_names = [
            ("bmad_route_request", "Route une requête vers le modèle LLM optimal"),
            ("bmad_classify_task", "Classifie la complexité d'une tâche"),
            ("bmad_router_stats", "Stats d'utilisation du router"),
            ("bmad_rag_search", "Recherche sémantique Qdrant"),
            ("bmad_rag_augment", "Augmente un prompt avec contexte RAG"),
            ("bmad_rag_status", "État des collections Qdrant"),
            ("bmad_memory_push", "Push mémoire MD → Qdrant"),
            ("bmad_memory_diff", "Diff MD vs Qdrant"),
        ]
        discovered = discover_synapse_tools()
        print(f"""BMAD MCP Tools Server v{BMAD_MCP_TOOLS_VERSION}
Intelligence Layer : LLM Router + RAG + Memory Sync + Synapse Auto-Discovery

Transport : stdio (MCP standard)
Project   : {PROJECT_ROOT}

Legacy Tools (8) :""")
        for t_name, t_desc in legacy_names:
            print(f"  {t_name:<30s} {t_desc}")
        if discovered:
            print(f"\nAuto-Discovered Synapse Tools ({len(discovered)}) :")
            for t_name in sorted(discovered):
                desc = discovered[t_name]["info"]["description"]
                src = discovered[t_name]["source_file"]
                print(f"  {t_name:<30s} {desc}  [{src}]")
        print(f"""
Total : {8 + len(discovered)} tools

Configuration MCP (VS Code mcp.json) :
  {{
    "servers": {{
      "bmad-intelligence": {{
        "command": "python3",
        "args": ["{Path(__file__).resolve()}"],
        "env": {{ "BMAD_PROJECT_ROOT": "{PROJECT_ROOT}" }}
      }}
    }}
  }}
""")
        sys.exit(0)

    # Mode test : --list-tools (pas besoin du SDK MCP)
    if "--list-tools" in sys.argv:
        legacy_list = [
            "bmad_route_request", "bmad_classify_task", "bmad_router_stats",
            "bmad_rag_search", "bmad_rag_augment", "bmad_rag_status",
            "bmad_memory_push", "bmad_memory_diff",
        ]
        print("Legacy Tools:")
        for t in legacy_list:
            print(f"  ✅ {t}")

        discovered = discover_synapse_tools()
        if discovered:
            print(f"\nAuto-Discovered Synapse Tools ({len(discovered)}):")
            for t_name in sorted(discovered):
                src = discovered[t_name]["source_file"]
                print(f"  🔍 {t_name}  [{src}]")

        total = len(legacy_list) + len(discovered)
        print(f"\n  {total} tools registered — Project: {PROJECT_ROOT}")
        sys.exit(0)

    # Mode normal : MCP stdio server
    import asyncio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
