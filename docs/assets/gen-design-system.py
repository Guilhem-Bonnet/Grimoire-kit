#!/usr/bin/env python3
"""Grimoire Kit — Design System Asset Generator.

Generates the complete icon set, banners, dividers, and preview page.
All SVG assets are GitHub-safe (no <filter>, no <script>).

Usage:
    python3 docs/assets/gen-design-system.py

Output:
    docs/assets/icons/*.svg      — 25+ custom icons
    docs/assets/banner-dark.svg  — Hero banner (dark mode)
    docs/assets/banner-light.svg — Hero banner (light mode)
    docs/assets/divider.svg      — Section divider
    docs/assets/preview.html     — Visual review page
"""

import math
import os

# ─── Design Tokens ────────────────────────────────────────────────────────────

DARK = {
    "primary": "#a371f7",
    "secondary": "#c9a0ff",
    "tertiary": "#8957e5",
    "bg": "#0d1117",
    "surface": "#161b22",
    "border": "#30363d",
    "text": "#c9d1d9",
    "muted": "#8b949e",
}

LIGHT = {
    "primary": "#6e40c9",
    "secondary": "#8250df",
    "tertiary": "#553098",
    "bg": "#ffffff",
    "surface": "#f6f8fa",
    "border": "#d0d7de",
    "text": "#1f2328",
    "muted": "#656d76",
}

STROKE_WIDTH = "1.5"
LINECAP = "round"
LINEJOIN = "round"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def star_points(cx, cy, n, outer_r, inner_r, rotation=-90):
    """Generate vertices for an n-pointed star."""
    pts = []
    for i in range(n * 2):
        r = outer_r if i % 2 == 0 else inner_r
        angle = math.radians(rotation + 360 * i / (n * 2))
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def star_path(cx, cy, n, outer_r, inner_r, rotation=-90):
    """SVG path data for an n-pointed star polygon."""
    pts = star_points(cx, cy, n, outer_r, inner_r, rotation)
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for p in pts[1:]:
        d.append(f"L{p[0]:.1f},{p[1]:.1f}")
    d.append("Z")
    return " ".join(d)


def hex_points(cx, cy, r, flat_top=True):
    """Generate hexagon vertices."""
    offset = 0 if flat_top else 30
    return [
        (cx + r * math.cos(math.radians(60 * i + offset)),
         cy + r * math.sin(math.radians(60 * i + offset)))
        for i in range(6)
    ]


def hex_path(cx, cy, r, flat_top=True):
    """SVG path data for a regular hexagon."""
    pts = hex_points(cx, cy, r, flat_top)
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for p in pts[1:]:
        d.append(f"L{p[0]:.1f},{p[1]:.1f}")
    d.append("Z")
    return " ".join(d)


def circle_arc(cx, cy, r, start_deg, end_deg):
    """SVG arc path from start_deg to end_deg."""
    s = math.radians(start_deg)
    e = math.radians(end_deg)
    x1 = cx + r * math.cos(s)
    y1 = cy + r * math.sin(s)
    x2 = cx + r * math.cos(e)
    y2 = cy + r * math.sin(e)
    large = 1 if (end_deg - start_deg) > 180 else 0
    return f"M{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 {x2:.1f},{y2:.1f}"


# ─── Icon Template ────────────────────────────────────────────────────────────

ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">
<style>
.s{{stroke:{dp};stroke-width:{sw};stroke-linecap:{lc};stroke-linejoin:{lj};fill:none}}
.f{{fill:{dp}}}
.fs{{fill:{ds}}}
@media(prefers-color-scheme:light){{
.s{{stroke:{lp}}}
.f{{fill:{lp}}}
.fs{{fill:{ls}}}
}}
</style>
{content}
</svg>'''


def wrap_icon(content):
    return ICON_SVG.format(
        content=content,
        dp=DARK["primary"], ds=DARK["secondary"],
        lp=LIGHT["primary"], ls=LIGHT["secondary"],
        sw=STROKE_WIDTH, lc=LINECAP, lj=LINEJOIN,
    )


# ─── Icon Definitions ────────────────────────────────────────────────────────
# Each icon is hand-crafted SVG content within a 24×24 viewBox.
# Design language: geometric precision, arcane-tech aesthetic,
# outline stroke style with selective fills for accent.

ICONS = {}

# ── 1. Grimoire (open spellbook) ─────────────────────────────────────────────
ICONS["grimoire"] = '''
<g class="s">
  <path d="M12 5.5 L3.5 7.5 V18.5 L12 16.5 Z"/>
  <path d="M12 5.5 L20.5 7.5 V18.5 L12 16.5 Z"/>
  <line x1="12" y1="5.5" x2="12" y2="16.5"/>
  <path d="M16.5 10.5 L15.2 12 L16.5 13.5 L17.8 12 Z" class="f" style="fill-opacity:0.5;stroke:none"/>
