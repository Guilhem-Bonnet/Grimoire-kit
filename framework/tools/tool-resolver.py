#!/usr/bin/env python3
"""
tool-resolver.py — Tool Discovery, Provision & Resolution for Grimoire Agents.
===============================================================================

Le chaînon manquant entre "je veux faire X" et "j'utilise l'outil Y".

Comportement : quand un agent reçoit une tâche, au lieu de foncer tête baissée,
il consulte le resolver qui :
  1. RESOLVE  — Analyse l'intention → identifie les capabilities nécessaires
  2. DISCOVER — Cherche les outils capables (registre interne + MCP + npm/pypi)
  3. CHECK    — Vérifie la disponibilité (installé ? actif ? healthy ?)
  4. PROVISION — Installe/active si nécessaire et approuvé (pip, npx, mcp enable)
  5. PLAN     — Retourne le plan d'exécution avec les outils concrets
  6. CACHE    — Garde en mémoire ce qui a déjà été résolu

Philosophie : NE PAS RÉINVENTER LA ROUE. Chercher un outil existant avant de
coder quoi que ce soit from scratch.

Modes :
  resolve    — Résout une intention en plan d'outils
  discover   — Cherche les outils pour une capability donnée
  check      — Vérifie si un outil est disponible et prêt
  provision  — Prépare un outil (install, activate, configure)
  catalog    — Affiche le catalogue complet (internes + MCP + externes)
  cache      — Gère le cache de résolution

Usage :
  python3 tool-resolver.py --project-root . resolve --intent "créer une icône SVG 24x24"
  python3 tool-resolver.py --project-root . resolve --intent "rendre un objet 3D low-poly"
  python3 tool-resolver.py --project-root . discover --capability "svg-creation"
  python3 tool-resolver.py --project-root . check --tool inkscape-mcp
  python3 tool-resolver.py --project-root . provision --tool playwright --method pip
  python3 tool-resolver.py --project-root . catalog
  python3 tool-resolver.py --project-root . cache --clear

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.tool_resolver")

TOOL_RESOLVER_VERSION = "1.0.0"

CACHE_DIR = "_grimoire-output/.tool-resolver"
CACHE_FILE = "resolution-cache.json"
PROVISION_LOG = "provision-log.jsonl"

# ── Capability Taxonomy ──────────────────────────────────────────────────────
# Maps high-level capabilities to concrete tools/servers that provide them.
# This is the knowledge base that lets agents find tools instead of guessing.

CAPABILITY_CATALOG: dict[str, dict[str, Any]] = {
    # ── Visual Creation ──────────────────────────────────────────
    "3d-modeling": {
        "description": "Modélisation 3D — objets, scènes, matériaux",
        "providers": [
            {
                "id": "blender-mcp",
                "type": "mcp_server",
                "name": "Blender MCP",
                "tools": ["create_mesh", "edit_mesh", "apply_material",
                          "set_lighting", "render_scene", "export_format"],
                "provision": {"method": "mcp_enable", "server": "blender-mcp"},
                "check": {"method": "command", "command": "blender --version"},
                "priority": 1,
            },
            {
                "id": "openscad-cli",
                "type": "cli_external",
                "name": "OpenSCAD (CSG modeling)",
                "tools": ["render_csg"],
                "provision": {"method": "apt", "package": "openscad"},
                "check": {"method": "command", "command": "openscad --version"},
                "priority": 3,
            },
        ],
    },
    "svg-creation": {
        "description": "Création de fichiers SVG — icônes, illustrations vectorielles",
        "providers": [
            {
                "id": "svg-code",
                "type": "builtin",
                "name": "SVG Code pur (agent-generated)",
                "tools": ["generate_svg_code"],
                "provision": {"method": "none"},
                "check": {"method": "always_available"},
                "priority": 1,
                "note": "Adapté pour icônes simples, géométriques. Pas pour illustrations complexes.",
            },
            {
                "id": "inkscape-mcp",
                "type": "mcp_server",
                "name": "Inkscape MCP",
                "tools": ["create_path", "edit_path", "apply_filter",
                          "export_svg", "export_png"],
                "provision": {"method": "mcp_enable", "server": "inkscape-mcp"},
                "check": {"method": "command", "command": "inkscape --version"},
                "priority": 2,
                "note": "Pour illustrations complexes, paths de Bézier, filtres.",
            },
        ],
    },
    "svg-optimization": {
        "description": "Optimisation de fichiers SVG — poids, accessibilité, qualité",
        "providers": [
            {
                "id": "svgo",
                "type": "npm_package",
                "name": "SVGO — SVG Optimizer",
                "tools": ["optimize_svg"],
                "provision": {"method": "npx", "package": "svgo"},
                "check": {"method": "command", "command": "npx svgo --version"},
                "priority": 1,
            },
            {
                "id": "vision-judge-svg",
                "type": "grimoire_tool",
                "name": "vision-judge SVG offline validation",
                "tools": ["validate_svg_offline"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "vision-judge.py"},
                "priority": 2,
            },
        ],
    },
    "visual-evaluation": {
        "description": "Évaluation visuelle de la qualité d'un output (image, render, SVG)",
        "providers": [
            {
                "id": "vision-judge",
                "type": "grimoire_tool",
                "name": "vision-judge.py",
                "tools": ["evaluate_image", "compare_images", "validate_svg"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "vision-judge.py"},
                "priority": 1,
            },
            {
                "id": "vision-mcp",
                "type": "mcp_server",
                "name": "Vision MCP Provider",
                "tools": ["evaluate_image", "compare_images"],
                "provision": {"method": "mcp_enable", "server": "vision-provider"},
                "check": {"method": "command", "command": "python3 -m grimoire.mcp.vision_server --version"},
                "priority": 2,
            },
        ],
    },
    "image-generation": {
        "description": "Génération d'images — prompts, assets visuels",
        "providers": [
            {
                "id": "image-prompt",
                "type": "grimoire_tool",
                "name": "image-prompt.py (génère prompts, pas d'API)",
                "tools": ["generate_prompt"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "image-prompt.py"},
                "priority": 1,
            },
        ],
    },
    # ── Code & Analysis ──────────────────────────────────────────
    "code-analysis": {
        "description": "Analyse statique de code",
        "providers": [
            {
                "id": "ruff",
                "type": "pip_package",
                "name": "Ruff — Fast Python linter",
                "tools": ["lint", "format"],
                "provision": {"method": "pip", "package": "ruff"},
                "check": {"method": "command", "command": "ruff --version"},
                "priority": 1,
            },
            {
                "id": "shellcheck",
                "type": "cli_external",
                "name": "ShellCheck — Bash linter",
                "tools": ["lint_bash"],
                "provision": {"method": "apt", "package": "shellcheck"},
                "check": {"method": "command", "command": "shellcheck --version"},
                "priority": 1,
            },
        ],
    },
    "testing": {
        "description": "Exécution de tests automatisés",
        "providers": [
            {
                "id": "pytest",
                "type": "pip_package",
                "name": "pytest",
                "tools": ["run_tests"],
                "provision": {"method": "pip", "package": "pytest"},
                "check": {"method": "command", "command": "python3 -m pytest --version"},
                "priority": 1,
            },
            {
                "id": "agent-test",
                "type": "grimoire_tool",
                "name": "agent-test.py — behavioral tests for agents",
                "tools": ["test_agent", "benchmark_agents"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "agent-test.py"},
                "priority": 1,
            },
        ],
    },
    # ── Web & Browser ────────────────────────────────────────────
    "web-browsing": {
        "description": "Navigation web, scraping, screenshots",
        "providers": [
            {
                "id": "web-browser-grimoire",
                "type": "grimoire_tool",
                "name": "web-browser.py (fetch, screenshot, interact, readability)",
                "tools": ["mcp_web_fetch", "mcp_web_screenshot", "mcp_web_interact", "mcp_web_readability"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "web-browser.py"},
                "priority": 1,
                "note": "Toujours disponible (fallback urllib). Playwright optionnel pour JS rendering.",
            },
            {
                "id": "browser-mcp",
                "type": "mcp_server",
                "name": "Anthropic Browser MCP",
                "tools": ["navigate", "click", "type", "screenshot"],
                "provision": {"method": "mcp_enable", "server": "browser"},
                "check": {"method": "command", "command": "npx @anthropic/mcp-browser --version"},
                "priority": 2,
            },
        ],
    },
    # ── Task Management ──────────────────────────────────────────
    "task-management": {
        "description": "Gestion de tâches agents — DAG, scheduling, delivery",
        "providers": [
            {
                "id": "agent-task-system",
                "type": "grimoire_tool",
                "name": "agent-task-system.py",
                "tools": ["create_task", "schedule", "status", "graph"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "agent-task-system.py"},
                "priority": 1,
            },
        ],
    },
    # ── Documentation ────────────────────────────────────────────
    "documentation": {
        "description": "Génération et maintenance de documentation",
        "providers": [
            {
                "id": "auto-doc",
                "type": "grimoire_tool",
                "name": "auto-doc.py",
                "tools": ["generate_docs"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "auto-doc.py"},
                "priority": 1,
            },
        ],
    },
    # ── Memory & Context ─────────────────────────────────────────
    "memory-management": {
        "description": "Gestion de la mémoire agent — indexation, recherche, consolidation",
        "providers": [
            {
                "id": "rag-indexer",
                "type": "grimoire_tool",
                "name": "rag-indexer.py + rag-retriever.py",
                "tools": ["index", "search", "retrieve"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "rag-indexer.py"},
                "priority": 1,
            },
            {
                "id": "memory-lint",
                "type": "grimoire_tool",
                "name": "memory-lint.py",
                "tools": ["lint_memory", "check_freshness"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "memory-lint.py"},
                "priority": 2,
            },
        ],
    },
    # ── Monitoring ───────────────────────────────────────────────
    "agent-monitoring": {
        "description": "Surveillance et détection de dérive des agents",
        "providers": [
            {
                "id": "agent-watch",
                "type": "grimoire_tool",
                "name": "agent-watch.py",
                "tools": ["snapshot", "check_drift", "history"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "agent-watch.py"},
                "priority": 1,
            },
            {
                "id": "fitness-tracker",
                "type": "grimoire_tool",
                "name": "fitness-tracker.py",
                "tools": ["check_fitness", "trend"],
                "provision": {"method": "none"},
                "check": {"method": "grimoire_tool", "tool": "fitness-tracker.py"},
                "priority": 2,
            },
        ],
    },
}

# ── Intent → Capability Mapping ─────────────────────────────────────────────
# Maps natural language patterns to capabilities. This lets agents describe
# WHAT they want to do and get back WHICH tools to use.

INTENT_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": r"(?:3d|3D|blender|modélis|mesh|render(?!.*page)|rendu\s*3d|scène\s*3d|objet\s*3d|low.?poly)",
        "capabilities": ["3d-modeling", "visual-evaluation"],
        "description": "Modélisation ou rendu 3D",
    },
    {
        "pattern": r"(?:svg|icône|icon|illustration|vectoriel|inkscape|dessin)",
        "capabilities": ["svg-creation", "visual-evaluation"],
        "description": "Création SVG ou illustration vectorielle",
    },
    {
        "pattern": r"(?:optimi\w*\s*svg|compress\w*\s*svg|minif\w*\s*svg|nettoy\w*\s*svg)",
        "capabilities": ["svg-optimization"],
        "description": "Optimisation de fichiers SVG",
    },
    {
        "pattern": r"(?:évalue\w*\s*visuel|visual\s*quality|juge\w*\s*image|vision\s*loop|score\s*visuel)",
        "capabilities": ["visual-evaluation"],
        "description": "Évaluation visuelle",
    },
    {
        "pattern": r"(?:image|photo|asset\s*visuel|visuels?|graphi)",
        "capabilities": ["image-generation"],
        "description": "Génération d'images",
    },
    {
        "pattern": r"(?:test|tdd|pytest|qualité|qa|bench)",
        "capabilities": ["testing"],
        "description": "Tests et QA",
    },
    {
        "pattern": r"(?:lint|format|style|ruff|shellcheck|analyse.*code)",
        "capabilities": ["code-analysis"],
        "description": "Analyse de code",
    },
    {
        "pattern": r"(?:web|browser|scrape|screenshot|navig|site)",
        "capabilities": ["web-browsing"],
        "description": "Navigation web",
    },
    {
        "pattern": r"(?:tâche|task|planif|schedul|dag|workflow\s*agent)",
        "capabilities": ["task-management"],
        "description": "Gestion de tâches agents",
    },
    {
        "pattern": r"(?:doc|documentation|readme|guide|api\s*doc)",
        "capabilities": ["documentation"],
        "description": "Documentation",
    },
    {
        "pattern": r"(?:mémoire|memory|rag|index|context|session|consoli)",
        "capabilities": ["memory-management"],
        "description": "Gestion de mémoire",
    },
    {
        "pattern": r"(?:monitor|surveill|drift|watch|dérive|fitness|santé|health)",
        "capabilities": ["agent-monitoring"],
        "description": "Monitoring agents",
    },
]


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ToolCandidate:
    """Un outil candidat pour résoudre une intention."""

    provider_id: str = ""
    provider_type: str = ""       # grimoire_tool | mcp_server | pip_package | npm_package | cli_external | builtin
    name: str = ""
    capability: str = ""
    tools_offered: list[str] = field(default_factory=list)
    priority: int = 99
    available: bool = False
    provision_method: str = ""    # none | pip | npx | apt | mcp_enable
    provision_detail: str = ""
    note: str = ""


@dataclass
class ProvisionAction:
    """Une action de provision à exécuter."""

    provider_id: str = ""
    method: str = ""              # pip | npx | apt | mcp_enable | post_install | none
    command: str = ""
    package: str = ""
    safe: bool = True             # Peut être exécuté sans risque ?
    requires_confirmation: bool = True


@dataclass
class ResolutionPlan:
    """Plan de résolution complet pour une intention."""

    intent: str = ""
    timestamp: str = ""
    matched_capabilities: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    recommended: list[dict[str, Any]] = field(default_factory=list)
    provision_needed: list[dict[str, Any]] = field(default_factory=list)
    ready_to_use: list[dict[str, Any]] = field(default_factory=list)
    fallback_plan: str = ""


# ── Availability Checks ─────────────────────────────────────────────────────


def _check_command(command: str) -> bool:
    """Vérifie si une commande est disponible."""
    parts = command.split()
    if not parts:
        return False
    executable = parts[0]
    # For npx commands, just check if npx is available
    if executable == "npx":
        return shutil.which("npx") is not None
    if shutil.which(executable) is None:
        return False
    # Actually run the version check
    try:
        cp = subprocess.run(
            parts, capture_output=True, timeout=10,
        )
        return cp.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_python_import(module: str) -> bool:
    """Vérifie si un module Python est importable."""
    try:
        cp = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, timeout=10,
        )
        return cp.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_grimoire_tool(tool_name: str, project_root: Path) -> bool:
    """Vérifie si un outil Grimoire existe dans framework/tools/."""
    tool_path = project_root / "framework" / "tools" / tool_name
    return tool_path.exists()


def check_provider_availability(
    provider: dict[str, Any],
    project_root: Path,
) -> bool:
    """Vérifie si un provider est disponible."""
    check = provider.get("check", {})
    method = check.get("method", "")

    if method == "always_available":
        return True
    if method == "command":
        return _check_command(check.get("command", ""))
    if method == "python_import":
        return _check_python_import(check.get("module", ""))
    if method == "grimoire_tool":
        return _check_grimoire_tool(check.get("tool", ""), project_root)
    return False


# ── Provision Planning ───────────────────────────────────────────────────────


def plan_provision(provider: dict[str, Any]) -> list[ProvisionAction]:
    """Planifie les actions de provision pour un provider."""
    prov = provider.get("provision", {})
    method = prov.get("method", "none")
    actions: list[ProvisionAction] = []

    if method == "none":
        return actions

    if method == "pip":
        package = prov.get("package", "")
        actions.append(ProvisionAction(
            provider_id=provider.get("id", ""),
            method="pip",
            command=f"{sys.executable} -m pip install {package}",
            package=package,
            safe=True,
            requires_confirmation=True,
        ))
        # Post-install command (e.g., playwright install chromium)
        post = prov.get("post_install", "")
        if post:
            actions.append(ProvisionAction(
                provider_id=provider.get("id", ""),
                method="post_install",
                command=post,
                package=package,
                safe=True,
                requires_confirmation=True,
            ))

    elif method == "npx":
        package = prov.get("package", "")
        actions.append(ProvisionAction(
            provider_id=provider.get("id", ""),
            method="npx",
            command=f"npx {package} --version",
            package=package,
            safe=True,
            requires_confirmation=False,  # npx is ephemeral
        ))

    elif method == "apt":
        package = prov.get("package", "")
        actions.append(ProvisionAction(
            provider_id=provider.get("id", ""),
            method="apt",
            command=f"sudo apt install -y {package}",
            package=package,
            safe=False,  # Needs sudo
            requires_confirmation=True,
        ))

    elif method == "mcp_enable":
        server = prov.get("server", "")
        actions.append(ProvisionAction(
            provider_id=provider.get("id", ""),
            method="mcp_enable",
            command=f"Activer le serveur MCP '{server}' dans _grimoire/_config/mcp-servers.json",
            package=server,
            safe=True,
            requires_confirmation=True,
        ))

    return actions


# ── Resolution Engine ────────────────────────────────────────────────────────


def resolve_intent(intent: str, project_root: Path) -> ResolutionPlan:
    """Résout une intention en plan d'outils concrets.

    C'est la fonction principale : elle prend un texte libre décrivant ce que
    l'agent veut faire, et retourne un plan avec les outils à utiliser.
    """
    plan = ResolutionPlan(
        intent=intent,
        timestamp=datetime.now().isoformat(),
    )

    # 1. Match intent → capabilities
    intent_lower = intent.lower()
    matched_caps: set[str] = set()
    for pattern_entry in INTENT_PATTERNS:
        if re.search(pattern_entry["pattern"], intent_lower, re.IGNORECASE):
            for cap in pattern_entry["capabilities"]:
                matched_caps.add(cap)

    # If no match, try keyword-based fallback against capability descriptions
    if not matched_caps:
        for cap_id, cap_info in CAPABILITY_CATALOG.items():
            desc = cap_info.get("description", "").lower()
            # Simple word overlap
            intent_words = set(re.findall(r'\w{3,}', intent_lower))
            desc_words = set(re.findall(r'\w{3,}', desc))
            if len(intent_words & desc_words) >= 2:
                matched_caps.add(cap_id)

    plan.matched_capabilities = sorted(matched_caps)

    # 2. Discover providers for each capability
    all_candidates: list[ToolCandidate] = []
    for cap_id in plan.matched_capabilities:
        cap_info = CAPABILITY_CATALOG.get(cap_id)
        if not cap_info:
            continue
        for provider in cap_info.get("providers", []):
            available = check_provider_availability(provider, project_root)
            candidate = ToolCandidate(
                provider_id=provider.get("id", ""),
                provider_type=provider.get("type", ""),
                name=provider.get("name", ""),
                capability=cap_id,
                tools_offered=provider.get("tools", []),
                priority=provider.get("priority", 99),
                available=available,
                provision_method=provider.get("provision", {}).get("method", "none"),
                provision_detail=json.dumps(provider.get("provision", {})),
                note=provider.get("note", ""),
            )
            all_candidates.append(candidate)

    plan.candidates = [asdict(c) for c in all_candidates]

    # 3. Classify: ready vs needs provision
    ready = [c for c in all_candidates if c.available]
    needs_provision = [c for c in all_candidates if not c.available and c.provision_method != "none"]

    # 4. Recommend: prefer available, then by priority
    recommended = sorted(ready, key=lambda c: c.priority)
    if not recommended and needs_provision:
        # If nothing available, recommend provision with lowest effort
        recommended = sorted(needs_provision, key=lambda c: (
            0 if c.provision_method in ("none", "mcp_enable") else
            1 if c.provision_method == "npx" else
            2 if c.provision_method == "pip" else
            3
        ))

    plan.recommended = [asdict(c) for c in recommended]
    plan.ready_to_use = [asdict(c) for c in ready]

    # 5. Provision actions for unavailable tools
    provision_actions: list[dict[str, Any]] = []
    for candidate in needs_provision:
        # Re-find the original provider dict
        cap_info = CAPABILITY_CATALOG.get(candidate.capability, {})
        for provider in cap_info.get("providers", []):
            if provider.get("id") == candidate.provider_id:
                actions = plan_provision(provider)
                for a in actions:
                    provision_actions.append(asdict(a))
    plan.provision_needed = provision_actions

    # 6. Fallback plan
    if not ready and not needs_provision:
        plan.fallback_plan = (
            "Aucun outil trouvé pour cette intention. "
            "L'agent devra travailler en mode natif (code pur) sans outil spécialisé."
        )
    elif not ready and needs_provision:
        methods = {a.get("method", "") for a in provision_actions}
        plan.fallback_plan = (
            f"Outils identifiés mais non installés. "
            f"Méthodes de provision disponibles : {', '.join(sorted(methods))}. "
            f"En attendant, utiliser le mode natif si possible."
        )

    # 7. Cache the resolution
    _cache_resolution(plan, project_root)

    return plan


def discover_for_capability(
    capability: str,
    project_root: Path,
) -> list[ToolCandidate]:
    """Découvre tous les providers pour une capability donnée."""
    cap_info = CAPABILITY_CATALOG.get(capability)
    if not cap_info:
        return []

    candidates: list[ToolCandidate] = []
    for provider in cap_info.get("providers", []):
        available = check_provider_availability(provider, project_root)
        candidates.append(ToolCandidate(
            provider_id=provider.get("id", ""),
            provider_type=provider.get("type", ""),
            name=provider.get("name", ""),
            capability=capability,
            tools_offered=provider.get("tools", []),
            priority=provider.get("priority", 99),
            available=available,
            provision_method=provider.get("provision", {}).get("method", "none"),
            note=provider.get("note", ""),
        ))
    return sorted(candidates, key=lambda c: (not c.available, c.priority))


def execute_provision(
    action: ProvisionAction,
    project_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Exécute une action de provision.

    SÉCURITÉ : les commandes sont construites à partir de données internes
    (CAPABILITY_CATALOG), pas d'input utilisateur direct dans les commandes.
    """
    result: dict[str, Any] = {
        "provider_id": action.provider_id,
        "method": action.method,
        "command": action.command,
        "dry_run": dry_run,
        "success": False,
        "output": "",
    }

    if dry_run:
        result["output"] = f"[DRY RUN] Would execute: {action.command}"
        result["success"] = True
        return result

    if action.method == "mcp_enable":
        # Enable MCP server in config
        success = _enable_mcp_server(action.package, project_root)
        result["success"] = success
        result["output"] = f"MCP server '{action.package}' enabled" if success else "Failed to enable"
        return result

    # For pip/npx/apt — only execute whitelisted commands
    allowed_prefixes = [
        f"{sys.executable} -m pip install ",
        "npx ",
        "playwright install ",
    ]
    if not any(action.command.startswith(prefix) for prefix in allowed_prefixes):
        result["output"] = f"Command not in whitelist: {action.command}"
        return result

    try:
        cp = subprocess.run(
            action.command.split(),
            capture_output=True,
            timeout=120,
            text=True,
        )
        result["success"] = cp.returncode == 0
        result["output"] = cp.stdout[-500:] if cp.stdout else cp.stderr[-500:]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        result["output"] = str(e)

    # Log provision
    _log_provision(result, project_root)

    return result


