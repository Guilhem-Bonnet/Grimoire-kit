"""Atelier de spec, en-tête compact : le texte d'abord.

À 1440x900 le document commence haut (titre, dates et avancement sur une ligne, une seule barre collante pour les phases, les
étapes et « Valider et découper »), les aides se replient derrière un lien, et rien ne déborde sur un écran étroit.

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 (clair et sombre) ; sans la variable le test de capture ne
fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page, expect

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import APPEAR_MS, CANVAS, _box, _chantier, _open

MOA_HELP = "[data-role='moa-help']"
WHY = "[data-act='moa-why']"
SPLIT = "[data-act='split']"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-entete-compact", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _ready(page: Page) -> None:
    """Le document est dessiné et l'état des exigences lu (le lien d'aide MOA n'existe qu'ensuite)."""
    page.locator(".at-sheet .at-sec").first.wait_for(timeout=APPEAR_MS)
    page.wait_for_function("() => document.querySelector('[data-role=\"live-summary\"]')?.textContent.includes('REQ-001')", timeout=APPEAR_MS)


def test_the_document_starts_high_and_the_help_is_folded(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "En-tete compact", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        canvas = _box(page, "#canvas")
        sheet = _box(page, ".at-sheet")
        assert sheet["y"] <= 260, f"la feuille commence à {sheet['y']} px"
        # Le titre de la première section et son premier paragraphe se lisent aussi dans le premier écran.
        assert _box(page, ".at-sec h2")["y"] < 340
        # Rien n'a disparu : avancement, détail, étapes, phases, bouton et son état sont tous là.
        expect(page.locator("[data-role='avancement']")).to_contain_text("au gabarit", use_inner_text=True)
        expect(page.locator("[data-role='avancement-detail'] summary")).to_be_visible()
        expect(page.locator("[data-role='steps'] li")).to_have_count(4)
        expect(page.locator(".at-phase")).to_have_count(5)
        expect(page.locator("[data-role='live-summary']")).to_contain_text("REQ-001", use_inner_text=True)
        expect(page.locator(SPLIT)).to_be_visible()
        for selector in ("[data-role='steps']", ".at-phases", SPLIT, "[data-role='live-summary']"):  # une seule barre
            inside = page.evaluate("([s]) => !!document.querySelector(s).closest('.at-phasebar')", [selector])
            assert inside, selector
        # Aide MOA repliée par défaut, ouverte sur place par son lien court, refermée par le même.
        expect(page.locator(MOA_HELP)).to_have_count(1)
        expect(page.locator(MOA_HELP)).to_be_hidden()
        why = page.locator(WHY)
        expect(why).to_be_visible()
        expect(why).to_have_attribute("aria-expanded", "false")
        assert canvas["y"] < sheet["y"]
        why.click()
        expect(why).to_have_attribute("aria-expanded", "true")
        expect(page.locator(MOA_HELP)).to_contain_text("governance.roles.moa", use_inner_text=True)
        why.click()
        expect(page.locator(MOA_HELP)).to_be_hidden()


def test_a_refused_split_opens_the_moa_help(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Refus ouvre l'aide")
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        expect(page.locator(MOA_HELP)).to_be_hidden()
        page.locator(SPLIT).click(force=True)  # inerte (grisé) : la raison est redite, et l'aide s'ouvre car la MOA est la cause
        page.locator(MOA_HELP).wait_for(state="visible", timeout=APPEAR_MS)
        expect(page.locator(WHY)).to_have_attribute("aria-expanded", "true")


def test_the_bar_stays_visible_while_scrolling_and_is_keyboard_friendly(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Barre collante", long=True)
    for page in _open(browser, url, f"#atelier?chantier={slug}"):
        _ready(page)
        page.evaluate(f"{CANVAS}.scrollTop = {CANVAS}.scrollHeight")
        bar, canvas = _box(page, ".at-phasebar"), _box(page, "#canvas")
        assert abs(bar["y"] - canvas["y"]) <= 2 and bar["height"] <= 90
        expect(page.locator(SPLIT)).to_be_visible()
        expect(page.locator(".at-phase[data-phase='brief']")).to_be_visible()
        page.evaluate(f"{CANVAS}.scrollTop = 0")
        # Clavier : retour, détail d'avancement, phases, bouton, lien d'aide, dans l'ordre de lecture ; focus visible.
        order: list[str] = []
        page.locator(".at-back").focus()
        for _ in range(16):
            order.append(str(page.evaluate("() => { const a = document.activeElement; return a.getAttribute('data-act') || a.getAttribute('data-phase') || a.className || a.tagName; }")))
            page.keyboard.press("Tab")
        assert order[0] == "at-btn ghost at-back"
        assert order.index("brief") < order.index("split") < order.index("moa-why")
        page.locator(WHY).focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(page.locator(":focus")).not_to_have_css("outline-style", "none")
        page.keyboard.press("Enter")
        page.locator(MOA_HELP).wait_for(state="visible", timeout=APPEAR_MS)


def test_no_horizontal_overflow_on_narrow_widths(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Etroit", long=True)
    for width, height in ((800, 900), (480, 900)):
        for page in _open(browser, url, f"#atelier?chantier={slug}", (width, height)):
            _ready(page)
            page.locator(WHY).click()
            page.locator("[data-role='avancement-detail'] summary").click()
            # La mise en page se pose en place après l'ouverture des détails : on attend l'état voulu (aucun débordement), le test
            # échoue si elle ne s'y pose jamais.
            page.wait_for_function(
                "() => Math.max(...[document.documentElement, document.body, document.getElementById('canvas')].map((n) => n.scrollWidth - n.clientWidth)) <= 1",
                timeout=APPEAR_MS,
            )
            # Hors de la barre des phases : elle défile (ses pastilles ne tronquent plus leur libellé), son conteneur, lui, ne déborde pas.
            page.wait_for_function(
                "(w) => Math.max(...[...document.querySelectorAll('.at-page *:not(.at-phases *)')].map((n) => n.getBoundingClientRect().right)) <= w + 1",
                arg=width,
                timeout=APPEAR_MS,
            )


def test_capture_entete_compact(browser: Browser, project_on: tuple[str, Path]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = _chantier(root, "Spec vivante du cockpit", long=True)
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("GRIMOIRE_E2E_CAPTURE_NAME", "apres")
    for theme in ("light", "dark"):
        for page in _open(browser, url, f"#atelier?chantier={slug}"):
            page.locator(f"[data-theme-pref='{theme}']").click()
            page.locator(".at-sheet .at-sec").first.wait_for(timeout=APPEAR_MS)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(folder / f"01-{name}-{'clair' if theme == 'light' else 'sombre'}-1440.png"))