</g>
<circle cx="18" cy="3.5" r="1" class="f" style="stroke:none"/>
<circle cx="20" cy="5.5" r="0.6" class="f" style="fill-opacity:0.6;stroke:none"/>
<circle cx="16" cy="2.5" r="0.5" class="f" style="fill-opacity:0.4;stroke:none"/>
'''

# ── 2. Bolt (lightning — Quick Start) ────────────────────────────────────────
ICONS["bolt"] = '''
<path class="s" d="M13 2 L7.5 11.5 H11.5 L9.5 22 L17 11 H12.5 Z"/>
'''

# ── 3. Temple (architecture — pillars) ───────────────────────────────────────
ICONS["temple"] = '''
<g class="s">
  <path d="M12 2.5 L4.5 8.5 H19.5 Z"/>
  <line x1="7.5" y1="8.5" x2="7.5" y2="18.5"/>
  <line x1="12" y1="8.5" x2="12" y2="18.5"/>
  <line x1="16.5" y1="8.5" x2="16.5" y2="18.5"/>
  <line x1="4" y1="18.5" x2="20" y2="18.5"/>
  <line x1="3.5" y1="21" x2="20.5" y2="21"/>
  <line x1="4" y1="18.5" x2="3.5" y2="21"/>
  <line x1="20" y1="18.5" x2="20.5" y2="21"/>
</g>
'''

# ── 4. Sparkle (features — four-pointed stars) ───────────────────────────────
_main_star = star_path(11, 12, 4, 7.5, 2.5)
_small_star = star_path(19.5, 5, 4, 3, 1)
ICONS["sparkle"] = f'''
<path class="s" d="{_main_star}"/>
<path class="f" style="stroke:none" d="{_small_star}"/>
'''

# ── 5. Puzzle (archetypes — jigsaw piece) ────────────────────────────────────
ICONS["puzzle"] = '''
<path class="s" d="
  M5 4 H10
  C10 4, 10 2, 12 2 C14 2, 14 4, 14 4
  H19 V9.5
  C19 9.5, 21 9.5, 21 12 C21 14.5, 19 14.5, 19 14.5
  V20 H14
  C14 20, 14 18, 12 18 C10 18, 10 20, 10 20
  H5 V14.5
  C5 14.5, 7 14.5, 7 12 C7 9.5, 5 9.5, 5 9.5
  Z"/>
'''

# ── 6. Wrench (CLI tools) ───────────────────────────────────────────────────
ICONS["wrench"] = '''
<g class="s">
  <path d="M14.7 6.3 C13 4.6, 13 3, 14 2 L16.5 4.5 L18 4.5 L18 3 L20.5 3
    C21.5 4, 21 6.5, 18.5 7.5 C17 8, 15.5 7.5, 14.7 6.3 Z"/>
  <line x1="14" y1="7" x2="4.5" y2="16.5"/>
  <path d="M4.5 16.5 L3 18 C2.5 18.5, 2.5 19.5, 3 20 L4 21
    C4.5 21.5, 5.5 21.5, 6 21 L7.5 19.5 Z"/>
</g>
'''

# ── 7. Plug (MCP server — connection) ───────────────────────────────────────
ICONS["plug"] = '''
<g class="s">
  <line x1="8.5" y1="2" x2="8.5" y2="7"/>
  <line x1="15.5" y1="2" x2="15.5" y2="7"/>
  <rect x="5.5" y="7" width="13" height="7" rx="2"/>
  <line x1="12" y1="14" x2="12" y2="18"/>
  <path d="M8 18 H16 C16 18, 18 18, 18 20 V22 H6 V20 C6 18, 8 18, 8 18 Z"/>
</g>
'''

# ── 8. Flask (tests — erlenmeyer) ───────────────────────────────────────────
ICONS["flask"] = '''
<g class="s">
  <path d="M9 2 H15 V8 L20.5 19 C21 20, 20 21.5, 18.5 21.5 H5.5
    C4 21.5, 3 20, 3.5 19 L9 8 Z"/>
  <line x1="9" y1="2" x2="9" y2="4"/>
  <line x1="15" y1="2" x2="15" y2="4"/>
  <line x1="6" y1="16" x2="18" y2="16" style="stroke-dasharray:2 1.5"/>
</g>
<circle cx="10" cy="19" r="0.8" class="f" style="stroke:none;fill-opacity:0.6"/>
<circle cx="14" cy="18" r="0.6" class="f" style="stroke:none;fill-opacity:0.4"/>
'''

# ── 9. Chart (comparison — bar chart) ───────────────────────────────────────
ICONS["chart"] = '''
<g class="s">
  <rect x="3" y="13" width="4.5" height="8.5" rx="1"/>
  <rect x="9.75" y="5" width="4.5" height="16.5" rx="1"/>
  <rect x="16.5" y="9" width="4.5" height="12.5" rx="1"/>
  <line x1="2" y1="21.5" x2="22" y2="21.5"/>
</g>
'''

# ── 10. Folder-tree (project structure) ──────────────────────────────────────
ICONS["folder-tree"] = '''
<g class="s">
  <path d="M3 4 H9 L10.5 5.5 H18 V10 H3 Z"/>
  <rect x="3" y="10" width="15" height="4" rx="0"/>
  <line x1="7" y1="14" x2="7" y2="17"/>
  <line x1="7" y1="17" x2="10" y2="17"/>
  <line x1="7" y1="17" x2="7" y2="20.5"/>
  <line x1="7" y1="20.5" x2="10" y2="20.5"/>
  <rect x="10" y="15.5" width="8" height="3" rx="0.5"/>
  <rect x="10" y="19" width="8" height="3" rx="0.5"/>
