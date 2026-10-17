"""Le sprite d'icônes maison tient sa grammaire, et la coque ne dessine plus en ligne.

Même esprit que ``test_workspace_tokens.py`` : ce qui se mesure sans navigateur
est mesuré ici. Le sprite est la seule source de dessin des icônes d'espace et
de chrome ; la couleur vient du ``currentColor`` de l'élément hôte.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[2] / "web" / "workspace"
SPRITE = WORKSPACE / "icons.svg"
_NS = "{http://www.w3.org/2000/svg}"

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|color|oklch|lab)\s*\(", re.IGNORECASE)
_NAMED = re.compile(
    r"(?<![\w-])(?:white|black|red|green|blue|orange|grey|gray|silver|yellow|"
    r"purple|teal|cyan|magenta|navy|olive|maroon|lime|aqua|fuchsia)(?![\w-])",
    re.IGNORECASE,
)
#: Seules valeurs de remplissage et de trait qui n'engagent aucune palette.
_PAINT_OK = {"none", "currentColor"}


def _symbols() -> list[ET.Element]:
    return list(ET.parse(SPRITE).getroot().iter(f"{_NS}symbol"))


def test_le_sprite_porte_cinq_icones_d_espace_et_neuf_de_chrome() -> None:
    ids = [s.get("id", "") for s in _symbols()]
    assert len(ids) == len(set(ids)), "identifiants en double"
    assert sum(i.startswith("i-space-") for i in ids) == 5
    assert sum(i.startswith("i-ui-") for i in ids) == 9
    assert all(i.startswith(("i-space-", "i-ui-")) for i in ids)


@pytest.mark.parametrize("symbol", _symbols(), ids=lambda s: s.get("id", "?"))
def test_chaque_symbole_respecte_la_grammaire(symbol: ET.Element) -> None:
    assert symbol.get("viewBox") == "0 0 24 24"
    titles = symbol.findall(f"{_NS}title")
    assert len(titles) == 1 and (titles[0].text or "").strip(), "un <title> non vide par symbole"
    for node in symbol.iter():
        for attr in ("fill", "stroke"):
            value = node.get(attr)
            assert value is None or value in _PAINT_OK, f"{attr}={value!r} : couleur littérale"


def test_aucune_couleur_litterale_ni_style_dans_le_sprite() -> None:
    text = SPRITE.read_text(encoding="utf-8")
    assert not _HEX.search(text)
    assert not _FUNC.search(text)
    assert not _NAMED.search(text)
    assert "<style" not in text.lower()
    assert not [n for n in ET.parse(SPRITE).getroot().iter() if n.get("style")]


def test_la_coque_appelle_le_sprite_et_ne_dessine_plus_en_ligne() -> None:
    known = {s.get("id") for s in _symbols()}
    shell = (WORKSPACE / "shell.js").read_text(encoding="utf-8")
    html = (WORKSPACE / "index.html").read_text(encoding="utf-8")
    assert "<path" not in shell
    assert not re.search(r"^\s*path:\s*'", shell, re.MULTILINE)
    assert "<rect" not in html and "<path" not in html
    rail_icons = re.findall(r"icon:\s*'(i-[a-z-]+)'", shell)
    assert len(rail_icons) == 5
    called = set(rail_icons) | set(re.findall(r"icons\.svg#(i-[a-z-]+)", html))
    assert called <= known, f"symboles appelés mais absents : {called - known}"
