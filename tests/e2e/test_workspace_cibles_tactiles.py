"""Cibles tactiles du cockpit (GAO-i14-rendre-002).

À 390 px, les rubriques de Savoir et de Configuration tombaient à 18 px de haut
(``#section-tabs .tab { height: 100% }`` dans un conteneur à hauteur automatique)
et bien d'autres contrôles restaient sous 30 px. Le jeton ``--hit`` (44 px) porte
la taille minimale d'une cible sous ``(max-width: 640px), (pointer: coarse)`` ;
le bureau garde ses dimensions.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

SPACES = ["a-traiter", "travail", "flotte", "savoir", "configuration"]
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
HIT = 44

#: Exclusions, une par raison :
#:  - les cases à cocher et boutons radio natifs (``input``) ne sont pas des ``button`` ;
#:    ils ne sont pas retenus par le sélecteur, et leur zone est portée par leur <label> ;
#:  - un lien dans une phrase (``p a``, ``li a`` sans classe ``btn``) suit la ligne de
#:    texte : WCAG 2.5.8 l'exempte (cible « en ligne »), seuls ``a.btn`` sont mesurés ;
#:  - ``.skip-link`` : lien d'évitement, hors flux tant qu'il n'a pas le focus.
_MEASURE_JS = """
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = [];
  for (const el of document.querySelectorAll('button, a.btn, [role=tab]')) {
    if (el.closest('[hidden], [inert]') || el.matches('.skip-link, .panel-close[hidden]')) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right <= 0 || r.bottom <= 0 || r.left >= vw || r.top >= vh) continue;
    // Une cible rognée par son conteneur n'est pas une cible : elle doit tenir dans la fenêtre,
    // ou vivre dans un conteneur qui défile exprès.
    let scrolls = false;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') { scrolls = true; break; }
    }
    const clipped = !scrolls && (r.right > vw + 1 || r.left < -1);
    out.push({
      clipped,
      sel: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
        '.' + String(el.className).trim().split(/\\s+/).join('.'),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  }
  return out;
}
"""

_HEIGHTS_JS = """
() => {
  const h = (sel) => [...document.querySelectorAll(sel)]
    .filter((e) => e.getBoundingClientRect().height > 0)
    .map((e) => [e.id || e.className, Math.round(e.getBoundingClientRect().height)]);
  return { spaces: h('[id^=tab-space-]'), sections: h('[id^=tab-section-]'), chips: h('.chip') };
}
"""


def _open(browser: Browser, served: str, viewport: dict[str, int], **kw: object) -> tuple[Page, object]:
    context = browser.new_context(viewport=viewport, reduced_motion="reduce", **kw)  # type: ignore[arg-type]
    page = context.new_page()
    page.goto(f"{served}/workspace/index.html", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    return page, context


@pytest.fixture
def phone(browser: Browser, served: str) -> Iterator[Page]:
    page, context = _open(browser, served, PHONE, has_touch=True)
    try:
        yield page
    finally:
        context.close()  # type: ignore[attr-defined]


@pytest.fixture
def desktop(browser: Browser, served: str) -> Iterator[Page]:
    page, context = _open(browser, served, DESKTOP)
    try:
        yield page
    finally:
        context.close()  # type: ignore[attr-defined]


def _goto(page: Page, space: str) -> None:
    page.evaluate("(id) => window.GrimoireWorkspace.goto(id)", space)
    page.wait_for_function("(id) => window.GrimoireWorkspace.space === id", arg=space)
    page.wait_for_load_state("networkidle")
    page.evaluate("() => document.fonts.ready")
    page.wait_for_function("() => document.getElementById('canvas').getAttribute('aria-busy') !== 'true'")
    page.wait_for_timeout(250)


@pytest.mark.parametrize("space", SPACES)
def test_a_390_px_toute_cible_fait_au_moins_44_px(phone: Page, space: str) -> None:
    """Chaque ``button``, ``a.btn`` et ``[role=tab]`` visible dans la fenêtre fait
    au moins ``--hit`` dans les deux sens. Exclusions : voir ``_MEASURE_JS``."""
    _goto(phone, space)
    small = [t for t in phone.evaluate(_MEASURE_JS) if t["w"] < HIT or t["h"] < HIT or t["clipped"]]
    assert not small, f"{space}: {len(small)} cibles sous {HIT} px : {small}"


@pytest.mark.parametrize("space", SPACES)
def test_a_390_px_la_page_ne_defile_pas_a_l_horizontale(phone: Page, space: str) -> None:
    _goto(phone, space)
    assert phone.evaluate("() => document.documentElement.scrollWidth === window.innerWidth")


#: Instantané des mesures du bureau avant le jeton (main, 3.76.0).
_DESKTOP_SNAPSHOT = {
    "tab-space-": 34,
    "tab-section-": 34,
    "project-chip": 30,
    "palette-open": 30,
}


@pytest.mark.parametrize("space", ["travail", "savoir"])
def test_a_1440_px_les_hauteurs_du_bureau_sont_inchangees(desktop: Page, space: str) -> None:
    _goto(desktop, space)
    heights = desktop.evaluate(
        """() => [...document.querySelectorAll('[id^=tab-space-], [id^=tab-section-], #project-chip, #palette-open')]
          .map((e) => [e.id, Math.round(e.getBoundingClientRect().height)]).filter(([, h]) => h > 0)"""
    )
    assert heights
    for ident, height in heights:
        expected = next(v for k, v in _DESKTOP_SNAPSHOT.items() if ident.startswith(k))
        assert height == expected, (ident, height, expected)
