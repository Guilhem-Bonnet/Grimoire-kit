#!/usr/bin/env python3
"""
vision-judge.py — Visual Quality Assessment for Agent Outputs.
==============================================================

Évalue visuellement les outputs d'agents (images, renders, SVGs)
via un LLM multimodal. Retourne un score structuré + feedback actionnable.

Pipeline :
  1. Capture (fichier image, render, SVG exporté)
  2. Encode (base64 pour API multimodal)
  3. Evaluate (LLM multimodal avec rubric)
  4. Score (0-1 par critère + overall)
  5. Decide (accept / iterate / escalate)
  6. Feedback (instructions de correction si iterate)

Modes :
  evaluate   — Évalue une image selon une rubrique
  compare    — Compare deux images (avant/après)
  batch      — Évalue un dossier d'images
  rubric     — Affiche les rubriques disponibles

Usage :
  python3 vision-judge.py --project-root . evaluate --image output.png --rubric icon
  python3 vision-judge.py --project-root . evaluate --image render.png --rubric 3d-object --criteria "low-poly robot"
  python3 vision-judge.py --project-root . compare --before v1.png --after v2.png --rubric icon
  python3 vision-judge.py --project-root . batch --dir exports/ --rubric svg-icon
  python3 vision-judge.py --project-root . rubric --list

Stdlib only (sauf appel LLM multimodal via provider configurable).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import mimetypes
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.vision_judge")

VISION_JUDGE_VERSION = "1.0.0"

# ── Rubrics ──────────────────────────────────────────────────────────────────

RUBRICS: dict[str, dict[str, Any]] = {
    "icon": {
        "name": "Icon / SVG Icon",
        "description": "Évalue une icône (SVG, PNG, etc.) sur clarté, cohérence, accessibilité et qualité fichier.",
        "criteria": [
            {
                "name": "clarity",
                "weight": 0.30,
                "prompt": "Is the icon clearly recognizable at 16x16 and 24x24? Are shapes distinguishable? No aliasing? Rate 0-10.",
            },
            {
                "name": "consistency",
                "weight": 0.25,
                "prompt": "Does it match a consistent design system? Uniform stroke width, grid-aligned, consistent corners? Rate 0-10.",
            },
            {
                "name": "accessibility",
                "weight": 0.20,
                "prompt": "Good contrast ratio? Not relying on color alone to convey meaning? Simple enough for screen readers? Rate 0-10.",
            },
            {
                "name": "aesthetic",
                "weight": 0.25,
                "prompt": "Is it visually pleasing? Professional quality? Balanced negative space? Rate 0-10.",
            },
        ],
        "acceptance_threshold": 0.75,
    },
    "3d-object": {
        "name": "3D Rendered Object",
        "description": "Évalue un rendu 3D sur géométrie, matériaux, éclairage, composition et esthétique.",
        "criteria": [
            {
                "name": "geometry",
                "weight": 0.25,
                "prompt": "Is the geometry clean? No visible artifacts, ngons, or topology issues? Proper proportions? Rate 0-10.",
            },
            {
                "name": "materials",
                "weight": 0.20,
                "prompt": "Are materials realistic/purposeful? PBR correct? No UV stretching? Proper scale of textures? Rate 0-10.",
            },
            {
                "name": "lighting",
                "weight": 0.20,
                "prompt": "Is lighting well-balanced? No blown highlights or crushed shadows? Mood appropriate? Rate 0-10.",
            },
            {
                "name": "composition",
                "weight": 0.15,
                "prompt": "Is the camera angle and framing good? Clear focal point? No clipping? Rate 0-10.",
            },
            {
                "name": "overall_aesthetic",
                "weight": 0.20,
                "prompt": "Overall professional quality? Does it match the brief? Would this pass in a portfolio? Rate 0-10.",
            },
        ],
        "acceptance_threshold": 0.70,
    },
    "illustration": {
        "name": "Illustration / Vector Art",
        "description": "Évalue une illustration vectorielle sur style, composition, couleur et pertinence.",
        "criteria": [
            {
                "name": "style_coherence",
                "weight": 0.25,
                "prompt": "Is the style consistent throughout? Line weights, color palette, detail level uniform? Rate 0-10.",
            },
            {
                "name": "composition",
                "weight": 0.25,
                "prompt": "Is the layout balanced? Good use of negative space? Visual hierarchy clear? Rate 0-10.",
            },
            {
                "name": "color_harmony",
                "weight": 0.20,
                "prompt": "Is the color palette harmonious? Good contrast? Accessible? Rate 0-10.",
            },
            {
                "name": "relevance",
                "weight": 0.15,
                "prompt": "Does it communicate the intended message? Is the metaphor/symbol clear? Rate 0-10.",
            },
            {
                "name": "technical_quality",
                "weight": 0.15,
                "prompt": "Clean paths? No stray points? Proper layering? Export-ready? Rate 0-10.",
            },
        ],
        "acceptance_threshold": 0.75,
    },
    "ui-screenshot": {
        "name": "UI Screenshot",
        "description": "Évalue un screenshot d'interface sur layout, typographie, accessibilité et cohérence design system.",
        "criteria": [
            {
                "name": "layout",
                "weight": 0.25,
                "prompt": "Is spacing consistent? Alignment correct? Visual hierarchy clear? Rate 0-10.",
            },
            {
                "name": "typography",
                "weight": 0.20,
                "prompt": "Are fonts readable? Proper size hierarchy? Good line height and spacing? Rate 0-10.",
            },
            {
                "name": "color_and_contrast",
                "weight": 0.20,
                "prompt": "WCAG AA contrast met? Color palette cohesive? Dark/light mode handled? Rate 0-10.",
            },
            {
                "name": "interactivity_cues",
                "weight": 0.15,
                "prompt": "Are clickable elements obvious? Hover states implied? Disabled states clear? Rate 0-10.",
            },
            {
                "name": "polish",
                "weight": 0.20,
                "prompt": "Overall professional feel? No orphaned elements? Consistent with design system? Rate 0-10.",
            },
        ],
        "acceptance_threshold": 0.80,
    },
}

# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class CriterionScore:
    """Score pour un critère individuel."""

    name: str = ""
    score: float = 0.0  # 0.0 - 1.0
    weight: float = 0.0
    feedback: str = ""


@dataclass
class VisionVerdict:
    """Verdict complet d'évaluation visuelle."""

    image_path: str = ""
    rubric_used: str = ""
    criteria_scores: list[dict[str, Any]] = field(default_factory=list)
    overall_score: float = 0.0
    decision: str = "iterate"  # accept | iterate | escalate
    feedback: list[str] = field(default_factory=list)
    confidence: float = 0.0
    iteration: int = 0
    brief: str = ""


