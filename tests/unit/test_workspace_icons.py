"""Garde du sprite d'icônes du cockpit (web/workspace/icons.svg).

Le sprite est généré par scripts/build-icon-sprite.py depuis docs/assets/icons
(famille Douce du site, sans leur <style>) et docs/assets/cockpit-icons (chrome
« Douce » : `i-space-*`, `i-ui-*`, grille 24, trait 1,75, un <title> par
symbole), plus un symbole par ancien nom encore cité par le cockpit (table
`ALIASES` du script, dupliqué depuis son remplaçant Douce). Chaque `icon('x')`, `icon: 'x'` ou `icons.svg#x` cité sous
web/workspace/ doit y avoir un symbole, aucun symbole ne porte de couleur
littérale (le sprite suit `currentColor`, donc le thème), et la coque ne
dessine plus d'icône en ligne.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
SPRITE = WORKSPACE / "icons.svg"
SCRIPT = ROOT / "scripts" / "build-icon-sprite.py"


def _aliases() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("build_icon_sprite", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.ALIASES)


COCKPIT_ICONS = ROOT / "docs" / "assets" / "cockpit-icons"
_SYMBOL_RE = re.compile(r'<symbol id="(i-[a-z0-9-]+)"([^>]*)>(.*?)</symbol>', re.DOTALL)
_TAG_RE = re.compile(r"<([a-zA-Z]+)\b([^>]*)>")
_ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|color|oklch|lab)\s*\(", re.IGNORECASE)
_NAMED = re.compile(
    r"(?<![\w-])(?:white|black|red|green|blue|orange|grey|gray|silver|yellow|"
    r"purple|teal|cyan|magenta|navy|olive|maroon|lime|aqua|fuchsia)(?![\w-])",
    re.IGNORECASE,
)
#: Seules valeurs de remplissage et de trait qui n'engagent aucune palette.
_PAINT_OK = {"none", "currentColor"}

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
    cockpit = {p.stem for p in COCKPIT_ICONS.glob("*.svg")}
    aliases = _aliases()
    assert sources and cockpit and not (sources & cockpit)
    assert not (set(aliases) & (sources | cockpit)), "un alias masque une icône réelle"
    assert set(aliases.values()) <= sources, "un alias pointe hors de la famille"
    assert sources | cockpit | set(aliases) == _symbols()


def test_sprite_follows_current_color() -> None:
    text = SPRITE.read_text(encoding="utf-8")
    assert "<style" not in text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text), "couleur littérale dans le sprite"
    assert "currentColor" in text


def _douce_symbols() -> list[tuple[str, dict[str, str], str]]:
    """Les symboles du chrome « Douce » (`i-*`), copiés tels quels dans le sprite :
    (id, attributs du <symbol>, corps). Lus par regex : le sprite est un fichier du
    dépôt, pas une donnée non sûre, et le dépôt n'embarque pas defusedxml."""
    text = SPRITE.read_text(encoding="utf-8")
    return [(sid, dict(_ATTR_RE.findall(attrs)), body) for sid, attrs, body in _SYMBOL_RE.findall(text)]


def test_le_sprite_porte_cinq_icones_d_espace_et_neuf_de_chrome() -> None:
    ids = [sid for sid, _, _ in _douce_symbols()]
    assert len(ids) == len(set(ids)), "identifiants en double"
    assert sum(i.startswith("i-space-") for i in ids) == 5
    assert sum(i.startswith("i-ui-") for i in ids) == 9
    assert all(i.startswith(("i-space-", "i-ui-")) for i in ids)


@pytest.mark.parametrize("symbol", _douce_symbols(), ids=lambda s: s[0])
def test_chaque_symbole_douce_respecte_la_grammaire(symbol: tuple[str, dict[str, str], str]) -> None:
    _, attrs, body = symbol
    assert attrs.get("viewBox") == "0 0 24 24"
    assert attrs.get("stroke-width") == "1.75"
    titles = re.findall(r"<title>([^<]*)</title>", body)
    assert len(titles) == 1 and titles[0].strip(), "un <title> non vide par symbole"
    nodes = [attrs, *(dict(_ATTR_RE.findall(a)) for _, a in _TAG_RE.findall(body))]
    for node in nodes:
        assert "style" not in node, "attribut style dans un symbole Douce"
        for attr in ("fill", "stroke"):
            value = node.get(attr)
            assert value is None or value in _PAINT_OK, f"{attr}={value!r} : couleur littérale"


def test_aucune_couleur_litterale_ni_style_dans_le_sprite() -> None:
    text = SPRITE.read_text(encoding="utf-8")
    assert not _HEX.search(text)
    assert not _FUNC.search(text)
    assert not _NAMED.search(text)
    assert "<style" not in text.lower()


def test_la_coque_appelle_le_sprite_et_ne_dessine_plus_en_ligne() -> None:
    known = _symbols()
    shell = (WORKSPACE / "shell.js").read_text(encoding="utf-8")
    html = (WORKSPACE / "index.html").read_text(encoding="utf-8")
    assert "<path" not in shell
    assert not re.search(r"^\s*path:\s*'", shell, re.MULTILINE)
    assert "<rect" not in html and "<path" not in html
    rail_icons = re.findall(r"icon:\s*'(i-[a-z-]+)'", shell)
    assert len(rail_icons) == 5, "le rail appelle cinq icônes Douce"
    assert html.count("icons.svg#i-ui-pin") == 3, "les trois boutons d'épinglage appellent i-ui-pin"
    called = set(rail_icons) | set(re.findall(r"icons\.svg#(i-[a-z-]+)", html))
    assert called <= known, f"symboles appelés mais absents : {called - known}"


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
    for block in tokens.split(':root[data-theme="light"]')[:2]:
        theme = _resolve(dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6}|var\(--[a-z0-9-]+\));", block)))
        for surface in ("e1", "bar"):
            hi, lo = sorted((_lum(theme["ink3"]), _lum(theme[surface])), reverse=True)
            assert (hi + 0.05) / (lo + 0.05) >= 4.5, (theme["ink3"], surface)


def _resolve(raw: dict[str, str]) -> dict[str, str]:
    """Suit les alias `--e1: var(--panel)` jusqu'à une couleur littérale."""
    out: dict[str, str] = {}
    for name, value in raw.items():
        seen = 0
        while value.startswith("var(") and seen < 8:
            value = raw[value[6:-1]]
            seen += 1
        out[name] = value
    return out
