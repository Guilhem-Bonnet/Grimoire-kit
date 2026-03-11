#!/usr/bin/env python3
"""
expert-tool-chain.py — Expert Tool Chain (ETC) pour agents avec outils MCP externes.
=====================================================================================

Pipeline qui combine :
  1. Expertise Layer  — Connaissances domaine, best practices, patterns d'erreur
  2. MCP Bridge       — Communication avec serveurs MCP externes (Blender, Inkscape, etc.)
  3. Vision Loop      — Évaluation visuelle itérative via LLM multimodal
  4. Iteration Engine — Boucle create → judge → refine → judge → accept/escalate

L'objectif est qu'un agent ne soit pas juste "connecté" à un outil, mais
qu'il soit EXPERT de cet outil — avec feedback visuel et itération.

Modes :
  execute  — Exécuter un workflow expert complet (create → judge → iterate)
  inspect  — Inspecter les capabilities d'un serveur MCP
  catalog  — Lister les expertise profiles disponibles
  history  — Historique des exécutions ETC

Usage :
  python3 expert-tool-chain.py --project-root . execute --profile blender-simple --brief "Low-poly robot"
  python3 expert-tool-chain.py --project-root . execute --profile svg-icon --brief "Memory icon 24x24"
  python3 expert-tool-chain.py --project-root . inspect --server blender-mcp
  python3 expert-tool-chain.py --project-root . catalog
  python3 expert-tool-chain.py --project-root . history --last 5

Stdlib only — invoque vision-judge.py et mcp-proxy.py via importlib.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.expert_tool_chain")

ETC_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

ETC_DIR = "_grimoire-output/.etc"
HISTORY_FILE = "etc-history.jsonl"
MAX_ITERATIONS = 5
DEFAULT_ACCEPTANCE_THRESHOLD = 0.75

# ── Expertise Profiles ──────────────────────────────────────────────────────

EXPERTISE_PROFILES: dict[str, dict[str, Any]] = {
    "blender-simple": {
        "name": "Blender — Simple 3D Object",
        "description": "Création d'objets 3D simples (primitives, low-poly, icônes 3D)",
        "domain": "3d-modeling",
        "mcp_server": "blender-mcp",
        "vision_rubric": "3d-object",
        "max_iterations": 5,
        "acceptance_threshold": 0.70,
        "knowledge": {
            "topology": "Quad-based topology for deformation. Triangulate only for game export.",
            "materials": "PBR workflow: Base Color, Metallic, Roughness, Normal map.",
            "lighting": "Three-point lighting as baseline. HDRI for realistic environments.",
            "scale": "Real-world units. 1 Blender Unit = 1 meter.",
            "export": "glTF 2.0 for web, FBX for game engines, OBJ for interchange.",
        },
        "workflow_steps": [
            {
                "step": "create_base_mesh",
                "description": "Create the base primitive or mesh",
                "mcp_tool": "create_mesh",
                "tips": ["Start with simplest primitive that fits the shape",
                         "Use subdivision surface modifier for organic shapes"],
            },
            {
                "step": "refine_geometry",
                "description": "Add detail and refine shape",
                "mcp_tool": "edit_mesh",
                "tips": ["Keep quad-based topology",
                         "Use loop cuts for edge control"],
            },
            {
                "step": "apply_material",
                "description": "Apply PBR materials",
                "mcp_tool": "apply_material",
                "tips": ["Use Principled BSDF shader",
                         "Set metallic based on real material properties"],
            },
            {
                "step": "set_lighting",
                "description": "Set up scene lighting",
                "mcp_tool": "set_lighting",
                "tips": ["Key light at 45° angle",
                         "Fill light at half intensity opposite key",
                         "Rim light from behind for edge definition"],
            },
            {
                "step": "render_preview",
                "description": "Render preview for evaluation",
                "mcp_tool": "render_scene",
                "tips": ["Use Eevee for fast preview, Cycles for final",
                         "64 samples for preview, 256+ for final"],
            },
        ],
        "common_errors": {
            "inverted_normals": "If faces appear dark, recalculate normals (Shift+N in edit mode)",
            "z_fighting": "Overlapping faces cause flickering — merge or delete duplicates",
            "stretched_uvs": "UV stretching visible in textures — re-unwrap with Smart UV Project",
        },
    },
    "blender-scene": {
        "name": "Blender — Scene Composition",
        "description": "Composition de scènes 3D avec multiples objets, éclairage, caméra",
        "domain": "3d-modeling",
        "mcp_server": "blender-mcp",
        "vision_rubric": "3d-object",
        "max_iterations": 8,
        "acceptance_threshold": 0.70,
        "knowledge": {
            "composition": "Rule of thirds for camera placement. Use leading lines.",
            "depth": "Use depth of field (DoF) to guide viewer attention.",
            "atmosphere": "Volumetric lighting or fog adds depth and mood.",
            "color_theory": "Warm/cool contrast for visual interest. Monochromatic for mood.",
        },
        "workflow_steps": [
            {"step": "block_out", "description": "Place basic shapes for composition", "mcp_tool": "create_mesh"},
            {"step": "refine_hero", "description": "Detail the main subject", "mcp_tool": "edit_mesh"},
            {"step": "populate_secondary", "description": "Add secondary elements", "mcp_tool": "create_mesh"},
            {"step": "material_pass", "description": "Apply materials to all objects", "mcp_tool": "apply_material"},
            {"step": "lighting_pass", "description": "Set up scene lighting", "mcp_tool": "set_lighting"},
            {"step": "camera_setup", "description": "Position camera and set DoF", "mcp_tool": "set_camera"},
            {"step": "render_preview", "description": "Render for evaluation", "mcp_tool": "render_scene"},
        ],
        "common_errors": {
            "scale_mismatch": "Objects at different scales look wrong — use real-world dimensions",
            "flat_lighting": "Single light source makes scene flat — add fill and rim lights",
        },
    },
    "svg-icon": {
        "name": "SVG Icon — Line Art",
        "description": "Création d'icônes SVG line art (24x24 ou 16x16)",
        "domain": "vector-graphics",
        "mcp_server": "inkscape-mcp",
        "vision_rubric": "icon",
        "max_iterations": 5,
        "acceptance_threshold": 0.80,
        "knowledge": {
            "grid": "24x24 grid with 2px padding. Stroke width 1.5-2px.",
            "corners": "Rounded line caps and joins for friendly feel. Square for technical.",
            "consistency": "All icons in a set must share stroke width, corner radius, and optical size.",
            "accessibility": "Always include <title> and <desc> elements.",
            "optimization": "Remove metadata, comments, empty groups. Use viewBox, not width/height.",
            "colors": "Monochrome by default. Use currentColor for theming support.",
        },
        "workflow_steps": [
            {
                "step": "structure_svg",
                "description": "Create SVG structure with viewBox and accessibility",
                "mcp_tool": "create_svg",
                "tips": ["Start with <svg viewBox='0 0 24 24'>",
                         "Add <title> and <desc> immediately"],
            },
            {
                "step": "draw_paths",
                "description": "Draw the icon paths",
                "mcp_tool": "create_path",
                "tips": ["Use stroke, not fill, for line icons",
                         "Keep paths simple — fewer control points = better"],
            },
            {
                "step": "refine_paths",
                "description": "Adjust curves and alignment",
                "mcp_tool": "edit_path",
                "tips": ["Snap to pixel grid to avoid anti-aliasing blur",
                         "Test at 16x16 — if unreadable, simplify"],
            },
            {
                "step": "optimize",
                "description": "Optimize SVG output",
                "mcp_tool": "export_svg",
                "tips": ["Remove editor metadata",
                         "Simplify paths with minimal quality loss"],
            },
        ],
        "common_errors": {
            "anti_aliasing_blur": "Paths not on pixel grid → blurry at small sizes. Snap to grid.",
            "inconsistent_stroke": "Mixed stroke widths look unprofessional. Standardize.",
            "missing_viewbox": "Without viewBox, SVG won't scale properly.",
        },
    },
    "svg-illustration": {
        "name": "SVG Illustration — Complex Vector",
        "description": "Illustrations vectorielles complexes, logos, assets marketing",
        "domain": "vector-graphics",
        "mcp_server": "inkscape-mcp",
        "vision_rubric": "illustration",
        "max_iterations": 6,
        "acceptance_threshold": 0.75,
        "knowledge": {
            "layers": "Organize in semantic layers: background, mid-ground, foreground.",
            "color": "Use a defined palette (3-5 colors + shades). Test in grayscale.",
            "typography": "Convert text to paths for portability. Keep originals in hidden layer.",
            "export": "SVG for web/screen. PDF for print. PNG @2x for social media.",
        },
        "workflow_steps": [
            {"step": "sketch_composition", "description": "Block out composition", "mcp_tool": "create_path"},
            {"step": "build_shapes", "description": "Create main shapes and forms", "mcp_tool": "create_path"},
            {"step": "apply_colors", "description": "Apply color palette", "mcp_tool": "edit_path"},
            {"step": "add_details", "description": "Add fine details and textures", "mcp_tool": "create_path"},
            {"step": "apply_effects", "description": "Add shadows, gradients, effects", "mcp_tool": "apply_filter"},
            {"step": "export_final", "description": "Export in required formats", "mcp_tool": "export_svg"},
        ],
        "common_errors": {
            "raster_in_vector": "Embedded raster images defeat the purpose. Use vector alternatives.",
            "too_complex": "SVG with >1000 paths is slow to render. Simplify or rasterize background.",
        },
    },
    "svg-code-only": {
        "name": "SVG Pure Code — No External Tool",
        "description": "Génération SVG en code pur, sans serveur MCP (icônes géométriques simples)",
        "domain": "vector-graphics",
        "mcp_server": None,  # Pas de MCP nécessaire
        "vision_rubric": "icon",
        "max_iterations": 3,
        "acceptance_threshold": 0.75,
        "knowledge": {
            "elements": "Use <circle>, <rect>, <line>, <polyline>, <polygon> for geometric shapes.",
            "paths": "Use <path d='...'> for custom shapes. M=move, L=line, C=cubic bezier, Z=close.",
            "transforms": "translate(), rotate(), scale() for positioning without changing coordinates.",
            "currentColor": "Use fill='currentColor' or stroke='currentColor' for CSS theming.",
        },
        "workflow_steps": [
            {"step": "write_svg", "description": "Write SVG code directly", "mcp_tool": None},
            {"step": "validate_svg", "description": "Validate SVG structure", "mcp_tool": None},
            {"step": "screenshot_evaluate", "description": "Render and evaluate visually", "mcp_tool": None},
        ],
        "common_errors": {
            "no_xmlns": "Missing xmlns attribute breaks rendering in some browsers.",
            "hardcoded_colors": "Use CSS custom properties or currentColor, not hardcoded hex.",
        },
    },
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class IterationRecord:
    """Enregistrement d'une itération create-judge."""

    iteration: int = 0
    timestamp: str = ""
    action_taken: str = ""
    mcp_calls: list[dict[str, Any]] = field(default_factory=list)
    vision_score: float = 0.0
    vision_decision: str = ""  # accept | iterate | escalate
    vision_feedback: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class ETCExecution:
    """Exécution complète d'une Expert Tool Chain."""

    execution_id: str = ""
    profile: str = ""
    brief: str = ""
    status: str = "pending"  # pending | running | completed | failed | escalated
    iterations: list[dict[str, Any]] = field(default_factory=list)
    final_score: float = 0.0
    final_outputs: list[str] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    escalation_reason: str = ""

    def __post_init__(self) -> None:
        if not self.execution_id:
            self.execution_id = f"etc-{uuid.uuid4().hex[:8]}"