@dataclass
class ComparisonResult:
    """Résultat de comparaison avant/après."""

    before_score: float = 0.0
    after_score: float = 0.0
    improvement: float = 0.0
    changes_detected: list[str] = field(default_factory=list)
    recommendation: str = ""


# ── Image Encoding ───────────────────────────────────────────────────────────


def encode_image_base64(image_path: Path) -> str:
    """Encode une image en base64 pour envoi à un LLM multimodal."""
    data = image_path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def detect_mime_type(image_path: Path) -> str:
    """Détecte le type MIME d'une image."""
    mime, _ = mimetypes.guess_type(str(image_path))
    if mime:
        return mime
    suffix = image_path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


# ── Evaluation Prompt Builder ────────────────────────────────────────────────


def build_evaluation_prompt(
    rubric_id: str,
    brief: str = "",
    iteration: int = 0,
    previous_feedback: list[str] | None = None,
) -> str:
    """Construit le prompt système pour l'évaluation multimodale."""
    rubric = RUBRICS.get(rubric_id)
    if not rubric:
        return f"Error: rubric '{rubric_id}' not found."

    lines = [
        "You are a professional visual quality assessor. Evaluate the provided image strictly.",
        f"Rubric: {rubric['name']} — {rubric['description']}",
        "",
    ]

    if brief:
        lines.append(f"Original brief: {brief}")
        lines.append("")

    if iteration > 0 and previous_feedback:
        lines.append(f"This is iteration {iteration}. Previous feedback was:")
        for fb in previous_feedback:
            lines.append(f"  - {fb}")
        lines.append("Evaluate if these issues have been addressed.")
        lines.append("")

    lines.append("Rate each criterion on a scale of 0-10. Respond in JSON format:")
    lines.append("{")
    lines.append('  "scores": {')

    criteria_lines = []
    for c in rubric["criteria"]:
        criteria_lines.append(
            f'    "{c["name"]}": {{"score": <0-10>, "feedback": "<specific feedback>"}}'
        )
    lines.append(",\n".join(criteria_lines))

    lines.append("  },")
    lines.append('  "overall_feedback": ["<actionable improvement 1>", "..."],')
    lines.append('  "confidence": <0.0-1.0>')
    lines.append("}")

    return "\n".join(lines)