</g>
'''

# ── 11. Brain (semantic memory) ──────────────────────────────────────────────
ICONS["brain"] = '''
<g class="s">
  <path d="M12 2.5 C9 2.5, 7 4, 7 6 C5 6, 3.5 7.5, 3.5 10
    C2.5 11, 2 12.5, 2.5 14 C2 15.5, 3 17, 4.5 17.5
    C5 19.5, 7 21, 9.5 21 C10.5 21.5, 11.5 21.5, 12 21"/>
  <path d="M12 2.5 C15 2.5, 17 4, 17 6 C19 6, 20.5 7.5, 20.5 10
    C21.5 11, 22 12.5, 21.5 14 C22 15.5, 21 17, 19.5 17.5
    C19 19.5, 17 21, 14.5 21 C13.5 21.5, 12.5 21.5, 12 21"/>
  <line x1="12" y1="2.5" x2="12" y2="21"/>
  <path d="M12 7 C10 7, 8 8, 7.5 10" style="fill:none"/>
  <path d="M12 7 C14 7, 16 8, 16.5 10" style="fill:none"/>
  <path d="M12 13 C10 13, 8 14.5, 7 16" style="fill:none"/>
  <path d="M12 13 C14 13, 16 14.5, 17 16" style="fill:none"/>
</g>
'''

# ── 12. Rocket (management / launch) ────────────────────────────────────────
ICONS["rocket"] = '''
<g class="s">
  <path d="M12 2 C12 2, 8 6, 8 14 H16 C16 6, 12 2, 12 2 Z"/>
  <circle cx="12" cy="10" r="2"/>
  <path d="M8 14 L5 18 L8 17 Z"/>
  <path d="M16 14 L19 18 L16 17 Z"/>
  <path d="M10 17.5 C10 17.5, 11 22, 12 22 C13 22, 14 17.5, 14 17.5"/>
</g>
'''

# ── 13. Clipboard (prerequisites / checklist) ───────────────────────────────
ICONS["clipboard"] = '''
<g class="s">
  <rect x="4" y="3.5" width="16" height="18" rx="2"/>
  <rect x="8" y="1.5" width="8" height="4" rx="1"/>
  <path d="M8.5 11 L10.5 13 L15.5 8"/>
  <line x1="8.5" y1="17" x2="15.5" y2="17"/>
</g>
'''

# ── 14. Handshake (contributing) ─────────────────────────────────────────────
ICONS["handshake"] = '''
<g class="s">
  <path d="M2 14 L6 9 L9.5 12 L12 10"/>
  <path d="M22 14 L18 9 L14.5 12 L12 10"/>
  <path d="M12 10 L14 12 L12 14 L10 12 Z"/>
  <path d="M6 9 C6 9, 4 7, 5 5 L8 3"/>
  <path d="M18 9 C18 9, 20 7, 19 5 L16 3"/>
  <circle cx="5" cy="16" r="1.5"/>
  <circle cx="19" cy="16" r="1.5"/>
  <line x1="5" y1="17.5" x2="5" y2="21.5"/>
  <line x1="19" y1="17.5" x2="19" y2="21.5"/>
</g>
'''

# ── 15. Team (team of teams — connected people) ─────────────────────────────
ICONS["team"] = '''
<g class="s">
  <circle cx="12" cy="5" r="2.5"/>
  <path d="M7.5 14 C7.5 11, 9.5 9, 12 9 C14.5 9, 16.5 11, 16.5 14"/>
  <circle cx="4.5" cy="11" r="2"/>
  <path d="M1 18.5 C1 16.5, 2.5 15, 4.5 15 C5.5 15, 6.5 15.5, 7 16"/>
  <circle cx="19.5" cy="11" r="2"/>
  <path d="M23 18.5 C23 16.5, 21.5 15, 19.5 15 C18.5 15, 17.5 15.5, 17 16"/>
  <line x1="7" y1="12.5" x2="9" y2="10.5" style="stroke-dasharray:1.5 1"/>
  <line x1="17" y1="12.5" x2="15" y2="10.5" style="stroke-dasharray:1.5 1"/>
</g>
'''

# ── 16. Shield-pulse (self-healing / resilience) ────────────────────────────
ICONS["shield-pulse"] = '''
<g class="s">
  <path d="M12 2 L3.5 6 V12 C3.5 17, 7 20, 12 22 C17 20, 20.5 17, 20.5 12 V6 Z"/>
  <polyline points="7,12.5 9.5,12.5 10.5,10 12,15 13.5,10 14.5,12.5 17,12.5"/>
</g>
'''

# ── 17. DNA (agent darwinism — double helix) ────────────────────────────────
ICONS["dna"] = '''
<g class="s">
  <path d="M8 2 C8 2, 4 5.5, 8 8 C12 10.5, 16 10.5, 16 14 C16 17.5, 8 22, 8 22"/>
  <path d="M16 2 C16 2, 20 5.5, 16 8 C12 10.5, 8 10.5, 8 14 C8 17.5, 16 22, 16 22"/>
  <line x1="6" y1="5" x2="18" y2="5"/>
  <line x1="5.5" y1="9" x2="18.5" y2="9"/>
  <line x1="5.5" y1="15" x2="18.5" y2="15"/>
  <line x1="6" y1="19" x2="18" y2="19"/>