# ── History I/O ──────────────────────────────────────────────────────────────


def _etc_dir(project_root: Path) -> Path:
    return project_root / ETC_DIR


def _history_path(project_root: Path) -> Path:
    return _etc_dir(project_root) / HISTORY_FILE


def append_history(project_root: Path, execution: ETCExecution) -> None:
    """Ajoute une exécution à l'historique."""
    hp = _history_path(project_root)
    hp.parent.mkdir(parents=True, exist_ok=True)
    with open(hp, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(execution), ensure_ascii=False) + "\n")


def load_history(project_root: Path, last_n: int = 10) -> list[dict[str, Any]]:
    """Charge les N dernières exécutions."""
    hp = _history_path(project_root)
    if not hp.exists():
        return []
    lines = hp.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines[-last_n:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ── Expertise Engine ─────────────────────────────────────────────────────────


def get_expertise_context(profile_id: str) -> str:
    """Génère le contexte d'expertise pour un profil donné.

    Retourne un prompt enrichi avec les best practices, erreurs communes,
    et workflow steps du domaine.
    """
    profile = EXPERTISE_PROFILES.get(profile_id)
    if not profile:
        return f"Unknown profile: {profile_id}"

    lines = [
        f"# Expert Context: {profile['name']}",
        f"Domain: {profile['domain']}",
        "",
        "## Domain Knowledge",
    ]

    for key, value in profile.get("knowledge", {}).items():
        lines.append(f"- **{key}**: {value}")

    lines.append("")
    lines.append("## Workflow Steps")
    for i, step in enumerate(profile.get("workflow_steps", []), 1):
        lines.append(f"{i}. **{step['step']}** — {step['description']}")
        for tip in step.get("tips", []):
            lines.append(f"   - Tip: {tip}")

    lines.append("")
    lines.append("## Common Errors to Avoid")
    for key, value in profile.get("common_errors", {}).items():
        lines.append(f"- ⚠️ **{key}**: {value}")

    return "\n".join(lines)


def build_creation_prompt(profile_id: str, brief: str, iteration: int = 0,
                          previous_feedback: list[str] | None = None) -> str:
    """Construit le prompt pour la création/itération via MCP."""
    profile = EXPERTISE_PROFILES.get(profile_id)
    if not profile:
        return f"Unknown profile: {profile_id}"

    expertise_ctx = get_expertise_context(profile_id)

    lines = [
        expertise_ctx,
        "",
        "---",
        "",
        f"## Task Brief: {brief}",
        "",
    ]

    if iteration == 0:
        lines.append("This is the FIRST iteration. Follow the workflow steps above in order.")
        lines.append("Apply all domain knowledge. Avoid common errors listed above.")
    else:
        lines.append(f"This is iteration #{iteration}. The previous attempt was evaluated.")
        if previous_feedback:
            lines.append("Issues to fix:")
            for fb in previous_feedback:
                lines.append(f"  - {fb}")
        lines.append("")
        lines.append("Focus ONLY on fixing the reported issues. Do not change what already works.")

    mcp_server = profile.get("mcp_server")
    if mcp_server:
        lines.append(f"\nUse MCP server: {mcp_server}")
        lines.append("Available tools from the workflow steps above.")
    else:
        lines.append("\nNo MCP server needed — generate output directly (e.g., SVG code).")

    return "\n".join(lines)


# ── Execution Plan ───────────────────────────────────────────────────────────


def plan_execution(profile_id: str, brief: str) -> dict[str, Any]:
    """Planifie une exécution ETC sans l'exécuter."""
    profile = EXPERTISE_PROFILES.get(profile_id)
    if not profile:
        return {"error": f"Unknown profile: {profile_id}"}

    return {
        "profile": profile_id,
        "name": profile["name"],
        "brief": brief,
        "domain": profile["domain"],
        "mcp_server": profile.get("mcp_server"),
        "vision_rubric": profile["vision_rubric"],
        "max_iterations": profile["max_iterations"],
        "acceptance_threshold": profile["acceptance_threshold"],
        "workflow_steps": [s["step"] for s in profile.get("workflow_steps", [])],
        "creation_prompt": build_creation_prompt(profile_id, brief),
        "expertise_context": get_expertise_context(profile_id),
        "instruction": (
            "Execute each workflow step using the MCP tools indicated. "
            "After producing output, use vision-judge.py to evaluate. "
            "If score < threshold, iterate with the feedback. "
            f"Max {profile['max_iterations']} iterations."
        ),
    }


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_etc_execute(
    profile: str,
    brief: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: planifie et retourne le plan d'exécution d'une Expert Tool Chain.

    L'exécution réelle est faite par l'agent appelant — ce tool fournit
    le plan complet, les prompts d'expertise, et les instructions de la
    boucle vision.

    Args:
        profile: ID du profil d'expertise (blender-simple, svg-icon, etc.).
        brief: Description de ce qu'il faut créer.
        project_root: Racine du projet.

    Returns:
        Plan d'exécution complet avec prompts et instructions.
    """
    plan = plan_execution(profile, brief)

    # Créer un enregistrement d'exécution
    execution = ETCExecution(
        profile=profile,
        brief=brief,
        status="running",
        started_at=datetime.now().isoformat(),
    )
    root = Path(project_root).resolve()
    append_history(root, execution)

    plan["execution_id"] = execution.execution_id
    return plan


def mcp_etc_record_iteration(
    execution_id: str,
    iteration: int,
    action_taken: str,
    vision_score: float,
    vision_decision: str,
    vision_feedback: str = "",
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: enregistre une itération d'exécution ETC.

    Args:
        execution_id: ID de l'exécution en cours.
        iteration: Numéro d'itération.
        action_taken: Description de l'action effectuée.
        vision_score: Score visuel obtenu.
        vision_decision: Décision (accept, iterate, escalate).
        vision_feedback: Feedback du vision judge (comma-separated).
        project_root: Racine du projet.

    Returns:
        Résumé de l'itération + prochain prompt si iterate.
    """
    root = Path(project_root).resolve()
    feedback_list = [f.strip() for f in vision_feedback.split(",") if f.strip()] if vision_feedback else []

    record = IterationRecord(
        iteration=iteration,
        timestamp=datetime.now().isoformat(),
        action_taken=action_taken,
        vision_score=vision_score,
        vision_decision=vision_decision,
        vision_feedback=feedback_list,
    )

    result: dict[str, Any] = {
        "execution_id": execution_id,
        "iteration": asdict(record),
        "decision": vision_decision,
    }

    if vision_decision == "iterate":
        # Charger l'historique pour trouver le profil
        history = load_history(root, last_n=20)
        profile_id = ""
        brief = ""
        for entry in reversed(history):
            if entry.get("execution_id") == execution_id:
                profile_id = entry.get("profile", "")
                brief = entry.get("brief", "")
                break

        if profile_id:
            result["next_prompt"] = build_creation_prompt(
                profile_id, brief,
                iteration=iteration + 1,
                previous_feedback=feedback_list,
            )
        result["message"] = f"Score {vision_score:.2f} below threshold. Iterate with feedback."
    elif vision_decision == "accept":
        result["message"] = f"Score {vision_score:.2f} meets threshold. Output accepted."
    elif vision_decision == "escalate":
        result["message"] = "Max iterations reached or critical failure. Escalating to human review."

    return result


def mcp_etc_catalog() -> dict[str, Any]:
    """MCP tool: liste les profils d'expertise disponibles.

    Returns:
        {profiles: [{id, name, description, domain, mcp_server, rubric}]}
    """
    profiles = []
    for pid, profile in EXPERTISE_PROFILES.items():
        profiles.append({
            "id": pid,
            "name": profile["name"],
            "description": profile["description"],
            "domain": profile["domain"],
            "mcp_server": profile.get("mcp_server"),
            "vision_rubric": profile["vision_rubric"],
            "max_iterations": profile["max_iterations"],
            "acceptance_threshold": profile["acceptance_threshold"],
            "steps_count": len(profile.get("workflow_steps", [])),
            "requires_mcp": profile.get("mcp_server") is not None,
        })
    return {"profiles": profiles, "count": len(profiles)}


def mcp_etc_expertise_context(profile: str) -> dict[str, Any]:
    """MCP tool: retourne le contexte d'expertise pour un profil.

    Args:
        profile: ID du profil d'expertise.

    Returns:
        Contexte d'expertise formaté.
    """
    if profile not in EXPERTISE_PROFILES:
        return {"error": f"Unknown profile: {profile}", "available": list(EXPERTISE_PROFILES.keys())}

    return {
        "profile": profile,
        "context": get_expertise_context(profile),
        "knowledge": EXPERTISE_PROFILES[profile].get("knowledge", {}),
        "common_errors": EXPERTISE_PROFILES[profile].get("common_errors", {}),
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_execute(args: argparse.Namespace) -> int:
    plan = plan_execution(args.profile, args.brief)

    if "error" in plan:
        print(f"  ❌ {plan['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔧 Expert Tool Chain — {plan['name']}")
        print(f"  📝 Brief: {args.brief}")
        print(f"  🎯 Domain: {plan['domain']}")
        if plan.get("mcp_server"):
            print(f"  🔌 MCP Server: {plan['mcp_server']}")
        else:
            print("  💻 Mode: Code-only (no MCP server)")
        print(f"  👁️ Vision Rubric: {plan['vision_rubric']}")
        print(f"  🔄 Max Iterations: {plan['max_iterations']}")
        print(f"  📏 Threshold: {plan['acceptance_threshold']}")
        print("\n  📋 Workflow Steps:")
        for i, step in enumerate(plan["workflow_steps"], 1):
            print(f"    {i}. {step}")
        print(f"\n  📝 Creation Prompt (first {60} chars):")
        prompt = plan.get("creation_prompt", "")
        for line in prompt.split("\n")[:15]:
            print(f"    {line}")
        if len(prompt.split("\n")) > 15:
            print(f"    ... ({len(prompt.split(chr(10)))} lines total)")

    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    catalog = mcp_etc_catalog()

    if args.json:
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔧 Expert Tool Chain — Profiles ({catalog['count']})\n")
        for p in catalog["profiles"]:
            mcp = f"🔌 {p['mcp_server']}" if p["requires_mcp"] else "💻 Code-only"
            print(f"  📋 {p['id']}")
            print(f"     {p['name']} — {p['description']}")
            print(f"     {mcp} | 👁️ {p['vision_rubric']} | 🔄 max {p['max_iterations']} iter | 📏 {p['acceptance_threshold']}")
            print()

    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    server_name = args.server
    # Find profiles that use this server
    using = [pid for pid, p in EXPERTISE_PROFILES.items() if p.get("mcp_server") == server_name]

    if args.json:
        print(json.dumps({"server": server_name, "used_by_profiles": using}, indent=2))
    else:
        print(f"\n  🔌 MCP Server: {server_name}")
        if using:
            print(f"  Used by profiles: {', '.join(using)}")
            for pid in using:
                profile = EXPERTISE_PROFILES[pid]
                tools = [s.get("mcp_tool") for s in profile.get("workflow_steps", []) if s.get("mcp_tool")]
                print(f"    {pid}: tools = {', '.join(filter(None, tools))}")
        else:
            print("  ⚠️  Not used by any expertise profile")

    return 0


def cmd_history(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    entries = load_history(root, last_n=getattr(args, "last", 10))

    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        if not entries:
            print("\n  📜 Aucun historique ETC")
            return 0
        print(f"\n  📜 ETC History ({len(entries)} entries)\n")
        for e in entries:
            status_icon = {"completed": "✅", "failed": "❌", "escalated": "⚠️", "running": "🔄"}.get(
                e.get("status", ""), "❓"
            )
            print(f"  {status_icon} {e.get('execution_id', '?')} — {e.get('profile', '?')}")
            print(f"     Brief: {e.get('brief', 'N/A')}")
            iters = e.get("iterations", [])
            if iters:
                last = iters[-1]
                print(f"     Iterations: {len(iters)} | Last score: {last.get('vision_score', 'N/A')}")
            print()

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Expert Tool Chain — MCP expertise + vision loop for agents",
    )
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--version", action="version", version=f"expert-tool-chain {ETC_VERSION}")

    sub = parser.add_subparsers(dest="command")

    # execute
    p_exec = sub.add_parser("execute", help="Execute an expert tool chain")
    p_exec.add_argument("--profile", required=True, choices=sorted(EXPERTISE_PROFILES.keys()))
    p_exec.add_argument("--brief", required=True, help="What to create")

    # catalog
    sub.add_parser("catalog", help="List expertise profiles")

    # inspect
    p_insp = sub.add_parser("inspect", help="Inspect an MCP server")
    p_insp.add_argument("--server", required=True, help="MCP server name")

    # history
    p_hist = sub.add_parser("history", help="Execution history")
    p_hist.add_argument("--last", type=int, default=10)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "execute": cmd_execute,
        "catalog": cmd_catalog,
        "inspect": cmd_inspect,
        "history": cmd_history,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
