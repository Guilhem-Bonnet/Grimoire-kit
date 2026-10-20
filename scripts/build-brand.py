#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools[woff]"]
# ///
"""Génère docs/assets/brand/ : la marque « › Grimoire », sa tuile et le sprite d'icônes.

Source unique de la marque (règles : docs/assets/brand/README-brand.md). Le
mot « Grimoire » est dessiné en tracés depuis web/fonts/instrument-sans-600.woff2
(OFL) : un SVG chargé en <img> par GitHub ne lit pas de police, il faut donc des
contours, pas du texte. Les PNG sont rendus depuis les SVG par Inkscape, à défaut
par ImageMagick (délégué librsvg).

Usage :
    uv run scripts/build-brand.py            # écrit SVG, PNG et sprite
    uv run scripts/build-brand.py --check    # échoue si un SVG ou le sprite est périmé

`uv run` installe fontTools d'après l'en-tête du script ; le sprite seul
(`build_sprite`) n'en a pas besoin et reste importable sans lui.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "brand"
ICONS = ROOT / "docs" / "assets" / "icons"
FONTS = ROOT / "web" / "fonts"

RUST, RUST_DARK = "#b4441d", "#f08a5d"
INK, INK_DARK = "#14171d", "#eef0f4"
INK2 = "#4a515c"
GROUND, CARD, LINE = "#eef0f4", "#ffffff", "#e3e6eb"

# Chevron du mot-symbole (grille 40 de haut) et de la tuile (64 × 64).
_WORD_CHEVRON = "M10 9 L21.5 20 L10 31"
_TILE_CHEVRON = "M24 18 L40 32 L24 46"
_ROUND = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'

# Phrase produit courte (décision 2 de la synthèse identité) et commande de l'aperçu social.
SOCIAL_PHRASE = (
    "Dashboard, task board, project memory and evidence check for code you delegate to agents (Claude Code, Copilot)."
)
SOCIAL_COMMAND = "pip install grimoire-kit"


# ── Contours de police ────────────────────────────────────────────────────────


class _Font:
    """Une fonte woff2 réduite à ce qu'il faut pour poser une ligne en tracés."""

    def __init__(self, path: Path) -> None:
        from fontTools.ttLib import TTFont

        self.font = TTFont(path)
        self.upem = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.kern = self._pair_kerning()

    def _pair_kerning(self) -> dict[tuple[str, str], int]:
        """Paires de crénage GPOS (PairPos formats 1 et 2), sans le reste du moteur."""
        pairs: dict[tuple[str, str], int] = {}
        if "GPOS" not in self.font:
            return pairs
        for lookup in self.font["GPOS"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                if lookup.LookupType == 9:
                    sub = sub.ExtSubTable
                if getattr(sub, "LookupType", 2) != 2 or not hasattr(sub, "Format"):
                    continue
                firsts = sub.Coverage.glyphs
                if sub.Format == 1:
                    for first, pset in zip(firsts, sub.PairSet, strict=True):
                        for rec in pset.PairValueRecord:
                            value = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                            pairs.setdefault((first, rec.SecondGlyph), value)
                elif sub.Format == 2:
                    class1 = sub.ClassDef1.classDefs
                    class2 = sub.ClassDef2.classDefs
                    seconds = set(self.cmap.values())
                    for first in firsts:
                        row = sub.Class1Record[class1.get(first, 0)]
                        for second in seconds:
                            rec = row.Class2Record[class2.get(second, 0)]
                            value = getattr(rec.Value1, "XAdvance", 0) if rec.Value1 else 0
                            if value:
                                pairs.setdefault((first, second), value)
        return pairs

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        return self._layout(text, size, tracking)[1]

    def _layout(self, text: str, size: float, tracking: float) -> tuple[list[tuple[str, float]], float]:
        scale = size / self.upem
        names = [self.cmap[ord(ch)] for ch in text]
        placed: list[tuple[str, float]] = []
        x = 0.0
        for i, name in enumerate(names):
            placed.append((name, x))
            x += self.hmtx[name][0] * scale + tracking
            if i + 1 < len(names):
                x += self.kern.get((name, names[i + 1]), 0) * scale
        return placed, x - tracking

    def path(self, text: str, x: float, baseline: float, size: float, tracking: float = 0.0) -> str:
        """Le texte en un seul attribut `d`, coordonnées SVG (axe y vers le bas)."""
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen

        scale = size / self.upem
        parts: list[str] = []
        for name, gx in self._layout(text, size, tracking)[0]:
            pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
            self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + gx, baseline)))
            parts.append(pen.getCommands())
        return "".join(parts)