</g>
'''

# ── 18. Network (stigmergy / coordination — connected nodes) ────────────────
_nc = [(6, 6), (18, 5), (4, 16), (14, 18), (20, 14), (11, 11)]
_nl = [(0, 5), (1, 5), (0, 2), (2, 3), (3, 4), (1, 4), (5, 0), (5, 3)]
_network_circles = "\n".join(
    f'  <circle cx="{x}" cy="{y}" r="2.2"/>' for x, y in _nc
)
_network_lines = "\n".join(
    f'  <line x1="{_nc[a][0]}" y1="{_nc[a][1]}" x2="{_nc[b][0]}" y2="{_nc[b][1]}" style="stroke-opacity:0.5"/>'
    for a, b in _nl
)
ICONS["network"] = f'''
<g class="s">
{_network_lines}
{_network_circles}
</g>
'''

# ── 19. Moon (dream mode — crescent + stars) ─────────────────────────────────
ICONS["moon"] = '''
<g class="s">
  <path d="M18 12 C18 16.4, 14.4 20, 10 20 C8 20, 6.2 19.2, 4.8 17.8
    C6 19, 7.8 19.5, 9.8 19.5 C14 19.5, 17.5 15.5, 17.5 11
    C17.5 8.5, 16 6.5, 14.5 5.5 C16.5 7, 18 9.3, 18 12 Z"/>
</g>
<path class="f" style="stroke:none" d="{star}" />
<path class="f" style="stroke:none;fill-opacity:0.6" d="{star2}" />
<path class="f" style="stroke:none;fill-opacity:0.4" d="{star3}" />
'''.format(
    star=star_path(7, 5.5, 4, 2, 0.7),
    star2=star_path(4, 10, 4, 1.3, 0.5),
    star3=star_path(9.5, 2.5, 4, 1, 0.35),
)

# ── 20. Microscope (R&D engine) ──────────────────────────────────────────────
ICONS["microscope"] = '''
<g class="s">
  <circle cx="10" cy="7" r="4.5"/>
  <line x1="13.2" y1="10.2" x2="18" y2="15"/>
  <path d="M16 13 L20 17 L18.5 18.5 L14.5 14.5 Z"/>
  <circle cx="10" cy="7" r="1.5" class="f" style="fill-opacity:0.2;stroke:none"/>
  <line x1="3" y1="20.5" x2="21" y2="20.5"/>
  <line x1="10" y1="11.5" x2="10" y2="20.5"/>
  <line x1="6" y1="17" x2="14" y2="17"/>
</g>
'''

# ── 21. Boomerang (orchestration — curved arc) ──────────────────────────────
ICONS["boomerang"] = '''
<g class="s">
  <path d="M4 4 C4 4, 8 3, 12 6 C16 9, 20 10, 20 10
    L18 12
    C18 12, 14 11, 11 9 C8 7, 6 8, 6 12
    C6 16, 10 18, 14 20
    L12 22
    C12 22, 6 20, 4 15 C2 10, 4 4, 4 4 Z"/>
  <path d="M15 8 L17 6 M16 9 L18 7" style="stroke-opacity:0.4"/>
</g>
'''

# ── 22. Branch (session branching — git-like) ───────────────────────────────
ICONS["branch"] = '''
<g class="s">
  <circle cx="7" cy="5" r="2"/>
  <circle cx="7" cy="19" r="2"/>
  <circle cx="17" cy="12" r="2"/>
  <line x1="7" y1="7" x2="7" y2="17"/>
  <path d="M7 8 C7 8, 7 12, 12 12 L15 12"/>
</g>
'''

# ── 23. Lightbulb (plan/act/think) ──────────────────────────────────────────
ICONS["lightbulb"] = '''
<g class="s">
  <path d="M12 2 C8 2, 5 5, 5 9 C5 12, 7 13.5, 8 15 V17 H16 V15
    C17 13.5, 19 12, 19 9 C19 5, 16 2, 12 2 Z"/>
  <line x1="9" y1="17" x2="9" y2="19.5"/>
  <line x1="15" y1="17" x2="15" y2="19.5"/>
  <path d="M9 19.5 C9 21.5, 15 21.5, 15 19.5"/>
  <line x1="10" y1="9" x2="12" y2="12"/>
  <line x1="14" y1="9" x2="12" y2="12"/>
  <line x1="12" y1="6" x2="12" y2="12"/>
</g>
'''

# ── 24. Seal (completion contract — document with seal) ─────────────────────
ICONS["seal"] = '''
<g class="s">
  <rect x="4" y="2" width="13" height="17" rx="1.5"/>
  <line x1="7" y1="6" x2="14" y2="6"/>
  <line x1="7" y1="9" x2="14" y2="9"/>
  <line x1="7" y1="12" x2="11" y2="12"/>
  <circle cx="16" cy="17" r="4.5"/>
  <path d="M14 17 L15.5 18.5 L18.5 15"/>
</g>
'''

# ── 25. Hexagon (universal grimoire motif) ───────────────────────────────────
_hex = hex_path(12, 12, 9.5)
_hex_inner = hex_path(12, 12, 5.5)
ICONS["hexagon"] = f'''
<g class="s">
  <path d="{_hex}"/>
  <path d="{_hex_inner}" style="stroke-opacity:0.5"/>
  <circle cx="12" cy="12" r="2" class="f" style="fill-opacity:0.3;stroke:none"/>
</g>
'''

# ── 26. Cognition (brain + gears — tool category) ───────────────────────────
ICONS["cognition"] = '''
<g class="s">
  <path d="M10 3 C7 3, 4.5 5, 4.5 8 C3 8.5, 2 10, 2.5 11.5
    C2 13, 3 14.5, 4.5 15 C5 17, 7 18.5, 10 18.5 L10 18.5"/>
  <path d="M10 3 C13 3, 14.5 5, 14.5 7"/>
  <line x1="10" y1="3" x2="10" y2="18.5"/>
  <path d="M10 8 C8 8, 6 9.5, 5.5 11" style="fill:none"/>
  <path d="M10 13 C8 13, 6.5 14, 5.5 15" style="fill:none"/>
