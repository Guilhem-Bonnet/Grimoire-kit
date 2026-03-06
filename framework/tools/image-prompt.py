#!/usr/bin/env python3
"""
image-prompt.py — Générateur de prompts pour génération d'images.
================================================================

Génère des prompts structurés et optimisés pour les outils de
génération d'images (Midjourney, DALL-E, Stable Diffusion, etc.)
à partir d'une description en langage naturel.

N'appelle AUCUNE API externe — produit uniquement le texte du prompt.

Usage :
  python3 image-prompt.py --project-root . generate --description "logo minimaliste pour un outil dev" --style midjourney
  python3 image-prompt.py --project-root . generate --description "dashboard UI sombre" --style dalle --aspect 16:9
  python3 image-prompt.py --project-root . refine --prompt "a cat" --enhance
  python3 image-prompt.py --project-root . batch --file prompts.txt --style stable-diffusion

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.image_prompt")

IMAGE_PROMPT_VERSION = "1.0.0"

# ── Style Presets ────────────────────────────────────────────────────────────

STYLE_PRESETS: dict[str, dict] = {
    "midjourney": {
        "suffix": "--v 6.1 --q 2",
        "separator": ", ",
        "tips": ["Use :: for weighted sections", "Add --ar for aspect ratio",
                 "Add --style raw for photorealistic"],
    },
    "dalle": {
        "suffix": "",
        "separator": ". ",
        "tips": ["Be descriptive and specific", "Mention art style explicitly",
                 "Avoid negatives — describe what you want, not what you don't"],
    },
    "stable-diffusion": {
        "suffix": "",
        "separator": ", ",
        "negative_prefix": "Negative prompt: ",
        "tips": ["Use weighted tokens (word:1.3)", "Add quality tags: masterpiece, best quality",
                 "Specify negative prompt for unwanted elements"],
    },
    "generic": {
        "suffix": "",
        "separator": ", ",
        "tips": ["Be specific about composition, lighting, and style"],
    },
}

QUALITY_MODIFIERS = [
    "high quality", "detailed", "professional",
    "sharp focus", "well-composed",
]

LIGHTING_OPTIONS = [
    "natural lighting", "studio lighting", "golden hour",
    "dramatic lighting", "soft diffused light", "neon glow",
    "backlit", "rim lighting",
]

COMPOSITION_OPTIONS = [
    "centered composition", "rule of thirds", "symmetrical",
    "bird's eye view", "close-up", "wide angle",
    "isometric", "flat lay",
]

ART_STYLES = [
    "photorealistic", "digital art", "watercolor", "oil painting",
    "vector illustration", "pixel art", "3d render", "flat design",
    "minimalist", "brutalist", "art nouveau", "cyberpunk",
    "vaporwave", "ukiyo-e", "bauhaus",
]


# ── Data Model ───────────────────────────────────────────────────────────────


@dataclass
class ImagePrompt:
    """Un prompt de génération d'image structuré."""

    description: str = ""
    style: str = "generic"
    art_style: str = ""
    lighting: str = ""
    composition: str = ""
    aspect_ratio: str = ""
    quality_modifiers: list[str] = field(default_factory=list)
    negative: str = ""
    final_prompt: str = ""
    tips: list[str] = field(default_factory=list)


# ── Prompt Generation ────────────────────────────────────────────────────────


def generate_prompt(
    description: str,
    style: str = "generic",
    art_style: str = "",
    lighting: str = "",
    composition: str = "",
    aspect_ratio: str = "",
    quality: bool = True,
    negative: str = "",
) -> ImagePrompt:
    """Génère un prompt structuré pour la génération d'images.

    Args:
        description: Description en langage naturel de l'image souhaitée.
        style: Preset de style (midjourney, dalle, stable-diffusion, generic).
        art_style: Style artistique (photorealistic, watercolor, etc.).
        lighting: Type d'éclairage.
        composition: Type de composition.
        aspect_ratio: Ratio (16:9, 1:1, etc.).
        quality: Ajouter des modificateurs de qualité.
        negative: Éléments à exclure (pour stable-diffusion).

    Returns:
        ImagePrompt avec le prompt final assemblé.
    """
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["generic"])
    sep = preset["separator"]

    parts: list[str] = [description.strip()]

    if art_style:
        parts.append(art_style)
    if lighting:
        parts.append(lighting)
    if composition:
        parts.append(composition)

    quality_mods = []
    if quality:
        quality_mods = QUALITY_MODIFIERS[:3]
        parts.extend(quality_mods)

    # Assemble the prompt
    prompt = sep.join(parts)

    # Add style-specific suffix
    suffix = preset.get("suffix", "")
    if suffix:
        prompt = f"{prompt} {suffix}"

    # Add aspect ratio
    if aspect_ratio:
        if style == "midjourney":
            prompt = f"{prompt} --ar {aspect_ratio}"
        else:
            prompt = f"{prompt} (aspect ratio: {aspect_ratio})"

    # Handle negative prompt
    neg_text = ""
    if negative and style == "stable-diffusion":
        neg_prefix = preset.get("negative_prefix", "Negative prompt: ")
        neg_text = f"\n{neg_prefix}{negative}"

    final = prompt + neg_text

    return ImagePrompt(
        description=description,
        style=style,
        art_style=art_style,
        lighting=lighting,
        composition=composition,
        aspect_ratio=aspect_ratio,
        quality_modifiers=quality_mods,
        negative=negative,
        final_prompt=final.strip(),
        tips=preset.get("tips", []),
    )