def _fonts() -> dict[str, _Font]:
    return {
        "word": _Font(FONTS / "instrument-sans-600.woff2"),
        "text": _Font(FONTS / "instrument-sans-500.woff2"),
        "mono": _Font(FONTS / "JetBrainsMono-Regular.woff2"),
    }


# ── Les trois formes ──────────────────────────────────────────────────────────


def _svg(view: str, body: str, label: str | None = "Grimoire", size: tuple[float, float] | None = None) -> str:
    dims = f' width="{size[0]:g}" height="{size[1]:g}"' if size else ""
    a11y = f' role="img" aria-label="{label}"' if label else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"{dims}{a11y}>{body}</svg>\n'


def wordmark(word: str, chevron: str, ink: str) -> str:
    """Forme A : chevron tracé puis « Grimoire » en contours, ligne de base commune."""
    body = f'<path d="{_WORD_CHEVRON}" stroke="{chevron}" stroke-width="4.5" {_ROUND}/><path d="{word}" fill="{ink}"/>'
    return _svg("0 0 176 40", body, size=(176, 40))


def tile(square: bool = False) -> str:
    """Forme B : tuile rouille, chevron blanc. `square` pour l'icône iOS (coins posés par le système)."""
    rx = "" if square else ' rx="14"'
    body = (
        f'<rect width="64" height="64"{rx} fill="{RUST}"/>'
        f'<path d="{_TILE_CHEVRON}" stroke="#ffffff" stroke-width="7" {_ROUND}/>'
    )
    return _svg("0 0 64 64", body, size=(64, 64))


def favicon() -> str:
    body = (
        f"<style>.t{{fill:{RUST}}}.c{{stroke:#fff}}"
        f"@media(prefers-color-scheme:dark){{.t{{fill:{RUST_DARK}}}.c{{stroke:#1c2027}}}}</style>"
        '<rect class="t" width="64" height="64" rx="14"/>'
        f'<path class="c" d="{_TILE_CHEVRON}" stroke-width="7" {_ROUND}/>'
    )
    return _svg("0 0 64 64", body, label=None)


def mark(color: str) -> str:
    """Forme C : chevron nu sur la grille 24 des icônes."""
    return _svg(
        "0 0 24 24", f'<path d="M8.5 5 L15.5 12 L8.5 19" stroke="{color}" stroke-width="3" {_ROUND}/>', label=None
    )