</g>
<g class="s" transform="translate(17,15)">
  <circle cx="0" cy="0" r="3.5"/>
  <circle cx="0" cy="0" r="1.2"/>
  <line x1="0" y1="-4.5" x2="0" y2="-2.5"/>
  <line x1="0" y1="2.5" x2="0" y2="4.5"/>
  <line x1="-4.5" y1="0" x2="-2.5" y2="0"/>
  <line x1="2.5" y1="0" x2="4.5" y2="0"/>
</g>
'''

# ── 27. Resilience (shield + waves — tool category) ─────────────────────────
ICONS["resilience"] = '''
<g class="s">
  <path d="M12 2 L4 5.5 V11 C4 16, 7.5 19.5, 12 22 C16.5 19.5, 20 16, 20 11 V5.5 Z"/>
  <path d="M7 11 C8 9, 10 9, 12 11 C14 13, 16 13, 17 11" style="fill:none"/>
  <path d="M7 14.5 C8 12.5, 10 12.5, 12 14.5 C14 16.5, 16 16.5, 17 14.5" style="fill:none;stroke-opacity:0.5"/>
</g>
'''

# ── 28. Workflow (flow diagram — tool category) ──────────────────────────────
ICONS["workflow"] = '''
<g class="s">
  <rect x="2" y="3" width="7" height="5" rx="1.5"/>
  <rect x="15" y="3" width="7" height="5" rx="1.5"/>
  <rect x="8.5" y="16" width="7" height="5" rx="1.5"/>
  <line x1="9" y1="5.5" x2="15" y2="5.5"/>
  <line x1="5.5" y1="8" x2="5.5" y2="12"/>
  <line x1="18.5" y1="8" x2="18.5" y2="12"/>
  <line x1="5.5" y1="12" x2="12" y2="16"/>
  <line x1="18.5" y1="12" x2="12" y2="16"/>
  <circle cx="5.5" cy="12" r="1" class="f" style="stroke:none;fill-opacity:0.5"/>
  <circle cx="18.5" cy="12" r="1" class="f" style="stroke:none;fill-opacity:0.5"/>
</g>
'''

# ── 29. Integration (puzzle-plug — tool category) ───────────────────────────
ICONS["integration"] = '''
<g class="s">
  <rect x="3" y="8" width="7.5" height="8" rx="1"/>
  <rect x="13.5" y="8" width="7.5" height="8" rx="1"/>
  <path d="M10.5 11 C10.5 11, 12 10, 12 12 C12 14, 10.5 13, 10.5 13"/>
  <path d="M13.5 11 C13.5 11, 12 10, 12 12 C12 14, 13.5 13, 13.5 13"/>
  <line x1="6.75" y1="8" x2="6.75" y2="5"/>
  <line x1="17.25" y1="8" x2="17.25" y2="5"/>
  <line x1="6.75" y1="16" x2="6.75" y2="19"/>
  <line x1="17.25" y1="16" x2="17.25" y2="19"/>
</g>
'''

# ── 30. Server (MCP exposé) ─────────────────────────────────────────────────
ICONS["server"] = '''
<g class="s">
  <rect x="3" y="2" width="18" height="6" rx="1.5"/>
  <rect x="3" y="9.5" width="18" height="6" rx="1.5"/>
  <rect x="3" y="17" width="18" height="5" rx="1.5"/>
  <circle cx="17" cy="5" r="1" class="f" style="stroke:none"/>
  <circle cx="17" cy="12.5" r="1" class="f" style="stroke:none"/>
  <circle cx="17" cy="19.5" r="1" class="f" style="stroke:none"/>
  <line x1="6" y1="5" x2="10" y2="5"/>
  <line x1="6" y1="12.5" x2="10" y2="12.5"/>
  <line x1="6" y1="19.5" x2="10" y2="19.5"/>