def parse_evaluation_response(
    response_text: str,
    rubric_id: str,
    image_path: str,
    brief: str = "",
    iteration: int = 0,
) -> VisionVerdict:
    """Parse la réponse JSON du LLM en VisionVerdict."""
    rubric = RUBRICS[rubric_id]

    # Extraire le JSON de la réponse (peut être entouré de markdown)
    json_text = response_text
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0]
    elif "```" in json_text:
        json_text = json_text.split("```")[1].split("```")[0]

    try:
        data = json.loads(json_text.strip())
    except json.JSONDecodeError:
        return VisionVerdict(
            image_path=image_path,
            rubric_used=rubric_id,
            overall_score=0.0,
            decision="escalate",
            feedback=["Failed to parse LLM response as JSON"],
            confidence=0.0,
            iteration=iteration,
            brief=brief,
        )

    scores = data.get("scores", {})
    criteria_scores = []
    weighted_sum = 0.0

    for criterion in rubric["criteria"]:
        name = criterion["name"]
        weight = criterion["weight"]
        score_data = scores.get(name, {})

        if isinstance(score_data, dict):
            raw_score = float(score_data.get("score", 0)) / 10.0
            feedback = score_data.get("feedback", "")
        else:
            raw_score = float(score_data) / 10.0
            feedback = ""

        raw_score = max(0.0, min(1.0, raw_score))
        weighted_sum += raw_score * weight

        criteria_scores.append(asdict(CriterionScore(
            name=name,
            score=raw_score,
            weight=weight,
            feedback=feedback,
        )))

    overall = round(weighted_sum, 3)
    threshold = rubric["acceptance_threshold"]

    if overall >= threshold:
        decision = "accept"
    elif iteration >= 4:
        decision = "escalate"
    else:
        decision = "iterate"

    return VisionVerdict(
        image_path=image_path,
        rubric_used=rubric_id,
        criteria_scores=criteria_scores,
        overall_score=overall,
        decision=decision,
        feedback=data.get("overall_feedback", []),
        confidence=float(data.get("confidence", 0.5)),
        iteration=iteration,
        brief=brief,
    )


# ── Offline SVG Validation ──────────────────────────────────────────────────