def refine_prompt(
    prompt: str,
    enhance: bool = False,
    add_quality: bool = False,
    style: str = "generic",
) -> ImagePrompt:
    """Raffine un prompt existant.

    Args:
        prompt: Prompt existant à améliorer.
        enhance: Ajouter des détails de composition et éclairage.
        add_quality: Ajouter des modificateurs de qualité.
        style: Style cible.

    Returns:
        ImagePrompt avec le prompt amélioré.
    """
    parts = [prompt.strip()]
    mods: list[str] = []

    if enhance:
        parts.append("detailed")
        parts.append("sharp focus")
    if add_quality:
        mods = QUALITY_MODIFIERS[:3]
        parts.extend(mods)

    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["generic"])
    sep = preset["separator"]
    final = sep.join(parts)

    if preset.get("suffix"):
        final = f"{final} {preset['suffix']}"

    return ImagePrompt(
        description=prompt,
        style=style,
        quality_modifiers=mods,
        final_prompt=final.strip(),
        tips=preset.get("tips", []),
    )


# ── Display ──────────────────────────────────────────────────────────────────


def display_prompt(result: ImagePrompt) -> None:
    """Affiche le prompt généré."""
    print("\n🎨 Image Prompt Generator")
    print("=" * 60)
    print(f"  Style   : {result.style}")
    if result.art_style:
        print(f"  Art     : {result.art_style}")
    if result.lighting:
        print(f"  Light   : {result.lighting}")
    if result.composition:
        print(f"  Compo   : {result.composition}")
    if result.aspect_ratio:
        print(f"  Ratio   : {result.aspect_ratio}")
    print()
    print("  📋 Prompt :")
    print(f"  {result.final_prompt}")
    if result.negative:
        print(f"\n  🚫 Négatif : {result.negative}")
    if result.tips:
        print("\n  💡 Conseils :")
        for tip in result.tips:
            print(f"    - {tip}")
    print()


def display_options() -> None:
    """Affiche les options disponibles."""
    print("\n🎨 Options disponibles")
    print("=" * 60)
    print("\n  Styles :", ", ".join(STYLE_PRESETS.keys()))
    print("\n  Art styles :")
    for i, s in enumerate(ART_STYLES):
        end = "\n" if (i + 1) % 5 == 0 else ""
        print(f"    {s:20s}", end=end)
    print("\n\n  Lighting :")
    for lt in LIGHTING_OPTIONS:
        print(f"    {lt}")
    print("\n  Composition :")
    for c in COMPOSITION_OPTIONS:
        print(f"    {c}")
    print()


# ── MCP Interface ───────────────────────────────────────────────────────────


def mcp_image_prompt(
    project_root: str,
    action: str = "generate",
    description: str = "",
    style: str = "generic",
    art_style: str = "",
    lighting: str = "",
    composition: str = "",
    aspect_ratio: str = "",
    negative: str = "",
    prompt: str = "",
    enhance: bool = False,
) -> dict:
    """MCP tool ``bmad_image_prompt`` — génère des prompts pour images.

    Args:
        project_root: Racine du projet (non utilisé mais requis par convention MCP).
        action: generate | refine | options.
        description: Description de l'image (pour action=generate).
        style: Preset de style.
        art_style: Style artistique.
        lighting: Éclairage.
        composition: Composition.
        aspect_ratio: Ratio d'aspect.
        negative: Éléments à exclure.
        prompt: Prompt existant (pour action=refine).
        enhance: Améliorer le prompt (pour action=refine).

    Returns:
        dict avec le prompt généré.
    """
    if action == "generate":
        if not description:
            return {"status": "error", "error": "description required"}
        result = generate_prompt(description, style, art_style, lighting,
                                 composition, aspect_ratio, negative=negative)
        return {"status": "ok", **asdict(result)}

    if action == "refine":
        if not prompt:
            return {"status": "error", "error": "prompt required"}
        result = refine_prompt(prompt, enhance=enhance, style=style)
        return {"status": "ok", **asdict(result)}

    if action == "options":
        return {
            "status": "ok",
            "styles": list(STYLE_PRESETS.keys()),
            "art_styles": ART_STYLES,
            "lighting": LIGHTING_OPTIONS,
            "composition": COMPOSITION_OPTIONS,
        }

    return {"status": "error", "error": f"Unknown action: {action}"}


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="image-prompt",
        description="Image Prompt Generator — Prompts structurés pour génération d'images",
    )
    p.add_argument("--project-root", type=Path, default=Path("."))
    p.add_argument("--json", action="store_true")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {IMAGE_PROMPT_VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Générer un prompt")
    gen.add_argument("--description", required=True, help="Description de l'image")
    gen.add_argument("--style", default="generic", choices=list(STYLE_PRESETS.keys()))
    gen.add_argument("--art-style", default="")
    gen.add_argument("--lighting", default="")
    gen.add_argument("--composition", default="")
    gen.add_argument("--aspect-ratio", default="")
    gen.add_argument("--negative", default="")
    gen.add_argument("--no-quality", action="store_true")

    ref = sub.add_parser("refine", help="Raffiner un prompt existant")
    ref.add_argument("--prompt", required=True)
    ref.add_argument("--enhance", action="store_true")
    ref.add_argument("--style", default="generic", choices=list(STYLE_PRESETS.keys()))

    sub.add_parser("options", help="Afficher les options disponibles")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        result = generate_prompt(
            args.description, args.style,
            art_style=args.art_style,
            lighting=args.lighting,
            composition=args.composition,
            aspect_ratio=args.aspect_ratio,
            quality=not args.no_quality,
            negative=args.negative,
        )
        if getattr(args, "json", False):
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        else:
            display_prompt(result)
        return 0

    if args.command == "refine":
        result = refine_prompt(args.prompt, enhance=args.enhance, style=args.style)
        if getattr(args, "json", False):
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        else:
            display_prompt(result)
        return 0

    if args.command == "options":
        display_options()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
