"""Budget de la vitrine (GAO-p39-page-de-001) : poids, JS, requêtes tierces, mouvement réduit.

``web/index.html`` est l'unique page (vitrine et cockpit). Le budget compte HTML + CSS + JS,
hors images et polices (les pictogrammes ``data:`` en CSS sont des images).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")

BUDGET_TOTAL = 120 * 1024
BUDGET_JS = 20 * 1024


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _scripts() -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script\s*>", PAGE, re.DOTALL | re.IGNORECASE)


def test_html_css_js_sous_le_budget() -> None:
    sans_data = re.sub(r"url\((['\"]?)data:.*?\1\)", "url()", PAGE)
    sans_data = re.sub(r"<link[^>]*href=\"data:[^\"]*\"[^>]*>", "", sans_data)
    assert _size(sans_data) <= BUDGET_TOTAL, _size(sans_data)


def test_js_sous_le_budget() -> None:
    assert sum(_size(s) for s in _scripts()) <= BUDGET_JS


def test_aucun_fichier_externe_charge_par_la_page() -> None:
    """Zéro requête tierce : toute ressource chargée (src, link, url(), import) est locale ou data:."""
    refs = re.findall(r"""<(?:script|img|video|source|iframe|link)\b[^>]*?\b(?:src|href)=["']([^"']+)["']""", PAGE)
    refs = [r for r in refs if not r.startswith(("#", "data:"))]
    # <link rel=canonical> et métadonnées de partage ne chargent rien
    tags = re.findall(r"<link\b[^>]*>", PAGE)
    chargeants = [t for t in tags if re.search(r"rel=\"(?:stylesheet|preload|icon|modulepreload)\"", t)]
    for t in chargeants:
        href = re.search(r'href="([^"]+)"', t).group(1)
        assert not re.match(r"(?:https?:)?//", href), t[:80]
    for r in refs:
        if r.startswith(("http:", "https:", "//")):
            assert any(r in t for t in tags if "canonical" in t), f"ressource tierce : {r}"
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", PAGE)
    assert not re.search(r"@import\s+(?:url\()?['\"]?(?:https?:)?//", PAGE)
    for js in _scripts():
        assert not re.search(r"""fetch\(\s*['"](?:https?:)?//""", js)
        assert not re.search(r"""import\(\s*['"](?:https?:)?//""", js)


def test_mouvement_reduit_supprime_scenes_collantes_et_animations_liees_au_scroll() -> None:
    bloc = re.search(r"@media \(prefers-reduced-motion:reduce\)\{\s*\.plan\.tall\{[^@]*?\n\}", PAGE, re.DOTALL)
    assert bloc, "bloc reduced-motion des plans absent"
    css = bloc.group(0)
    assert "position:static" in css and "min-height:0" in css and ".beat{opacity:1" in css
    # le pilotage du scroll ne s'installe qu'après le test matchMedia
    js = next(s for s in _scripts() if "data-scroll" in s)
    garde = js.index("if(rm)return;")
    assert "prefers-reduced-motion: reduce" in js[:garde]
    assert "addEventListener('scroll'" in js[garde:]
    assert "addEventListener('scroll'" not in js[:garde]


def test_dix_plans_dans_l_ordre() -> None:
    nums = [int(n) for n in re.findall(r'class="nb" data-plan="(\d+)"', PAGE)]
    ids = re.findall(r'<section class="[^"]*\bplan\b[^"]*" id="p(\d+)"', PAGE)
    assert sorted({1, *nums, *map(int, ids)}) == list(range(1, 11))
    assert [int(i) for i in ids] == sorted(int(i) for i in ids)


def test_la_section_limites_est_citee_et_datee() -> None:
    sect = PAGE[PAGE.index('id="limites"') :]
    sect = sect[: sect.index("</section>")]
    assert re.search(r"\d{4}-\d{2}-\d{2}", sect)
    assert 'href="docs/' in sect


def test_bobines_de_la_page_existent_et_sont_a_jour() -> None:
    index = json.loads((WEB / "data" / "reels" / "index.json").read_text(encoding="utf-8"))
    ids = {r["id"] for r in index["reels"]}
    cites = set(re.findall(r'data-reel="([^"]+)"', PAGE))
    assert cites and cites <= ids
    for rid in cites:
        reel = json.loads((WEB / "data" / "reels" / f"{rid}.json").read_text(encoding="utf-8"))
        for deb, fin in re.findall(rf'data-reel="{rid}" data-frames="(\d+)-(\d+)"', PAGE):
            assert int(fin) < len(reel["frames"]) and int(deb) <= int(fin)


def test_lexique_pas_de_gain_chiffre() -> None:
    texte = re.sub(r"<(script|style)\b.*?</\1>", "", PAGE, flags=re.DOTALL)
    assert not re.search(r"\d\s*(?:%|x\b|×|fois)", re.sub(r"data-[a-z-]+=\"[^\"]*\"", "", texte))
    assert "Aucun gain n'est mesuré" in PAGE