def _wrap(font: _Font, text: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.width(trial, size) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return [*lines, current]


def social(fonts: dict[str, _Font], word: str) -> str:
    """Aperçu social 1280 × 640 : mot-symbole, phrase courte, commande d'installation. Texte seul."""
    margin, width = 96, 1280 - 2 * 96
    size = 40
    lines = _wrap(fonts["text"], SOCIAL_PHRASE, size, width)
    while len(lines) > 2:
        size -= 2
        lines = _wrap(fonts["text"], SOCIAL_PHRASE, size, width)
    leading = round(size * 1.4)
    mark_h, gap, pill_h = 96, 44, 64
    block = mark_h + gap + leading * len(lines) + gap + pill_h
    top = (640 - block) / 2
    scale = mark_h / 40
    parts = [
        f'<rect width="1280" height="640" fill="{GROUND}"/>',
        f'<g transform="translate({margin - 10 * scale:g} {top:g}) scale({scale:g})">'
        f'<path d="{_WORD_CHEVRON}" stroke="{RUST}" stroke-width="4.5" {_ROUND}/>'
        f'<path d="{word}" fill="{INK}"/></g>',
    ]
    y = top + mark_h + gap
    for line in lines:
        y += leading
        parts.append(f'<path d="{fonts["text"].path(line, margin, y - leading * 0.28, size)}" fill="{INK2}"/>')
    mono = 28
    pill_w = fonts["mono"].width(SOCIAL_COMMAND, mono) + 2 * 28
    py = y + gap
    parts.append(
        f'<rect x="{margin}" y="{py:g}" width="{pill_w:.1f}" height="{pill_h}" rx="{pill_h / 2:g}" fill="{CARD}" stroke="{LINE}"/>'
    )
    parts.append(
        f'<path d="{fonts["mono"].path(SOCIAL_COMMAND, margin + 28, py + pill_h / 2 + mono * 0.36, mono)}" fill="{INK}"/>'
    )
    return _svg("0 0 1280 640", "".join(parts), label="Grimoire", size=(1280, 640))


# ── Sprite d'icônes ───────────────────────────────────────────────────────────

_STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
_SVG_RE = re.compile(r"<svg\b([^>]*)>(.*)</svg>", re.DOTALL)
_KEEP = ("stroke-width", "stroke-linecap", "stroke-linejoin")


def build_sprite() -> str:
    """`<symbol>` par icône de docs/assets/icons, en `currentColor`, sans `<style>`."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg">',
        "  <!-- Généré par scripts/build-brand.py depuis docs/assets/icons — ne pas éditer. -->",
    ]
    for path in sorted(ICONS.glob("*.svg")):
        match = _SVG_RE.search(_STYLE_RE.sub("", path.read_text(encoding="utf-8")))
        if not match:
            raise ValueError(f"{path.name} : pas de <svg>")
        attrs = dict(re.findall(r'([a-z-]+)="([^"]*)"', match.group(1)))
        stroke = " ".join(f'{k}="{attrs[k]}"' for k in _KEEP if k in attrs)
        parts.append(f'  <symbol id="{path.stem}" viewBox="0 0 24 24" fill="none" stroke="currentColor" {stroke}>')
        parts.append(f"    {match.group(2).strip()}")
        parts.append("  </symbol>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ── Assemblage ────────────────────────────────────────────────────────────────


def build_svgs() -> dict[str, str]:
    fonts = _fonts()
    word = fonts["word"].path("Grimoire", 32, 30, 30, tracking=-0.6)
    files = {
        "mark.svg": mark("currentColor"),
        "mark-mono.svg": mark(INK),
        "tile.svg": tile(),
        "tile-square.svg": tile(square=True),
        "favicon.svg": favicon(),
        "wordmark-light.svg": wordmark(word, RUST, INK),
        "wordmark-dark.svg": wordmark(word, RUST_DARK, INK_DARK),
        "wordmark-mono.svg": wordmark(word, "currentColor", "currentColor"),
        "social-preview.svg": social(fonts, word),
    }
    files["icons.svg"] = build_sprite()
    return files


# PNG : (source SVG, largeur, hauteur).
PNGS = {
    "favicon-16.png": ("tile.svg", 16, 16),
    "favicon-32.png": ("tile.svg", 32, 32),
    "apple-touch-icon.png": ("tile-square.svg", 180, 180),
    "org-icon-512.png": ("tile.svg", 512, 512),
    "wordmark-light.png": ("wordmark-light.svg", 352, 80),
    "wordmark-dark.png": ("wordmark-dark.svg", 352, 80),
    "social-preview.png": ("social-preview.svg", 1280, 640),
}


def _rasterize(src: Path, dest: Path, width: int, height: int) -> None:
    if shutil.which("inkscape"):
        cmd = [
            "inkscape",
            str(src),
            "--export-type=png",
            f"--export-filename={dest}",
            f"--export-width={width}",
            f"--export-height={height}",
        ]
    elif shutil.which("magick"):
        cmd = [
            "magick",
            "-background",
            "none",
            "-density",
            "384",
            str(src),
            "-resize",
            f"{width}x{height}!",
            "-strip",
            str(dest),
        ]
    elif shutil.which("rsvg-convert"):
        cmd = ["rsvg-convert", "-w", str(width), "-h", str(height), "-o", str(dest), str(src)]
    else:
        raise SystemExit(
            "aucun moteur de rendu PNG (inkscape, magick ou rsvg-convert) : les SVG sont écrits, pas les PNG"
        )
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="échoue si un SVG ou le sprite n'est pas à jour")
    args = parser.parse_args()
    files = build_svgs()
    if args.check:
        stale = [
            name
            for name, text in files.items()
            if not (OUT / name).exists() or (OUT / name).read_text(encoding="utf-8") != text
        ]
        for name in stale:
            print(f"docs/assets/brand/{name} périmé : lancer uv run scripts/build-brand.py", file=sys.stderr)
        return 1 if stale else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        for name, (src, width, height) in PNGS.items():
            rendered = Path(tmp) / name
            _rasterize(OUT / src, rendered, width, height)
            shutil.copyfile(rendered, OUT / name)
    print(
        f"docs/assets/brand : {len(files)} SVG, {len(PNGS)} PNG, {files['icons.svg'].count('<symbol')} icônes au sprite"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