</g>
'''


# ─── Banner Generation ────────────────────────────────────────────────────────

def generate_hex_grid(cx, cy, size, cols, rows, flat_top=True):
    """Generate a honeycomb grid of hexagons as SVG paths."""
    paths = []
    if flat_top:
        col_step = size * 1.5
        row_step = size * math.sqrt(3)
    else:
        col_step = size * math.sqrt(3)
        row_step = size * 1.5

    for row in range(rows):
        for col in range(cols):
            if flat_top:
                x = cx + col * col_step
                y = cy + row * row_step + (col % 2) * (row_step / 2)
            else:
                x = cx + col * col_step + (row % 2) * (col_step / 2)
                y = cy + row * row_step
            paths.append(hex_path(x, y, size * 0.95, flat_top))
    return paths


def generate_particles(count, x_range, y_range, r_range, seed=42):
    """Deterministic pseudo-random particle positions."""
    import random
    rng = random.Random(seed)
    particles = []
    for _ in range(count):
        particles.append({
            "x": rng.uniform(*x_range),
            "y": rng.uniform(*y_range),
            "r": rng.uniform(*r_range),
            "opacity": rng.uniform(0.15, 0.6),
            "delay": rng.uniform(0, 5),
        })
    return particles


def sacred_geometry_arcs(cx, cy, radii, segments_per_ring=3, seed=42):
    """Generate partial circle arcs for decorative sacred geometry."""
    import random
    rng = random.Random(seed)
    arcs = []
    for r in radii:
        for _ in range(segments_per_ring):
            start = rng.randint(0, 359)
            span = rng.randint(40, 120)
            arcs.append(circle_arc(cx, cy, r, start, start + span))
    return arcs


def mini_icon_path(name):
    """Return a simplified icon path for banner feature pills."""
    icons = {
        "team": '<circle cx="10" cy="5" r="2"/><path d="M6 12 C6 10, 8 8, 10 8 C12 8, 14 10, 14 12"/><circle cx="4" cy="8.5" r="1.5"/><circle cx="16" cy="8.5" r="1.5"/>',
        "memory": '<path d="M10 2 C6 2, 3 4.5, 3 8 C3 11.5, 6 14, 10 14"/><path d="M10 2 C14 2, 17 4.5, 17 8 C17 11.5, 14 14, 10 14"/><line x1="10" y1="2" x2="10" y2="14"/>',
        "tools": '<circle cx="10" cy="8" r="5.5"/><line x1="4" y1="3" x2="16" y2="3"/><line x1="4" y1="13" x2="16" y2="13"/><line x1="10" y1="3" x2="10" y2="13"/>',
        "shield": '<path d="M10 1.5 L3 4.5 V9 C3 13, 6 16, 10 18 C14 16, 17 13, 17 9 V4.5 Z"/><polyline points="6,9 9,12 14,7"/>',
    }
    return icons.get(name, "")


def generate_banner(mode="dark"):
    """Generate the hero banner SVG."""
    c = DARK if mode == "dark" else LIGHT

    # Hex grid (sparse for smaller file size)
    hex_size = 28
    hexes = generate_hex_grid(-30, -20, hex_size, 16, 7)
    hex_svg = "\n".join(
        f'    <path d="{h}" fill="none" stroke="{c["border"]}" stroke-width="0.3" opacity="0.3"/>'
        for h in hexes
    )

    # Sacred geometry arcs
    arcs_left = sacred_geometry_arcs(120, 160, [60, 80, 100, 120], 2, seed=42)
    arcs_right = sacred_geometry_arcs(780, 160, [60, 80, 100, 120], 2, seed=77)
    arcs_svg = "\n".join(
        f'    <path d="{a}" fill="none" stroke="{c["primary"]}" stroke-width="0.5" opacity="0.15"/>'
        for a in arcs_left + arcs_right
    )

    # Particles (fewer for cleaner look)
    particles = generate_particles(18, (0, 900), (0, 300), (0.5, 2.0))
    particles_svg = "\n".join(
        f'    <circle cx="{p["x"]:.0f}" cy="{p["y"]:.0f}" r="{p["r"]:.1f}" '
        f'fill="{c["primary"]}" opacity="{p["opacity"]:.2f}" '
        f'style="animation:float 4s ease-in-out {p["delay"]:.1f}s infinite alternate"/>'
        for p in particles
    )

    # Central grimoire illustration
    grimoire_svg = f'''
    <!-- Central Grimoire -->
    <g transform="translate(390, 42)" opacity="0.85">
      <!-- Book body -->
      <rect x="0" y="18" width="50" height="62" rx="3" fill="{c['surface']}" stroke="{c['primary']}" stroke-width="1.5"/>
      <rect x="0" y="18" width="10" height="62" rx="2" fill="{c['border']}" stroke="{c['primary']}" stroke-width="0.8"/>
      <!-- Page lines -->
      <line x1="16" y1="32" x2="40" y2="32" stroke="{c['border']}" stroke-width="1" stroke-linecap="round"/>
      <line x1="16" y1="40" x2="40" y2="40" stroke="{c['border']}" stroke-width="1" stroke-linecap="round"/>
      <line x1="16" y1="48" x2="40" y2="48" stroke="{c['border']}" stroke-width="1" stroke-linecap="round"/>
      <line x1="16" y1="56" x2="34" y2="56" stroke="{c['border']}" stroke-width="1" stroke-linecap="round"/>
      <!-- Rune on cover -->
      <path d="{hex_path(25, 50, 9)}" fill="none" stroke="{c['primary']}" stroke-width="1" opacity="0.5"/>
      <path d="{star_path(25, 50, 6, 6, 2.5)}" fill="{c['primary']}" opacity="0.15" stroke="none"/>
      <!-- Energy emanation -->
      <path d="{circle_arc(25, 10, 18, 200, 340)}" fill="none" stroke="{c['primary']}" stroke-width="0.8" opacity="0.3"/>
      <path d="{circle_arc(25, 10, 25, 210, 330)}" fill="none" stroke="{c['secondary']}" stroke-width="0.5" opacity="0.2"/>
      <!-- Sparkle particles -->
      <circle cx="10" cy="8" r="1.5" fill="{c['primary']}" opacity="0.7"/>
      <circle cx="42" cy="5" r="1" fill="{c['secondary']}" opacity="0.5"/>
      <circle cx="25" cy="0" r="1.8" fill="{c['primary']}" opacity="0.6"
        style="animation:pulse 3s ease-in-out infinite alternate"/>
      <circle cx="50" cy="12" r="0.8" fill="{c['secondary']}" opacity="0.4"/>
      <circle cx="-2" cy="12" r="0.8" fill="{c['primary']}" opacity="0.4"/>
    </g>'''

    # Nebula radial gradient
    nebula_id = "nebula"

    # Feature pills with mini icons
    features = [
        ("Team of Teams", "team"),
        ("Semantic Memory", "memory"),
        ("93+ Tools", "tools"),
        ("Self-Healing", "shield"),
    ]

    pills_svg_parts = []
    pill_x = 155
    for label, icon_name in features:
        icon_content = mini_icon_path(icon_name)
        pill_w = len(label) * 8 + 40
        pills_svg_parts.append(f'''
    <g transform="translate({pill_x}, 232)">
      <rect width="{pill_w}" height="30" rx="15" fill="{c['surface']}" stroke="{c['border']}" stroke-width="0.8"/>
      <g transform="translate(8, 5) scale(0.9)" fill="none" stroke="{c['primary']}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
        {icon_content}
      </g>
      <text x="30" y="20" font-family="'Segoe UI',system-ui,sans-serif" font-size="12" fill="{c['text']}" font-weight="500">{label}</text>
    </g>''')
        pill_x += pill_w + 12

    pills_svg = "\n".join(pills_svg_parts)

    # Title gradients
    title_grad = f'''
    <linearGradient id="titleGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c['secondary']}"/>
      <stop offset="50%" stop-color="{c['primary']}"/>
      <stop offset="100%" stop-color="{c['tertiary']}"/>
    </linearGradient>
    <linearGradient id="titleGlow" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c['secondary']}" stop-opacity="0.3"/>
      <stop offset="50%" stop-color="{c['primary']}" stop-opacity="0.2"/>
      <stop offset="100%" stop-color="{c['tertiary']}" stop-opacity="0.1"/>
    </linearGradient>
    <radialGradient id="{nebula_id}" cx="50%" cy="45%" r="35%">
      <stop offset="0%" stop-color="{c['primary']}" stop-opacity="{"0.12" if mode == "dark" else "0.06"}"/>
      <stop offset="100%" stop-color="{c['primary']}" stop-opacity="0"/>
    </radialGradient>'''

    bg_grad = f'''
    <linearGradient id="bgGrad" x1="0" y1="0" x2="900" y2="300" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="{c['bg']}"/>
      <stop offset="50%" stop-color="{c['surface']}"/>
      <stop offset="100%" stop-color="{c['bg']}"/>
    </linearGradient>'''

    # Version badge
    version_badge = f'''
    <g transform="translate(820, 12)">
      <rect width="52" height="18" rx="9" fill="{c['surface']}" stroke="{c['border']}" stroke-width="0.6"/>
      <text x="26" y="13" text-anchor="middle" font-family="'Segoe UI',system-ui,monospace" font-size="10" fill="{c['muted']}">v2.4.1</text>
    </g>'''

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 300" fill="none">
  <defs>
    {bg_grad}
    {title_grad}
  </defs>

  <style>
    @keyframes float {{
      from {{ transform: translateY(0); }}
      to {{ transform: translateY(-4px); }}
    }}
    @keyframes pulse {{
      from {{ opacity: 0.3; }}
      to {{ opacity: 0.8; }}
    }}
  </style>

  <!-- Background -->
  <rect width="900" height="300" fill="url(#bgGrad)"/>

  <!-- Hex grid -->
  <g>
{hex_svg}
  </g>

  <!-- Nebula glow -->
  <rect width="900" height="300" fill="url(#{nebula_id})"/>

  <!-- Sacred geometry - decorative arcs -->
  <g>
{arcs_svg}
  </g>

  <!-- Particles -->
  <g>
{particles_svg}
  </g>

  {grimoire_svg}

  <!-- Title glow layer -->
  <text x="450" y="148" text-anchor="middle"
    font-family="'Segoe UI',system-ui,-apple-system,sans-serif"
    font-size="54" font-weight="800" letter-spacing="-1"
    fill="url(#titleGlow)">Grimoire Kit</text>

  <!-- Title -->
  <text x="450" y="147" text-anchor="middle"
    font-family="'Segoe UI',system-ui,-apple-system,sans-serif"
    font-size="52" font-weight="800" letter-spacing="-1"
    fill="url(#titleGrad)">Grimoire Kit</text>

  <!-- Decorative line under title -->
  <line x1="310" y1="163" x2="590" y2="163" stroke="url(#titleGrad)" stroke-width="0.8" opacity="0.35"/>

  <!-- Subtitle -->
  <text x="450" y="188" text-anchor="middle"
    font-family="'Segoe UI',system-ui,sans-serif"
    font-size="15" fill="{c['muted']}" letter-spacing="3"
    font-weight="400">COMPOSABLE AI AGENT PLATFORM</text>

  <!-- Feature pills -->
  <g>
{pills_svg}
  </g>

  {version_badge}
</svg>'''

    return svg


