"""Garde du sprite d'icônes du cockpit (web/workspace/icons.svg).

Le sprite est généré depuis docs/assets/icons par scripts/build-icon-sprite.py ;
chaque `icon('x')`, `icon: 'x'` ou `icons.svg#x` cité sous web/workspace/ doit
y avoir un symbole, et aucun symbole ne porte de couleur littérale (le sprite
suit `currentColor`, donc le thème).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
SPRITE = WORKSPACE / "icons.svg"
SCRIPT = ROOT / "scripts" / "build-icon-sprite.py"

_REFS = (
    re.compile(r"""\bicon\(\s*['"]([a-z0-9-]+)['"]"""),
    re.compile(r"""\bicon:\s*['"]([a-z0-9-]+)['"]"""),
    re.compile(r"""icons\.svg#([a-z0-9-]+)"""),
)
# Noms d'icônes portés par des tables (`STATE_ICON`) ou des littéraux de
# `kpiCard` : couverts par `icon:` ci-dessus ; les valeurs de STATE_ICON le sont ici.
_STATE_ICON = re.compile(r"STATE_ICON\s*=\s*\{([^}]*)\}")


def _symbols() -> set[str]:
    return set(re.findall(r'<symbol id="([a-z0-9-]+)"', SPRITE.read_text(encoding="utf-8")))


def _referenced() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in [*WORKSPACE.rglob("*.js"), *WORKSPACE.glob("*.html")]:
        text = path.read_text(encoding="utf-8")
        names: list[str] = []
        for pattern in _REFS:
            names += pattern.findall(text)
        for table in _STATE_ICON.findall(text):
            names += re.findall(r""":\s*['"]([a-z0-9-]+)['"]""", table)
        for name in names:
            refs.setdefault(name, set()).add(path.name)
    return refs


def test_sprite_is_up_to_date() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_every_referenced_icon_exists_in_sprite() -> None:
    symbols = _symbols()
    refs = _referenced()
    assert refs, "aucune icône référencée : la regex de garde est cassée"
    missing = {name: sorted(files) for name, files in refs.items() if name not in symbols}
    assert not missing, f"icônes citées mais absentes du sprite : {missing}"


def test_every_source_icon_is_in_sprite() -> None:
    sources = {p.stem for p in (ROOT / "docs" / "assets" / "icons").glob("*.svg")}
    assert sources == _symbols()


def test_sprite_follows_current_color() -> None:
    text = SPRITE.read_text(encoding="utf-8")
    assert "<style" not in text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text), "couleur littérale dans le sprite"
    assert "currentColor" in text


def _lum(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_disabled_ink_keeps_contrast() -> None:
    """`.seg > button:disabled`, `.btn:disabled` : encre --ink3, sans opacité, ≥ 4,5:1."""
    css = (WORKSPACE / "shell.css").read_text(encoding="utf-8")
    block = re.search(r"\.seg > button:disabled \{([^}]*)\}", css)
    assert block and "opacity" not in block.group(1)
    tokens = (WORKSPACE / "tokens.css").read_text(encoding="utf-8")
    dark = dict(re.findall(r"--(ink3|e1|bar):\s*(#[0-9a-fA-F]{6})", tokens.split(':root[data-theme="light"]')[0]))
    light = dict(re.findall(r"--(ink3|e1|bar):\s*(#[0-9a-fA-F]{6})", tokens.split(':root[data-theme="light"]')[1]))
    for theme in (dark, light):
        for surface in ("e1", "bar"):
            hi, lo = sorted((_lum(theme["ink3"]), _lum(theme[surface])), reverse=True)
            assert (hi + 0.05) / (lo + 0.05) >= 4.5, (theme["ink3"], surface)
