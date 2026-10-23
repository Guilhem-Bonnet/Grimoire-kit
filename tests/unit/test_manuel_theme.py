"""Garde du thème Douce du manuel (mkdocs.yml, docs/stylesheets/douce.css)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "docs" / "stylesheets" / "douce.css"
MKDOCS = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
FONTS = ROOT / "docs" / "assets" / "fonts"


def test_le_manuel_charge_le_theme_douce_seul() -> None:
    assert "stylesheets/douce.css" in MKDOCS
    assert "forge-theme" not in MKDOCS and "presentation-onepager" not in MKDOCS
    assert [p.name for p in (ROOT / "docs" / "stylesheets").glob("*.css")] == ["douce.css"]


def test_aucune_ressource_tierce_dans_le_theme() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?https?:", css)
    assert re.search(r"^\s*font:\s*false", MKDOCS, re.MULTILINE), "theme.font doit rester à false"
    assert "googleapis" not in MKDOCS


def test_les_polices_declarees_sont_embarquees() -> None:
    css = CSS.read_text(encoding="utf-8")
    files = re.findall(r"url\(\"\.\./assets/fonts/([^\"]+)\"\)", css)
    assert len(files) == 5
    for name in files:
        assert (FONTS / name).is_file(), name


def test_les_deux_schemas_portent_les_tokens_du_site() -> None:
    css = CSS.read_text(encoding="utf-8")
    for token in ("#eef0f4", "#3451d1", "#b4441d", "#0f1115", "#8ea2ff", "#f08a5d"):
        assert token in css, token
    assert 'data-md-color-scheme="default"' in css and 'data-md-color-scheme="slate"' in css
    assert MKDOCS.count("scheme:") >= 2