# ─── Divider Generation ──────────────────────────────────────────────────────

def generate_divider():
    """Enhanced section divider with hexagonal accent."""
    hex_small = hex_path(400, 2, 3)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 4" fill="none">
<style>
  .l{{stroke:{DARK["primary"]}}}
  .h{{fill:{DARK["primary"]}}}
  @media(prefers-color-scheme:light){{
    .l{{stroke:{LIGHT["primary"]}}}
    .h{{fill:{LIGHT["primary"]}}}
  }}
</style>
<line x1="100" y1="2" x2="360" y2="2" class="l" stroke-width="0.5" opacity="0.3"/>
<path d="{hex_small}" class="h" opacity="0.6"/>
<line x1="440" y1="2" x2="700" y2="2" class="l" stroke-width="0.5" opacity="0.3"/>
</svg>'''


# ─── Preview HTML Generation ─────────────────────────────────────────────────

def generate_preview_html(icon_names):
    """Generate an HTML page for visual review of all assets."""
    icon_grid = "\n".join(
        f'''      <div style="text-align:center;padding:16px;border:1px solid #30363d;border-radius:8px;background:#161b22">
        <img src="icons/{name}.svg" width="48" height="48" style="margin-bottom:8px"><br>
        <code style="color:#8b949e;font-size:11px">{name}</code>
      </div>'''
        for name in icon_names
    )

    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Grimoire Kit — Design System Preview</title>
<style>
  body {{ background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; padding: 40px; max-width: 960px; margin: 0 auto; }}
  h1 {{ color: #a371f7; border-bottom: 1px solid #30363d; padding-bottom: 16px; }}
  h2 {{ color: #c9a0ff; margin-top: 48px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 12px; margin-top: 16px; }}
  .banner {{ margin: 24px 0; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }}
  .banner img {{ width: 100%; display: block; }}
  .divider {{ margin: 32px 0; }}
  .divider img {{ width: 100%; }}
  .size-demo {{ display: flex; align-items: center; gap: 24px; margin-top: 16px; padding: 16px; background: #161b22; border-radius: 8px; }}
  .size-demo span {{ color: #8b949e; font-size: 12px; }}
  code {{ background: #21262d; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  .toggle {{ position: fixed; top: 16px; right: 16px; padding: 8px 16px; background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; cursor: pointer; }}
</style>
</head>
<body>
<button class="toggle" onclick="document.body.style.background=document.body.style.background==='#ffffff'?'#0d1117':'#ffffff';document.body.style.color=document.body.style.color==='#1f2328'?'#c9d1d9':'#1f2328'">Toggle Light/Dark</button>

<h1>Grimoire Kit — Design System</h1>
<p>Asset audit preview. All icons adapt to dark/light mode via CSS <code>@media (prefers-color-scheme)</code>.</p>

<h2>Banner (Dark)</h2>
<div class="banner"><img src="banner-dark.svg" alt="Banner Dark"></div>

<h2>Banner (Light)</h2>
<div class="banner" style="background:#fff"><img src="banner-light.svg" alt="Banner Light"></div>

<h2>Divider</h2>
<div class="divider"><img src="divider.svg" alt="Divider"></div>

<h2>Icon Set ({len(icon_names)} icons)</h2>
<div class="grid">
{icon_grid}
</div>

<h2>Size Reference</h2>
<div class="size-demo">
  <div><img src="icons/grimoire.svg" width="16" height="16"><span> 16px</span></div>
  <div><img src="icons/grimoire.svg" width="24" height="24"><span> 24px</span></div>
  <div><img src="icons/grimoire.svg" width="32" height="32"><span> 32px</span></div>
  <div><img src="icons/grimoire.svg" width="48" height="48"><span> 48px</span></div>
  <div><img src="icons/grimoire.svg" width="64" height="64"><span> 64px</span></div>
</div>

<h2>Inline Usage Demo</h2>
<div style="padding:16px;background:#161b22;border-radius:8px;margin-top:16px">
  <h3 style="display:flex;align-items:center;gap:8px">
    <img src="icons/temple.svg" width="28" height="28"> Architecture
  </h3>
  <p style="color:#8b949e">Section header with inline icon at 28px.</p>

  <table style="width:100%;margin-top:16px">
    <tr>
      <td style="text-align:center;padding:16px;border:1px solid #30363d;border-radius:8px">
        <img src="icons/team.svg" width="36" height="36"><br>
        <b>Team of Teams</b><br>
        <small style="color:#8b949e">Feature card at 36px</small>
      </td>
      <td style="text-align:center;padding:16px;border:1px solid #30363d;border-radius:8px">
        <img src="icons/brain.svg" width="36" height="36"><br>
        <b>Semantic Memory</b><br>
        <small style="color:#8b949e">Feature card at 36px</small>
      </td>
      <td style="text-align:center;padding:16px;border:1px solid #30363d;border-radius:8px">
        <img src="icons/shield-pulse.svg" width="36" height="36"><br>
        <b>Self-Healing</b><br>
        <small style="color:#8b949e">Feature card at 36px</small>
      </td>
    </tr>
  </table>
</div>

</body>
</html>'''


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    icons_dir = os.path.join(base, "icons")
    os.makedirs(icons_dir, exist_ok=True)

    # Generate icons
    icon_names = sorted(ICONS.keys())
    for name in icon_names:
        svg = wrap_icon(ICONS[name])
        path = os.path.join(icons_dir, f"{name}.svg")
        with open(path, "w") as f:
            f.write(svg)
    print(f"[OK] {len(icon_names)} icons → {icons_dir}/")

    # Generate banners
    for mode in ("dark", "light"):
        svg = generate_banner(mode)
        path = os.path.join(base, f"banner-{mode}.svg")
        with open(path, "w") as f:
            f.write(svg)
    print(f"[OK] Banners → {base}/")

    # Generate divider
    svg = generate_divider()
    with open(os.path.join(base, "divider.svg"), "w") as f:
        f.write(svg)
    print(f"[OK] Divider → {base}/")

    # Generate preview
    html = generate_preview_html(icon_names)
    with open(os.path.join(base, "preview.html"), "w") as f:
        f.write(html)
    print(f"[OK] Preview → {base}/preview.html")

    # Summary
    print(f"\n{'─' * 50}")
    print(f"  Design System: {len(icon_names)} icons, 2 banners, 1 divider")
    print(f"  Preview: file://{base}/preview.html")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    main()