def validate_svg_offline(svg_path: Path) -> dict[str, Any]:
    """Validation SVG sans LLM — checks structurels."""
    if not svg_path.exists():
        return {"valid": False, "errors": ["File not found"]}

    content = svg_path.read_text(encoding="utf-8")
    checks: dict[str, Any] = {"valid": True, "errors": [], "warnings": [], "stats": {}}

    # Basic XML validity
    if "<svg" not in content:
        checks["valid"] = False
        checks["errors"].append("No <svg> element found")
        return checks

    # viewBox
    if "viewBox" not in content:
        checks["warnings"].append("No viewBox attribute — scaling may be broken")

    # Raster embedded
    if "<image" in content and ("data:image" in content or "href=" in content):
        checks["warnings"].append("Embedded raster image detected — consider pure vector")

    # File size
    size = svg_path.stat().st_size
    checks["stats"]["file_size_bytes"] = size
    if size > 50_000:
        checks["warnings"].append(f"Large SVG ({size} bytes) — consider optimization")

    # Accessibility
    has_title = "<title>" in content or "<title " in content
    has_desc = "<desc>" in content or "<desc " in content
    if not has_title:
        checks["warnings"].append("No <title> element — poor accessibility")
    if not has_desc:
        checks["warnings"].append("No <desc> element — consider adding for screen readers")

    # xmlns
    if 'xmlns="http://www.w3.org/2000/svg"' not in content:
        checks["warnings"].append("Missing SVG namespace declaration")

    checks["stats"]["has_title"] = has_title
    checks["stats"]["has_desc"] = has_desc
    checks["stats"]["path_count"] = content.count("<path")
    checks["stats"]["group_count"] = content.count("<g")

    return checks


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_vision_evaluate(
    image_path: str,
    rubric: str = "icon",
    brief: str = "",
    iteration: int = 0,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: évalue visuellement une image selon une rubrique.

    Args:
        image_path: Chemin vers l'image à évaluer.
        rubric: Rubrique d'évaluation (icon, 3d-object, illustration, ui-screenshot).
        brief: Description originale de ce qui était demandé.
        iteration: Numéro d'itération (0 = première évaluation).
        project_root: Racine du projet.

    Returns:
        VisionVerdict sérialisé en dict.
    """
    img = Path(project_root) / image_path if not Path(image_path).is_absolute() else Path(image_path)

    if not img.exists():
        return asdict(VisionVerdict(
            image_path=str(image_path),
            rubric_used=rubric,
            decision="escalate",
            feedback=[f"Image not found: {image_path}"],
        ))

    if rubric not in RUBRICS:
        return asdict(VisionVerdict(
            image_path=str(image_path),
            rubric_used=rubric,
            decision="escalate",
            feedback=[f"Unknown rubric: {rubric}. Available: {list(RUBRICS.keys())}"],
        ))

    # SVG offline validation
    if img.suffix.lower() == ".svg":
        svg_check = validate_svg_offline(img)
        if not svg_check["valid"]:
            return asdict(VisionVerdict(
                image_path=str(image_path),
                rubric_used=rubric,
                decision="iterate",
                feedback=svg_check["errors"],
            ))

    prompt = build_evaluation_prompt(rubric, brief, iteration)

    # Return prompt + encoded image for the LLM to actually evaluate
    return {
        "evaluation_prompt": prompt,
        "image_base64": encode_image_base64(img),
        "image_mime": detect_mime_type(img),
        "rubric": rubric,
        "threshold": RUBRICS[rubric]["acceptance_threshold"],
        "brief": brief,
        "iteration": iteration,
        "svg_validation": validate_svg_offline(img) if img.suffix.lower() == ".svg" else None,
        "instruction": (
            "Send this prompt + image to a multimodal LLM, then call "
            "mcp_vision_parse_response with the LLM's JSON response."
        ),
    }


def mcp_vision_parse_response(
    llm_response: str,
    rubric: str,
    image_path: str,
    brief: str = "",
    iteration: int = 0,
) -> dict[str, Any]:
    """MCP tool: parse la réponse d'un LLM multimodal en verdict structuré.

    Args:
        llm_response: Réponse texte du LLM multimodal (JSON attendu).
        rubric: Rubrique utilisée pour l'évaluation.
        image_path: Chemin de l'image évaluée.
        brief: Brief original.
        iteration: Numéro d'itération.

    Returns:
        VisionVerdict sérialisé.
    """
    verdict = parse_evaluation_response(llm_response, rubric, image_path, brief, iteration)
    return asdict(verdict)


def mcp_vision_list_rubrics() -> dict[str, Any]:
    """MCP tool: liste les rubriques d'évaluation disponibles.

    Returns:
        {rubrics: [{id, name, description, criteria_count, threshold}]}
    """
    rubrics = []
    for rid, rubric in RUBRICS.items():
        rubrics.append({
            "id": rid,
            "name": rubric["name"],
            "description": rubric["description"],
            "criteria_count": len(rubric["criteria"]),
            "acceptance_threshold": rubric["acceptance_threshold"],
            "criteria": [c["name"] for c in rubric["criteria"]],
        })
    return {"rubrics": rubrics}


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_evaluate(args: argparse.Namespace) -> int:
    image = Path(args.image)
    if not image.exists():
        print(f"  ❌ Image not found: {image}", file=sys.stderr)
        return 1

    rubric_id = args.rubric
    if rubric_id not in RUBRICS:
        print(f"  ❌ Unknown rubric: {rubric_id}", file=sys.stderr)
        print(f"     Available: {', '.join(RUBRICS.keys())}", file=sys.stderr)
        return 1

    # SVG offline validation
    if image.suffix.lower() == ".svg":
        check = validate_svg_offline(image)
        if check["warnings"]:
            print("\n  ⚠️  SVG Warnings:")
            for w in check["warnings"]:
                print(f"     - {w}")
        if check["stats"]:
            print(f"  📊 SVG Stats: {json.dumps(check['stats'], indent=2)}")

    prompt = build_evaluation_prompt(
        rubric_id,
        brief=getattr(args, "criteria", ""),
        iteration=getattr(args, "iteration", 0),
    )

    result = {
        "rubric": rubric_id,
        "image": str(image),
        "prompt": prompt,
        "image_base64_length": len(encode_image_base64(image)),
        "mime_type": detect_mime_type(image),
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        rubric = RUBRICS[rubric_id]
        print(f"\n  👁️  Vision Judge — {rubric['name']}")
        print(f"  📄 Image: {image}")
        print(f"  📏 Threshold: {rubric['acceptance_threshold']}")
        print(f"  📊 Criteria: {len(rubric['criteria'])}")
        print("\n  📝 Evaluation Prompt:")
        print(f"  {'─' * 60}")
        for line in prompt.split("\n"):
            print(f"  {line}")
        print(f"  {'─' * 60}")
        print("\n  💡 Send this prompt + image to a multimodal LLM to get the evaluation.")

    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before = Path(args.before)
    after = Path(args.after)

    if not before.exists() or not after.exists():
        print("  ❌ Both images required", file=sys.stderr)
        return 1

    result = {
        "before": str(before),
        "after": str(after),
        "rubric": args.rubric,
        "before_base64_length": len(encode_image_base64(before)),
        "after_base64_length": len(encode_image_base64(after)),
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n  👁️  Vision Judge — Compare")
        print(f"  Before: {before}")
        print(f"  After: {after}")
        print(f"  Rubric: {args.rubric}")

    return 0


def cmd_rubric(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(mcp_vision_list_rubrics(), indent=2, ensure_ascii=False))
    else:
        print("\n  👁️  Vision Judge — Available Rubrics\n")
        for rid, rubric in RUBRICS.items():
            print(f"  📋 {rid}")
            print(f"     {rubric['name']} — {rubric['description']}")
            print(f"     Threshold: {rubric['acceptance_threshold']}")
            print(f"     Criteria: {', '.join(c['name'] for c in rubric['criteria'])}")
            print()
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"  ❌ Not a directory: {directory}", file=sys.stderr)
        return 1

    image_exts = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
    images = sorted(f for f in directory.iterdir() if f.suffix.lower() in image_exts)

    if not images:
        print(f"  ⚠️  No images found in {directory}")
        return 0

    print(f"\n  👁️  Vision Judge — Batch ({len(images)} images)")
    print(f"  Rubric: {args.rubric}\n")

    results = []
    for img in images:
        r = {"image": str(img), "rubric": args.rubric}
        if img.suffix.lower() == ".svg":
            r["svg_validation"] = validate_svg_offline(img)
        results.append(r)
        print(f"  📄 {img.name} — prepared")

    if args.json:
        print(json.dumps({"batch": results}, indent=2, ensure_ascii=False))

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vision Judge — Visual Quality Assessment for Agent Outputs",
    )
    parser.add_argument("--project-root", default=".", help="Project root directory")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--version", action="version", version=f"vision-judge {VISION_JUDGE_VERSION}")

    sub = parser.add_subparsers(dest="command")

    # evaluate
    p_eval = sub.add_parser("evaluate", help="Evaluate an image")
    p_eval.add_argument("--image", required=True, help="Path to image")
    p_eval.add_argument("--rubric", default="icon", choices=list(RUBRICS.keys()), help="Evaluation rubric")
    p_eval.add_argument("--criteria", default="", help="Original brief/criteria")
    p_eval.add_argument("--iteration", type=int, default=0, help="Iteration number")

    # compare
    p_cmp = sub.add_parser("compare", help="Compare before/after")
    p_cmp.add_argument("--before", required=True, help="Before image")
    p_cmp.add_argument("--after", required=True, help="After image")
    p_cmp.add_argument("--rubric", default="icon", choices=list(RUBRICS.keys()))

    # batch
    p_batch = sub.add_parser("batch", help="Batch evaluate a directory")
    p_batch.add_argument("--dir", required=True, help="Directory of images")
    p_batch.add_argument("--rubric", default="icon", choices=list(RUBRICS.keys()))

    # rubric
    sub.add_parser("rubric", help="List available rubrics")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "evaluate": cmd_evaluate,
        "compare": cmd_compare,
        "batch": cmd_batch,
        "rubric": cmd_rubric,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
