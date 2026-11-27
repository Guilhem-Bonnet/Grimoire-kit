"""Atelier de spec, pleine largeur : le document prend la toile, l'inspecteur et le dock se replient.

Lu à l'écran, dans un vrai navigateur : à l'arrivée dans l'Atelier la coque pose son « Plein écran » (`data-focus`, le mécanisme
de ⇧⌘L / ⇧⌘F existant) ; à la sortie, l'état d'avant revient tel quel ; le bouton « Pleine largeur » rouvre ou replie les
panneaux et le choix est mémorisé par navigateur (localStorage, sans lequel la page marche de même).

``GRIMOIRE_E2E_CAPTURES=<dossier>`` écrit les captures 1440x900 (``01-atelier-avant`` / ``01-atelier-apres``) ; sans la
variable le test de capture ne fait rien (il ne se skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright absent — harnais e2e ignoré")

from playwright.sync_api import Browser, Page

from grimoire.tools import workspace_atelier

from .conftest import _serve_empty_project
from .test_workspace_atelier_mise_en_page import APPEAR_MS, _box, _chantier, _open

TOGGLE = "[data-role='atelier-largeur']"
PREF_KEY = "grimoire.atelier.panneaux"


@pytest.fixture(scope="module")
def project_on(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    roots: list[Path] = []
    for url in _serve_empty_project(tmp_path_factory, "atelier-pleine-largeur", roots):
        workspace_atelier.set_atelier(roots[0], True)
        yield url, roots[0]


def _focus(page: Page) -> str:
    return str(page.evaluate("document.documentElement.dataset.focus"))


def _visible(page: Page, selector: str) -> bool:
    return bool(page.evaluate("(s) => { const n = document.querySelector(s); return !!n && getComputedStyle(n).display !== 'none'; }", selector))


def _enter(page: Page, slug: str) -> None:
    page.evaluate("(h) => { location.hash = h; }", f"#atelier?chantier={slug}")
    page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)


def _leave(page: Page) -> None:
    page.evaluate("() => { location.hash = '#hotes'; }")
    page.wait_for_function("document.body.dataset.activeArea === 'configuration'", timeout=APPEAR_MS)


def _panels_on(page: Page) -> None:
    """L'état d'avant l'Atelier : les panneaux d'atelier rouverts par le raccourci « Panneaux »."""
    if page.evaluate("document.documentElement.dataset.atelier") != "on":
        page.locator("#st-atelier").click()
    assert _visible(page, "#dock")


def test_entering_the_atelier_folds_the_panels_and_leaving_restores_them(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Pleine largeur", long=True)
    for page in _open(browser, url, "#hotes"):
        _panels_on(page)
        assert _focus(page) == "off"
        _enter(page, slug)
        # Le plein écran de la coque est posé : ni dock, ni inspecteur, et la feuille prend la largeur utile.
        assert _focus(page) == "on"
        assert not _visible(page, "#dock") and not _visible(page, "#panel-inspector")
        assert page.locator(TOGGLE).get_attribute("aria-pressed") == "true"
        sheet = _box(page, ".at-sheet")
        assert sheet["width"] > 900, sheet
        # Colonne de lecture centrée : la feuille n'est pas collée à gauche.
        left, right = sheet["x"], 1440 - (sheet["x"] + sheet["width"])
        assert left > 24 and abs(left - right) < 420
        assert _box(page, "[data-role='assistant']")["x"] >= sheet["x"] + sheet["width"] - 1  # l'assistant reste accessible
        # Sortie : l'état d'avant revient, panneaux d'atelier et plein écran compris.
        _leave(page)
        assert _focus(page) == "off"
        assert _visible(page, "#dock")
        assert page.evaluate("document.documentElement.dataset.atelier") == "on"
        # Le raccourci « Plein écran » n'est pas cassé : il fonctionne hors Atelier comme dans l'Atelier.
        page.locator("#st-fullscreen").click()
        assert _focus(page) == "on" and not _visible(page, "#dock")
        page.locator("#st-fullscreen").click()
        assert _focus(page) == "off" and _visible(page, "#dock")


def test_a_fullscreen_set_before_the_atelier_is_given_back_untouched(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Plein ecran avant", long=False)
    for page in _open(browser, url, "#hotes"):
        page.locator("#st-fullscreen").click()
        assert _focus(page) == "on"
        _enter(page, slug)
        assert _focus(page) == "on"
        _leave(page)
        assert _focus(page) == "on", "le plein écran posé avant l'Atelier survit à l'Atelier"


def test_reopening_the_panels_in_the_atelier_is_remembered_by_browser(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Choix memorise", long=False)
    for page in _open(browser, url, "#hotes"):
        _panels_on(page)
        _enter(page, slug)
        assert _focus(page) == "on"
        page.locator(TOGGLE).click()
        assert _focus(page) == "off" and _visible(page, "#dock")
        assert page.locator(TOGGLE).get_attribute("aria-pressed") == "false"
        assert page.evaluate("(k) => localStorage.getItem(k)", PREF_KEY) == "panels"
        # Respecté aux entrées suivantes, et au rechargement.
        _leave(page)
        _enter(page, slug)
        assert _focus(page) == "off" and _visible(page, "#dock")
        page.reload()
        page.wait_for_selector("body[data-ready='1']", timeout=APPEAR_MS)
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        assert _focus(page) == "off"
        # Replier de nouveau est mémorisé aussi.
        page.locator(TOGGLE).click()
        assert _focus(page) == "on" and page.evaluate("(k) => localStorage.getItem(k)", PREF_KEY) == "wide"


def test_the_atelier_is_wide_even_when_storage_is_blocked(browser: Browser, project_on: tuple[str, Path]) -> None:
    url, root = project_on
    slug = _chantier(root, "Sans stockage", long=False)
    for page in _open(browser, url, f"#atelier?chantier={slug}", block_storage=True):
        page.locator(".at-sheet").wait_for(timeout=APPEAR_MS)
        assert _focus(page) == "on"
        page.locator(TOGGLE).click()
        assert _focus(page) == "off"  # le choix vaut pour la session, faute de pouvoir le mémoriser


def test_capture_pleine_largeur(browser: Browser, project_on: tuple[str, Path]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    url, root = project_on
    slug = _chantier(root, "Spec vivante du cockpit", long=True)
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    for page in _open(browser, url, "#hotes"):
        _panels_on(page)
        _enter(page, slug)
        page.locator(TOGGLE).click()  # les panneaux rouverts : l'état d'avant le lot
        page.wait_for_timeout(300)
        page.screenshot(path=str(folder / "01-atelier-avant-1440.png"))
        page.locator(TOGGLE).click()  # pleine largeur
        page.wait_for_timeout(300)
        page.screenshot(path=str(folder / "01-atelier-apres-1440.png"))