# ── MCP Server Activation ───────────────────────────────────────────────────


def _enable_mcp_server(server_name: str, project_root: Path) -> bool:
    """Active un serveur MCP dans la config."""
    config_path = project_root / "_grimoire" / "_config" / "mcp-servers.json"
    if not config_path.exists():
        return False
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for srv in config.get("servers", []):
            if srv.get("name") == server_name:
                srv["enabled"] = True
                config_path.write_text(
                    json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


# ── Cache ────────────────────────────────────────────────────────────────────


def _cache_resolution(plan: ResolutionPlan, project_root: Path) -> None:
    """Cache un résultat de résolution."""
    cache_dir = project_root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / CACHE_FILE

    cache: dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    # Key: normalized intent
    key = re.sub(r'\s+', ' ', plan.intent.lower().strip())
    resolutions = cache.setdefault("resolutions", {})
    resolutions[key] = {
        "plan": asdict(plan),
        "cached_at": datetime.now().isoformat(),
    }

    # Limit cache size
    if len(resolutions) > 200:
        sorted_entries = sorted(resolutions.items(), key=lambda x: x[1].get("cached_at", ""))
        cache["resolutions"] = dict(sorted_entries[-100:])

    cache_path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_cached_resolution(intent: str, project_root: Path) -> ResolutionPlan | None:
    """Récupère une résolution depuis le cache."""
    cache_path = project_root / CACHE_DIR / CACHE_FILE
    if not cache_path.exists():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    key = re.sub(r'\s+', ' ', intent.lower().strip())
    entry = cache.get("resolutions", {}).get(key)
    if not entry:
        return None

    plan_data = entry.get("plan", {})
    plan = ResolutionPlan()
    for k, v in plan_data.items():
        if hasattr(plan, k):
            setattr(plan, k, v)
    return plan


def clear_cache(project_root: Path) -> bool:
    """Vide le cache de résolution."""
    cache_path = project_root / CACHE_DIR / CACHE_FILE
    if cache_path.exists():
        cache_path.unlink()
        return True
    return False


# ── Provision Log ────────────────────────────────────────────────────────────


def _log_provision(result: dict[str, Any], project_root: Path) -> None:
    """Enregistre une action de provision dans le log."""
    log_dir = project_root / CACHE_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / PROVISION_LOG
    entry = {
        **result,
        "timestamp": datetime.now().isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_tool_resolve(
    intent: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: résout une intention en plan d'outils concrets.

    Quand un agent veut faire quelque chose, il appelle cette fonction AVANT
    d'essayer de le faire lui-même. Le resolver cherche les outils existants.

    Args:
        intent: Description en langage naturel de ce que l'agent veut faire.
            Ex: "créer une icône SVG 24x24 de cerveau"
            Ex: "rendre un objet 3D low-poly en glTF"
            Ex: "évaluer la qualité visuelle d'une image"
        project_root: Racine du projet.

    Returns:
        ResolutionPlan avec candidats, outils prêts, et actions de provision.
    """
    root = Path(project_root).resolve()
    plan = resolve_intent(intent, root)
    return asdict(plan)


def mcp_tool_discover(
    capability: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: découvre les outils pour une capability.

    Args:
        capability: ID de la capability recherchée.
            Capabilities disponibles : 3d-modeling, svg-creation, svg-optimization,
            visual-evaluation, image-generation, code-analysis, testing,
            web-browsing, task-management, documentation, memory-management,
            agent-monitoring
        project_root: Racine du projet.

    Returns:
        {capability, providers: [...], available_count, total_count}
    """
    root = Path(project_root).resolve()
    candidates = discover_for_capability(capability, root)
    return {
        "capability": capability,
        "providers": [asdict(c) for c in candidates],
        "available_count": sum(1 for c in candidates if c.available),
        "total_count": len(candidates),
    }


def mcp_tool_check(
    tool_id: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: vérifie si un outil spécifique est disponible.

    Args:
        tool_id: ID du provider à vérifier (ex: "blender-mcp", "ruff", "svgo").
        project_root: Racine du projet.

    Returns:
        {tool_id, available, check_detail}
    """
    root = Path(project_root).resolve()
    for cap_info in CAPABILITY_CATALOG.values():
        for provider in cap_info.get("providers", []):
            if provider.get("id") == tool_id:
                available = check_provider_availability(provider, root)
                return {
                    "tool_id": tool_id,
                    "name": provider.get("name", ""),
                    "type": provider.get("type", ""),
                    "available": available,
                    "provision": provider.get("provision", {}),
                }
    return {"tool_id": tool_id, "available": False, "error": "Unknown tool ID"}


def mcp_tool_provision(
    tool_id: str,
    dry_run: bool = True,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: provisionne (installe/active) un outil.

    ATTENTION: dry_run=True par défaut. Mettre à False pour exécuter réellement.

    Args:
        tool_id: ID du provider à provisionner.
        dry_run: Si True, montre ce qui serait fait sans exécuter.
        project_root: Racine du projet.

    Returns:
        {tool_id, actions: [{method, command, success, output}]}
    """
    root = Path(project_root).resolve()
    for cap_info in CAPABILITY_CATALOG.values():
        for provider in cap_info.get("providers", []):
            if provider.get("id") == tool_id:
                actions = plan_provision(provider)
                results = []
                for action in actions:
                    result = execute_provision(action, root, dry_run=dry_run)
                    results.append(result)
                return {
                    "tool_id": tool_id,
                    "dry_run": dry_run,
                    "actions": results,
                }
    return {"tool_id": tool_id, "error": "Unknown tool ID"}


def mcp_tool_catalog(
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: catalogue complet de toutes les capabilities et providers.

    Args:
        project_root: Racine du projet.

    Returns:
        {capabilities: [{id, description, providers: [{id, name, available}]}]}
    """
    root = Path(project_root).resolve()
    capabilities = []
    for cap_id, cap_info in CAPABILITY_CATALOG.items():
        providers = []
        for provider in cap_info.get("providers", []):
            available = check_provider_availability(provider, root)
            providers.append({
                "id": provider.get("id", ""),
                "name": provider.get("name", ""),
                "type": provider.get("type", ""),
                "available": available,
                "tools": provider.get("tools", []),
                "note": provider.get("note", ""),
            })
        capabilities.append({
            "id": cap_id,
            "description": cap_info.get("description", ""),
            "providers": providers,
            "available_count": sum(1 for p in providers if p["available"]),
        })
    return {"capabilities": capabilities, "total_capabilities": len(capabilities)}


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_resolve(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    plan = resolve_intent(args.intent, root)

    if args.json:
        print(json.dumps(asdict(plan), indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔍 Résolution : \"{plan.intent}\"\n")

        if plan.matched_capabilities:
            print(f"  📋 Capabilities identifiées : {', '.join(plan.matched_capabilities)}\n")
        else:
            print("  ⚠️  Aucune capability identifiée pour cette intention.\n")

        if plan.ready_to_use:
            print(f"  ✅ Outils PRÊTS ({len(plan.ready_to_use)}) :")
            for c in plan.ready_to_use:
                print(f"    🟢 {c['name']} [{c['provider_type']}]")
                print(f"       Tools: {', '.join(c['tools_offered'])}")
                if c.get("note"):
                    print(f"       💡 {c['note']}")

        if plan.provision_needed:
            print(f"\n  📦 Provision nécessaire ({len(plan.provision_needed)}) :")
            for a in plan.provision_needed:
                confirm = " ⚠️ confirmation requise" if a.get("requires_confirmation") else ""
                print(f"    🔧 [{a['method']}] {a['command']}{confirm}")

        if plan.recommended and not plan.ready_to_use:
            print("\n  💡 Recommandé (après provision) :")
            for c in plan.recommended[:3]:
                print(f"    ➡️  {c['name']} [{c['provision_method']}]")

        if plan.fallback_plan:
            print(f"\n  🔄 Fallback : {plan.fallback_plan}")

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    candidates = discover_for_capability(args.capability, root)

    if args.json:
        print(json.dumps([asdict(c) for c in candidates], indent=2, ensure_ascii=False))
    else:
        if not candidates:
            print(f"  ❌ Aucun provider pour capability '{args.capability}'")
            print(f"  Capabilities connues : {', '.join(sorted(CAPABILITY_CATALOG.keys()))}")
            return 1

        cap_desc = CAPABILITY_CATALOG.get(args.capability, {}).get("description", "")
        print(f"\n  🔎 Providers pour '{args.capability}' — {cap_desc}\n")
        for c in candidates:
            icon = "🟢" if c.available else "🔴"
            print(f"  {icon} {c.name} [{c.provider_type}] (priority: {c.priority})")
            print(f"     Tools: {', '.join(c.tools_offered)}")
            if not c.available and c.provision_method != "none":
                print(f"     📦 Provision: {c.provision_method}")
            if c.note:
                print(f"     💡 {c.note}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    result = mcp_tool_check(args.tool, str(root))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        icon = "🟢" if result.get("available") else "🔴"
        print(f"\n  {icon} {result.get('name', args.tool)} — "
              f"{'disponible' if result.get('available') else 'non disponible'}")
        if not result.get("available") and "provision" in result:
            prov = result["provision"]
            if prov.get("method") != "none":
                print(f"  📦 Pour installer : {prov.get('method')} {prov.get('package', '')}")
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    dry_run = not args.execute
    result = mcp_tool_provision(args.tool, dry_run=dry_run, project_root=str(root))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if "error" in result:
            print(f"  ❌ {result['error']}")
            return 1
        mode = "EXÉCUTION" if not dry_run else "DRY RUN"
        print(f"\n  📦 Provision [{mode}] : {args.tool}\n")
        for a in result.get("actions", []):
            icon = "✅" if a.get("success") else "❌"
            print(f"  {icon} [{a['method']}] {a['command']}")
            if a.get("output"):
                print(f"     → {a['output'][:200]}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    result = mcp_tool_catalog(str(root))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n  📚 Tool Catalog — {result['total_capabilities']} capabilities\n")
        for cap in result["capabilities"]:
            avail = cap["available_count"]
            icon = "🟢" if avail > 0 else "🔴"
            print(f"  {icon} {cap['id']:25s} {cap['description']}")
            for p in cap["providers"]:
                pi = "✅" if p["available"] else "⬜"
                print(f"     {pi} {p['name']} [{p['type']}]")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    if args.clear:
        cleared = clear_cache(root)
        print("  ✅ Cache vidé" if cleared else "  ⚪ Pas de cache à vider")
        return 0

    cache_path = root / CACHE_DIR / CACHE_FILE
    if not cache_path.exists():
        print("  ⚪ Pas de cache")
        return 0

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    resolutions = cache.get("resolutions", {})

    if args.json:
        print(json.dumps(cache, indent=2, ensure_ascii=False))
    else:
        print(f"\n  📦 Cache de résolution — {len(resolutions)} entrées\n")
        for key, entry in list(resolutions.items())[-10:]:
            cached_at = entry.get("cached_at", "?")[:16]
            plan = entry.get("plan", {})
            caps = plan.get("matched_capabilities", [])
            ready = len(plan.get("ready_to_use", []))
            print(f"  • \"{key[:60]}\" [{cached_at}] → {', '.join(caps)} ({ready} ready)")
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tool-resolver",
        description="Tool Discovery, Provision & Resolution for Grimoire Agents",
    )
    p.add_argument("--project-root", default=".", help="Project root directory")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = p.add_subparsers(dest="command")

    # resolve
    r = sub.add_parser("resolve", help="Resolve an intent into a tool plan")
    r.add_argument("--intent", required=True, help="Natural language description of what to do")
    r.set_defaults(func=cmd_resolve)

    # discover
    d = sub.add_parser("discover", help="Discover tools for a capability")
    d.add_argument("--capability", required=True, help="Capability ID")
    d.set_defaults(func=cmd_discover)

    # check
    c = sub.add_parser("check", help="Check if a tool is available")
    c.add_argument("--tool", required=True, help="Tool/provider ID")
    c.set_defaults(func=cmd_check)

    # provision
    pv = sub.add_parser("provision", help="Provision (install/activate) a tool")
    pv.add_argument("--tool", required=True, help="Tool/provider ID to provision")
    pv.add_argument("--execute", action="store_true", help="Actually execute (default is dry-run)")
    pv.set_defaults(func=cmd_provision)

    # catalog
    cat = sub.add_parser("catalog", help="Full capability & tool catalog")
    cat.set_defaults(func=cmd_catalog)

    # cache
    ca = sub.add_parser("cache", help="Manage resolution cache")
    ca.add_argument("--clear", action="store_true", help="Clear the cache")
    ca.set_defaults(func=cmd_cache)

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
